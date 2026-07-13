"""加载器验证 pass：非法 IR / 非法 OutputPolicy / 非法绑定拒绝加载（IR_SPEC §5.1/§5.2、ENGINE_SCAN_SPEC §4）。"""
import unittest

from prototype_05 import ir, programs
from prototype_05.descriptors import (REGISTRY, BlockDescriptor,
                                      DuplicateDescriptorError, register)
from prototype_05.engine import Engine
from prototype_05.ir import (Binding, Instr, IOMap, OutputPolicy,
                             ProgramInstance, Task, VarDecl)
from prototype_05.loader import LoadError


def _mini_task(code, gvl=None):
    return Task(programs=[ProgramInstance("P", code)],
                gvl=gvl or [VarDecl("B1", "BOOL", section="VAR_GLOBAL"),
                            VarDecl("R1", "REAL", section="VAR_GLOBAL")])


class TestLoaderValidation(unittest.TestCase):
    def test_untyped_instruction_rejected(self):
        """无类型的指令列表不是合法 IR（IR_SPEC §5.1）。"""
        code = [Instr("LOAD_VAR", key="B1"), ir.STORE_VAR("B1", "BOOL")]
        with self.assertRaisesRegex(LoadError, "无类型"):
            Engine(_mini_task(code))

    def test_binop_operand_type_mismatch_rejected(self):
        code = [ir.LOAD_CONST(1, "INT"), ir.LOAD_CONST(1.0, "REAL"),
                ir.BINOP("ADD", "INT"), ir.CONVERT("INT", "REAL"),
                ir.STORE_VAR("R1", "REAL")]
        with self.assertRaisesRegex(LoadError, "类型不匹配"):
            Engine(_mini_task(code))

    def test_store_type_mismatch_rejected(self):
        """STORE_VAR 栈顶类型必须与其 type 严格相等——隐式提升必须显式 CONVERT。"""
        code = [ir.LOAD_CONST(1, "INT"), ir.STORE_VAR("R1", "REAL")]
        with self.assertRaisesRegex(LoadError, "类型不匹配"):
            Engine(_mini_task(code))

    def test_undeclared_variable_rejected(self):
        code = [ir.LOAD_VAR("NoSuch", "BOOL"), ir.STORE_VAR("B1", "BOOL")]
        with self.assertRaisesRegex(LoadError, "未声明"):
            Engine(_mini_task(code))

    def test_jump_to_missing_label_rejected(self):
        code = [ir.JMP("L99")]
        with self.assertRaisesRegex(LoadError, "不存在的 LABEL"):
            Engine(_mini_task(code))

    def test_call_fb_on_undeclared_instance_rejected(self):
        code = [ir.CALL_FB("TON_X")]
        with self.assertRaisesRegex(LoadError, "未声明库实例"):
            Engine(_mini_task(code))

    def test_forced_safe_policy_fields_reject_hold(self):
        """on_safety_trip / on_scan_fault / on_watchdog 配 'hold' = 非法工程（§4 约束 1）。"""
        for f in ("on_safety_trip", "on_scan_fault", "on_watchdog"):
            policy = OutputPolicy(var="B1", iec_type="BOOL", safe_value=False,
                                  **{f: "hold"})
            task = _mini_task([ir.LOAD_CONST(True, "BOOL"), ir.STORE_VAR("B1", "BOOL")])
            task.io_map = [IOMap("B1", "DO", "OUT", policy)]
            with self.assertRaisesRegex(LoadError, "非法工程"):
                Engine(task)

    def test_inout_binding_to_const_rejected(self):
        """INOUT 必须绑定可写 StoreKey，禁止 Const（IR_SPEC §5.2 模式约束）。"""
        task = programs.var_in_out_task()
        task.programs[0].code = [ir.CALL_FB_INSTANCE("ACC1", [
            Binding("INC", "IN", "const", 2.5, "REAL"),
            Binding("ACC", "INOUT", "const", 1.0, "REAL"),
        ])]
        with self.assertRaisesRegex(LoadError, "只能绑定可写 StoreKey"):
            Engine(task)

    def test_missing_binding_rejected(self):
        """必连形参缺失 = 加载错误（绑定表齐全性检查，IR_SPEC §5.2）。"""
        task = programs.var_in_out_task()
        task.programs[0].code = [ir.CALL_FB_INSTANCE("ACC1", [
            Binding("INC", "IN", "const", 2.5, "REAL"),
        ])]
        with self.assertRaisesRegex(LoadError, "形参未绑定"):
            Engine(task)

    def test_binding_mode_must_match_declaration(self):
        """VAR_IN_OUT 形参按 IN 绑定（值拷贝往返）被拒绝。"""
        task = programs.var_in_out_task()
        task.programs[0].code = [ir.CALL_FB_INSTANCE("ACC1", [
            Binding("INC", "IN", "const", 2.5, "REAL"),
            Binding("ACC", "IN", "var", "Total", "REAL"),
        ])]
        with self.assertRaisesRegex(LoadError, "声明 VAR_IN_OUT"):
            Engine(task)

    def test_duplicate_descriptor_registration_rejected(self):
        """同 (block_type, variant) 重复注册显式报错（COMPONENT_CONTRACT §5）。"""
        desc = BlockDescriptor(block_type="TON", cls=object)   # 与既有 TON 同键
        self.assertIn(("TON", "engineering"), REGISTRY)
        with self.assertRaises(DuplicateDescriptorError):
            register(desc)


if __name__ == "__main__":
    unittest.main()
