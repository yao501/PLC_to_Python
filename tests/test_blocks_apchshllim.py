"""APCHSHLLIM 业务块的契约测试。

按 02 业务块规则 + 复核加固任务书（HL1/HL2/HL3）边界覆盖：

* 三分支正确性：IN > HL / IN < LL / 区间内
* 边界等号：IN == HL / IN == LL 不被截
* LL > HL 静默修正语义（HL1：不交换、不报错，LL := HL；含 IN 高/低/等于 HL 三种）
* 静默修正不写回参数（下一拍参数值不变）
* HL == LL 单点限幅
* 合法负区间 HL=-10, LL=-20 不做 ABS（HL3）
* dt_ms 与块行为无关
* 无跨周期状态（HL2）：连续两次相同输入相同输出；self.AV 不参与下一拍判定
"""

from __future__ import annotations

import unittest

from src.blocks import APCHSHLLIM


class TestAPCHSHLLIMBasicClamp(unittest.TestCase):
    def test_in_above_hl_clamped_to_hl(self):
        b = APCHSHLLIM()
        out = b.step(100, IN=50.0, HL=10.0, LL=-10.0)
        self.assertEqual(out["AV"], 10.0)

    def test_in_below_ll_clamped_to_ll(self):
        b = APCHSHLLIM()
        out = b.step(100, IN=-50.0, HL=10.0, LL=-10.0)
        self.assertEqual(out["AV"], -10.0)

    def test_in_within_range_pass_through(self):
        b = APCHSHLLIM()
        out = b.step(100, IN=3.5, HL=10.0, LL=-10.0)
        self.assertEqual(out["AV"], 3.5)


class TestAPCHSHLLIMBoundaryEqualities(unittest.TestCase):
    def test_in_equals_hl_not_clamped(self):
        """IN == HL 走 ELSE 分支（IN > HL 为假），输出原值。"""
        b = APCHSHLLIM()
        out = b.step(100, IN=10.0, HL=10.0, LL=-10.0)
        self.assertEqual(out["AV"], 10.0)

    def test_in_equals_ll_not_clamped(self):
        """IN == LL 走 ELSE 分支（IN < LL 为假），输出原值。"""
        b = APCHSHLLIM()
        out = b.step(100, IN=-10.0, HL=10.0, LL=-10.0)
        self.assertEqual(out["AV"], -10.0)


class TestAPCHSHLLIMSilentLLFix(unittest.TestCase):
    """HL1：LL > HL 时块内静默修正 LL := HL（不交换，不报错）。"""

    def test_ll_above_hl_collapses_to_hl(self):
        """HL=5, LL=10 → 修正后 LL'=5；IN 在 [5,5] 之外都被钳到 5。"""
        b = APCHSHLLIM()
        self.assertEqual(b.step(100, IN=8.0, HL=5.0, LL=10.0)["AV"], 5.0)
        self.assertEqual(b.step(100, IN=2.0, HL=5.0, LL=10.0)["AV"], 5.0)
        self.assertEqual(b.step(100, IN=5.0, HL=5.0, LL=10.0)["AV"], 5.0)

    def test_collapsed_to_single_point_all_three_in_branches(self):
        """复核加固任务书 §5.1：LL>HL 时，IN 高于 HL / 低于 HL / 等于 HL 三种
        情况均输出 HL（区间退化为单点）。锁死"不可改成交换"或"不可改成
        报错"两种常见误改。"""
        b = APCHSHLLIM()
        HL, LL = 5.0, 10.0
        self.assertEqual(b.step(100, IN=100.0, HL=HL, LL=LL)["AV"], 5.0)
        self.assertEqual(b.step(100, IN=-100.0, HL=HL, LL=LL)["AV"], 5.0)
        self.assertEqual(b.step(100, IN=5.0, HL=HL, LL=LL)["AV"], 5.0)

    def test_silent_fix_does_not_persist(self):
        """静默修正不写回任何持久状态：下一拍传入合理参数应正常工作。"""
        b = APCHSHLLIM()
        out1 = b.step(100, IN=8.0, HL=5.0, LL=10.0)
        self.assertEqual(out1["AV"], 5.0)

        out2 = b.step(100, IN=8.0, HL=20.0, LL=-20.0)
        self.assertEqual(out2["AV"], 8.0)

    def test_negative_range_with_inverted_limits(self):
        """HL=-5, LL=-1 → LL>HL，触发 HL1 静默修正：LL' = HL = -5；
        区间退化为单点 -5。注意这里区间是 [-5, -5]，不是 [-5, -1]。"""
        b = APCHSHLLIM()
        self.assertEqual(b.step(100, IN=0.0, HL=-5.0, LL=-1.0)["AV"], -5.0)
        self.assertEqual(b.step(100, IN=-100.0, HL=-5.0, LL=-1.0)["AV"], -5.0)


class TestAPCHSHLLIMNegativeRangeIsLegal(unittest.TestCase):
    """HL3：HL/LL 不做 ABS。合法负区间（LL < HL，两者都为负）按源码三分支执行。"""

    def test_pure_negative_range_pass_through(self):
        """HL=-10, LL=-20 是合法区间 [-20, -10]。
        IN=-15 在区间内 → 直通；不能因为 LL/HL 是负数就 ABS()。"""
        b = APCHSHLLIM()
        out = b.step(100, IN=-15.0, HL=-10.0, LL=-20.0)
        self.assertEqual(out["AV"], -15.0)

    def test_pure_negative_range_clamp_high(self):
        """IN=-5 > HL=-10 → 钳到 HL=-10。"""
        b = APCHSHLLIM()
        out = b.step(100, IN=-5.0, HL=-10.0, LL=-20.0)
        self.assertEqual(out["AV"], -10.0)

    def test_pure_negative_range_clamp_low(self):
        """IN=-30 < LL=-20 → 钳到 LL=-20。"""
        b = APCHSHLLIM()
        out = b.step(100, IN=-30.0, HL=-10.0, LL=-20.0)
        self.assertEqual(out["AV"], -20.0)

    def test_negative_input_in_negative_range_does_not_get_absed(self):
        """防回归：确保实现里没有 abs(IN) / abs(HL) / abs(LL) 偷渡。
        如果偷加了 abs()，下面 IN=-15 会被解释成 +15，输出会偏离 -15。"""
        b = APCHSHLLIM()
        out = b.step(100, IN=-15.0, HL=-10.0, LL=-20.0)
        self.assertEqual(out["AV"], -15.0)
        self.assertNotEqual(out["AV"], 15.0)
        self.assertNotEqual(out["AV"], -10.0)


class TestAPCHSHLLIMSinglePointWhenHLEqualsLL(unittest.TestCase):
    """HL == LL 单点限幅锁定（复核加固任务书 §5.3）。"""

    def test_hl_equals_ll_collapses_to_single_point(self):
        b = APCHSHLLIM()
        HL = LL = 3.0
        self.assertEqual(b.step(100, IN=100.0, HL=HL, LL=LL)["AV"], 3.0)
        self.assertEqual(b.step(100, IN=-1.0, HL=HL, LL=LL)["AV"], 3.0)
        self.assertEqual(b.step(100, IN=3.0, HL=HL, LL=LL)["AV"], 3.0)

    def test_hl_equals_ll_negative_value(self):
        """HL == LL 为负数时仍是合法单点限幅。"""
        b = APCHSHLLIM()
        HL = LL = -7.5
        self.assertEqual(b.step(100, IN=0.0, HL=HL, LL=LL)["AV"], -7.5)
        self.assertEqual(b.step(100, IN=-100.0, HL=HL, LL=LL)["AV"], -7.5)


class TestAPCHSHLLIMDtMsIgnored(unittest.TestCase):
    def test_dt_ms_does_not_affect_behavior(self):
        b = APCHSHLLIM()
        outs = [
            b.step(dt, IN=42.0, HL=10.0, LL=-10.0)["AV"]
            for dt in (0, 1, 100, 1000, 100000)
        ]
        self.assertTrue(all(v == 10.0 for v in outs))


class TestAPCHSHLLIMStateless(unittest.TestCase):
    """HL2：无跨周期判定状态。self.AV 仅缓存最后一次输出，不影响下一拍。"""

    def test_repeated_same_input_repeated_same_output(self):
        b = APCHSHLLIM()
        for _ in range(50):
            self.assertEqual(b.step(100, IN=7.0, HL=10.0, LL=-10.0)["AV"], 7.0)

    def test_self_av_does_not_affect_next_tick(self):
        """先用一拍把 self.AV 钳到 HL，再传一个区间内的 IN，
        必须直通 IN，不能受上一拍的 self.AV 影响。"""
        b = APCHSHLLIM()
        out1 = b.step(100, IN=100.0, HL=10.0, LL=-10.0)
        self.assertEqual(out1["AV"], 10.0)
        self.assertEqual(b.AV, 10.0)

        out2 = b.step(100, IN=3.5, HL=10.0, LL=-10.0)
        self.assertEqual(out2["AV"], 3.5)


if __name__ == "__main__":
    unittest.main()
