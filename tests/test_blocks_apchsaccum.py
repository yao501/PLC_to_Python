"""APCHSACCUM 业务块的契约测试。

按提示词 A~F 覆盖（离散积算 / 单次回绕 / 顺序锁定 / 源字面量锁定）：

* A 基础与导出：导入 / 冷启动初值 / 双实例隔离
* B 正常积算：MC 缩放 / dt_ms 不参与公式
* C MS 回绕：< / == / > 边界、单拍只减一次、下一拍才置 IV
* D 负值与 IV 恢复：当拍不修正、下一拍置 IV（含非零 IV）
* E RS 上升沿复位：先积算后复位、LR 存积算后值、电平持续不重复、回绕后 LR
* F 未使用变量与源字面量：bPositiveAccum 不改语义、默认 MS=1.797693134862e38
"""

from __future__ import annotations

import unittest

from src.blocks import APCHSACCUM


class TestExportAndColdStart(unittest.TestCase):
    """A. 基础与导出。"""

    def test_importable(self):
        self.assertTrue(callable(APCHSACCUM))

    def test_cold_start_defaults(self):
        b = APCHSACCUM()
        self.assertEqual(b.AV, 0.0)
        self.assertIs(b.SS, False)
        self.assertEqual(b.LR, 0.0)
        self.assertIs(b.preRS, False)
        self.assertIs(b.bPositiveAccum, False)

    def test_cold_start_av_is_zero_even_with_nonzero_iv(self):
        """即使 IV 非零，冷启动 AV 仍为 0.0（VAR_OUTPUT RETAIN 默认零值）。"""
        b = APCHSACCUM(IV=3.0, MS=100.0, MC=1.0)
        self.assertEqual(b.AV, 0.0)

    def test_two_instances_do_not_share_state(self):
        a = APCHSACCUM(MC=1.0)
        b = APCHSACCUM(MC=1.0)
        a.step(500, I1=5.0)
        self.assertEqual(a.AV, 5.0)
        self.assertEqual(b.AV, 0.0)


class TestNormalAccumulation(unittest.TestCase):
    """B. 正常积算。"""

    def test_default_mc_three_ticks(self):
        b = APCHSACCUM(MC=1.0)
        self.assertEqual(b.step(500, I1=2.0)["AV"], 2.0)
        self.assertEqual(b.step(500, I1=2.0)["AV"], 4.0)
        self.assertEqual(b.step(500, I1=2.0)["AV"], 6.0)

    def test_custom_mc_scales_input(self):
        b = APCHSACCUM(MC=0.5)
        out = b.step(500, I1=4.0)
        self.assertEqual(out["AV"], 2.0)

    def test_same_sequence_different_dt_same_output(self):
        seq = [1.0, 2.0, 3.0, -1.0, 5.0]
        a = APCHSACCUM(MC=1.0, MS=1000.0)
        b = APCHSACCUM(MC=1.0, MS=1000.0)
        for v in seq:
            oa = a.step(1, I1=v)
            ob = b.step(100000, I1=v)
            self.assertEqual(oa["AV"], ob["AV"])
            self.assertEqual(oa["SS"], ob["SS"])

    def test_dt_ms_not_in_formula(self):
        """dt_ms 不乘进累积量：同样 I1 在任意 dt_ms 下累加量相同。"""
        for dt in (0, 1, 100, 500, 100000):
            b = APCHSACCUM(MC=1.0, MS=1000.0)
            self.assertEqual(b.step(dt, I1=2.0)["AV"], 2.0)
            self.assertEqual(b.step(dt, I1=2.0)["AV"], 4.0)


class TestMSWraparound(unittest.TestCase):
    """C. MS 回绕。MS=10.0, MC=1.0, IV=0.0。"""

    def test_below_ms_direct_accumulate(self):
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        out = b.step(500, I1=3.0)
        self.assertEqual(out["AV"], 3.0)
        self.assertIs(out["SS"], False)

    def test_equal_ms_goes_else_branch(self):
        """AV+I1 == MS 时 `< MS` 为假 → else 分支：AV=0.0, SS=True。"""
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        out = b.step(500, I1=10.0)
        self.assertEqual(out["AV"], 0.0)
        self.assertIs(out["SS"], True)

    def test_above_ms_subtract_once(self):
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        out = b.step(500, I1=12.0)
        self.assertEqual(out["AV"], 2.0)
        self.assertIs(out["SS"], True)

    def test_single_tick_crossing_multiple_ms_subtracts_only_once(self):
        """单拍跨多个 MS 也只减一次：I1=25 → AV=15（仍 >= MS），不取模/不循环。"""
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        out = b.step(500, I1=25.0)
        self.assertEqual(out["AV"], 15.0)
        self.assertIs(out["SS"], True)

    def test_residual_over_ms_reset_to_iv_next_tick(self):
        """上一拍留下 AV>=MS，下一拍开头才先置 IV，再本拍积算。"""
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        b.step(500, I1=25.0)
        self.assertEqual(b.AV, 15.0)
        out = b.step(500, I1=0.0)
        self.assertEqual(out["AV"], 0.0)
        self.assertIs(out["SS"], False)


class TestNegativeAndIVRecovery(unittest.TestCase):
    """D. 负值与 IV 恢复。"""

    def test_negative_input_makes_av_negative_no_correction_this_tick(self):
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        out = b.step(500, I1=-5.0)
        self.assertEqual(out["AV"], -5.0)
        self.assertIs(out["SS"], False)

    def test_next_tick_resets_negative_av_to_iv_then_accumulates(self):
        b = APCHSACCUM(IV=0.0, MS=10.0, MC=1.0)
        b.step(500, I1=-5.0)
        self.assertEqual(b.AV, -5.0)
        out = b.step(500, I1=2.0)
        self.assertEqual(out["AV"], 2.0)

    def test_recovery_uses_custom_nonzero_iv(self):
        """非零 IV：AV<0 时下一拍恢复到 IV，再叠加本拍 MC*I1。"""
        b = APCHSACCUM(IV=3.0, MS=100.0, MC=1.0)
        b.step(500, I1=-5.0)
        self.assertEqual(b.AV, -5.0)
        out = b.step(500, I1=2.0)
        self.assertEqual(out["AV"], 5.0)  # IV(3) + 2


class TestRSRisingEdgeReset(unittest.TestCase):
    """E. RS 上升沿复位：必须锁死执行顺序。IV=3.0, MS=100.0, MC=1.0。"""

    def test_rs_false_normal_accumulate(self):
        b = APCHSACCUM(IV=3.0, MS=100.0, MC=1.0)
        out = b.step(500, I1=10.0, RS=False)
        self.assertEqual(out["AV"], 10.0)
        self.assertIs(b.preRS, False)

    def test_first_rs_true_accumulates_then_resets(self):
        b = APCHSACCUM(IV=3.0, MS=100.0, MC=1.0)
        b.step(500, I1=10.0, RS=False)  # AV=10
        out = b.step(500, I1=10.0, RS=True)
        self.assertEqual(b.LR, 20.0)        # 积算后 AV（10+10）
        self.assertEqual(out["AV"], 3.0)    # 复位到 IV
        self.assertIs(out["SS"], False)
        self.assertIs(b.preRS, True)

    def test_rs_held_second_tick_does_not_reset_again(self):
        b = APCHSACCUM(IV=3.0, MS=100.0, MC=1.0)
        b.step(500, I1=10.0, RS=False)      # AV=10
        b.step(500, I1=10.0, RS=True)       # reset → AV=3
        out = b.step(500, I1=10.0, RS=True)  # held high, no reset
        self.assertEqual(out["AV"], 13.0)   # 3 + 10 正常积算
        self.assertIs(out["SS"], False)

    def test_rs_falling_then_rising_triggers_again(self):
        b = APCHSACCUM(IV=3.0, MS=100.0, MC=1.0)
        b.step(500, I1=10.0, RS=False)      # AV=10
        b.step(500, I1=10.0, RS=True)       # reset → AV=3
        b.step(500, I1=10.0, RS=True)       # held → AV=13
        b.step(500, I1=10.0, RS=False)      # preRS=False, AV=23
        self.assertEqual(b.AV, 23.0)
        out = b.step(500, I1=10.0, RS=True)  # rising again → reset
        self.assertEqual(b.LR, 33.0)        # 23 + 10
        self.assertEqual(out["AV"], 3.0)

    def test_lr_saves_wrapped_av_when_wraparound_on_reset_tick(self):
        """复位拍前发生 MS 回绕，LR 必须保存回绕后的 AV（非回绕前 12）。"""
        b = APCHSACCUM(IV=3.0, MS=10.0, MC=1.0)
        out = b.step(500, I1=12.0, RS=True)
        self.assertEqual(b.LR, 2.0)         # 回绕后 12-10=2，不是 12
        self.assertEqual(out["AV"], 3.0)    # 复位到 IV


class TestUnusedVarAndLiteralLock(unittest.TestCase):
    """F. 未使用变量与源字面量锁定。"""

    def test_bpositive_accum_does_not_block_negative_input(self):
        """bPositiveAccum=True 后负输入仍按原 ST 参与积算（属性保留无语义）。"""
        b = APCHSACCUM(IV=0.0, MS=100.0, MC=1.0)
        b.bPositiveAccum = True
        out = b.step(500, I1=-5.0)
        self.assertEqual(out["AV"], -5.0)

    def test_default_ms_literal_locked(self):
        b = APCHSACCUM()
        self.assertEqual(b.MS, 1.797693134862e38)

    def test_default_ms_is_not_e308(self):
        b = APCHSACCUM()
        self.assertNotEqual(b.MS, 1.79769313486232e308)


if __name__ == "__main__":
    unittest.main()
