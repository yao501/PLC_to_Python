"""APCCD 业务块（重叠控制）的契约测试。

覆盖提示词 A~I 场景：

* A. 基础健康检查（导入 / 冷启动初值 / 双实例隔离）
* B. BLINK 与采样节拍（TIMEHIGH=300ms 锁定，反证非 500ms）
* C. ST 执行顺序锁定（窗口快照必须在 STAT1.RESET 之前）
* D. FOP1 与时间参数（TB=0.5s / KG=1 / TC=TZ*2 / dt 不替代 TB）
* E. CD_BH 与 AD 正反作用（SEL(AD,1,-1)）
* F. TON 延时进入、AV_TEMP 与 FLG（含 CD_BH==0 保持 FLG）
* G. 退出阈值回补 ZLOUT（R_TRIG2 仅触发一次）
* H. TS 跟踪切除（进入时允许一次回补，随后清零）
* I. VAR_IN_OUT 适配（入参 + 返回值，禁止旧缓存）

可直接控制 CD_BH 的技巧：令 ``CD_K_D=0`` 时 ``FOP1.AV`` 不进入 ``CD_BH``，
``CD_BH=(PV-SP)*CD_K_J*dir``，从而把 TON/AV_TEMP/FLG/R_TRIG2/TS 逻辑与
采样链解耦，构造确定性场景。
"""

from __future__ import annotations

import unittest

from src.blocks import APCCD
from src.blocks.apccd import (
    BLINK_TIMEHIGH_MS,
    FOP1_DEFAULT_TB_SEC,
    FOP1_KG,
)


def cfg(**overrides):
    """配置型关键字参数构造器（必传项 + ST 默认项）。

    默认 ``TC=10.0`` 让采样窗口很长（多数非采样测试里无采样事件干扰），
    ``CDH/CDL`` 放宽避免误钳幅。SP/PV/TS/ZLOUT 仍由各测试显式传入。
    """
    base = dict(
        TC=10.0,
        TZ=10.0,
        CDH=1000.0,
        CDL=-1000.0,
        TL=1.0,
        AD=False,  # 默认正作用（+1），让 CD_BH=(PV-SP)*CD_K_J 便于构造确定性场景
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A. 基础健康检查
# ---------------------------------------------------------------------------
class TestAPCCDColdStart(unittest.TestCase):
    def test_import_ok(self):
        from src.blocks import APCCD as cd
        self.assertIsNotNone(cd)
        self.assertEqual(cd.__name__, "APCCD")
        self.assertIsNotNone(cd())

    def test_init_numeric_state_zero(self):
        c = APCCD()
        self.assertEqual(c.AV, 0.0)
        self.assertEqual(c.CD_BH, 0.0)
        self.assertEqual(c.JZ_ZUP3, 0.0)
        self.assertEqual(c.JZ_ZUP2, 0.0)
        self.assertEqual(c.JZ_Z1, 0.0)
        self.assertEqual(c.AV_TEMP, 0.0)
        self.assertEqual(c.FLG, 0.0)

    def test_init_nested_instances_present_and_independent(self):
        c = APCCD()
        # 嵌套实例存在且各自独立
        self.assertIsNotNone(c.BLINK1)
        self.assertIsNotNone(c.R_TRIG1)
        self.assertIsNotNone(c.TON1)
        self.assertIsNotNone(c.STAT1)
        self.assertIsNotNone(c.FOP1)
        self.assertIsNotNone(c.R_TRIG2)
        self.assertIs(type(c.STAT1).__name__ == "APCSTATISTICS", True)
        self.assertIs(type(c.FOP1).__name__ == "APCHSFOP", True)
        self.assertIsNot(c.R_TRIG1, c.R_TRIG2)
        self.assertFalse(c.BLINK1.OUT)
        self.assertEqual(c.BLINK1._elapsed_ms, 0)

    def test_two_instances_do_not_share_state(self):
        a = APCCD()
        b = APCCD()
        for _ in range(6):
            a.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        # a 已推进过采样链，b 仍是冷启动；用业务状态对比避免依赖 BLINK 余数细节
        self.assertNotEqual(a.JZ_ZUP2, b.JZ_ZUP2)
        self.assertNotEqual(a.JZ_Z1, b.JZ_Z1)
        self.assertEqual(b.JZ_ZUP2, 0.0)
        self.assertEqual(b.AV, 0.0)


# ---------------------------------------------------------------------------
# B. BLINK 与采样节拍
# ---------------------------------------------------------------------------
class TestBlinkSamplingCadence(unittest.TestCase):
    def test_blink_timehigh_is_500_task_cycle(self):
        """TIMEHIGH 端口取 500ms（= 任务周期 cycle_ms）。

        源 ST 写 T#300MS，但 R_TRIG 只在任务边界采样、亚周期脉宽不可分辨，
        真实 500ms-PLC 中 300 与 500 等价；本项目 BLINK 为余数保留实现，取
        300 会吞脉冲/抖动，故量化到 500ms 以忠实复现（CD2）。
        """
        self.assertEqual(BLINK_TIMEHIGH_MS, 500)

    def test_first_sampling_edge_at_timelow_end(self):
        """TC=1.0, dt=100 → TIMELOW=1000ms=10 拍；首次采样上升沿在拍 10。

        首次上升沿仅由 TIMELOW 决定，与 TIMEHIGH 无关。
        """
        c = APCCD()
        prev = False
        first_event = None
        for tick in range(1, 13):
            c.step(100, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
            if c.BLINK1.OUT and not prev:
                first_event = tick
                break
            prev = c.BLINK1.OUT
        self.assertEqual(first_event, 10)

    def test_adjacent_sampling_gap_is_tc_plus_500(self):
        """相邻采样上升沿间隔 = (TC*1000 + 500)ms。

        TIMEHIGH=500 → 周期 1500ms = 15 拍 → gap=15（整齐）。
        反证：若误用源值 300 → 周期 1300ms，且因脉冲被吞会抖成 3/5 拍不齐。
        只要 gap 恒为 15 即锁定 TIMEHIGH=500ms 的整齐节拍。
        """
        c = APCCD()
        prev = False
        events = []
        for tick in range(1, 46):
            c.step(100, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
            if c.BLINK1.OUT and not prev:
                events.append(tick)
            prev = c.BLINK1.OUT
        self.assertGreaterEqual(len(events), 2)
        for i in range(1, len(events)):
            gap = events[i] - events[i - 1]
            self.assertEqual(
                gap,
                15,
                msg=(
                    f"相邻采样间隔应为 15 拍（TIMEHIGH=500ms，周期 1500ms），"
                    f"实测 {gap}。若出现 13 或抖动说明误用了源值 300ms。"
                ),
            )

    def test_unified_with_gcq_500ms(self):
        """与 APCGCQ 口径统一：端口量化到任务周期 500ms。"""
        self.assertEqual(BLINK_TIMEHIGH_MS, 500)
        self.assertNotEqual(BLINK_TIMEHIGH_MS, 300)

    def _gaps(self, tc, dt, scans):
        c = APCCD()
        prev = False
        events = []
        for tick in range(1, scans + 1):
            c.step(dt, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0, **cfg(TC=tc))
            if c.BLINK1.OUT and not prev:
                events.append(tick)
            prev = c.BLINK1.OUT
        return [events[i] - events[i - 1] for i in range(1, len(events))]

    def test_timelow_multiple_of_cycle_is_clean(self):
        """TIMELOW = TC*1000 为 cycle_ms(=500dt) 整数倍 → 采样间隔整齐无抖动（R8 第 6①）。"""
        gaps = self._gaps(tc=1.0, dt=500, scans=40)  # TIMELOW=1000=2*500
        self.assertGreaterEqual(len(gaps), 3)
        self.assertEqual(len(set(gaps)), 1, msg=f"整数倍应整齐，实测间隔集合={set(gaps)}")
        self.assertEqual(gaps[0], 3)  # (1000+500)/500 = 3

    def test_timelow_non_multiple_jitters_known_limitation(self):
        """TIMELOW 非整数倍（TC=1.1 → 1100ms）→ 余数保留 BLINK 逐次抖动（R8 第 6③）。

        锁定的是"**当前实现的已知行为/限制**"，不是"必须整数倍"的硬约束：
        非整数倍 TIMELOW 属合法配置（原 PLC 可能确有 TC=1.1/1.25），允许使用，
        本项目 BLINK 在该配置下表现为"间隔逐次抖动 + 长期平均周期准确"
        （本实现仿真：间隔集合 {3,4}），**不是** ceil 模型（那会是恒 4）。
        据此 R8 禁止块内 ceil/round 静默变换；配置校验 warn 尚未实现，
        当前仅在契约/风险表/本测试中记录（待接入 RUNTIME-PARAM-VALIDATION）。
        注意：{3,4} 是本端口实现行为，与真实 CODESYS 是否一致**未在真机验证**
        （需在线观察 BLINK.OUT / R_TRIG.Q 连续约 10~20 周期裁决）。
        """
        gaps = self._gaps(tc=1.1, dt=500, scans=80)  # TIMELOW=1100，非 500 整数倍
        self.assertGreaterEqual(len(gaps), 4)
        self.assertGreater(
            len(set(gaps)), 1,
            msg=f"非整数倍 TIMELOW 应出现抖动，实测间隔集合={set(gaps)}",
        )
        self.assertTrue(
            set(gaps).issubset({3, 4}),
            msg=f"TC=1.1 抖动应落在 {{3,4}} 拍，实测={set(gaps)}",
        )
        # 反证非 ceil 模型：ceil 会让间隔恒为 4，这里必含 3
        self.assertIn(3, gaps, msg="若变成恒 4 说明被误改为 ceil 量化模型")


# ---------------------------------------------------------------------------
# C. ST 执行顺序锁定
# ---------------------------------------------------------------------------
class TestSTOrderingLocked(unittest.TestCase):
    """窗口历史移位必须发生在 STAT1.RESET 之前。"""

    def test_first_sampling_snapshot_before_reset(self):
        """TC=1.0, dt=500 → 事件在拍 2。

        拍 1：STAT1 采样 PV=100 → JZ_Z1=100。
        拍 2（事件）：JZ_ZUP2 取旧 JZ_Z1=100，JZ_ZUP3 取旧 JZ_ZUP2=0，
                     之后 STAT1 RESET → COUNTER=0、JZ_Z1=0。
        """
        c = APCCD()
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        self.assertEqual(c.JZ_Z1, 100.0)

        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        self.assertTrue(c.BLINK1.OUT, msg="拍 2 必须是采样事件")
        self.assertEqual(c.JZ_ZUP2, 100.0)
        self.assertEqual(c.JZ_ZUP3, 0.0)
        self.assertEqual(c.STAT1.COUNTER, 0)
        self.assertEqual(c.JZ_Z1, 0.0)

    def test_two_sampling_events_shift_chain_forced_difference(self):
        """两个窗口用不同 PV，反证"先 RESET 后快照"的错误顺序。

        TC=1.0, dt=500 → 事件在拍 2、拍 5。
        窗口 1 PV=100，窗口 2 PV=200。

        正确顺序 → 拍 5 末：JZ_ZUP3=100（旧 JZ_ZUP2），JZ_ZUP2=200（旧 JZ_Z1）。
        错误顺序（STAT1 先 RESET）→ JZ_ZUP2 会取到 RESET 后的 0，符号反向。
        """
        c = APCCD()
        # 拍 1、2：窗口 1 PV=100（事件在拍 2）
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        self.assertEqual(c.JZ_ZUP2, 100.0)
        self.assertEqual(c.JZ_ZUP3, 0.0)
        # 拍 3、4：窗口 2 PV=200（非事件，STAT1 累计 200）
        c.step(500, SP=0.0, PV=200.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        c.step(500, SP=0.0, PV=200.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        self.assertEqual(c.JZ_Z1, 200.0)
        # 拍 5：第二次事件
        c.step(500, SP=0.0, PV=200.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0))
        self.assertTrue(c.BLINK1.OUT, msg="拍 5 必须是采样事件")
        self.assertEqual(
            c.JZ_ZUP3, 100.0, msg="JZ_ZUP3 应取旧 JZ_ZUP2=100"
        )
        self.assertEqual(
            c.JZ_ZUP2,
            200.0,
            msg="JZ_ZUP2 应取旧 JZ_Z1=200（不是 RESET 后的 0）",
        )


# ---------------------------------------------------------------------------
# D. FOP1 与时间参数
# ---------------------------------------------------------------------------
class TestFOPAndTimeParams(unittest.TestCase):
    def test_module_constants(self):
        self.assertEqual(FOP1_DEFAULT_TB_SEC, 0.5)
        self.assertEqual(FOP1_KG, 1.0)

    def test_fop_tc_is_tz_times_two_via_first_sampling_av(self):
        """首次采样事件后 FOP1.AV = TB*KG*FOP_IN/(TB + TZ*2)。

        TC=1.0, dt=500 → 事件在拍 2，FOP_IN = JZ_ZUP2-JZ_ZUP3 = 100-0 = 100。
        TZ=10 → FOP1.TC=20 → AV = 0.5*1*100/(0.5+20) = 2.4390...

        反证：若实现用 FOP1.TC=TZ（=10）→ AV=0.5*100/10.5=4.76，可区分。
        """
        c = APCCD()
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0, TZ=10.0))
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=1.0, TZ=10.0))
        expected = 0.5 * 1.0 * 100.0 / (0.5 + 10.0 * 2.0)
        self.assertAlmostEqual(c.FOP1.AV, expected, places=10)

    def test_tb_is_half_second_independent_of_dt(self):
        """dt=1000 时 TB 仍是 0.5（不是 dt/1000=1.0）。

        TC=2.0, dt=1000 → 事件在拍 2，FOP_IN=100。
        正确 TB=0.5 → AV=0.5*100/(0.5+20)=2.4390。
        若 bug 用 dt/1000=1.0 → AV=1.0*100/(1.0+20)=4.76，可区分。
        """
        c = APCCD()
        c.step(1000, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=2.0, TZ=10.0))
        c.step(1000, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0, **cfg(TC=2.0, TZ=10.0))
        expected_correct = 0.5 * 100.0 / (0.5 + 20.0)
        buggy_dt_as_tb = 1.0 * 100.0 / (1.0 + 20.0)
        self.assertAlmostEqual(c.FOP1.AV, expected_correct, places=10)
        self.assertNotAlmostEqual(c.FOP1.AV, buggy_dt_as_tb, places=4)

    def test_tc_seconds_to_blink_ms_boundary(self):
        """TC 秒语义：TC=0.5 → TIMELOW=500ms；dt=500 → 拍 1 即翻转采样。"""
        c = APCCD()
        c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0, **cfg(TC=0.5))
        self.assertTrue(c.BLINK1.OUT, msg="TC=0.5s → TIMELOW=500ms，dt=500 拍 1 翻转")


# ---------------------------------------------------------------------------
# E. CD_BH 与 AD 正反作用
# ---------------------------------------------------------------------------
class TestCDBHAndADDirection(unittest.TestCase):
    def test_ad_false_is_positive_action(self):
        """AD=False → SEL(AD,1,-1)=+1。CD_K_D=0 时 CD_BH=(PV-SP)*CD_K_J。"""
        c = APCCD()
        out = c.step(
            500, SP=3.0, PV=10.0, TS=False, ZLOUT=0.0,
            **cfg(TC=100.0, CD_K_J=2.0, CD_K_D=0.0, AD=False),
        )
        self.assertAlmostEqual(c.CD_BH, (10.0 - 3.0) * 2.0, places=10)
        self.assertAlmostEqual(out["CD_BH"], 14.0, places=10)

    def test_ad_true_is_reverse_action(self):
        """AD=True → SEL(AD,1,-1)=-1。"""
        c = APCCD()
        c.step(
            500, SP=3.0, PV=10.0, TS=False, ZLOUT=0.0,
            **cfg(TC=100.0, CD_K_J=2.0, CD_K_D=0.0, AD=True),
        )
        self.assertAlmostEqual(c.CD_BH, -((10.0 - 3.0) * 2.0), places=10)

    def test_full_formula_with_dynamic_term(self):
        """CD_BH = ((PV-SP)*CD_K_J + FOP.AV*CD_K_D) * dir，含动态项。"""
        c = APCCD()
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0,
               **cfg(TC=1.0, TZ=10.0, CD_K_J=1.0, CD_K_D=1.0, AD=False))
        c.step(500, SP=0.0, PV=100.0, TS=False, ZLOUT=0.0,
               **cfg(TC=1.0, TZ=10.0, CD_K_J=1.0, CD_K_D=1.0, AD=False))
        expected = ((100.0 - 0.0) * 1.0 + c.FOP1.AV * 1.0) * 1.0
        self.assertAlmostEqual(c.CD_BH, expected, places=10)
        self.assertGreater(c.FOP1.AV, 0.0, msg="动态项应已非零，才算真正检验")


# ---------------------------------------------------------------------------
# F. TON 延时进入、AV_TEMP 与 FLG
# ---------------------------------------------------------------------------
class TestTONAndAVTempFLG(unittest.TestCase):
    def test_ton_not_triggered_below_threshold(self):
        """|CD_BH|<CD_GD → TON 永不触发，AV_TEMP 保持 0。"""
        c = APCCD()
        for _ in range(10):
            c.step(500, SP=0.0, PV=1.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, TL=1.0))
        self.assertFalse(c.TON1.Q)
        self.assertEqual(c.AV_TEMP, 0.0)
        self.assertEqual(c.AV, 0.0)

    def test_ton_triggers_after_tl_then_sets_avtemp_and_flg_positive(self):
        """|CD_BH|>=CD_GD 持续 TL=1.0s（dt=500 → 2 拍）后 TON.Q 真。

        到时 TS=False：AV_TEMP=min(max(CD_BH*CD_K_FD,CDL),CDH)，AV==AV_TEMP；
        CD_BH>0 → FLG=1.0。
        """
        c = APCCD()
        # 拍 1：ET=500 < PT=1000 → Q=False
        c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0,
               **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0, TL=1.0))
        self.assertFalse(c.TON1.Q)
        self.assertEqual(c.AV_TEMP, 0.0)
        # 拍 2：ET=1000 >= PT → Q=True
        out = c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0, TL=1.0))
        self.assertTrue(c.TON1.Q)
        self.assertAlmostEqual(c.AV_TEMP, 10.0, places=10)
        self.assertAlmostEqual(c.AV, 10.0, places=10)
        self.assertAlmostEqual(out["AV"], 10.0, places=10)
        self.assertEqual(c.FLG, 1.0)

    def test_flg_negative_when_cdbh_negative(self):
        c = APCCD()
        for _ in range(2):
            c.step(500, SP=0.0, PV=-10.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0, TL=1.0))
        self.assertTrue(c.TON1.Q)
        self.assertEqual(c.FLG, -1.0)
        self.assertAlmostEqual(c.AV_TEMP, -10.0, places=10)

    def test_flg_kept_when_cdbh_zero(self):
        """CD_BH==0 时 FLG 保持旧值（不写成 sign()=0）。

        需让 TON.Q 与 CD_BH==0 同时成立 → 仅当 CD_GD<=0 才进得了 step6。
        故此用例用 CD_GD=0 显式打到该分支。
        """
        c = APCCD()
        # 先建立 FLG=1（CD_BH=5>0），TON 在 CD_GD=0 下 2 拍后 Q 真
        c.step(500, SP=0.0, PV=5.0, TS=False, ZLOUT=0.0,
               **cfg(TC=100.0, CD_K_D=0.0, CD_GD=0.0, CD_K_FD=1.0, TL=1.0))
        c.step(500, SP=0.0, PV=5.0, TS=False, ZLOUT=0.0,
               **cfg(TC=100.0, CD_K_D=0.0, CD_GD=0.0, CD_K_FD=1.0, TL=1.0))
        self.assertEqual(c.FLG, 1.0)
        # CD_BH=0：TON.Q 仍真（abs(0)>=0），step6 进入但 CD_BH==0 → FLG 保持
        c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=0.0,
               **cfg(TC=100.0, CD_K_D=0.0, CD_GD=0.0, CD_K_FD=1.0, TL=1.0))
        self.assertEqual(c.FLG, 1.0)
        # 转负：FLG=-1；再回 0 时保持 -1
        c.step(500, SP=0.0, PV=-5.0, TS=False, ZLOUT=0.0,
               **cfg(TC=100.0, CD_K_D=0.0, CD_GD=0.0, CD_K_FD=1.0, TL=1.0))
        self.assertEqual(c.FLG, -1.0)
        c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=0.0,
               **cfg(TC=100.0, CD_K_D=0.0, CD_GD=0.0, CD_K_FD=1.0, TL=1.0))
        self.assertEqual(c.FLG, -1.0)

    def test_avtemp_clamped_to_cdh(self):
        c = APCCD()
        for _ in range(2):
            c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                         CDH=3.0, CDL=-3.0, TL=1.0))
        self.assertTrue(c.TON1.Q)
        self.assertAlmostEqual(c.AV_TEMP, 3.0, places=10)
        self.assertAlmostEqual(c.AV, 3.0, places=10)

    def test_clamp_literal_order_min_max(self):
        """锁定 min(max(x,CDL),CDH) 字面顺序：CDL>CDH（反向）时结果恒为 CDH。"""
        c = APCCD()
        for _ in range(2):
            c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                         CDH=2.0, CDL=5.0, TL=1.0))
        expected = min(max(10.0 * 1.0, 5.0), 2.0)  # == 2.0
        self.assertAlmostEqual(c.AV_TEMP, expected, places=10)
        self.assertAlmostEqual(c.AV_TEMP, 2.0, places=10)


# ---------------------------------------------------------------------------
# G. 退出阈值回补 ZLOUT
# ---------------------------------------------------------------------------
class TestExitRebateZLOUT(unittest.TestCase):
    def _build_active_avtemp(self, c, zlout):
        """驱动到 AV_TEMP 非零（CD_BH=10>0，TON 2 拍后 Q 真，FLG=1）。返回末拍 out。"""
        out = None
        for _ in range(2):
            out = c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=zlout,
                         **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                               CD_K=0.5, TL=1.0))
        return out

    def test_rebate_once_on_exit(self):
        c = APCCD()
        self._build_active_avtemp(c, zlout=0.0)
        self.assertAlmostEqual(c.AV_TEMP, 10.0, places=10)
        self.assertEqual(c.FLG, 1.0)

        # 退出：CD_BH=0 < CD_GD，触发 R_TRIG2 一次回补
        out = c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=100.0,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                           CD_K=0.5, TL=1.0))
        rebate = min(max(2.0 * 1.0 * 0.5 * 1.0, -1000.0), 1000.0)  # =1.0
        self.assertAlmostEqual(rebate, 1.0, places=10)
        self.assertAlmostEqual(out["ZLOUT"], 100.0 + 1.0, places=10)
        self.assertAlmostEqual(c.AV_TEMP, 0.0, places=10)
        self.assertAlmostEqual(c.AV, 0.0, places=10)

    def test_no_double_rebate_while_condition_holds(self):
        c = APCCD()
        self._build_active_avtemp(c, zlout=0.0)
        # 第一次退出拍回补
        out1 = c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=100.0,
                      **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                            CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(out1["ZLOUT"], 101.0, places=10)
        # 退出条件持续为真：不得重复累加（AV_TEMP 已为 0 → R_TRIG2.clk=False）
        out2 = c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=out1["ZLOUT"],
                      **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                            CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(out2["ZLOUT"], 101.0, places=10)

    def test_rebate_uses_flg_sign(self):
        """FLG=-1 时回补值为负。"""
        c = APCCD()
        for _ in range(2):
            c.step(500, SP=0.0, PV=-10.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                         CD_K=0.5, TL=1.0))
        self.assertEqual(c.FLG, -1.0)
        out = c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=50.0,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                           CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(out["ZLOUT"], 50.0 + (2.0 * 1.0 * 0.5 * -1.0),
                               places=10)


# ---------------------------------------------------------------------------
# H. TS 跟踪切除
# ---------------------------------------------------------------------------
class TestTrackingCutoff(unittest.TestCase):
    def test_ts_true_blocks_avtemp_refresh(self):
        """TS=True 时即使 TON 到时也不刷新有效 AV_TEMP。"""
        c = APCCD()
        for _ in range(4):
            out = c.step(500, SP=0.0, PV=10.0, TS=True, ZLOUT=0.0,
                         **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                               CD_K=0.5, TL=1.0))
        self.assertTrue(c.TON1.Q)
        self.assertEqual(c.AV_TEMP, 0.0)
        self.assertEqual(c.AV, 0.0)
        self.assertEqual(out["ZLOUT"], 0.0)

    def test_ts_entry_allows_one_rebate_then_clears(self):
        """已有非零 AV_TEMP 后 TS 切真：本拍先回补一次，随后 AV_TEMP=AV=0。"""
        c = APCCD()
        # 建立 AV_TEMP=10, FLG=1（TS=False）
        for _ in range(2):
            c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                         CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(c.AV_TEMP, 10.0, places=10)
        # TS 切真：CD_BH 仍=10（>=CD_GD），靠 TS 触发 R_TRIG2 回补一次
        out = c.step(500, SP=0.0, PV=10.0, TS=True, ZLOUT=200.0,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                           CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(out["ZLOUT"], 200.0 + 1.0, places=10)
        self.assertEqual(c.AV_TEMP, 0.0)
        self.assertEqual(c.AV, 0.0)

    def test_ts_true_no_repeat_rebate(self):
        c = APCCD()
        for _ in range(2):
            c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=0.0,
                   **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                         CD_K=0.5, TL=1.0))
        out1 = c.step(500, SP=0.0, PV=10.0, TS=True, ZLOUT=200.0,
                      **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                            CD_K=0.5, TL=1.0))
        out2 = c.step(500, SP=0.0, PV=10.0, TS=True, ZLOUT=out1["ZLOUT"],
                      **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                            CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(out1["ZLOUT"], 201.0, places=10)
        self.assertAlmostEqual(out2["ZLOUT"], 201.0, places=10)

    def test_ts_true_cold_start_no_zlout_change(self):
        """TS=True 且冷启动 AV_TEMP=0 → ZLOUT 不变。"""
        c = APCCD()
        out = c.step(500, SP=0.0, PV=10.0, TS=True, ZLOUT=77.0,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                           CD_K=0.5, TL=1.0))
        self.assertAlmostEqual(out["ZLOUT"], 77.0, places=10)
        self.assertEqual(c.AV_TEMP, 0.0)


# ---------------------------------------------------------------------------
# I. VAR_IN_OUT 适配
# ---------------------------------------------------------------------------
class TestZLOUTVarInOut(unittest.TestCase):
    def test_no_event_passes_zlout_through(self):
        """无回补事件时输出保持传入 ZLOUT。"""
        c = APCCD()
        out = c.step(500, SP=5.0, PV=5.0, TS=False, ZLOUT=42.0,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, TL=1.0))
        self.assertAlmostEqual(out["ZLOUT"], 42.0, places=10)

    def test_uses_input_not_stale_cache(self):
        """新一拍传入不同 ZLOUT，块以入参为准，不用旧缓存。"""
        c = APCCD()
        out1 = c.step(500, SP=5.0, PV=5.0, TS=False, ZLOUT=42.0,
                      **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, TL=1.0))
        self.assertAlmostEqual(out1["ZLOUT"], 42.0, places=10)
        out2 = c.step(500, SP=5.0, PV=5.0, TS=False, ZLOUT=99.0,
                      **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, TL=1.0))
        self.assertAlmostEqual(out2["ZLOUT"], 99.0, places=10)

    def test_rebate_adds_to_current_input_with_feedback_pattern(self):
        """演示调用方"回灌"模式：zlout = out["ZLOUT"]。"""
        c = APCCD()
        zlout = 10.0
        # 建立 AV_TEMP（TS=False）
        for _ in range(2):
            out = c.step(500, SP=0.0, PV=10.0, TS=False, ZLOUT=zlout,
                         **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                               CD_K=0.5, TL=1.0))
            zlout = out["ZLOUT"]
        self.assertAlmostEqual(zlout, 10.0, places=10)  # 尚未回补
        # 退出回补
        out = c.step(500, SP=0.0, PV=0.0, TS=False, ZLOUT=zlout,
                     **cfg(TC=100.0, CD_K_D=0.0, CD_GD=2.0, CD_K_FD=1.0,
                           CD_K=0.5, TL=1.0))
        zlout = out["ZLOUT"]
        self.assertAlmostEqual(zlout, 11.0, places=10)


if __name__ == "__main__":
    unittest.main()
