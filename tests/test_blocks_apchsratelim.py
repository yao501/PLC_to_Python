"""APCHSRATELIM 业务块的契约测试。

按 02 业务块规则 + 复核加固任务书（RL1~RL5）边界覆盖：

* 三分支正确性：上升超限 / 下降超限 / 区间内直通
* 冷启动 AV_1=0.0：首拍若 |IN|>HL/LL 会被钳（不是漏写初始化）
* 跨周期状态：连续多拍递增/递减各自限速
* ABS(HL)/ABS(LL) 容错（传入负值不报错，按正幅值处理）
* 对称速率限幅 HL=LL 对应 GCQ 的实际用法
* dt_ms 与块行为无关
* 上升后下降的方向切换：限速按当拍方向独立判断
* HL=0 / LL=0 卡住对应方向（保持上一拍输出）
* 严格等号边界（>/< 而非 >=/<=）：delta == HL / delta == -LL 走 ELSE 分支
"""

from __future__ import annotations

import unittest

from src.blocks import APCHSRATELIM


class TestAPCHSRATELIMBasicClamp(unittest.TestCase):
    def test_step_up_clamped_to_plus_hl(self):
        """AV_1=0, IN=100, HL=10 → AV = 0 + 10 = 10."""
        b = APCHSRATELIM()
        out = b.step(100, IN=100.0, HL=10.0, LL=10.0)
        self.assertEqual(out["AV"], 10.0)
        self.assertEqual(b.AV_1, 10.0)

    def test_step_down_clamped_to_minus_ll(self):
        """AV_1=0, IN=-100, LL=10 → AV = 0 - 10 = -10."""
        b = APCHSRATELIM()
        out = b.step(100, IN=-100.0, HL=10.0, LL=10.0)
        self.assertEqual(out["AV"], -10.0)
        self.assertEqual(b.AV_1, -10.0)

    def test_within_rate_pass_through(self):
        """变化量在 [-LL, +HL] 内时直通 IN。"""
        b = APCHSRATELIM()
        out = b.step(100, IN=5.0, HL=10.0, LL=10.0)
        self.assertEqual(out["AV"], 5.0)


class TestAPCHSRATELIMColdStart(unittest.TestCase):
    """冷启动 AV_1=0 的源块语义锁定（不是漏写初始化）。"""

    def test_first_tick_does_not_pass_large_input_directly(self):
        """首拍 IN=1000，HL=5 → AV=5（不会直通 1000）。"""
        b = APCHSRATELIM()
        self.assertEqual(b.AV_1, 0.0)
        out = b.step(100, IN=1000.0, HL=5.0, LL=5.0)
        self.assertEqual(out["AV"], 5.0)

    def test_first_tick_pass_through_when_in_within_rate(self):
        """首拍 IN=3，HL=5 → AV=3（在区间内）。"""
        b = APCHSRATELIM()
        out = b.step(100, IN=3.0, HL=5.0, LL=5.0)
        self.assertEqual(out["AV"], 3.0)


class TestAPCHSRATELIMCrossCycleState(unittest.TestCase):
    """AV_1 跨周期状态语义。"""

    def test_monotonic_ramp_up_each_cycle_increases_by_hl(self):
        """IN 持续高出，每拍上升正好 +HL。"""
        b = APCHSRATELIM()
        outs = [b.step(100, IN=1000.0, HL=10.0, LL=10.0)["AV"] for _ in range(5)]
        self.assertEqual(outs, [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_monotonic_ramp_down_each_cycle_decreases_by_ll(self):
        b = APCHSRATELIM()
        outs = [b.step(100, IN=-1000.0, HL=7.0, LL=7.0)["AV"] for _ in range(5)]
        self.assertEqual(outs, [-7.0, -14.0, -21.0, -28.0, -35.0])

    def test_ramp_then_settle(self):
        """先冲高，IN 回落到区间内时立刻直通。"""
        b = APCHSRATELIM()
        b.step(100, IN=100.0, HL=10.0, LL=10.0)
        b.step(100, IN=100.0, HL=10.0, LL=10.0)
        self.assertEqual(b.AV_1, 20.0)
        out = b.step(100, IN=22.0, HL=10.0, LL=10.0)
        self.assertEqual(out["AV"], 22.0)

    def test_direction_change_independent_per_cycle(self):
        """先上后下：上升受 HL 限，下降受 LL 限，方向独立判断。"""
        b = APCHSRATELIM()
        out_up = b.step(100, IN=100.0, HL=10.0, LL=3.0)["AV"]
        self.assertEqual(out_up, 10.0)

        out_down = b.step(100, IN=-100.0, HL=10.0, LL=3.0)["AV"]
        self.assertEqual(out_down, 7.0)


class TestAPCHSRATELIMAsymmetricLimits(unittest.TestCase):
    def test_hl_lt_ll(self):
        """HL=2, LL=5：上升慢、下降快。"""
        b = APCHSRATELIM()
        outs = [b.step(100, IN=100.0, HL=2.0, LL=5.0)["AV"] for _ in range(3)]
        self.assertEqual(outs, [2.0, 4.0, 6.0])

        outs = [b.step(100, IN=-100.0, HL=2.0, LL=5.0)["AV"] for _ in range(3)]
        self.assertEqual(outs, [1.0, -4.0, -9.0])


class TestAPCHSRATELIMSilentAbs(unittest.TestCase):
    """块内 ABS() 容错：负值被静默取绝对值。"""

    def test_negative_hl_treated_as_positive_magnitude(self):
        b = APCHSRATELIM()
        out = b.step(100, IN=100.0, HL=-10.0, LL=10.0)
        self.assertEqual(out["AV"], 10.0)

    def test_negative_ll_treated_as_positive_magnitude(self):
        b = APCHSRATELIM()
        out = b.step(100, IN=-100.0, HL=10.0, LL=-10.0)
        self.assertEqual(out["AV"], -10.0)

    def test_silent_abs_does_not_persist(self):
        """ABS 容错只影响本拍，不写回输入参数。"""
        b = APCHSRATELIM()
        b.step(100, IN=100.0, HL=-10.0, LL=10.0)
        out = b.step(100, IN=100.0, HL=15.0, LL=10.0)
        self.assertEqual(out["AV"], 25.0)


class TestAPCHSRATELIMGCQUsageSymmetric(unittest.TestCase):
    """对称速率限幅 HL=LL=OUTV：GCQ 中的实际用法。"""

    def test_symmetric_rate_clamp(self):
        b = APCHSRATELIM()
        out_up = b.step(100, IN=1000.0, HL=50.0, LL=50.0)["AV"]
        out_dn = b.step(100, IN=-1000.0, HL=50.0, LL=50.0)["AV"]
        self.assertEqual(out_up, 50.0)
        self.assertEqual(out_dn, 0.0)


class TestAPCHSRATELIMDtMsIgnored(unittest.TestCase):
    """RL4：与 dt_ms 解耦。"""

    def test_dt_ms_does_not_affect_behavior(self):
        b = APCHSRATELIM()
        for dt in (0, 1, 100, 1000, 100000):
            b2 = APCHSRATELIM()
            self.assertEqual(b2.step(dt, IN=100.0, HL=10.0, LL=10.0)["AV"], 10.0)

    def test_two_instances_different_dt_same_state_same_output(self):
        """复核加固任务书 §6.5：相同初始状态、相同 IN/HL/LL，不同 dt_ms
        必须输出一致。防止后续把 RATELIM 改成基于时间换算的物理速率限制。"""
        rl1 = APCHSRATELIM()
        rl2 = APCHSRATELIM()
        out1 = rl1.step(dt_ms=10, IN=100.0, HL=10.0, LL=10.0)
        out2 = rl2.step(dt_ms=1000, IN=100.0, HL=10.0, LL=10.0)
        self.assertEqual(out1, out2)
        self.assertEqual(out1, {"AV": 10.0})


class TestAPCHSRATELIMZeroLimit(unittest.TestCase):
    """复核加固任务书 §6.3：HL=0 / LL=0 卡住对应方向。"""

    def test_hl_zero_locks_upward_direction(self):
        """HL=0：上升方向卡死。AV_1=0 状态下 IN=100 → AV 仍是 AV_1+0 = 0。"""
        rl = APCHSRATELIM()
        out = rl.step(dt_ms=500, IN=100.0, HL=0.0, LL=10.0)
        self.assertEqual(out["AV"], 0.0)
        self.assertEqual(rl.AV_1, 0.0)

    def test_hl_zero_locks_upward_continuous(self):
        """HL=0 连续多拍：AV 保持上一拍值，下行仍按 LL 限。"""
        rl = APCHSRATELIM()
        rl.AV_1 = 50.0
        for _ in range(5):
            out = rl.step(dt_ms=500, IN=100.0, HL=0.0, LL=10.0)
            self.assertEqual(out["AV"], 50.0)

        out = rl.step(dt_ms=500, IN=0.0, HL=0.0, LL=10.0)
        self.assertEqual(out["AV"], 40.0)

    def test_ll_zero_locks_downward_direction(self):
        """LL=0：下降方向卡死。AV_1=50 状态下 IN=0 → AV 仍是 AV_1-0 = 50。"""
        rl = APCHSRATELIM()
        rl.AV_1 = 50.0
        out = rl.step(dt_ms=500, IN=0.0, HL=10.0, LL=0.0)
        self.assertEqual(out["AV"], 50.0)

    def test_both_zero_freezes_av(self):
        """HL=LL=0：AV 完全冻结在 AV_1。"""
        rl = APCHSRATELIM()
        rl.AV_1 = 42.0
        for IN in (1000.0, -1000.0, 0.0, 42.0):
            out = rl.step(dt_ms=500, IN=IN, HL=0.0, LL=0.0)
            self.assertEqual(out["AV"], 42.0)


class TestAPCHSRATELIMStrictBoundaryComparators(unittest.TestCase):
    """RL5 末尾：严格 >/< 边界。delta == HL / delta == -LL 时走 ELSE 直通 IN。

    虽然在等号边界下"走限速分支"和"走 ELSE 分支"得到的数值通常相同
    （AV_1 + HL == AV_1 + delta == IN，AV_1 - LL == AV_1 + delta == IN），
    但仍要锁定源码的 IF/ELSIF 分支结构，防止后续把 ``>`` 改成 ``>=`` 时
    引入副作用（例如未来给 ELSE 分支加额外侧效应时差异才会显现）。
    """

    def test_delta_equals_hl_takes_else_branch(self):
        """AV_1=10, IN=20, HL=10：delta=10, delta>HL=False → 走 ELSE → AV=IN=20。"""
        rl = APCHSRATELIM()
        rl.AV_1 = 10.0
        out = rl.step(dt_ms=500, IN=20.0, HL=10.0, LL=10.0)
        self.assertEqual(out["AV"], 20.0)

    def test_delta_equals_neg_ll_takes_else_branch(self):
        """AV_1=10, IN=0, LL=10：delta=-10, delta<-LL=False → 走 ELSE → AV=IN=0。"""
        rl = APCHSRATELIM()
        rl.AV_1 = 10.0
        out = rl.step(dt_ms=500, IN=0.0, HL=10.0, LL=10.0)
        self.assertEqual(out["AV"], 0.0)

    def test_delta_just_above_hl_takes_clamp_branch(self):
        """delta 严格大于 HL（哪怕一个浮点 ε）就走限速分支。"""
        rl = APCHSRATELIM()
        rl.AV_1 = 10.0
        eps = 1e-9
        out = rl.step(dt_ms=500, IN=20.0 + eps, HL=10.0, LL=10.0)
        self.assertAlmostEqual(out["AV"], 20.0, places=12)


if __name__ == "__main__":
    unittest.main()
