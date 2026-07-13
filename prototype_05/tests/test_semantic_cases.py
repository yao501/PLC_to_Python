"""语义敏感案例 ①②③⑤（PLATFORM_ROADMAP 阶段 0.5）。

① 整数临时位宽/存储截断：int_intermediate_policy 两策略可切换且结果不同（IR_SPEC §5.4）。
② REAL/F1 量化：F1-expr 与 E 结果可区分（IR_SPEC §5.3、TARGET_PROFILE §4.2）。
③ VAR_IN_OUT：ValueRef 别名语义，写透到调用方（IR_SPEC §3/§5.2）。
⑤ 双 FB 实例隔离：定义/实例分离，状态互不串扰（IR_SPEC §3）。
"""
import unittest

from prototype_05 import programs
from prototype_05.engine import Engine
from prototype_05.numeric import NumericMode, is_binary32_encodable


class TestCase1IntTruncation(unittest.TestCase):
    """C := (A+B)/2，A=60000, B=30000（WORD）：中间和 90000 溢出 16 位。"""

    def _run(self, mode):
        eng = Engine(programs.int_trunc_task(), mode=mode)
        eng.run_scan()
        return eng

    def test_policies_switchable_and_results_differ(self):
        # native_width（默认假设）：中间 90000 不截断 → C = 45000
        native = self._run(NumericMode(mode="fidelity_f1",
                                       int_intermediate_policy="native_width"))
        # declared_width（逐步截断）：ADD 出口 90000 mod 2^16 = 24464 → C = 12232
        declared = self._run(NumericMode(mode="fidelity_f1",
                                         int_intermediate_policy="declared_width"))
        self.assertEqual(native.store["C"], 45000)
        self.assertEqual(declared.store["C"], 12232)
        self.assertNotEqual(native.store["C"], declared.store["C"])
        # 哪种匹配真机由黄金轨迹 #7 裁决（GOLDEN_TRACE_FORMAT §3）；此处只证明可切换且可区分。

    def test_engineering_mode_ints_do_not_wrap(self):
        eng = self._run(NumericMode())
        self.assertEqual(eng.store["C"], 45000)
        self.assertEqual(eng.store["D"], 70000)     # E：CONVERT/STORE 均不回绕（IR_SPEC §8）

    def test_store_convert_truncation_guaranteed_in_fidelity(self):
        """STORE_VAR/CONVERT 是保证截断点（所有 fidelity 模式，IR_SPEC §5.4）。"""
        for policy in ("native_width", "declared_width"):
            eng = self._run(NumericMode(mode="fidelity_f1",
                                        int_intermediate_policy=policy))
            self.assertEqual(eng.store["D"], 70000 % 65536)   # = 4464，与中间政策无关


class TestCase2RealF1Quantization(unittest.TestCase):
    """R := RB + RC，RB=2^24，RC=1.0：binary32 逐指令量化丢失 +1，float64 不丢。"""

    def test_f1_expr_and_e_results_distinguishable(self):
        e = Engine(programs.real_quant_task(), mode=NumericMode())
        f1 = Engine(programs.real_quant_task(), mode=NumericMode(mode="fidelity_f1"))
        e.run_scan()
        f1.run_scan()
        self.assertEqual(e.store["R"], 16777217.0)    # E：float64 保留 +1
        self.assertEqual(f1.store["R"], 16777216.0)   # F1-expr：BINOP 出口 binary32 量化
        self.assertNotEqual(e.store["R"], f1.store["R"])

    def test_f1_values_are_binary32_encodable(self):
        """F1 承诺的是表示层：值必为合法 binary32 可编码值（TARGET_PROFILE §4.1）。"""
        f1 = Engine(programs.real_quant_task(), mode=NumericMode(mode="fidelity_f1"))
        f1.run_scan()
        for key in ("RB", "RC", "R"):
            self.assertTrue(is_binary32_encodable(f1.store[key]), key)
        # E 结果 16777217.0 不可 binary32 编码——正是两模式可区分的原因
        self.assertFalse(is_binary32_encodable(16777217.0))


class TestCase3VarInOutWriteThrough(unittest.TestCase):
    """ACC 为 VAR_IN_OUT：FB 体内写 self.ACC 直接作用于调用方 GVL Total（别名，非值拷贝）。"""

    def test_writes_through_to_caller(self):
        eng = Engine(programs.var_in_out_task())
        for _ in range(3):
            eng.run_scan()
        # AccumFB 没有 OUT 拷回步骤，Total 的变化只能来自别名写透
        self.assertEqual(eng.store["Total"], 7.5)

    def test_no_shadow_copy_left_in_instance_memory(self):
        """别名语义：INOUT 形参不占实例内存拷贝（装载期初值不被调用刷新）。"""
        eng = Engine(programs.var_in_out_task())
        eng.run_scan()
        # 实例键 ACC1.ACC 仍是装载期初值（0.0），未被当作值拷贝通道使用
        self.assertEqual(eng.store["ACC1.ACC"], 0.0)
        self.assertEqual(eng.store["Total"], 2.5)


class TestCase5InstanceIsolation(unittest.TestCase):
    """同一 POUDefinition 两份实例 + 两份 TON 库实例：状态互不串扰（定义/实例分离）。"""

    def test_user_fb_instances_isolated(self):
        eng = Engine(programs.isolation_task())
        for scan in range(6):
            eng.run_scan({"EnA": True, "EnB": scan % 2 == 0})   # B 隔拍使能
        self.assertEqual(eng.store["CntA"], 6)
        self.assertEqual(eng.store["CntB"], 3)
        self.assertEqual(eng.store["CNT_A.N"], 6)               # 实例内存按路径独立
        self.assertEqual(eng.store["CNT_B.N"], 3)

    def test_definition_code_shared_state_not_shared(self):
        """定义级 IR 共享只读，实例内存独立（IR_SPEC §3 实例化规则）。"""
        eng = Engine(programs.isolation_task())
        self.assertEqual(eng.fb_instances["CNT_A"], eng.fb_instances["CNT_B"])  # 同一定义
        eng.run_scan({"EnA": True, "EnB": False})
        self.assertNotEqual(eng.store["CNT_A.N"], eng.store["CNT_B.N"])

    def test_library_instances_isolated(self):
        """两份 TON（PT=1000/2000）同输入不同时序，ET 各自累计。"""
        eng = Engine(programs.isolation_task())
        for _ in range(3):
            eng.run_scan({"EnA": True, "EnB": False})
        self.assertEqual(eng.store["T_A.ET"], 1000)   # 饱和到 PT=1000
        self.assertEqual(eng.store["T_B.ET"], 1500)   # 仍在计时
        self.assertTrue(eng.store["T_A.Q"])
        self.assertFalse(eng.store["T_B.Q"])


if __name__ == "__main__":
    unittest.main()
