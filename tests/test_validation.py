"""契约级配置校验测试。"""

from __future__ import annotations

import unittest
import warnings

from src.validation import PTMsConfigWarning, check_pt_ms


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


if __name__ == "__main__":
    unittest.main()
