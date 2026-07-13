"""APCSPFINDER（分析用设定值自动寻找）业务块的契约测试。

按提示词 A~K 覆盖，唯一事实来源是 ST 实际执行顺序（``APCSPFINDER.txt``）：

* A 初始默认状态（无 Context / 无授权依赖）
* B 阈值与量程兜底
* C 首拍稳定段
* D 稳定 / 不稳定判定边界
* E 自动 SP 合格与可信度
* F 历史自动 SP 保留语义
* G EN=False 语义
* H RESET 当拍行为
* I SP_TAG_BAD 边界
* J 最终优先级与编码
* K CYCLE 与 dt_ms 分离

不依赖系统时间、真实机器码、授权或网络。
"""

from __future__ import annotations

import unittest

from src.blocks import APCSPFINDER


def step_finder(
    f: APCSPFINDER,
    *,
    EN: bool = True,
    RESET: bool = False,
    CYCLE: float = 0.5,
    SAMPLE_OK: bool = True,
    SP_MAN: float = 0.0,
    SP_MAN_EN: bool = False,
    SP_TAG: float = 0.0,
    SP_TAG_EN: bool = True,
    SP_AUTO_EN: bool = True,
    SP_AUTO_REPLACE_BAD_TAG: bool = False,
    PV: float = 50.0,
    AV: float = 10.0,
    PVMU: float = 100.0,
    PVMD: float = 0.0,
    OUTT: float = 100.0,
    OUTB: float = 0.0,
    SP_STABLE_T: float = 300.0,
    SP_CONF_T: float = 900.0,
    PV_STABLE_K: float = 0.002,
    AV_STABLE_K: float = 0.001,
    PV_STABLE_ABS: float = 0.0,
    AV_STABLE_ABS: float = 0.0,
    SP_BAD_K: float = 0.05,
    SP_BAD_ABS: float = 0.0,
    dt_ms: int = 500,
) -> None:
    f.step(
        dt_ms,
        EN=EN,
        RESET=RESET,
        CYCLE=CYCLE,
        SAMPLE_OK=SAMPLE_OK,
        SP_MAN=SP_MAN,
        SP_MAN_EN=SP_MAN_EN,
        SP_TAG=SP_TAG,
        SP_TAG_EN=SP_TAG_EN,
        SP_AUTO_EN=SP_AUTO_EN,
        SP_AUTO_REPLACE_BAD_TAG=SP_AUTO_REPLACE_BAD_TAG,
        PV=PV,
        AV=AV,
        PVMU=PVMU,
        PVMD=PVMD,
        OUTT=OUTT,
        OUTB=OUTB,
        SP_STABLE_T=SP_STABLE_T,
        SP_CONF_T=SP_CONF_T,
        PV_STABLE_K=PV_STABLE_K,
        AV_STABLE_K=AV_STABLE_K,
        PV_STABLE_ABS=PV_STABLE_ABS,
        AV_STABLE_ABS=AV_STABLE_ABS,
        SP_BAD_K=SP_BAD_K,
        SP_BAD_ABS=SP_BAD_ABS,
    )


def make_qualified(f: APCSPFINDER, pv: float = 50.0, **kw) -> None:
    """单拍即合格：CYCLE=1.0, SP_STABLE_T=1.0 → 首拍 STABLE_T=1.0>=1，PV 恒定。"""
    step_finder(f, PV=pv, AV=10.0, CYCLE=1.0, SP_STABLE_T=1.0, **kw)


# ============================ A. 初始默认状态 ============================


class TestInitialState(unittest.TestCase):
    def test_outputs_zero(self):
        f = APCSPFINDER()
        self.assertEqual(f.SP_USE, 0.0)
        self.assertIs(f.SP_VALID, False)
        self.assertEqual(f.SP_SOURCE, 0)
        self.assertEqual(f.SP_REASON, 0)
        self.assertEqual(f.SP_AUTO, 0.0)
        self.assertIs(f.SP_AUTO_OK, False)
        self.assertEqual(f.SP_AUTO_CONF, 0.0)
        self.assertIs(f.SP_TAG_BAD, False)
        self.assertEqual(f.SP_STABLE_T_OUT, 0.0)
        self.assertEqual(f.SP_STABLE_PV_RANGE, 0.0)

    def test_internal_states_zero(self):
        f = APCSPFINDER()
        for name in (
            "CYCLE_S", "PV_RANGE", "OUT_RANGE", "PV_TH", "AV_TH", "SP_BAD_TH",
            "D_PV", "D_AV", "PV_1", "AV_1", "STABLE_T", "STABLE_N",
            "STABLE_PV_SUM", "STABLE_PV_MAX", "STABLE_PV_MIN", "STABLE_SP_TEMP",
            "STABLE_SPAN_K",
        ):
            self.assertEqual(getattr(f, name), 0, f"{name} 应为 0")
        self.assertIs(f.INIT_DONE, False)
        self.assertIs(f.STABLE_ACTIVE, False)

    def test_no_context_or_auth_attrs(self):
        f = APCSPFINDER()
        self.assertFalse(hasattr(f, "_ctx"))


# ============================ B. 阈值与量程兜底 ============================


class TestThresholds(unittest.TestCase):
    def test_cycle_zero_or_negative(self):
        f = APCSPFINDER()
        step_finder(f, CYCLE=0.0)
        self.assertEqual(f.CYCLE_S, 0.001)
        step_finder(f, CYCLE=-3.0)
        self.assertEqual(f.CYCLE_S, 0.001)

    def test_range_fallback_100(self):
        f = APCSPFINDER()
        step_finder(f, PVMU=5.0, PVMD=5.0, OUTT=7.0, OUTB=7.0)
        self.assertEqual(f.PV_RANGE, 100)
        self.assertEqual(f.OUT_RANGE, 100)

    def test_abs_threshold_priority(self):
        f = APCSPFINDER()
        step_finder(f, PV_STABLE_ABS=2.0, AV_STABLE_ABS=3.0, SP_BAD_ABS=4.0)
        self.assertAlmostEqual(f.PV_TH, 2.0)
        self.assertAlmostEqual(f.AV_TH, 3.0)
        self.assertAlmostEqual(f.SP_BAD_TH, 4.0)

    def test_default_proportional_thresholds(self):
        f = APCSPFINDER()
        step_finder(f)
        self.assertAlmostEqual(f.PV_TH, 0.2)  # 100*0.002
        self.assertAlmostEqual(f.AV_TH, 0.1)  # 100*0.001
        self.assertAlmostEqual(f.SP_BAD_TH, 5.0)  # 100*0.05

    def test_negative_k_uses_max_zero_then_floor(self):
        f = APCSPFINDER()
        step_finder(f, PV_STABLE_K=-1.0, AV_STABLE_K=-1.0, SP_BAD_K=-1.0)
        self.assertAlmostEqual(f.PV_TH, 0.01)  # max(0, 100*0.0001)
        self.assertAlmostEqual(f.AV_TH, 0.01)  # max(0, 100*0.0001)
        self.assertAlmostEqual(f.SP_BAD_TH, 0.1)  # max(0, 100*0.001)

    def test_min_floor_when_k_zero(self):
        f = APCSPFINDER()
        step_finder(f, PV_STABLE_K=0.0, AV_STABLE_K=0.0, SP_BAD_K=0.0)
        self.assertAlmostEqual(f.PV_TH, 0.01)
        self.assertAlmostEqual(f.AV_TH, 0.01)
        self.assertAlmostEqual(f.SP_BAD_TH, 0.1)

    def test_thresholds_computed_even_when_en_false(self):
        f = APCSPFINDER()
        step_finder(f, EN=False, PVMU=200.0, PVMD=0.0)
        self.assertEqual(f.PV_RANGE, 200.0)
        self.assertAlmostEqual(f.PV_TH, 0.4)  # 200*0.002


# ============================ C. 首拍稳定段 ============================


class TestFirstTick(unittest.TestCase):
    def test_first_tick_enters_stable_immediately(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, CYCLE=0.5)
        self.assertEqual(f.D_PV, 0.0)
        self.assertEqual(f.D_AV, 0.0)
        self.assertIs(f.STABLE_ACTIVE, True)
        self.assertAlmostEqual(f.STABLE_T, 0.5)
        self.assertEqual(f.STABLE_N, 1)
        self.assertAlmostEqual(f.STABLE_PV_SUM, 50.0)
        self.assertIs(f.INIT_DONE, True)


# ============================ D. 稳定 / 不稳定边界 ============================


class TestStabilityBoundary(unittest.TestCase):
    def test_d_pv_equal_th_is_stable(self):
        # 用 ABS 阈值取精确可表示的 0.5 步长，干净验证 <= 边界。
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, PV_STABLE_ABS=0.5)  # PV_TH=0.5
        step_finder(f, PV=50.5, AV=10.0, PV_STABLE_ABS=0.5)  # D_PV=0.5 == 0.5 → 稳定
        self.assertIs(f.STABLE_ACTIVE, True)
        self.assertEqual(f.STABLE_N, 2)

    def test_d_av_equal_th_is_stable(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, AV_STABLE_ABS=0.5)  # AV_TH=0.5
        step_finder(f, PV=50.0, AV=10.5, AV_STABLE_ABS=0.5)  # D_AV=0.5 == 0.5 → 稳定
        self.assertIs(f.STABLE_ACTIVE, True)
        self.assertEqual(f.STABLE_N, 2)

    def test_just_above_pv_th_is_unstable(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, PV_STABLE_ABS=0.5)
        step_finder(f, PV=51.0, AV=10.0, PV_STABLE_ABS=0.5)  # D_PV=1.0 > 0.5 → 不稳定
        self.assertIs(f.STABLE_ACTIVE, False)
        self.assertEqual(f.STABLE_N, 0)
        self.assertEqual(f.STABLE_T, 0.0)
        self.assertAlmostEqual(f.STABLE_PV_MAX, 51.0)
        self.assertAlmostEqual(f.STABLE_PV_MIN, 51.0)

    def test_just_above_av_th_is_unstable(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, AV_STABLE_ABS=0.5)
        step_finder(f, PV=50.0, AV=11.0, AV_STABLE_ABS=0.5)  # D_AV=1.0 > 0.5 → 不稳定
        self.assertIs(f.STABLE_ACTIVE, False)
        self.assertEqual(f.STABLE_N, 0)

    def test_enter_clears_then_accumulates_same_tick(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, CYCLE=0.5)
        # 进入即同拍累计：STABLE_T=0.5（不是 0），N=1
        self.assertAlmostEqual(f.STABLE_T, 0.5)
        self.assertEqual(f.STABLE_N, 1)


# ============================ E. 自动 SP 合格与可信度 ============================


class TestAutoQualify(unittest.TestCase):
    def test_qualifies_when_stable_t_exactly_meets(self):
        f = APCSPFINDER()
        # CYCLE=1.0, SP_STABLE_T=1.0 → 首拍 STABLE_T=1.0 >= max(1,1)=1 → 合格
        step_finder(f, PV=50.0, CYCLE=1.0, SP_STABLE_T=1.0)
        self.assertIs(f.SP_AUTO_OK, True)
        self.assertAlmostEqual(f.SP_AUTO, 50.0)

    def test_pv_range_exactly_at_limit_qualifies(self):
        # PV_STABLE_ABS=1.0 → PV_TH=1.0；段范围限 = max(PV_TH*5=5.0, range*0.005=0.5)=5.0。
        # 每拍步进 1.0（== PV_TH，仍稳定），6 拍后 PV 跨度恰为 5.0 == 限制 → 合格。
        f = APCSPFINDER()
        for pv in (50.0, 51.0, 52.0, 53.0, 54.0, 55.0):
            step_finder(
                f, PV=pv, AV=10.0, CYCLE=1.0, SP_STABLE_T=1.0, PV_STABLE_ABS=1.0
            )
        self.assertIs(f.STABLE_ACTIVE, True)
        self.assertAlmostEqual(f.SP_STABLE_PV_RANGE, 5.0)
        self.assertIs(f.SP_AUTO_OK, True)  # range 5.0 <= 5.0 边界 <= 成立

    def test_stable_n_zero_not_qualify(self):
        f = APCSPFINDER()
        # SP_STABLE_T 大 → 首拍不合格；第二拍不稳定 → N=0 不合格。
        step_finder(f, PV=50.0, SP_STABLE_T=300.0)
        self.assertIs(f.SP_AUTO_OK, False)
        step_finder(f, PV=60.0, SP_STABLE_T=300.0)  # 不稳定，N=0
        self.assertEqual(f.STABLE_N, 0)
        self.assertIs(f.SP_AUTO_OK, False)

    def test_auto_sp_is_mean_of_stable_pv(self):
        f = APCSPFINDER()
        for pv in (50.0, 50.1, 50.2):
            step_finder(f, PV=pv, CYCLE=1.0, SP_STABLE_T=1.0)
        self.assertAlmostEqual(f.SP_AUTO, 50.1)  # (50+50.1+50.2)/3
        self.assertIs(f.SP_AUTO_OK, True)

    def test_conf_in_0_1(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, CYCLE=1.0, SP_STABLE_T=1.0)
        self.assertGreaterEqual(f.SP_AUTO_CONF, 0.0)
        self.assertLessEqual(f.SP_AUTO_CONF, 1.0)

    def test_longer_stable_time_higher_base_conf(self):
        f_short = APCSPFINDER()
        step_finder(f_short, PV=50.0, CYCLE=1.0, SP_STABLE_T=1.0)
        f_long = APCSPFINDER()
        for _ in range(5):
            step_finder(f_long, PV=50.0, CYCLE=1.0, SP_STABLE_T=1.0)
        self.assertGreater(f_long.SP_AUTO_CONF, f_short.SP_AUTO_CONF)

    def test_smaller_fluctuation_higher_conf(self):
        # 同样 3 拍稳定时间，恒定 PV（波动 0）应比波动 PV 可信度更高。
        f_const = APCSPFINDER()
        for _ in range(3):
            step_finder(f_const, PV=50.0, CYCLE=1.0, SP_STABLE_T=1.0)
        f_fluc = APCSPFINDER()
        for pv in (50.0, 50.1, 50.2):
            step_finder(f_fluc, PV=pv, CYCLE=1.0, SP_STABLE_T=1.0)
        self.assertGreater(f_const.SP_AUTO_CONF, f_fluc.SP_AUTO_CONF)


# ============================ F. 历史自动 SP 保留 ============================


class TestHistoryHold(unittest.TestCase):
    def test_unstable_keeps_sp_auto(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        self.assertIs(f.SP_AUTO_OK, True)
        conf = f.SP_AUTO_CONF
        # 不稳定一拍（PV 大跳）：稳定段清零，但 SP_AUTO/OK/CONF 保留。
        step_finder(f, PV=80.0, CYCLE=1.0, SP_STABLE_T=1.0)
        self.assertEqual(f.STABLE_N, 0)
        self.assertIs(f.STABLE_ACTIVE, False)
        self.assertAlmostEqual(f.SP_AUTO, 50.0)
        self.assertIs(f.SP_AUTO_OK, True)
        self.assertAlmostEqual(f.SP_AUTO_CONF, conf)

    def test_sample_ok_false_freezes_stable_but_updates_pv1_av1(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, CYCLE=1.0)  # 稳定段 N=1
        n_before = f.STABLE_N
        t_before = f.STABLE_T
        step_finder(f, PV=70.0, AV=30.0, CYCLE=1.0, SAMPLE_OK=False)
        self.assertEqual(f.STABLE_N, n_before)  # 冻结
        self.assertEqual(f.STABLE_T, t_before)
        self.assertEqual(f.PV_1, 70.0)  # 仍更新
        self.assertEqual(f.AV_1, 30.0)

    def test_sp_auto_en_false_only_clears_ok_and_conf(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        self.assertIs(f.SP_AUTO_OK, True)
        step_finder(f, PV=50.0, CYCLE=1.0, SP_AUTO_EN=False)
        self.assertIs(f.SP_AUTO_OK, False)
        self.assertEqual(f.SP_AUTO_CONF, 0.0)
        self.assertAlmostEqual(f.SP_AUTO, 50.0)  # 保留
        self.assertEqual(f.STABLE_N, 1)  # 稳定段保留

    def test_reset_clears_sp_auto(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(f, PV=50.0, RESET=True)
        self.assertEqual(f.SP_AUTO, 0.0)
        self.assertIs(f.SP_AUTO_OK, False)
        self.assertEqual(f.SP_AUTO_CONF, 0.0)


# ============================ G. EN=False 语义 ============================


class TestENFalse(unittest.TestCase):
    def test_en_false_freezes_stable_and_pv1(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, AV=10.0, CYCLE=1.0)  # 建立稳定段，PV_1=50
        n_before = f.STABLE_N
        step_finder(f, PV=70.0, AV=30.0, EN=False)
        self.assertEqual(f.STABLE_N, n_before)  # 自动段不推进
        self.assertEqual(f.PV_1, 50.0)  # PV_1 不更新
        self.assertEqual(f.AV_1, 10.0)

    def test_en_false_still_updates_thresholds(self):
        f = APCSPFINDER()
        step_finder(f, EN=False, PVMU=300.0, PVMD=0.0)
        self.assertEqual(f.PV_RANGE, 300.0)

    def test_en_false_recomputes_tag_bad_and_final_sp(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)  # SP_AUTO=50, SP_AUTO_OK=True
        # EN=False，SP_TAG 远离 SP_AUTO → 仍判 SP_TAG_BAD；现场 SP 仍被选用。
        step_finder(f, EN=False, SP_TAG=80.0, SP_TAG_EN=True)
        self.assertIs(f.SP_TAG_BAD, True)
        self.assertEqual(f.SP_SOURCE, 2)
        self.assertEqual(f.SP_REASON, 5)
        self.assertAlmostEqual(f.SP_USE, 80.0)

    def test_en_false_can_use_historical_auto_sp(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(f, EN=False, SP_MAN_EN=False, SP_TAG_EN=False, SP_AUTO_EN=True)
        self.assertEqual(f.SP_SOURCE, 3)
        self.assertAlmostEqual(f.SP_USE, 50.0)

    def test_en_false_auto_disabled_history_ok_replace_falls_to_pv(self):
        """边缘四者交叉（APCSPFINDER-EDGE-1）：按源 ST 原样保留，不修复。

        历史 SP_AUTO_OK=True + EN=False + SP_AUTO_EN=False + SP_TAG_EN=True +
        SP_AUTO_REPLACE_BAD_TAG=True + 现场 SP 远离历史自动 SP：

        * EN=False → 整段自动块（含 ``ELSIF NOT SP_AUTO_EN`` 的清 OK 分支）跳过，
          历史 SP_AUTO_OK 保留为 True；
        * SP_TAG_BAD 据历史 SP_AUTO 重算为 True；
        * 现场 SP 因"允许替换 AND 可疑 AND 自动有效"被排除；
        * 自动 SP 分支又因 SP_AUTO_EN=False 被拦下；
        * → 落到无有效 SP 兜底 SP_USE=PV / 源0 / 因4。

        这是看似反直觉但应按源代码保留的边缘路径，仅作回归锁定，不改业务逻辑。

        注：历史 ``SP_AUTO_OK=True`` 经此前若干次正常稳定采样（EN=True/SAMPLE_OK=True，
        PV 恒定）经真实 ``step()`` 跨扫描链自然形成，而非直接手写内部私有状态，
        以同时锁定分支与跨扫描状态链。
        """
        f = APCSPFINDER()
        # 多拍正常稳定采样自然形成 SP_AUTO_OK：CYCLE=0.5, SP_STABLE_T=2.0 →
        # 需 STABLE_T>=2.0，5 拍（STABLE_T=2.5）后合格；PV 恒定 → 段均值=50。
        for _ in range(5):
            step_finder(f, EN=True, SAMPLE_OK=True, PV=50.0, AV=10.0,
                        CYCLE=0.5, SP_STABLE_T=2.0)
        self.assertIs(f.SP_AUTO_OK, True)  # 经真实跨扫描链形成
        self.assertAlmostEqual(f.SP_AUTO, 50.0)
        self.assertEqual(f.STABLE_N, 5)
        step_finder(
            f,
            EN=False,
            SP_AUTO_EN=False,
            SP_TAG_EN=True,
            SP_AUTO_REPLACE_BAD_TAG=True,
            SP_TAG=80.0,  # 与历史 SP_AUTO=50 差 30 > SP_BAD_TH=5
            PV=60.0,
        )
        self.assertIs(f.SP_AUTO_OK, True)  # 历史标志被保留（清 OK 分支未执行）
        self.assertAlmostEqual(f.SP_AUTO, 50.0)
        self.assertIs(f.SP_TAG_BAD, True)  # 据历史自动 SP 重算
        self.assertAlmostEqual(f.SP_USE, 60.0)  # SP_USE=PV
        self.assertIs(f.SP_VALID, False)
        self.assertEqual(f.SP_SOURCE, 0)
        self.assertEqual(f.SP_REASON, 4)


# ============================ H. RESET 当拍行为 ============================


class TestResetTick(unittest.TestCase):
    def test_reset_resets_auto_and_skips_stable_but_runs_final(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(f, PV=50.0, RESET=True, EN=True, SP_TAG=42.0, SP_TAG_EN=True)
        # 自动被重置，当拍不进入稳定段
        self.assertEqual(f.STABLE_N, 0)
        self.assertIs(f.SP_AUTO_OK, False)
        # 最终 SP 仍执行：现场 SP 被使用
        self.assertEqual(f.SP_SOURCE, 2)
        self.assertAlmostEqual(f.SP_USE, 42.0)

    def test_reset_man_sp_priority(self):
        f = APCSPFINDER()
        step_finder(f, RESET=True, SP_MAN=33.0, SP_MAN_EN=True)
        self.assertEqual(f.SP_SOURCE, 1)
        self.assertAlmostEqual(f.SP_USE, 33.0)

    def test_reset_no_man_no_tag_uses_pv_invalid(self):
        f = APCSPFINDER()
        step_finder(f, RESET=True, PV=47.0, SP_MAN_EN=False, SP_TAG_EN=False)
        self.assertAlmostEqual(f.SP_USE, 47.0)
        self.assertIs(f.SP_VALID, False)
        self.assertEqual(f.SP_SOURCE, 0)
        self.assertEqual(f.SP_REASON, 4)


# ============================ I. SP_TAG_BAD 边界 ============================


class TestTagBadBoundary(unittest.TestCase):
    def test_tag_en_false_is_false(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(f, PV=50.0, CYCLE=1.0, SP_TAG=999.0, SP_TAG_EN=False)
        self.assertIs(f.SP_TAG_BAD, False)

    def test_auto_ok_false_is_false(self):
        f = APCSPFINDER()
        step_finder(f, PV=50.0, SP_TAG=999.0)  # 未合格，SP_AUTO_OK=False
        self.assertIs(f.SP_AUTO_OK, False)
        self.assertIs(f.SP_TAG_BAD, False)

    def test_diff_equal_th_is_false(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)  # SP_AUTO=50, SP_BAD_TH=5
        step_finder(f, PV=50.0, CYCLE=1.0, SP_TAG=55.0)  # diff=5 == 5 → 不 >
        self.assertIs(f.SP_TAG_BAD, False)

    def test_diff_just_above_is_true(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(f, PV=50.0, CYCLE=1.0, SP_TAG=55.01)  # diff=5.01 > 5
        self.assertIs(f.SP_TAG_BAD, True)


# ============================ J. 最终优先级与编码 ============================


class TestFinalPriority(unittest.TestCase):
    def test_manual_priority(self):
        f = APCSPFINDER()
        step_finder(f, SP_MAN=11.0, SP_MAN_EN=True, SP_TAG=22.0)
        self.assertAlmostEqual(f.SP_USE, 11.0)
        self.assertIs(f.SP_VALID, True)
        self.assertEqual(f.SP_SOURCE, 1)
        self.assertEqual(f.SP_REASON, 1)

    def test_tag_normal(self):
        f = APCSPFINDER()
        step_finder(f, SP_TAG=22.0, SP_TAG_EN=True)
        self.assertAlmostEqual(f.SP_USE, 22.0)
        self.assertEqual(f.SP_SOURCE, 2)
        self.assertEqual(f.SP_REASON, 2)

    def test_tag_suspicious_no_replace(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)  # SP_AUTO=50, OK
        step_finder(
            f, PV=50.0, CYCLE=1.0, SP_TAG=80.0, SP_AUTO_REPLACE_BAD_TAG=False
        )
        self.assertIs(f.SP_TAG_BAD, True)
        self.assertEqual(f.SP_SOURCE, 2)
        self.assertEqual(f.SP_REASON, 5)
        self.assertAlmostEqual(f.SP_USE, 80.0)

    def test_tag_suspicious_replace_to_auto(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(
            f, PV=50.0, CYCLE=1.0, SP_TAG=80.0, SP_AUTO_REPLACE_BAD_TAG=True
        )
        self.assertEqual(f.SP_SOURCE, 3)
        self.assertEqual(f.SP_REASON, 6)
        self.assertAlmostEqual(f.SP_USE, 50.0)

    def test_no_tag_auto_valid(self):
        f = APCSPFINDER()
        make_qualified(f, pv=50.0)
        step_finder(f, PV=50.0, CYCLE=1.0, SP_TAG_EN=False)
        self.assertEqual(f.SP_SOURCE, 3)
        self.assertEqual(f.SP_REASON, 3)
        self.assertAlmostEqual(f.SP_USE, 50.0)

    def test_no_valid_sp_uses_pv(self):
        f = APCSPFINDER()
        step_finder(
            f, PV=48.0, SP_MAN_EN=False, SP_TAG_EN=False, SP_AUTO_EN=False
        )
        self.assertAlmostEqual(f.SP_USE, 48.0)
        self.assertIs(f.SP_VALID, False)
        self.assertEqual(f.SP_SOURCE, 0)
        self.assertEqual(f.SP_REASON, 4)


# ============================ K. CYCLE 与 dt_ms 分离 ============================


class TestCycleVsDtMs(unittest.TestCase):
    def test_same_cycle_diff_dt_ms_same_stable_t(self):
        f1 = APCSPFINDER()
        f2 = APCSPFINDER()
        for _ in range(4):
            step_finder(f1, PV=50.0, CYCLE=0.5, dt_ms=500)
            step_finder(f2, PV=50.0, CYCLE=0.5, dt_ms=1000)
        self.assertAlmostEqual(f1.STABLE_T, f2.STABLE_T)
        self.assertAlmostEqual(f1.STABLE_T, 2.0)  # 4 拍 × 0.5

    def test_diff_cycle_same_dt_ms_scales_stable_t(self):
        f1 = APCSPFINDER()
        f2 = APCSPFINDER()
        for _ in range(4):
            step_finder(f1, PV=50.0, CYCLE=0.5, dt_ms=500)
            step_finder(f2, PV=50.0, CYCLE=2.0, dt_ms=500)
        self.assertAlmostEqual(f1.STABLE_T, 2.0)
        self.assertAlmostEqual(f2.STABLE_T, 8.0)  # 4 拍 × 2.0


if __name__ == "__main__":
    unittest.main()
