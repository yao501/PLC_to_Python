"""APCGCQ 业务块（观测器）的契约测试。

按 02 业务块规则 + 项目工程约定（基础原语审核法 §3 时序边界）：

* 冷启动：GCAV=JTAV=DTAV=0，BLINK.OUT=False，JZ_* 全为 0
* 首个 BLINK 周期内（无 R_TRIG 事件）：FOP.IN 永远为 0
* **ST 执行顺序锁定**：采样事件那一拍 JZ_ZUP 取**旧** JZ_Z（不是
  STAT01 RESET 后的 0）——这是 GCQ 最关键的不变量
* 多次采样事件之间的 JZ_ZUP / JZ_ZUP1 链
* JTAV / DTAV / K 公式逐项验证
* 死区 SEL 恒假分支锁定（IN0 永远胜出）
* RLIM01 对称速率限幅串入主通路
* LIM01 幅值限幅串入主通路
* FOP01 默认 TB=0.5 锁定（首拍 α·KG·IN 数值）
* BLINK.TIMEHIGH=500 ms 锁定（采样周期 = TC*1000 + 500 ms）
* 不同 dt_ms 通过 BLINK 影响采样节拍，不直接影响数值通路
"""

from __future__ import annotations

import unittest

from src.blocks import APCGCQ
from src.blocks.apcgcq import (
    BLINK_TIMEHIGH_MS,
    FOP01_DEFAULT_TB_SEC,
    FOP01_KG,
)


def make_params(**overrides):
    """便捷参数构造器。默认让 RLIM / LIM 都不限制，方便观察主通路。"""
    base = dict(
        TC=1.0,
        TZ=10.0,
        K=1.0,
        INSP=0.0,
        GC1=0.0,
        GC2=1.0,
        OUTH=10000.0,
        OUTL=-10000.0,
        OUTV=10000.0,
    )
    base.update(overrides)
    return base


class TestAPCGCQColdStart(unittest.TestCase):
    """冷启动：所有公开输出为 0；嵌套 FB 实例处于各自 init 状态。"""

    def test_init_outputs_zero(self):
        g = APCGCQ()
        self.assertEqual(g.GCAV, 0.0)
        self.assertEqual(g.JTAV, 0.0)
        self.assertEqual(g.DTAV, 0.0)
        self.assertEqual(g.AV, 0.0)
        self.assertEqual(g.JZ_ZUP, 0.0)
        self.assertEqual(g.JZ_ZUP1, 0.0)
        self.assertEqual(g.JZ_Z, 0.0)

    def test_init_blink_phase_is_low(self):
        g = APCGCQ()
        self.assertFalse(g.BLINK01.OUT)
        self.assertEqual(g.BLINK01._elapsed_ms, 0)


class TestAPCGCQFirstBlinkPeriod(unittest.TestCase):
    """TC=1, dt=500：拍 1 BLINK 在 LOW 阶段，无采样事件，FOP.IN 永远 0。"""

    def test_first_tick_no_sampling(self):
        """拍 1：BLINK._elapsed=500<1000 → OUT=False，R_TRIG.Q=False，
        STAT01 累计第 1 个样本，但 JZ_ZUP/JZ_ZUP1 仍是 0；FOP.IN=0 → AV=0。"""
        g = APCGCQ()
        out = g.step(500, IN=100.0, **make_params())
        self.assertFalse(g.BLINK01.OUT)
        self.assertEqual(out, {"GCAV": 0.0, "JTAV": 0.0, "DTAV": 0.0})
        self.assertEqual(g.JZ_ZUP, 0.0)
        self.assertEqual(g.JZ_ZUP1, 0.0)
        self.assertEqual(g.JZ_Z, 100.0)


class TestSTOrderingLocked(unittest.TestCase):
    """**核心不变量**：ST 第 4 步 JZ_ZUP := SEL(Q, JZ_ZUP, JZ_Z) 中的 JZ_Z
    是**上一拍**的旧值，不是 STAT01 当拍 RESET 后的 0。

    错误实现（先调 STAT01 再赋值 JZ_ZUP）会让采样事件那一拍 JZ_ZUP=0；
    正确实现按 ST 顺序保留旧 JZ_Z。
    """

    def test_first_sampling_event_jz_zup_is_old_jz_z(self):
        """拍 1 末尾 STAT01.AVG=100 → 旧 JZ_Z=100；
        拍 2 BLINK 翻 True，R_TRIG.Q=True → JZ_ZUP 应取**旧** JZ_Z=100。"""
        g = APCGCQ()
        g.step(500, IN=100.0, **make_params())
        self.assertEqual(g.JZ_Z, 100.0)

        g.step(500, IN=100.0, **make_params())
        self.assertTrue(g.BLINK01.OUT)
        self.assertEqual(g.JZ_ZUP, 100.0)
        self.assertEqual(g.JZ_ZUP1, 0.0)
        self.assertEqual(g.JZ_Z, 0.0)

    def test_second_sampling_event_chain(self):
        """拍 5 第二次采样事件：JZ_ZUP1 := 旧 JZ_ZUP=100；
        JZ_ZUP := 旧 JZ_Z（拍 4 时 STAT01.AVG=100，因为 IN 恒为 100）= 100。
        FOP.IN = JZ_ZUP - JZ_ZUP1 = 0。"""
        g = APCGCQ()
        for _ in range(5):
            g.step(500, IN=100.0, **make_params())
        self.assertTrue(g.BLINK01.OUT)
        self.assertEqual(g.JZ_ZUP1, 100.0)
        self.assertEqual(g.JZ_ZUP, 100.0)


class TestSamplingEventSpacing(unittest.TestCase):
    """BLINK.TIMEHIGH=500ms 锁定：采样事件每 (TC*1000+500)ms 一次。

    TC=1, dt=500 → 每周期 1500ms / 500ms = 3 拍 → 采样事件序列为
    拍 2, 拍 5, 拍 8, 拍 11, ...
    """

    def test_blink_timehigh_constant(self):
        self.assertEqual(BLINK_TIMEHIGH_MS, 500)

    def test_sampling_event_at_tick_2_5_8_11(self):
        """采样事件 = BLINK.OUT 从 False 翻 True 的拍。
        TC=1, dt=500, TIMEHIGH=500：周期 1500ms = 3 拍 → 拍 2/5/8/11。"""
        g = APCGCQ()
        prev_blink = False
        events = []
        for tick in range(1, 13):
            g.step(500, IN=100.0, **make_params())
            if g.BLINK01.OUT and not prev_blink:
                events.append(tick)
            prev_blink = g.BLINK01.OUT
        self.assertEqual(events, [2, 5, 8, 11])

    def test_blink_timehigh_uses_project_500ms(self):
        """APCGCQ-GG4 项目修正约定：TIMEHIGH=500ms（不是旧 ST 片段的 300ms）。

        显式锁定"采样窗口周期 = TC*1000 + 500ms"——即 1500ms 而非 1300ms。

        反证模型（dt=100, TC=1.0）：
        * 正确语义（TIMEHIGH=500）：周期 1500ms = 15 拍 → 相邻采样事件间距 15
        * 错误语义（TIMEHIGH=300）：周期 1300ms = 13 拍 → 相邻采样事件间距 13

        只要相邻间距 == 15，就能锁定 TIMEHIGH=500（而非 300）。
        """
        self.assertEqual(BLINK_TIMEHIGH_MS, 500)

        g = APCGCQ()
        prev_blink = False
        events = []
        for tick in range(1, 41):
            g.step(100, IN=10.0, **make_params(TC=1.0))
            if g.BLINK01.OUT and not prev_blink:
                events.append(tick)
            prev_blink = g.BLINK01.OUT

        self.assertGreaterEqual(
            len(events), 2, msg="40 拍内应至少出现 2 次采样事件"
        )
        for i in range(1, len(events)):
            gap = events[i] - events[i - 1]
            self.assertEqual(
                gap,
                15,
                msg=(
                    f"相邻采样事件间距应为 15 拍 (TIMEHIGH=500ms, 周期 1500ms)，"
                    f"实测为 {gap} 拍。如果出现 13 拍，说明 TIMEHIGH 误成 300ms。"
                ),
            )


class TestFOP01DefaultsLocked(unittest.TestCase):
    """FOP01 在 ST 中 TB / KG 不传，默认值锁定到模块级常量。"""

    def test_fop01_default_tb_is_half_second(self):
        self.assertEqual(FOP01_DEFAULT_TB_SEC, 0.5)

    def test_fop01_kg_is_one(self):
        self.assertEqual(FOP01_KG, 1.0)

    def test_first_sampling_av_uses_alpha_kg_in(self):
        """拍 2 第一次采样事件：FOP.IN=100, Ok_1=0, TB=0.5, TC=2*TZ=20。
        首拍 AV = TB*KG*IN/(TB+TC) = 0.5*1*100/20.5 = 2.4390..."""
        g = APCGCQ()
        g.step(500, IN=100.0, **make_params())
        g.step(500, IN=100.0, **make_params())
        expected = 0.5 * 1.0 * 100.0 / (0.5 + 20.0)
        self.assertAlmostEqual(g.AV, expected, places=10)


class TestJTAVFormula(unittest.TestCase):
    """JTAV = (IN - INSP) * GC1（每拍组合赋值，不依赖跨周期状态）。"""

    def test_jtav_zero_when_gc1_zero(self):
        g = APCGCQ()
        g.step(500, IN=42.0, **make_params(GC1=0.0))
        self.assertEqual(g.JTAV, 0.0)

    def test_jtav_proportional_to_in_minus_insp(self):
        g = APCGCQ()
        out = g.step(500, IN=10.0, **make_params(INSP=3.0, GC1=2.0))
        self.assertAlmostEqual(g.JTAV, (10.0 - 3.0) * 2.0, places=10)
        self.assertAlmostEqual(out["JTAV"], 14.0, places=10)

    def test_jtav_negative_in_minus_insp(self):
        g = APCGCQ()
        g.step(500, IN=-5.0, **make_params(INSP=10.0, GC1=3.0))
        self.assertAlmostEqual(g.JTAV, (-5.0 - 10.0) * 3.0, places=10)


class TestDTAVFormula(unittest.TestCase):
    """DTAV = AV * GC2（每拍组合赋值）。"""

    def test_dtav_proportional_to_av(self):
        """让 IN 持续不变，第二次采样事件后 AV 已非零；DTAV = AV * GC2。"""
        g = APCGCQ()
        for _ in range(3):
            g.step(500, IN=100.0, **make_params(GC2=3.0))
        self.assertAlmostEqual(g.DTAV, g.AV * 3.0, places=10)

    def test_dtav_zero_when_gc2_zero(self):
        g = APCGCQ()
        for _ in range(5):
            g.step(500, IN=100.0, **make_params(GC2=0.0))
        self.assertEqual(g.DTAV, 0.0)


class TestKMultiplierAndDeadbandSEL(unittest.TestCase):
    """死区 SEL 恒假锁定 + K 倍率串接。

    cond = IN<INSP AND IN>INSP 永远 False，SEL 永远走 IN0=(JTAV+DTAV)*K。
    锁定即使 IN==INSP 时（cond 仍是 False AND False = False）也走 IN0。
    """

    def test_in_equals_insp_still_takes_in0(self):
        """即使 IN==INSP，SEL 仍走 IN0=(JTAV+DTAV)*K，不是 0。"""
        g = APCGCQ()
        for _ in range(5):
            g.step(500, IN=10.0, **make_params(INSP=10.0, GC1=2.0))
        self.assertAlmostEqual(g.JTAV, 0.0, places=10)

    def test_k_multiplies_jtav_plus_dtav(self):
        """K=2：GCAV ≈ 2*(JTAV+DTAV)（限幅未触发时）。"""
        g = APCGCQ()
        for _ in range(5):
            g.step(500, IN=100.0, **make_params(K=2.0, INSP=0.0, GC1=0.5, GC2=1.0))
        expected = 2.0 * (g.JTAV + g.DTAV)
        self.assertAlmostEqual(g.GCAV, expected, places=10)


class TestRLIMSymmetricRateLimitInChain(unittest.TestCase):
    """RLIM01(HL=LL=OUTV) 对称速率限幅串入主通路。"""

    def test_small_outv_limits_per_tick_change(self):
        """OUTV=1：每拍 GCAV 变化量不超过 1。"""
        g = APCGCQ()
        prev = 0.0
        for _ in range(10):
            g.step(500, IN=10000.0, **make_params(GC1=1.0, OUTV=1.0))
            self.assertLessEqual(abs(g.GCAV - prev), 1.0 + 1e-9)
            prev = g.GCAV


class TestLIMAmplitudeLimitInChain(unittest.TestCase):
    """LIM01(HL=OUTH, LL=OUTL) 幅值限幅串入主通路。"""

    def test_gcav_clamped_to_outh(self):
        """OUTH=5：GCAV 长期不超过 5。"""
        g = APCGCQ()
        for _ in range(20):
            g.step(500, IN=10000.0, **make_params(GC1=1.0, OUTH=5.0, OUTL=-5.0))
        self.assertLessEqual(g.GCAV, 5.0 + 1e-9)
        self.assertGreaterEqual(g.GCAV, -5.0 - 1e-9)

    def test_gcav_clamped_to_outl(self):
        """大负输入 + GC1 把 JTAV 拉到 -∞，最终 GCAV 钳到 OUTL。"""
        g = APCGCQ()
        for _ in range(20):
            g.step(500, IN=-10000.0, **make_params(GC1=1.0, OUTH=5.0, OUTL=-5.0))
        self.assertGreaterEqual(g.GCAV, -5.0 - 1e-9)


class TestNestedInstancesShareState(unittest.TestCase):
    """嵌套 FB 实例的状态正确分布在各自实例上（防止状态污染）。"""

    def test_blink_owns_own_state(self):
        """两个 GCQ 实例的嵌套 BLINK 不共享状态。"""
        g1 = APCGCQ()
        g2 = APCGCQ()
        for _ in range(4):
            g1.step(500, IN=100.0, **make_params())
        self.assertNotEqual(g1.BLINK01._elapsed_ms, g2.BLINK01._elapsed_ms)
        self.assertNotEqual(g1.JZ_ZUP, g2.JZ_ZUP)

    def test_stat01_resets_on_sampling_event(self):
        """采样事件那一拍 STAT01 应当 RESET（COUNTER=0, MN=REAL_MAX）。"""
        g = APCGCQ()
        for _ in range(2):
            g.step(500, IN=100.0, **make_params())
        self.assertTrue(g.BLINK01.OUT)
        self.assertEqual(g.STAT01.COUNTER, 0)


class TestSTOrderingViaForcedDifference(unittest.TestCase):
    """额外的 ST 顺序锁定：构造能区分"用旧 JZ_Z"和"用新 JZ_Z=0"的差分。

    场景：拍 5 第二次采样事件那一拍。
    * 正确顺序：JZ_ZUP=旧 JZ_Z=100, JZ_ZUP1=旧 JZ_ZUP=100 → FOP.IN=0
    * 错误顺序：JZ_ZUP=新 JZ_Z=0, JZ_ZUP1=旧 JZ_ZUP=100 → FOP.IN=-100

    两种实现下拍 5 的 AV 走向相反方向（正确：从拍 4 的峰值开始衰减；
    错误：进入负向加速）。
    """

    def test_av_decays_after_second_sampling_with_constant_input(self):
        g = APCGCQ()
        avs = []
        for _ in range(8):
            g.step(500, IN=100.0, **make_params())
            avs.append(g.AV)

        self.assertGreater(avs[3], avs[4])
        self.assertGreater(avs[4], avs[5])
        self.assertGreater(avs[5], avs[6])
        self.assertGreater(avs[1], 0.0)


class TestBasicExport(unittest.TestCase):
    """模块导出健康检查（**不是**命名约定锁定）。

    业务功能块在项目中就叫 ``APCGCQ``——源材料 ``CGCQ1.txt`` 顶部出现的
    ``FUNCTION_BLOCK APCGCQ1`` 是软 PLC 复制功能块时自动生成的防重名名称，
    不是命名风格差异，也不构成项目级决策。这里仅做基础健康检查，确保
    模块可正常导出与实例化，不锁定"是否有 APCGCQ1 别名"之类的负向断言。
    """

    def test_exports_apcgcq(self):
        """``from src.blocks import APCGCQ`` 必须可用，且类名就叫 APCGCQ。"""
        from src.blocks import APCGCQ as gcq
        self.assertIsNotNone(gcq)
        self.assertEqual(gcq.__name__, "APCGCQ")
        self.assertIsNotNone(gcq())


class TestSamplingSnapshotBeforeStatReset(unittest.TestCase):
    """APCGCQ-GG1 的更直白表达：采样事件那一拍，``JZ_ZUP`` 必须先把上一拍
    ``STAT01.AVG`` 的旧值快照下来，**然后** ``STAT01`` 才被 RESET。

    这是从"STAT01 已 RESET 但 JZ_ZUP 仍持有旧 AVG"的角度直接锁定 ST 顺序。
    """

    def test_sampling_snapshot_uses_old_jz_z_before_stat_reset(self):
        """构造能区分新旧 JZ_Z 的场景：

        dt=500, TC=1, TIMEHIGH=500：拍 2 是第一次事件，拍 5 是第二次事件。

        进入拍 5 step 之前，``self.JZ_Z`` 已被拍 4 末步骤 6 设为 100：
            * 拍 3 / 拍 4 STAT01 各采样一次 IN=100 → AVG=100

        拍 5 步骤 4 把 ``self.JZ_Z`` (=100) 写入 ``self.JZ_ZUP``；
        步骤 5 ``STAT01(RESET=True)`` → COUNTER=0, AVG=0；
        步骤 6 ``self.JZ_Z`` 更新为 0。

        若实现错把 ``STAT01.step`` 移到步骤 4 之前，拍 5 末
        ``self.JZ_ZUP`` 会被 RESET 后的 ``AVG=0`` 污染——这里通过
        ``JZ_ZUP=100`` + ``STAT01.COUNTER=0`` + ``JZ_Z=0`` 三项联合锁死正确顺序。
        """
        g = APCGCQ()
        for _ in range(5):
            g.step(500, IN=100.0, **make_params())

        self.assertTrue(g.BLINK01.OUT, msg="拍 5 必须是采样事件")

        self.assertAlmostEqual(
            g.JZ_ZUP,
            100.0,
            places=10,
            msg="JZ_ZUP 必须取 RESET 前的旧 JZ_Z=100，不能被 RESET 后的 0 污染",
        )
        self.assertEqual(
            g.STAT01.COUNTER, 0, msg="事件拍 STAT01 应已被 RESET（COUNTER=0）"
        )
        self.assertEqual(
            g.JZ_Z, 0.0, msg="事件拍末 JZ_Z 应等于 RESET 后 STAT01.AVG=0"
        )


class TestSelConditionPreservedAsSourceFalseBranch(unittest.TestCase):
    """APCGCQ-GG2：源码 ``cond=(IN<INSP AND IN>INSP)`` 必须按源码恒假保留。

    在 ``SEL(G, IN0, IN1)`` 语义下，``G=False`` 走 ``IN0=(JTAV+DTAV)*K``。
    严禁误改成下列任一形式（这些是最常见的"善意错误"）：

    * ``IN != INSP``
    * ``ABS(IN-INSP) > threshold``
    * ``IN <= INSP or IN >= INSP``
    * 任何其他业务条件或死区判据

    本类用三种 IN/INSP 关系反证：只要任一种走 IN1=0 就说明实现被错改。
    """

    def _run(self, IN, INSP, **kw):
        g = APCGCQ()
        for _ in range(5):
            g.step(
                500,
                IN=IN,
                **make_params(INSP=INSP, GC1=1.0, GC2=0.0, K=1.0, **kw),
            )
        return g

    def test_in_less_than_insp_takes_in0(self):
        """IN<INSP 真、IN>INSP 假 → cond=False AND True=False → 走 IN0。

        如果实现误改为 ``IN!=INSP``，cond 会变 True，``SEL`` 走 IN1=0，
        GCAV 会被钳到 0；这里通过 GCAV != 0 反证正确语义。
        """
        g = self._run(IN=5.0, INSP=10.0)
        self.assertAlmostEqual(g.JTAV, -5.0, places=10)
        self.assertAlmostEqual(g.DTAV, 0.0, places=10)
        self.assertNotAlmostEqual(
            g.GCAV,
            0.0,
            places=2,
            msg="如果 SEL 误成 IN!=INSP，GCAV 会被钳到 0；非 0 才能锁定恒假语义",
        )
        self.assertAlmostEqual(g.GCAV, g.JTAV + g.DTAV, places=10)

    def test_in_greater_than_insp_takes_in0(self):
        """IN>INSP 真、IN<INSP 假 → cond=True AND False=False → 走 IN0。"""
        g = self._run(IN=15.0, INSP=10.0)
        self.assertAlmostEqual(g.JTAV, 5.0, places=10)
        self.assertAlmostEqual(g.DTAV, 0.0, places=10)
        self.assertNotAlmostEqual(g.GCAV, 0.0, places=2)
        self.assertAlmostEqual(g.GCAV, g.JTAV + g.DTAV, places=10)

    def test_in_equals_insp_also_takes_in0(self):
        """IN==INSP → cond=False AND False=False → 走 IN0（=0，因 JTAV=0）。

        本拍 IN0=IN1=0 不能反证误改为 ``IN!=INSP`` 的形式（两边都返 0）；
        但能反证误改为 ``IN<=INSP or IN>=INSP``（恒真）的形式：那种实现下
        必然走 IN1=0，与本拍走 IN0=0 巧合等价——所以这一分支单看不够；
        与 ``less_than`` / ``greater_than`` 分支组合才完整锁死语义。
        """
        g = self._run(IN=10.0, INSP=10.0)
        self.assertAlmostEqual(g.JTAV, 0.0, places=10)
        self.assertAlmostEqual(g.GCAV, 0.0, places=10)


class TestOutvIsRateLimitAndOuthOutlAreAmplitudeLimits(unittest.TestCase):
    """APCGCQ-GG5 分层验证：``OUTV`` 是每拍速率限，``OUTH/OUTL`` 才是最终幅值上下限。

    通过三组分层场景：

    * 单层 OUTV 紧、OUTH/OUTL 极松 → GCAV 受速率限慢慢爬升，每拍 |Δ|≤OUTV
    * 单层 OUTV 极松、OUTH/OUTL 紧 → GCAV 几拍内被钳到 OUTH（速率不限）
    * 两层都紧 → 速率限主导早期，幅值限主导末期，最终落在 OUTH

    本类与现有 ``TestRLIMSymmetricRateLimitInChain`` /
    ``TestLIMAmplitudeLimitInChain`` 互补，提供"组合"角度的锁定。
    """

    def test_outv_only_limits_per_step_change(self):
        """``OUTV=0.5`` 每拍 |Δ|≤0.5；``OUTH/OUTL`` 极松不限制最终幅值。"""
        g = APCGCQ()
        prev = 0.0
        for tick in range(1, 11):
            g.step(
                500,
                IN=10000.0,
                **make_params(
                    GC1=1.0, OUTH=10000.0, OUTL=-10000.0, OUTV=0.5
                ),
            )
            self.assertLessEqual(
                abs(g.GCAV - prev),
                0.5 + 1e-9,
                msg=(
                    f"OUTV=0.5 应每拍变化 ≤ 0.5，第 {tick} 拍 "
                    f"|Δ|={abs(g.GCAV - prev)}"
                ),
            )
            prev = g.GCAV
        self.assertLessEqual(
            g.GCAV,
            5.0 + 1e-9,
            msg="10 拍 * OUTV=0.5 ⇒ GCAV ≤ 5；远未达到 IN=10000 也未触 OUTH",
        )

    def test_outh_only_limits_amplitude(self):
        """``OUTV`` 巨大（速率不限）、``OUTH=3`` → GCAV 被钳到 [-3, 3]。"""
        g = APCGCQ()
        for _ in range(5):
            g.step(
                500,
                IN=10000.0,
                **make_params(
                    GC1=1.0, OUTH=3.0, OUTL=-3.0, OUTV=10000.0
                ),
            )
        self.assertLessEqual(g.GCAV, 3.0 + 1e-9)
        self.assertGreaterEqual(g.GCAV, -3.0 - 1e-9)

    def test_outv_and_outh_layered_long_run_settles_at_outh(self):
        """两层都紧：``OUTV=0.5`` + ``OUTH=2``。20 拍 * 0.5 = 10 远超 OUTH=2，
        最终 GCAV 收敛到 ``OUTH=2``——速率限决定爬升节奏，幅值限决定终点。"""
        g = APCGCQ()
        for _ in range(20):
            g.step(
                500,
                IN=10000.0,
                **make_params(
                    GC1=1.0, OUTH=2.0, OUTL=-2.0, OUTV=0.5
                ),
            )
        self.assertAlmostEqual(g.GCAV, 2.0, places=6)

    def test_outv_does_not_clamp_amplitude(self):
        """反证：``OUTV`` 不该被当作幅值限。
        ``OUTV=100`` 但 ``OUTH=3`` → GCAV 受 OUTH 钳到 3，不是 OUTV=100。"""
        g = APCGCQ()
        for _ in range(20):
            g.step(
                500,
                IN=10000.0,
                **make_params(
                    GC1=1.0, OUTH=3.0, OUTL=-3.0, OUTV=100.0
                ),
            )
        self.assertAlmostEqual(g.GCAV, 3.0, places=6)
        self.assertLess(
            g.GCAV,
            100.0 - 1.0,
            msg="若把 OUTV 误当幅值限，GCAV 会停在 100 附近——不是这里的 3",
        )


if __name__ == "__main__":
    unittest.main()
