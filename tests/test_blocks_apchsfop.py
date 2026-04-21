"""APCHSFOP（一阶惯性滤波）业务块单元测试。

覆盖：
    * 初始态
    * 首拍行为（不等于 IN，而是 α·KG·IN）
    * 常数输入的稳态收敛到 KG·IN
    * Step 响应的单调爬升性
    * 滤波公式字面数值锁定
    * α = TB/(TB+TC) 的强/弱滤波对比
    * 守护 1：``TB+TC <= 0.001`` 整拍跳过
    * 守护 2：``|AV_TEMP| >= 1e10`` 爆值冻结
    * KG != 1 的增益放大
    * 与 APCHXHCL 内嵌滤波段一致
    * step 接口契约（忽略 dt_ms / 关键字参数 / 返回键）
    * 多实例独立
    * RETAIN 语义：不 RESET 则持续累积
"""

from __future__ import annotations

import unittest

from src.blocks import APCHSFOP
from src.blocks.apchsfop import AV_LIMIT, TB_PLUS_TC_MIN


DT = 500


class TestInitialState(unittest.TestCase):
    def test_fresh_instance_outputs_zero(self) -> None:
        f = APCHSFOP()
        self.assertEqual(f.AV, 0.0)
        self.assertEqual(f.Ok_1, 0.0)
        self.assertEqual(f.AV_TEMP, 0.0)


class TestFirstTick(unittest.TestCase):
    def test_first_tick_equals_alpha_kg_in(self) -> None:
        """首拍 AV = α·KG·IN，其中 α = TB/(TB+TC)，不是 IN 本身。"""
        f = APCHSFOP()
        TC, KG, TB, IN = 2.0, 1.0, 0.5, 100.0
        out = f.step(DT, IN=IN, TC=TC, KG=KG, TB=TB)
        alpha = TB / (TB + TC)
        expected = alpha * KG * IN
        self.assertAlmostEqual(out["AV"], expected, places=12)

    def test_first_tick_not_equal_to_input(self) -> None:
        f = APCHSFOP()
        out = f.step(DT, IN=100.0, TC=2.0, KG=1.0, TB=0.5)
        self.assertNotAlmostEqual(out["AV"], 100.0, places=3)
        self.assertLess(out["AV"], 100.0)
        self.assertGreater(out["AV"], 0.0)


class TestSteadyState(unittest.TestCase):
    def test_constant_input_converges_to_kg_times_in(self) -> None:
        """常数输入跑足够多拍后应收敛到 KG·IN。"""
        f = APCHSFOP()
        for _ in range(500):
            f.step(DT, IN=100.0, TC=2.0, KG=1.0, TB=0.5)
        self.assertAlmostEqual(f.AV, 100.0, places=6)

    def test_steady_state_with_gain(self) -> None:
        f = APCHSFOP()
        for _ in range(500):
            f.step(DT, IN=10.0, TC=1.0, KG=2.5, TB=0.5)
        self.assertAlmostEqual(f.AV, 25.0, places=6)


class TestStepResponseMonotonic(unittest.TestCase):
    def test_step_response_is_monotonic_rising_for_positive_step(self) -> None:
        f = APCHSFOP()
        prev = -1.0
        for _ in range(50):
            out = f.step(DT, IN=100.0, TC=2.0, KG=1.0, TB=0.5)
            self.assertGreaterEqual(out["AV"], prev)
            prev = out["AV"]
        self.assertLess(prev, 100.0)

    def test_step_response_is_monotonic_falling_to_zero(self) -> None:
        f = APCHSFOP()
        for _ in range(500):
            f.step(DT, IN=100.0, TC=1.0, KG=1.0, TB=0.5)
        self.assertAlmostEqual(f.AV, 100.0, places=4)
        prev = f.AV + 1.0
        for _ in range(50):
            out = f.step(DT, IN=0.0, TC=1.0, KG=1.0, TB=0.5)
            self.assertLessEqual(out["AV"], prev)
            prev = out["AV"]


class TestFilterFormulaLiteral(unittest.TestCase):
    """字面锁定 ST 原公式 AV_TEMP = (TC·Ok_1 + KG·TB·IN) / (TB+TC)。"""

    def test_single_tick_numeric_value(self) -> None:
        f = APCHSFOP()
        TC, KG, TB, IN = 3.0, 1.5, 0.5, 42.0
        expected = (TC * 0.0 + KG * TB * IN) / (TB + TC)
        out = f.step(DT, IN=IN, TC=TC, KG=KG, TB=TB)
        self.assertAlmostEqual(out["AV"], expected, places=12)

    def test_three_ticks_numeric_value(self) -> None:
        TC, KG, TB = 2.0, 1.0, 0.5
        inputs = [10.0, 20.0, 30.0]
        ok_1 = 0.0
        for v in inputs:
            ok_1 = (TC * ok_1 + KG * TB * v) / (TB + TC)

        f = APCHSFOP()
        for v in inputs:
            f.step(DT, IN=v, TC=TC, KG=KG, TB=TB)
        self.assertAlmostEqual(f.AV, ok_1, places=12)


class TestAlphaStrongVsWeak(unittest.TestCase):
    """α = TB/(TB+TC) 决定滤波强度。"""

    def test_small_alpha_filters_more_than_large_alpha(self) -> None:
        """同样 100 拍阶跃输入，强滤波（α 小）应远未到稳态；弱滤波应接近稳态。

        strong: TC=50, TB=0.5 → α ≈ 0.0099，100 拍约 63%（≈1 - e^-1）
        weak:   TC=0.1, TB=0.5 → α ≈ 0.833，10 拍就已基本到稳态
        """
        strong = APCHSFOP()
        weak = APCHSFOP()
        for _ in range(100):
            strong.step(DT, IN=100.0, TC=50.0, KG=1.0, TB=0.5)
            weak.step(DT, IN=100.0, TC=0.1, KG=1.0, TB=0.5)
        self.assertLess(strong.AV, weak.AV)
        self.assertGreater(weak.AV, 99.9)
        self.assertLess(strong.AV, 80.0)


class TestGuardTbPlusTcTooSmall(unittest.TestCase):
    """守护 1：TB+TC <= 0.001 整拍跳过。"""

    def test_tb_plus_tc_at_threshold_skips_tick(self) -> None:
        f = APCHSFOP()
        f.step(DT, IN=50.0, TC=1.0, KG=1.0, TB=0.5)
        snapshot_av = f.AV
        snapshot_ok1 = f.Ok_1

        f.step(DT, IN=999.0, TC=0.0005, KG=1.0, TB=0.0005)
        self.assertEqual(f.AV, snapshot_av)
        self.assertEqual(f.Ok_1, snapshot_ok1)

    def test_tb_plus_tc_just_above_threshold_updates(self) -> None:
        f = APCHSFOP()
        f.step(DT, IN=1.0, TC=0.001, KG=1.0, TB=0.001)
        self.assertNotEqual(f.AV, 0.0)

    def test_zero_tc_and_zero_tb_integer_skips(self) -> None:
        f = APCHSFOP()
        f.AV = 7.5
        f.Ok_1 = 7.5
        f.step(DT, IN=999.0, TC=0.0, KG=1.0, TB=0.0)
        self.assertEqual(f.AV, 7.5)


class TestGuardAvTempExplodes(unittest.TestCase):
    """守护 2：|AV_TEMP| >= 1e10 冻结 AV / Ok_1（但 AV_TEMP 字段已被本拍写入）。"""

    def test_very_large_input_freezes_av(self) -> None:
        f = APCHSFOP()
        f.step(DT, IN=1.0, TC=1.0, KG=1.0, TB=0.5)
        snap_av = f.AV
        snap_ok1 = f.Ok_1

        big = 1e12
        f.step(DT, IN=big, TC=1.0, KG=1.0, TB=0.5)
        self.assertEqual(f.AV, snap_av)
        self.assertEqual(f.Ok_1, snap_ok1)

    def test_very_negative_input_freezes_av(self) -> None:
        f = APCHSFOP()
        f.step(DT, IN=1.0, TC=1.0, KG=1.0, TB=0.5)
        snap_av = f.AV

        f.step(DT, IN=-1e12, TC=1.0, KG=1.0, TB=0.5)
        self.assertEqual(f.AV, snap_av)

    def test_av_limit_constant_is_1e10(self) -> None:
        self.assertEqual(AV_LIMIT, 1e10)

    def test_guard_thresholds_match_st(self) -> None:
        self.assertEqual(TB_PLUS_TC_MIN, 0.001)
        self.assertEqual(AV_LIMIT, 1e10)


class TestGainKg(unittest.TestCase):
    def test_kg_zero_drives_av_to_zero(self) -> None:
        f = APCHSFOP()
        for _ in range(500):
            f.step(DT, IN=100.0, TC=1.0, KG=0.0, TB=0.5)
        self.assertAlmostEqual(f.AV, 0.0, places=9)

    def test_negative_kg_inverts_sign(self) -> None:
        f = APCHSFOP()
        for _ in range(500):
            f.step(DT, IN=10.0, TC=1.0, KG=-2.0, TB=0.5)
        self.assertAlmostEqual(f.AV, -20.0, places=6)


class TestConsistencyWithApchxhclEmbeddedFilter(unittest.TestCase):
    """验证本块输出与 APCHXHCL 内嵌同公式段数值一致（同参数、同输入）。"""

    def test_same_formula_same_numbers(self) -> None:
        TC, KG, TB = 2.0, 1.0, 0.5
        inputs = [1.0, 2.0, 3.0, 4.0, 5.0]

        ok_1 = 0.0
        for v in inputs:
            av_temp = (TC * ok_1 + KG * TB * v) / (TB + TC)
            if -1e10 < av_temp < 1e10:
                ok_1 = av_temp
        expected = ok_1

        f = APCHSFOP()
        for v in inputs:
            f.step(DT, IN=v, TC=TC, KG=KG, TB=TB)
        self.assertAlmostEqual(f.AV, expected, places=12)


class TestStepContract(unittest.TestCase):
    def test_step_ignores_dt_ms(self) -> None:
        a = APCHSFOP()
        b = APCHSFOP()
        for v in (1.0, 2.0, 3.0):
            a.step(1, IN=v, TC=1.0, KG=1.0, TB=0.5)
            b.step(10_000_000, IN=v, TC=1.0, KG=1.0, TB=0.5)
        self.assertEqual(a.AV, b.AV)
        self.assertEqual(a.Ok_1, b.Ok_1)

    def test_step_returns_dict_with_expected_keys(self) -> None:
        f = APCHSFOP()
        out = f.step(DT, IN=1.0, TC=1.0, KG=1.0, TB=0.5)
        self.assertEqual(set(out.keys()), {"AV"})

    def test_inputs_are_keyword_only(self) -> None:
        f = APCHSFOP()
        with self.assertRaises(TypeError):
            f.step(DT, 1.0, 1.0, 1.0, 0.5)  # type: ignore[misc]


class TestMultipleInstances(unittest.TestCase):
    def test_independence(self) -> None:
        a = APCHSFOP()
        b = APCHSFOP()
        a.step(DT, IN=100.0, TC=2.0, KG=1.0, TB=0.5)
        b.step(DT, IN=-100.0, TC=2.0, KG=1.0, TB=0.5)
        self.assertNotEqual(a.AV, b.AV)
        self.assertGreater(a.AV, 0.0)
        self.assertLess(b.AV, 0.0)


class TestTbDecoupledFromScanCycle(unittest.TestCase):
    """R7 回归测试：``TB`` 是显式输入脚，与 runtime ``dt_ms`` 解耦。

    对应 00a 契约 R7 条 + ``docs/RISKS.md::APCHSFOP-H5``。
    验证"``TB`` 与 ``cycle_ms`` 不对齐时 FB 仍按 FB 自身语义正确工作"。
    """

    def test_tb_independent_of_dt_ms_value(self) -> None:
        """同一份 (IN, TC, KG, TB) 下，dt_ms 任意值都给出完全相同的 AV 轨迹。"""
        inputs = [10.0, 20.0, 30.0, 40.0, 50.0]
        TC, KG, TB = 2.0, 1.0, 0.5

        f_cycle_aligned = APCHSFOP()
        f_cycle_unaligned_half = APCHSFOP()
        f_cycle_unaligned_fast = APCHSFOP()

        for v in inputs:
            f_cycle_aligned.step(500, IN=v, TC=TC, KG=KG, TB=TB)
            f_cycle_unaligned_half.step(250, IN=v, TC=TC, KG=KG, TB=TB)
            f_cycle_unaligned_fast.step(10, IN=v, TC=TC, KG=KG, TB=TB)

        self.assertEqual(f_cycle_aligned.AV, f_cycle_unaligned_half.AV)
        self.assertEqual(f_cycle_aligned.AV, f_cycle_unaligned_fast.AV)

    def test_tb_not_equal_to_scan_cycle_still_converges(self) -> None:
        """扫描周期 cycle_ms=500 但 TB=0.3 秒（业务采样节拍 ≠ 扫描节拍），
        FB 仍按 TB 自身语义正常收敛到 KG·IN。"""
        f = APCHSFOP()
        for _ in range(500):
            f.step(500, IN=42.0, TC=1.0, KG=1.0, TB=0.3)
        self.assertAlmostEqual(f.AV, 42.0, places=5)

    def test_tb_is_keyword_argument_cannot_be_omitted(self) -> None:
        """显式输入脚：不传 TB 必须报错（不得被 dt_ms 隐式替代）。"""
        f = APCHSFOP()
        with self.assertRaises(TypeError):
            f.step(500, IN=1.0, TC=1.0, KG=1.0)  # type: ignore[call-arg]

    def test_tb_larger_than_dt_ms_equivalent_works(self) -> None:
        """业务上可能需要"若干扫描周期合并为一个业务样本"：
        cycle_ms=100 但 TB=2.0（即业务节拍 2s），FB 应按 TB 语义工作。"""
        f = APCHSFOP()
        for _ in range(300):
            f.step(100, IN=10.0, TC=5.0, KG=1.0, TB=2.0)
        self.assertAlmostEqual(f.AV, 10.0, places=3)


class TestRetainSemantics(unittest.TestCase):
    """ST 里 AV / Ok_1 / AV_TEMP 带 RETAIN——Python 侧以实例属性保持。"""

    def test_state_persists_across_steps(self) -> None:
        f = APCHSFOP()
        f.step(DT, IN=100.0, TC=1.0, KG=1.0, TB=0.5)
        first = f.AV
        f.step(DT, IN=100.0, TC=1.0, KG=1.0, TB=0.5)
        self.assertGreater(f.AV, first)
        self.assertEqual(f.Ok_1, f.AV)


if __name__ == "__main__":
    unittest.main()
