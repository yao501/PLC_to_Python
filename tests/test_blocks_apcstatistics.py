"""APCSTATISTICS 业务块单元测试（修正版）。

对齐任务书 ``STATISTICS_修正版语义说明_与_Python改写任务书.md`` 的 §7 专项测试，
并补充修正版决策锁死测试。

覆盖清单：
    §7.1  初始态
    §7.2  RESET 分支
    §7.3  首个样本
    §7.4  连续递增 (10, 20, 30)
    §7.5  连续递减 (30, 20, 10)
    §7.6  常数输入
    §7.7  RESET 后重新统计
    §7.8  负数输入
    §7.9  浮点输入
    §7.10 长序列累计（无 COUNTER//2 残留）
    额外：修正版决策锁死（无 SUM 字段 / 无溢出分支 / Welford 公式数值）
    额外：step 接口契约（忽略 dt_ms / 返回键 / 关键字参数）
    额外：多实例独立
"""

from __future__ import annotations

import unittest

from src.blocks import APCSTATISTICS
from src.blocks.apcstatistics import REAL_MAX, REAL_MIN


DT = 500


def _feed(stats: APCSTATISTICS, inputs, reset: bool = False) -> None:
    for v in inputs:
        stats.step(DT, IN=v, RESET=reset)


class TestInitialState(unittest.TestCase):
    """§7.1"""

    def test_initial_mn_mx_are_unified_sentinels(self) -> None:
        s = APCSTATISTICS()
        self.assertEqual(s.MN, REAL_MAX)
        self.assertEqual(s.MX, REAL_MIN)
        self.assertEqual(s.AVG, 0.0)
        self.assertEqual(s.COUNTER, 0)

    def test_declared_init_matches_reset_values(self) -> None:
        """修正版决策：声明初值与 RESET 分支赋值必须一致。"""
        s = APCSTATISTICS()
        mn_init = s.MN
        mx_init = s.MX
        s.step(DT, IN=999.0, RESET=True)
        self.assertEqual(s.MN, mn_init)
        self.assertEqual(s.MX, mx_init)


class TestResetBranch(unittest.TestCase):
    """§7.2"""

    def test_reset_clears_all_state(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (1.0, 2.0, 3.0))
        s.step(DT, IN=999.0, RESET=True)
        self.assertEqual(s.AVG, 0.0)
        self.assertEqual(s.COUNTER, 0)
        self.assertEqual(s.MN, REAL_MAX)
        self.assertEqual(s.MX, REAL_MIN)

    def test_reset_does_not_sample_current_in(self) -> None:
        """RESET=True 当拍的 IN 不得进入统计。"""
        s = APCSTATISTICS()
        s.step(DT, IN=50.0, RESET=True)
        self.assertEqual(s.COUNTER, 0)
        self.assertEqual(s.MN, REAL_MAX)
        self.assertEqual(s.MX, REAL_MIN)
        self.assertEqual(s.AVG, 0.0)

        s.step(DT, IN=100.0, RESET=False)
        self.assertEqual(s.COUNTER, 1)
        out = s.step(DT, IN=-50.0, RESET=False)
        self.assertEqual(out["MN"], -50.0)
        self.assertEqual(out["MX"], 100.0)
        self.assertEqual(s.COUNTER, 2)

    def test_reset_dominates_even_with_extreme_in(self) -> None:
        s = APCSTATISTICS()
        s.step(DT, IN=100.0, RESET=False)
        self.assertEqual(s.MN, 100.0)
        s.step(DT, IN=-1e30, RESET=True)
        self.assertEqual(s.MN, REAL_MAX)
        self.assertEqual(s.MX, REAL_MIN)


class TestFirstSample(unittest.TestCase):
    """§7.3"""

    def test_first_sample_from_fresh_instance(self) -> None:
        s = APCSTATISTICS()
        out = s.step(DT, IN=42.0, RESET=False)
        self.assertEqual(out["MN"], 42.0)
        self.assertEqual(out["MX"], 42.0)
        self.assertEqual(out["AVG"], 42.0)
        self.assertEqual(s.COUNTER, 1)

    def test_first_sample_after_reset(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (1.0, 2.0, 3.0))
        s.step(DT, IN=0.0, RESET=True)
        out = s.step(DT, IN=3.14, RESET=False)
        self.assertEqual(out["MN"], 3.14)
        self.assertEqual(out["MX"], 3.14)
        self.assertEqual(out["AVG"], 3.14)
        self.assertEqual(s.COUNTER, 1)


class TestAscendingInputs(unittest.TestCase):
    """§7.4"""

    def test_10_20_30(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (10.0, 20.0, 30.0))
        self.assertEqual(s.MN, 10.0)
        self.assertEqual(s.MX, 30.0)
        self.assertAlmostEqual(s.AVG, 20.0, places=12)
        self.assertEqual(s.COUNTER, 3)


class TestDescendingInputs(unittest.TestCase):
    """§7.5"""

    def test_30_20_10(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (30.0, 20.0, 10.0))
        self.assertEqual(s.MN, 10.0)
        self.assertEqual(s.MX, 30.0)
        self.assertAlmostEqual(s.AVG, 20.0, places=12)
        self.assertEqual(s.COUNTER, 3)


class TestConstantInput(unittest.TestCase):
    """§7.6"""

    def test_constant_five(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (5.0, 5.0, 5.0, 5.0))
        self.assertEqual(s.MN, 5.0)
        self.assertEqual(s.MX, 5.0)
        self.assertAlmostEqual(s.AVG, 5.0, places=12)
        self.assertEqual(s.COUNTER, 4)


class TestResetAndRestat(unittest.TestCase):
    """§7.7"""

    def test_second_window_is_independent(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (100.0, 200.0, 300.0))
        self.assertEqual(s.MN, 100.0)
        self.assertEqual(s.MX, 300.0)
        self.assertAlmostEqual(s.AVG, 200.0, places=12)

        s.step(DT, IN=0.0, RESET=True)
        _feed(s, (-1.0, -2.0, -3.0))
        self.assertEqual(s.MN, -3.0)
        self.assertEqual(s.MX, -1.0)
        self.assertAlmostEqual(s.AVG, -2.0, places=12)
        self.assertEqual(s.COUNTER, 3)


class TestNegativeInputs(unittest.TestCase):
    """§7.8"""

    def test_negative_series(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (-5.0, -1.0, -10.0))
        self.assertEqual(s.MN, -10.0)
        self.assertEqual(s.MX, -1.0)
        self.assertAlmostEqual(s.AVG, (-5.0 - 1.0 - 10.0) / 3.0, places=12)


class TestFloatInputs(unittest.TestCase):
    """§7.9"""

    def test_fractional_series(self) -> None:
        s = APCSTATISTICS()
        _feed(s, (1.5, 2.5, 3.5))
        self.assertEqual(s.MN, 1.5)
        self.assertEqual(s.MX, 3.5)
        self.assertAlmostEqual(s.AVG, 2.5, places=12)


class TestLongRunAccumulation(unittest.TestCase):
    """§7.10 长序列累计，验证无 COUNTER//2 残留且 AVG 准确。"""

    def test_ten_thousand_samples_counter_monotonic(self) -> None:
        s = APCSTATISTICS()
        for i in range(1, 10001):
            s.step(DT, IN=float(i), RESET=False)
        self.assertEqual(s.COUNTER, 10000)
        self.assertEqual(s.MN, 1.0)
        self.assertEqual(s.MX, 10000.0)
        self.assertAlmostEqual(s.AVG, (1.0 + 10000.0) / 2.0, places=6)

    def test_counter_passes_two_billion_threshold_without_halving(self) -> None:
        """修正版已删除 COUNTER//2 分支：手动推到 > 2e9 下一步不会发生减半。"""
        s = APCSTATISTICS()
        s.COUNTER = 2_000_000_001
        s.AVG = 0.0
        s.step(DT, IN=1.0, RESET=False)
        self.assertEqual(s.COUNTER, 2_000_000_002)

    def test_counter_passes_ulint_wrap_threshold(self) -> None:
        """Python int 无限精度，直接验证 DINT 上界(~2.1e9)之上仍线性累积。"""
        s = APCSTATISTICS()
        s.COUNTER = 3_000_000_000
        s.AVG = 42.0
        s.step(DT, IN=42.0, RESET=False)
        self.assertEqual(s.COUNTER, 3_000_000_001)
        self.assertAlmostEqual(s.AVG, 42.0, places=12)


class TestWelfordFormula(unittest.TestCase):
    """修正版决策锁死：AVG 必须使用 Welford 增量公式。"""

    def test_two_samples_match_welford(self) -> None:
        s = APCSTATISTICS()
        s.step(DT, IN=3.0, RESET=False)
        s.step(DT, IN=5.0, RESET=False)
        expected = 0.0 + (3.0 - 0.0) / 1.0
        expected = expected + (5.0 - expected) / 2.0
        self.assertAlmostEqual(s.AVG, expected, places=12)

    def test_matches_arithmetic_mean_for_reasonable_length(self) -> None:
        inputs = [i * 0.1 for i in range(1, 101)]
        s = APCSTATISTICS()
        _feed(s, inputs)
        self.assertAlmostEqual(s.AVG, sum(inputs) / len(inputs), places=9)


class TestRevisionDecisionsLocked(unittest.TestCase):
    """修正版决策锁死：不得回归到原 ST 行为。"""

    def test_no_sum_field_on_instance(self) -> None:
        s = APCSTATISTICS()
        self.assertFalse(hasattr(s, "SUM"))

    def test_instance_fields_are_minimal(self) -> None:
        s = APCSTATISTICS()
        expected_fields = {"MN", "MX", "AVG", "COUNTER"}
        public_fields = {k for k in vars(s).keys() if not k.startswith("_")}
        self.assertEqual(public_fields, expected_fields)


class TestStepContract(unittest.TestCase):
    def test_step_ignores_dt_ms(self) -> None:
        a = APCSTATISTICS()
        b = APCSTATISTICS()
        for v in (1.0, 2.0, 3.0):
            a.step(1, IN=v, RESET=False)
            b.step(1_000_000, IN=v, RESET=False)
        self.assertEqual(a.MN, b.MN)
        self.assertEqual(a.MX, b.MX)
        self.assertAlmostEqual(a.AVG, b.AVG, places=12)
        self.assertEqual(a.COUNTER, b.COUNTER)

    def test_step_returns_dict_with_expected_keys(self) -> None:
        s = APCSTATISTICS()
        out = s.step(DT, IN=1.0, RESET=False)
        self.assertEqual(set(out.keys()), {"MN", "MX", "AVG"})

    def test_in_and_reset_are_keyword_only(self) -> None:
        s = APCSTATISTICS()
        with self.assertRaises(TypeError):
            s.step(DT, 1.0, False)  # type: ignore[misc]


class TestMultipleInstances(unittest.TestCase):
    def test_multiple_instances_are_independent(self) -> None:
        a = APCSTATISTICS()
        b = APCSTATISTICS()
        a.step(DT, IN=10.0, RESET=False)
        b.step(DT, IN=-10.0, RESET=False)
        self.assertEqual(a.MN, 10.0)
        self.assertEqual(b.MN, -10.0)


if __name__ == "__main__":
    unittest.main()
