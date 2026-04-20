"""契约级配置校验测试。"""

from __future__ import annotations

import unittest
import warnings

from src.validation import (
    PTMsConfigWarning,
    TBConfigWarning,
    check_pt_ms,
    check_tb_sample_n_integer,
)


class TestCheckPTMs(unittest.TestCase):
    def test_multiple_of_cycle_no_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", PTMsConfigWarning)
            check_pt_ms(pt_ms=1500, cycle_ms=500)

    def test_less_than_cycle_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            check_pt_ms(pt_ms=200, name="M1.TON_Start", cycle_ms=500)
        self.assertTrue(any(issubclass(w.category, PTMsConfigWarning) for w in caught))
        self.assertTrue(any("无实际意义" in str(w.message) for w in caught))
        self.assertTrue(any("M1.TON_Start" in str(w.message) for w in caught))

    def test_non_multiple_warns_with_quantization_hint(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            check_pt_ms(pt_ms=1300, cycle_ms=500)
        self.assertTrue(any(issubclass(w.category, PTMsConfigWarning) for w in caught))
        self.assertTrue(any("量化" in str(w.message) for w in caught))

    def test_type_error_on_float(self):
        with self.assertRaises(TypeError):
            check_pt_ms(pt_ms=1.5, cycle_ms=500)  # type: ignore[arg-type]

    def test_value_error_on_negative(self):
        with self.assertRaises(ValueError):
            check_pt_ms(pt_ms=-100, cycle_ms=500)


class TestCheckTbSampleNInteger(unittest.TestCase):
    """APCHXHCL R4：业务推荐 ``60/TB`` 为整数，否则 warning。"""

    def test_integer_ratios_no_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", TBConfigWarning)
            for tb in (0.5, 1.0, 2.0, 5.0, 6.0):
                check_tb_sample_n_integer(tb)

    def test_non_integer_ratio_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            check_tb_sample_n_integer(0.7, name="APCHXHCL_1")
        self.assertTrue(
            any(issubclass(w.category, TBConfigWarning) for w in caught)
        )
        self.assertTrue(any("APCHXHCL_1" in str(w.message) for w in caught))
        self.assertTrue(
            any("不是整数" in str(w.message) for w in caught)
        )

    def test_tb_0_3_is_float_representable_as_integer_ratio(self) -> None:
        """TB=0.3 时 ``60/0.3`` 在 Python 浮点下精确等于 200.0，不会 warning。
        这是浮点本身的限制，由 docs/RISKS.md R4 正式登记（用户配置时应避免）。
        """
        with warnings.catch_warnings():
            warnings.simplefilter("error", TBConfigWarning)
            check_tb_sample_n_integer(0.3)

    def test_custom_window(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", TBConfigWarning)
            check_tb_sample_n_integer(0.5, window_seconds=30.0)

    def test_zero_tb_raises(self):
        with self.assertRaises(ValueError):
            check_tb_sample_n_integer(0.0)

    def test_negative_tb_raises(self):
        with self.assertRaises(ValueError):
            check_tb_sample_n_integer(-1.0)

    def test_non_numeric_tb_raises(self):
        with self.assertRaises(TypeError):
            check_tb_sample_n_integer("0.5")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
