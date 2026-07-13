"""双前端合流验收：ST 与 CFC lower 出同一指令列表并跑通 N 拍（IR_SPEC §10、ENGINE_SCAN_SPEC §8/§9）。"""
import unittest

from prototype_05 import ir, programs
from prototype_05.engine import Engine
from prototype_05.numeric import NumericMode


class TestDualLowering(unittest.TestCase):
    def test_st_and_cfc_produce_identical_instruction_list(self):
        st = programs.motor_st_instrs()
        cfc = programs.motor_cfc_instrs()
        self.assertEqual(st, cfc)   # 结构相等：同一种可执行指令列表

    def test_instruction_list_matches_spec_example(self):
        """与 ENGINE_SCAN_SPEC §8 给出的指令序逐条一致。"""
        expected = [
            ir.LOAD_VAR("Start", "BOOL"), ir.STORE_VAR("TON1.IN", "BOOL"),
            ir.LOAD_CONST(5000, "TIME"), ir.STORE_VAR("TON1.PT", "TIME"),
            ir.CALL_FB("TON1"),
            ir.LOAD_VAR("TON1.Q", "BOOL"), ir.LOAD_VAR("Stop", "BOOL"),
            ir.UNOP("NOT", "BOOL"), ir.BINOP("AND", "BOOL"),
            ir.STORE_VAR("Motor", "BOOL"),
        ]
        self.assertEqual(programs.motor_st_instrs(), expected)

    def test_run_n_scans_results_identical_and_ton_timing_correct(self):
        """两条路径各建引擎跑 24 拍，逐拍逐值一致；TON 5s 延时与 Stop 语义正确。"""
        eng_st = Engine(programs.motor_task(programs.motor_st_instrs()))
        eng_cfc = Engine(programs.motor_task(programs.motor_cfc_instrs()))
        traces = {id(eng_st): [], id(eng_cfc): []}
        for scan in range(24):
            inputs = {"Start": True, "Stop": scan >= 20}
            for eng in (eng_st, eng_cfc):
                pending = eng.run_scan(dict(inputs))
                traces[id(eng)].append(
                    (pending["DO_Motor"], eng.store["TON1.ET"], eng.store["TON1.Q"]))
        self.assertEqual(traces[id(eng_st)], traces[id(eng_cfc)])   # 逐拍逐值一致

        motor = [t[0] for t in traces[id(eng_st)]]
        # 500ms/拍 × 10 拍 = 5000ms：第 10 拍（下标 9）Q 置位
        self.assertEqual(motor[:9], [False] * 9)
        self.assertTrue(all(motor[9:20]))
        self.assertEqual(motor[20:], [False] * 4)                   # Stop 后 Motor 复位
        self.assertEqual(eng_st.store["TON1.ET"], 5000)             # ET 饱和到 PT

    def test_engineering_and_f1_both_execute_same_ir(self):
        """同一份 IR 在 E 与 F1 模式都能执行（IR_SPEC §8）；本程序无 REAL，结果一致。"""
        for mode in (NumericMode(), NumericMode(mode="fidelity_f1")):
            eng = Engine(programs.motor_task(programs.motor_st_instrs()), mode=mode)
            for _ in range(12):
                eng.run_scan({"Start": True, "Stop": False})
            self.assertTrue(eng.store["Motor"])


if __name__ == "__main__":
    unittest.main()
