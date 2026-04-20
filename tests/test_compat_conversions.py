"""兼容层 conversions helper 的单元测试。

按《APCHXHCL_v2_最小转换层与风险收口任务书》§七.A 要求覆盖：

* ``real_to_int``：整数 / 正负小数 / 边界 / APCHXHCL ``60/TB`` 场景
* ``real_to_time_ms``：``0`` / 正数 / 小数 / 大值 / ``TL*1000`` 场景
* ``int_to_real``：基础转换 / 负数 / 0
* 异常输入拒绝：``NaN`` / ``±Inf`` / ``bool`` / 负时间
"""

from __future__ import annotations

import math
import unittest

from src.compat import int_to_real, real_to_int, real_to_time_ms


class TestRealToInt(unittest.TestCase):
    """real_to_int 契约：银行家舍入 + 拒绝 NaN/Inf。"""

    def test_integer_input(self) -> None:
        self.assertEqual(real_to_int(120), 120)
        self.assertEqual(real_to_int(0), 0)
        self.assertEqual(real_to_int(-5), -5)

    def test_exact_integer_float(self) -> None:
        self.assertEqual(real_to_int(60.0), 60)
        self.assertEqual(real_to_int(120.0), 120)
        self.assertEqual(real_to_int(-30.0), -30)

    def test_positive_decimals(self) -> None:
        self.assertEqual(real_to_int(1.4), 1)
        self.assertEqual(real_to_int(1.6), 2)
        self.assertEqual(real_to_int(2.1), 2)
        self.assertEqual(real_to_int(2.9), 3)

    def test_negative_decimals(self) -> None:
        self.assertEqual(real_to_int(-1.4), -1)
        self.assertEqual(real_to_int(-1.6), -2)
        self.assertEqual(real_to_int(-2.9), -3)

    def test_bankers_rounding_half_to_even(self) -> None:
        self.assertEqual(real_to_int(0.5), 0)
        self.assertEqual(real_to_int(1.5), 2)
        self.assertEqual(real_to_int(2.5), 2)
        self.assertEqual(real_to_int(3.5), 4)
        self.assertEqual(real_to_int(-0.5), 0)
        self.assertEqual(real_to_int(-1.5), -2)

    def test_apchxhcl_sample_n_scenarios(self) -> None:
        """业务场景：``SAMPLE_N = real_to_int(60.0 / TB)``。"""
        self.assertEqual(real_to_int(60.0 / 0.5), 120)
        self.assertEqual(real_to_int(60.0 / 1.0), 60)
        self.assertEqual(real_to_int(60.0 / 2.0), 30)
        self.assertEqual(real_to_int(60.0 / 2.5), 24)
        self.assertEqual(real_to_int(60.0 / 0.3), 200)
        self.assertEqual(real_to_int(60.0 / 7.0), 9)

    def test_reject_nan(self) -> None:
        with self.assertRaises(ValueError):
            real_to_int(float("nan"))

    def test_reject_inf(self) -> None:
        with self.assertRaises(ValueError):
            real_to_int(float("inf"))
        with self.assertRaises(ValueError):
            real_to_int(-math.inf)

    def test_reject_non_numeric(self) -> None:
        with self.assertRaises(TypeError):
            real_to_int("1.5")  # type: ignore[arg-type]


class TestRealToTimeMs(unittest.TestCase):
    """real_to_time_ms 契约：毫秒入毫秒出，拒绝负值 / NaN / Inf。"""

    def test_zero(self) -> None:
        self.assertEqual(real_to_time_ms(0), 0)
        self.assertEqual(real_to_time_ms(0.0), 0)

    def test_positive_integer_ms(self) -> None:
        self.assertEqual(real_to_time_ms(500), 500)
        self.assertEqual(real_to_time_ms(60_000), 60_000)

    def test_positive_fractional_ms(self) -> None:
        self.assertEqual(real_to_time_ms(500.0), 500)
        self.assertEqual(real_to_time_ms(500.4), 500)
        self.assertEqual(real_to_time_ms(500.6), 501)

    def test_large_values(self) -> None:
        self.assertEqual(real_to_time_ms(86_400_000.0), 86_400_000)

    def test_apchxhcl_tl_scenarios(self) -> None:
        """业务场景：``PT_ms = real_to_time_ms(TL * 1000)``。"""
        self.assertEqual(real_to_time_ms(60.0 * 1000), 60_000)
        self.assertEqual(real_to_time_ms(2.0 * 1000), 2_000)
        self.assertEqual(real_to_time_ms(0.5 * 1000), 500)
        self.assertEqual(real_to_time_ms(290.0 * 1000), 290_000)

    def test_reject_negative(self) -> None:
        with self.assertRaises(ValueError):
            real_to_time_ms(-1.0)
        with self.assertRaises(ValueError):
            real_to_time_ms(-0.1)

    def test_reject_nan_inf(self) -> None:
        with self.assertRaises(ValueError):
            real_to_time_ms(float("nan"))
        with self.assertRaises(ValueError):
            real_to_time_ms(float("inf"))


class TestIntToReal(unittest.TestCase):
    """int_to_real 契约：整数 ↔ float 的显式封装。"""

    def test_basic(self) -> None:
        self.assertEqual(int_to_real(3), 3.0)
        self.assertIsInstance(int_to_real(3), float)

    def test_negative(self) -> None:
        self.assertEqual(int_to_real(-5), -5.0)

    def test_zero(self) -> None:
        self.assertEqual(int_to_real(0), 0.0)

    def test_reject_bool(self) -> None:
        with self.assertRaises(TypeError):
            int_to_real(True)  # type: ignore[arg-type]

    def test_reject_float(self) -> None:
        with self.assertRaises(TypeError):
            int_to_real(1.5)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
