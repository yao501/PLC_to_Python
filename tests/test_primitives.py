"""基础原语功能块测试。

所有测试按 PLC 扫描周期模型运行：固定 ``dt_ms``，按周期调用 ``step``，
验证每周期输出是否符合 IEC 61131-3 / CODESYS 文档中的时序图，
并覆盖 ``00a-runtime-contract`` 规则中明确要求的测试点：

* R3 边沿检测冷启动首拍（``CLK=False`` / ``CLK=True``）
* R4 SR/RS 完整真值表 ``(S,R) ∈ {(0,0),(1,0),(0,1),(1,1)}``
* R5 长周期累积（1000、10000 周期）与阈值边界无漂移
"""

from __future__ import annotations

import unittest

from src.primitives import F_TRIG, RS, SR, TOF, TON, TP, R_TRIG


DT_MS = 100


class TestTON(unittest.TestCase):
    def test_reaches_pt_then_q_true(self):
        ton = TON()
        pt = 500
        qs = [ton.step(DT_MS, IN=True, PT_ms=pt)[0] for _ in range(10)]
        self.assertFalse(qs[0])
        self.assertFalse(qs[3])
        self.assertTrue(qs[4])
        self.assertTrue(qs[9])

    def test_in_drop_resets_immediately(self):
        ton = TON()
        for _ in range(6):
            ton.step(DT_MS, IN=True, PT_ms=500)
        self.assertTrue(ton.Q)

        q, et = ton.step(DT_MS, IN=False, PT_ms=500)
        self.assertFalse(q)
        self.assertEqual(et, 0)

    def test_et_clamped_at_pt(self):
        ton = TON()
        for _ in range(50):
            q, et = ton.step(DT_MS, IN=True, PT_ms=500)
        self.assertTrue(q)
        self.assertEqual(et, 500)
        self.assertIsInstance(et, int)

    def test_partial_pulse_does_not_set_q(self):
        ton = TON()
        pt = 1000
        for _ in range(5):
            ton.step(DT_MS, IN=True, PT_ms=pt)
        self.assertFalse(ton.Q)
        q, et = ton.step(DT_MS, IN=False, PT_ms=pt)
        self.assertFalse(q)
        self.assertEqual(et, 0)

    def test_long_cycle_no_drift(self):
        """R5：10000 周期累积无漂移，ET_ms 严格等于整数计算结果。"""
        ton = TON()
        pt = 10_000_000
        for _ in range(10_000):
            q, et = ton.step(DT_MS, IN=True, PT_ms=pt)
        self.assertEqual(et, 10_000 * DT_MS)
        self.assertIsInstance(et, int)
        self.assertFalse(q)

    def test_threshold_boundary_exact(self):
        """R5：阈值前后边界周期无漂移。"""
        ton = TON()
        pt = 1000
        for _ in range(9):
            q, et = ton.step(DT_MS, IN=True, PT_ms=pt)
        self.assertEqual(et, 900)
        self.assertFalse(q)
        q, et = ton.step(DT_MS, IN=True, PT_ms=pt)
        self.assertEqual(et, 1000)
        self.assertTrue(q)


class TestTOF(unittest.TestCase):
    def test_in_true_q_true_et_zero(self):
        tof = TOF()
        q, et = tof.step(DT_MS, IN=True, PT_ms=500)
        self.assertTrue(q)
        self.assertEqual(et, 0)

    def test_falling_edge_then_delay(self):
        tof = TOF()
        for _ in range(3):
            tof.step(DT_MS, IN=True, PT_ms=500)
        self.assertTrue(tof.Q)

        qs = [tof.step(DT_MS, IN=False, PT_ms=500)[0] for _ in range(10)]
        self.assertTrue(qs[0])
        self.assertTrue(qs[3])
        self.assertFalse(qs[4])
        self.assertFalse(qs[9])

    def test_in_rises_during_delay_resets(self):
        tof = TOF()
        tof.step(DT_MS, IN=True, PT_ms=1000)
        for _ in range(3):
            tof.step(DT_MS, IN=False, PT_ms=1000)
        self.assertTrue(tof.Q)
        self.assertGreater(tof.ET_ms, 0)

        q, et = tof.step(DT_MS, IN=True, PT_ms=1000)
        self.assertTrue(q)
        self.assertEqual(et, 0)

    def test_long_cycle_no_drift(self):
        tof = TOF()
        pt = 2_000_000
        tof.step(DT_MS, IN=True, PT_ms=pt)
        for _ in range(1000):
            q, et = tof.step(DT_MS, IN=False, PT_ms=pt)
        self.assertEqual(et, 1000 * DT_MS)
        self.assertIsInstance(et, int)
        self.assertTrue(q)


class TestTP(unittest.TestCase):
    def test_rising_edge_starts_pulse_of_width_pt(self):
        tp = TP()
        pt = 350
        qs = [tp.step(DT_MS, IN=True, PT_ms=pt)[0] for _ in range(6)]
        self.assertEqual(qs[:3], [True, True, True])
        self.assertFalse(qs[3])
        self.assertFalse(qs[5])

    def test_non_retriggerable_during_pulse(self):
        tp = TP()
        pt = 500
        tp.step(DT_MS, IN=True, PT_ms=pt)
        tp.step(DT_MS, IN=False, PT_ms=pt)
        self.assertTrue(tp.Q)
        tp.step(DT_MS, IN=True, PT_ms=pt)
        self.assertTrue(tp.Q)
        self.assertLess(tp.ET_ms, pt)

    def test_pulse_completes_even_if_in_drops(self):
        tp = TP()
        pt = 500
        tp.step(DT_MS, IN=True, PT_ms=pt)
        for _ in range(10):
            q, et = tp.step(DT_MS, IN=False, PT_ms=pt)
        self.assertFalse(q)
        self.assertEqual(et, 0)

    def test_retrigger_after_rearm(self):
        tp = TP()
        pt = 200
        for _ in range(5):
            tp.step(DT_MS, IN=True, PT_ms=pt)
        tp.step(DT_MS, IN=False, PT_ms=pt)
        self.assertFalse(tp.Q)
        self.assertEqual(tp.ET_ms, 0)

        q, _ = tp.step(DT_MS, IN=True, PT_ms=pt)
        self.assertTrue(q)


class TestRTrig(unittest.TestCase):
    """R3：必须覆盖冷启动首拍 CLK=False / CLK=True。"""

    def test_first_tick_clk_false_no_q(self):
        """冷启动首拍 CLK=False → Q 必须为 False。"""
        rt = R_TRIG()
        self.assertFalse(rt.step(CLK=False))

    def test_first_tick_clk_true_fires_per_iec(self):
        """冷启动首拍 CLK=True，按 IEC 标准会产生一次 Q=True，
        这种"上电边沿"必须由主程序的 system_ready 门控屏蔽。"""
        rt = R_TRIG()
        self.assertTrue(rt.step(CLK=True))

    def test_detects_single_rising_edge(self):
        rt = R_TRIG()
        qs = [rt.step(CLK) for CLK in [False, True, True, True, False, True]]
        self.assertEqual(qs, [False, True, False, False, False, True])

    def test_no_q_on_steady_high(self):
        rt = R_TRIG()
        rt.step(True)
        for _ in range(5):
            self.assertFalse(rt.step(True))


class TestFTrig(unittest.TestCase):
    """R3：必须覆盖冷启动首拍 CLK=False / CLK=True。"""

    def test_first_tick_clk_true_no_q(self):
        """冷启动首拍 CLK=True → Q 必须为 False。"""
        ft = F_TRIG()
        self.assertFalse(ft.step(CLK=True))

    def test_first_tick_clk_false_fires_per_iec(self):
        """冷启动首拍 CLK=False，按 IEC 标准会产生一次 Q=True，
        这种"上电边沿"必须由主程序的 system_ready 门控屏蔽。"""
        ft = F_TRIG()
        self.assertTrue(ft.step(CLK=False))

    def test_detects_single_falling_edge(self):
        ft = F_TRIG()
        ft.step(CLK=True)
        qs = [ft.step(CLK) for CLK in [True, False, False, True, False]]
        self.assertEqual(qs, [False, True, False, False, True])

    def test_no_q_on_steady_low(self):
        ft = F_TRIG()
        ft.step(False)
        for _ in range(5):
            self.assertFalse(ft.step(False))


class TestSR(unittest.TestCase):
    """R4：完整真值表 + 置位优先。"""

    def test_truth_table_00_hold(self):
        sr = SR()
        sr.step(SET1=True, RESET=False)
        q = sr.step(SET1=False, RESET=False)
        self.assertTrue(q)

    def test_truth_table_10_set(self):
        sr = SR()
        self.assertTrue(sr.step(SET1=True, RESET=False))

    def test_truth_table_01_reset(self):
        sr = SR()
        sr.step(SET1=True, RESET=False)
        self.assertFalse(sr.step(SET1=False, RESET=True))

    def test_truth_table_11_set_dominant(self):
        sr = SR()
        self.assertTrue(sr.step(SET1=True, RESET=True))
        self.assertTrue(sr.step(SET1=True, RESET=True))

    def test_reset_then_hold(self):
        sr = SR()
        sr.step(SET1=True, RESET=False)
        sr.step(SET1=False, RESET=True)
        q = sr.step(SET1=False, RESET=False)
        self.assertFalse(q)


class TestRS(unittest.TestCase):
    """R4：完整真值表 + 复位优先。"""

    def test_truth_table_00_hold(self):
        rs = RS()
        rs.step(SET=True, RESET1=False)
        q = rs.step(SET=False, RESET1=False)
        self.assertTrue(q)

    def test_truth_table_10_set(self):
        rs = RS()
        self.assertTrue(rs.step(SET=True, RESET1=False))

    def test_truth_table_01_reset(self):
        rs = RS()
        rs.step(SET=True, RESET1=False)
        self.assertFalse(rs.step(SET=False, RESET1=True))

    def test_truth_table_11_reset_dominant(self):
        rs = RS()
        self.assertFalse(rs.step(SET=True, RESET1=True))
        self.assertFalse(rs.step(SET=True, RESET1=True))

    def test_reset_clears_then_hold(self):
        rs = RS()
        rs.step(SET=True, RESET1=False)
        rs.step(SET=False, RESET1=True)
        self.assertFalse(rs.step(SET=False, RESET1=False))


class TestRTrigPlusSRColdStartPattern(unittest.TestCase):
    """演示 R_TRIG + SR 组合在冷启动 + system_ready 门控下的正确模式。

    关键点：**有效边沿在进入 SR 之前就必须被 system_ready 门控**，
    否则冷启动的上电边沿会把 SR 锁住，等 system_ready 放行时就会产生
    一次"被延迟的幽灵动作"。

    正确模式::

        raw_edge       = R_TRIG(CLK)
        valid_request  = raw_edge AND system_ready     # 门控在 SR 之前
        sr.step(SET1=valid_request, RESET=...)
        physical_out   = sr.Q1 AND system_ready AND safety_ok AND ...
    """

    @staticmethod
    def _scan(rt: R_TRIG, sr: SR, CLK: bool, system_ready: bool) -> bool:
        raw_edge = rt.step(CLK=CLK)
        valid_request = raw_edge and system_ready
        sr.step(SET1=valid_request, RESET=False)
        return sr.Q1 and system_ready

    def test_pre_ready_edge_does_not_produce_physical_output(self):
        rt = R_TRIG()
        sr = SR()

        outs = []
        for _ in range(3):
            outs.append(self._scan(rt, sr, CLK=True, system_ready=False))

        for _ in range(3):
            outs.append(self._scan(rt, sr, CLK=True, system_ready=True))

        self.assertTrue(all(o is False for o in outs))

    def test_new_edge_after_ready_produces_output(self):
        rt = R_TRIG()
        sr = SR()

        for _ in range(3):
            self.assertFalse(self._scan(rt, sr, CLK=True, system_ready=False))
        for _ in range(3):
            self.assertFalse(self._scan(rt, sr, CLK=True, system_ready=True))

        self.assertFalse(self._scan(rt, sr, CLK=False, system_ready=True))
        final = self._scan(rt, sr, CLK=True, system_ready=True)
        self.assertTrue(final)


if __name__ == "__main__":
    unittest.main()
