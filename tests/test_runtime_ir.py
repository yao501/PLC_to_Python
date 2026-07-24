"""WP-20260713-002：正式 IR 模型与装载期静态校验的测试。

覆盖：一个最小合法 Task 被接受 + 模型/校验规则的代表性失败用例
（缺失类型、未知变量、STORE 类型不匹配、栈下溢、重复/缺失标签、
控制流汇合栈不一致、未知实例/POU、绑定缺失/重复/模式或类型错误、
OUT/INOUT 常量绑定、非法 VAR_TEMP、递归实例循环等；Round 2 增补：
StackSlot.index 语义校验（重复/非连续/越界/类型经 index 核对）、
绑定齐全性收紧为全部形参必绑、Task.cycle_ms 固定 500ms）。

注意：这些测试只验证 Python 侧静态校验行为，**不构成与 CODESYS PLC
语义一致的证据**（PLC 一致性属阶段 6 对拍范围）。
"""
from __future__ import annotations

import unittest

from src.runtime import (
    BinOp,
    Binding,
    CallFb,
    CallFbInstance,
    CallFunc,
    CallStd,
    Const,
    Convert,
    IOMap,
    IRValidationError,
    InstanceDecl,
    Jmp,
    JmpIfFalse,
    Label,
    LoadConst,
    LoadPrev,
    LoadVar,
    MissingVariantError,
    POUDefinition,
    ProgramInstance,
    StackSlot,
    StdSig,
    StoreKey,
    StoreVar,
    Task,
    UnOp,
    UnknownBlockError,
    VarDecl,
    build_default_registry,
    validate_task,
)


def _gvl():
    return [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Stop", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Setpoint", "REAL", section="VAR_GLOBAL"),
    ]


def _program(code, name="Main", locals_=None, instances=None):
    return POUDefinition(
        name=name, pou_kind="PROGRAM", language="ST",
        locals=locals_ or [], instances=instances or [], code=code,
    )


def _task(code=None, pou=None, gvl=None, extra_pous=None):
    main = pou if pou is not None else _program(code or [])
    pou_lib = {main.name: main}
    for p in (extra_pous or []):
        pou_lib[p.name] = p
    return Task(
        programs=[ProgramInstance(definition=main.name, store_prefix="PLC_PRG")],
        gvl=gvl if gvl is not None else _gvl(),
        pou_lib=pou_lib,
    )


class _Base(unittest.TestCase):
    def assert_rejected(self, task, *needles):
        with self.assertRaises(IRValidationError) as cm:
            validate_task(task)
        text = str(cm.exception)
        for needle in needles:
            self.assertIn(needle, text)
        return cm.exception


# ---------------------------------------------------------------------------
# 接受用例
# ---------------------------------------------------------------------------

class TestAccept(_Base):
    def test_minimal_valid_task(self):
        """最小合法 Task：Motor := Start AND NOT Stop。"""
        code = [
            LoadVar("Start", "BOOL"),
            LoadVar("Stop", "BOOL"),
            UnOp("NOT", "BOOL"),
            BinOp("AND", "BOOL"),
            StoreVar("Motor", "BOOL"),
        ]
        validate_task(_task(code))  # 不抛异常即通过

    def test_control_flow_and_convert(self):
        """IF 分支 + CONVERT + 比较结果 BOOL + LOAD_PREV。"""
        code = [
            LoadVar("Setpoint", "REAL"),
            LoadConst(10, "INT"),
            Convert("INT", "REAL"),
            BinOp("GT", "REAL"),           # 比较 → BOOL
            JmpIfFalse("else"),
            LoadConst(True, "BOOL"),
            StoreVar("Motor", "BOOL"),
            Jmp("end"),
            Label("else"),
            LoadPrev("Motor", "BOOL"),     # 上一拍值
            StoreVar("Motor", "BOOL"),
            Label("end"),
        ]
        validate_task(_task(code))

    def test_user_fb_and_function_call(self):
        """用户 FUNCTION_BLOCK 经 CALL_FB_INSTANCE、FUNCTION 经 CALL_FUNC。"""
        fb = POUDefinition(
            name="Debounce", pou_kind="FUNCTION_BLOCK", language="ST",
            interface=[
                VarDecl("IN", "BOOL", section="VAR_INPUT"),
                VarDecl("Q", "BOOL", section="VAR_OUTPUT"),
                VarDecl("Buf", "BOOL", section="VAR_IN_OUT"),
            ],
            locals=[VarDecl("state", "BOOL"), VarDecl("scratch", "BOOL", section="VAR_TEMP")],
            code=[
                LoadVar("IN", "BOOL"),
                StoreVar("state", "BOOL"),
                LoadVar("state", "BOOL"),
                StoreVar("Q", "BOOL"),
            ],
        )
        fn = POUDefinition(
            name="Clamp01", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("X", "REAL", section="VAR_INPUT")],
            locals=[VarDecl("tmp", "REAL")],
            return_type="REAL",
            code=[LoadVar("X", "REAL")],   # 出口恰余返回值 REAL
        )
        main = _program(
            code=[
                CallFbInstance("FB1", (
                    Binding("IN", "IN", StoreKey("Start"), "BOOL"),
                    Binding("Q", "OUT", StoreKey("Motor"), "BOOL"),
                    Binding("Buf", "INOUT", StoreKey("Stop"), "BOOL"),
                )),
                CallFunc("Clamp01", (
                    Binding("X", "IN", Const(0.5, "REAL"), "REAL"),
                ), "REAL"),
                StoreVar("Setpoint", "REAL"),
            ],
            instances=[InstanceDecl("FB1", "Debounce", kind="user_fb")],
        )
        validate_task(_task(pou=main, extra_pous=[fb, fn]))

    def test_library_fb_and_std_call(self):
        """库块实例（管脚类型属 L2 描述符，本包只验证实例已声明）+ CALL_STD。"""
        code = [
            LoadVar("Start", "BOOL"),
            StoreVar("TON1.IN", "BOOL"),
            LoadConst(5000, "TIME"),
            StoreVar("TON1.PT", "TIME"),
            CallFb("TON1"),
            LoadVar("TON1.Q", "BOOL"),
            StoreVar("Motor", "BOOL"),
            LoadConst(1.0, "REAL"),
            LoadConst(0.0, "REAL"),
            LoadConst(2.0, "REAL"),
            CallStd("LIMIT", StdSig(("REAL", "REAL", "REAL"), "REAL")),
            StoreVar("Setpoint", "REAL"),
        ]
        main = _program(code, instances=[InstanceDecl("TON1", "TON", kind="library")])
        validate_task(_task(pou=main))

    def test_io_map_out_with_policy(self):
        task = _task([LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
        task.io_map = [
            IOMap("Start", "DI0", "IN"),
            IOMap("Motor", "DO0", "OUT", policy=object()),  # 占位即可
        ]
        validate_task(task)


# ---------------------------------------------------------------------------
# 失败用例：类型与栈
# ---------------------------------------------------------------------------

class TestTypeAndStackErrors(_Base):
    def test_missing_type_rejected(self):
        self.assert_rejected(_task([LoadConst(1, None), ]), "类型缺失或非法")
        self.assert_rejected(_task([LoadVar("Start", "NOT_A_TYPE"),
                                    StoreVar("Motor", "BOOL")]), "类型缺失或非法")

    def test_unknown_variable(self):
        self.assert_rejected(
            _task([LoadVar("Ghost", "BOOL"), StoreVar("Motor", "BOOL")]),
            "未知变量 'Ghost'")

    def test_store_type_mismatch_vs_declared(self):
        # 指令类型与声明类型不一致
        self.assert_rejected(
            _task([LoadConst(1, "INT"), StoreVar("Motor", "INT")]),
            "声明类型")

    def test_store_type_mismatch_vs_stack(self):
        # 栈顶 INT 写 REAL 变量（未插 CONVERT）
        self.assert_rejected(
            _task([LoadConst(1, "INT"), StoreVar("Setpoint", "REAL")]),
            "严格等于")

    def test_stack_underflow(self):
        self.assert_rejected(
            _task([BinOp("AND", "BOOL"), StoreVar("Motor", "BOOL")]),
            "栈下溢")

    def test_program_exit_stack_not_empty(self):
        self.assert_rejected(
            _task([LoadConst(True, "BOOL")]),
            "出口栈应为空")

    def test_jmp_if_false_requires_bool(self):
        self.assert_rejected(
            _task([LoadConst(1, "INT"), JmpIfFalse("end"), Label("end")]),
            "JMP_IF_FALSE")

    def test_mod_requires_integer(self):
        self.assert_rejected(
            _task([LoadConst(1.0, "REAL"), LoadConst(2.0, "REAL"),
                   BinOp("MOD", "REAL"), StoreVar("Setpoint", "REAL")]),
            "MOD 仅支持整数族")

    def test_neg_rejects_unsigned(self):
        self.assert_rejected(
            _task([LoadConst(1, "UINT"),
                   UnOp("NEG", "UINT"),
                   Convert("UINT", "INT"), StoreVar("Motor", "INT")]),
            "NEG 不支持")


# ---------------------------------------------------------------------------
# 失败用例：控制流
# ---------------------------------------------------------------------------

class TestControlFlowErrors(_Base):
    def test_duplicate_label(self):
        self.assert_rejected(
            _task([Label("a"), Label("a")]), "标签重复")

    def test_missing_jump_target(self):
        self.assert_rejected(
            _task([Jmp("nowhere")]), "跳转目标标签不存在")

    def test_merge_point_stack_mismatch(self):
        """两条路径汇合处栈深不一致：一路多压一个值。"""
        code = [
            LoadVar("Start", "BOOL"),
            JmpIfFalse("else"),
            LoadConst(1, "INT"),          # then 路径压 1 个 INT
            Jmp("merge"),
            Label("else"),                # else 路径不压
            Label("merge"),               # 汇合点：INT vs 空
            StoreVar("Motor", "BOOL"),
        ]
        self.assert_rejected(_task(code), "汇合点栈不一致")


# ---------------------------------------------------------------------------
# 失败用例：实例与 POU 引用
# ---------------------------------------------------------------------------

class TestReferenceErrors(_Base):
    def test_unknown_fb_instance(self):
        self.assert_rejected(_task([CallFb("TON9")]), "未声明实例")

    def test_call_fb_on_user_fb(self):
        fb = POUDefinition(name="U", pou_kind="FUNCTION_BLOCK", language="ST",
                           code=[])
        main = _program([CallFb("FB1")],
                        instances=[InstanceDecl("FB1", "U", kind="user_fb")])
        self.assert_rejected(_task(pou=main, extra_pous=[fb]),
                             "CALL_FB 只用于库块实例")

    def test_unknown_function(self):
        self.assert_rejected(
            _task([CallFunc("Nope", (), "REAL"), StoreVar("Setpoint", "REAL")]),
            "未定义 FUNCTION")

    def test_program_instance_unknown_definition(self):
        task = _task([])
        task.programs = [ProgramInstance("Ghost", "P1")]
        self.assert_rejected(task, "未定义的 POU")

    def test_pou_lib_key_name_mismatch(self):
        main = _program([])
        task = _task(pou=main)
        task.pou_lib["Alias"] = _program([], name="NotAlias")
        self.assert_rejected(task, "不一致")

    def test_reachable_pou_without_code(self):
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST",
                             code=None)
        self.assert_rejected(_task(pou=main), "code=None")

    def test_recursive_instance_cycle(self):
        a = POUDefinition(name="A", pou_kind="FUNCTION_BLOCK", language="ST",
                          instances=[InstanceDecl("b", "B", kind="user_fb")],
                          code=[])
        b = POUDefinition(name="B", pou_kind="FUNCTION_BLOCK", language="ST",
                          instances=[InstanceDecl("a", "A", kind="user_fb")],
                          code=[])
        main = _program([], instances=[InstanceDecl("x", "A", kind="user_fb")])
        self.assert_rejected(_task(pou=main, extra_pous=[a, b]),
                             "递归实例声明循环")

    def test_self_recursive_instance(self):
        a = POUDefinition(name="A", pou_kind="FUNCTION_BLOCK", language="ST",
                          instances=[InstanceDecl("inner", "A", kind="user_fb")],
                          code=[])
        main = _program([], instances=[InstanceDecl("x", "A", kind="user_fb")])
        self.assert_rejected(_task(pou=main, extra_pous=[a]), "循环")


# ---------------------------------------------------------------------------
# 失败用例：绑定
# ---------------------------------------------------------------------------

def _fb_def():
    return POUDefinition(
        name="FB", pou_kind="FUNCTION_BLOCK", language="ST",
        interface=[
            VarDecl("IN1", "BOOL", section="VAR_INPUT"),
            VarDecl("OUT1", "BOOL", section="VAR_OUTPUT"),
            VarDecl("IO1", "REAL", section="VAR_IN_OUT"),
        ],
        code=[],
    )


def _main_with_fb(bindings):
    return _program(
        [CallFbInstance("FB1", tuple(bindings))],
        instances=[InstanceDecl("FB1", "FB", kind="user_fb")],
    )


class TestBindingErrors(_Base):
    def _full(self):
        return [
            Binding("IN1", "IN", StoreKey("Start"), "BOOL"),
            Binding("OUT1", "OUT", StoreKey("Motor"), "BOOL"),
            Binding("IO1", "INOUT", StoreKey("Setpoint"), "REAL"),
        ]

    def test_valid_bindings_accepted(self):
        validate_task(_task(pou=_main_with_fb(self._full()), extra_pous=[_fb_def()]))

    def test_missing_inout_binding(self):
        b = self._full()[:2]                      # 缺 IO1（VAR_IN_OUT 必连）
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "未绑定", "VAR_IN_OUT")

    def test_missing_input_binding(self):
        """齐全性：VAR_INPUT 缺省同样拒绝（当前 IR 无默认值编码）。"""
        b = self._full()[1:]                      # 缺 IN1
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "未绑定", "VAR_INPUT")

    def test_missing_output_binding(self):
        """齐全性：VAR_OUTPUT 缺省同样拒绝（当前 IR 无丢弃 OUT 编码）。"""
        b = [self._full()[0], self._full()[2]]    # 缺 OUT1
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "未绑定", "VAR_OUTPUT")

    def test_duplicate_binding(self):
        b = self._full() + [Binding("IN1", "IN", Const(True, "BOOL"), "BOOL")]
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "重复绑定")

    def test_unknown_formal(self):
        b = self._full() + [Binding("GHOST", "IN", Const(1, "INT"), "INT")]
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "不存在于")

    def test_mode_mismatch(self):
        b = self._full()
        b[0] = Binding("IN1", "OUT", StoreKey("Start"), "BOOL")  # INPUT 绑成 OUT
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "应绑 IN 模式")

    def test_type_mismatch(self):
        b = self._full()
        b[0] = Binding("IN1", "IN", StoreKey("Setpoint"), "REAL")  # BOOL 形参绑 REAL
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "类型应为 BOOL")

    def test_out_const_rejected(self):
        b = self._full()
        b[1] = Binding("OUT1", "OUT", Const(True, "BOOL"), "BOOL")
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "禁止绑定 Const")

    def test_inout_const_rejected(self):
        b = self._full()
        b[2] = Binding("IO1", "INOUT", Const(1.0, "REAL"), "REAL")
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "禁止绑定 Const")

    def test_inout_stackslot_rejected(self):
        b = self._full()
        b[2] = Binding("IO1", "INOUT", StackSlot(0, writable=True), "REAL")
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "必须绑定可写 StoreKey")

    def test_out_writable_stackslot_conservatively_rejected(self):
        b = self._full()
        b[1] = Binding("OUT1", "OUT", StackSlot(0, writable=True), "BOOL")
        self.assert_rejected(_task(pou=_main_with_fb(b), extra_pous=[_fb_def()]),
                             "保守拒绝")

    def test_call_func_ret_type_mismatch(self):
        fn = POUDefinition(name="F", pou_kind="FUNCTION", language="ST",
                           return_type="REAL", code=[LoadConst(1.0, "REAL")])
        main = _program([CallFunc("F", (), "INT"),
                         Convert("INT", "BOOL"), StoreVar("Motor", "BOOL")])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]),
                             "return_type=REAL 不一致")

    def test_in_stackslot_consumes_stack(self):
        fn = POUDefinition(
            name="F", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("X", "REAL", section="VAR_INPUT")],
            return_type="REAL", code=[LoadVar("X", "REAL")],
        )
        main = _program([
            LoadConst(1.5, "REAL"),
            CallFunc("F", (Binding("X", "IN", StackSlot(0), "REAL"),), "REAL"),
            StoreVar("Setpoint", "REAL"),
        ])
        validate_task(_task(pou=main, extra_pous=[fn]))


# ---------------------------------------------------------------------------
# StackSlot.index 语义（工程约定：0=栈顶，同一调用须连续覆盖 {0..k-1}）
# ---------------------------------------------------------------------------

def _fn2():
    """双输入 FUNCTION：A: REAL, B: INT → REAL。"""
    return POUDefinition(
        name="F2", pou_kind="FUNCTION", language="ST",
        interface=[VarDecl("A", "REAL", section="VAR_INPUT"),
                   VarDecl("B", "INT", section="VAR_INPUT")],
        return_type="REAL", code=[LoadVar("A", "REAL")],
    )


def _main_fn2(bindings):
    """调用点栈：先压 REAL（将位于底部）再压 INT（栈顶）→ index 1=REAL, 0=INT。"""
    return _program([
        LoadConst(1.5, "REAL"),
        LoadConst(2, "INT"),
        CallFunc("F2", tuple(bindings), "REAL"),
        StoreVar("Setpoint", "REAL"),
    ])


class TestStackSlotIndex(_Base):
    def test_index_maps_by_offset_not_binding_order(self):
        """index 决定取值位置，绑定书写顺序无关：两种顺序均接受。"""
        b_ab = [Binding("A", "IN", StackSlot(1), "REAL"),
                Binding("B", "IN", StackSlot(0), "INT")]
        validate_task(_task(pou=_main_fn2(b_ab), extra_pous=[_fn2()]))
        b_ba = list(reversed(b_ab))
        validate_task(_task(pou=_main_fn2(b_ba), extra_pous=[_fn2()]))

    def test_index_type_checked_by_offset(self):
        """index 指错位置 → 按偏移核对类型后拒绝（旧实现按书写顺序会漏检）。"""
        b = [Binding("A", "IN", StackSlot(0), "REAL"),   # 栈顶实为 INT
             Binding("B", "IN", StackSlot(1), "INT")]    # 次顶实为 REAL
        self.assert_rejected(_task(pou=_main_fn2(b), extra_pous=[_fn2()]),
                             "StackSlot(0) 引用的栈值类型为 INT，应为 REAL")

    def test_duplicate_index_rejected(self):
        b = [Binding("A", "IN", StackSlot(0), "REAL"),
             Binding("B", "IN", StackSlot(0), "INT")]
        self.assert_rejected(_task(pou=_main_fn2(b), extra_pous=[_fn2()]),
                             "StackSlot.index 重复")

    def test_noncontiguous_index_rejected(self):
        """单个 IN 槽却写 index=1：未覆盖 {0} → 拒绝（禁止跳槽引用）。"""
        fn = POUDefinition(
            name="F", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("X", "REAL", section="VAR_INPUT")],
            return_type="REAL", code=[LoadVar("X", "REAL")],
        )
        main = _program([
            LoadConst(9, "INT"),
            LoadConst(1.5, "REAL"),
            CallFunc("F", (Binding("X", "IN", StackSlot(1), "REAL"),), "REAL"),
            StoreVar("Setpoint", "REAL"),
        ])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]),
                             "须恰好连续覆盖 {0..0}")

    def test_negative_index_rejected(self):
        fn = POUDefinition(
            name="F", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("X", "REAL", section="VAR_INPUT")],
            return_type="REAL", code=[LoadVar("X", "REAL")],
        )
        main = _program([
            LoadConst(1.5, "REAL"),
            CallFunc("F", (Binding("X", "IN", StackSlot(-1), "REAL"),), "REAL"),
            StoreVar("Setpoint", "REAL"),
        ])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]),
                             "必须为非负整数")

    def test_index_exceeds_stack_depth(self):
        """索引集合合法但调用点栈深不足 → 栈下溢拒绝。"""
        b = [Binding("A", "IN", StackSlot(1), "REAL"),
             Binding("B", "IN", StackSlot(0), "INT")]
        main = _program([
            LoadConst(2, "INT"),                          # 只压 1 个值
            CallFunc("F2", tuple(b), "REAL"),
            StoreVar("Setpoint", "REAL"),
        ])
        self.assert_rejected(_task(pou=main, extra_pous=[_fn2()]),
                             "栈下溢")


# ---------------------------------------------------------------------------
# Task.cycle_ms 冻结边界（单任务、固定 500ms）
# ---------------------------------------------------------------------------

class TestTaskCycleMs(_Base):
    def test_non_500_cycle_rejected(self):
        for bad in (100, 1000, 0, -5):
            task = _task([LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
            task.cycle_ms = bad
            self.assert_rejected(task, "固定 500ms")

    def test_non_int_cycle_rejected(self):
        for bad in (500.0, "500", None, True):
            task = _task([LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
            task.cycle_ms = bad
            self.assert_rejected(task, "固定 500ms")


# ---------------------------------------------------------------------------
# 失败用例：POU 结构与语义子集
# ---------------------------------------------------------------------------

class TestPouStructureErrors(_Base):
    def test_var_temp_in_function_rejected(self):
        fn = POUDefinition(
            name="F", pou_kind="FUNCTION", language="ST", return_type="BOOL",
            locals=[VarDecl("t", "BOOL", section="VAR_TEMP")],
            code=[LoadConst(True, "BOOL")],
        )
        main = _program([CallFunc("F", (), "BOOL"), StoreVar("Motor", "BOOL")])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]),
                             "FUNCTION 不允许 VAR_TEMP")

    def test_function_gvl_access_rejected(self):
        fn = POUDefinition(
            name="F", pou_kind="FUNCTION", language="ST", return_type="BOOL",
            code=[LoadVar("Start", "BOOL")],   # Start 是 GVL
        )
        main = _program([CallFunc("F", (), "BOOL"), StoreVar("Motor", "BOOL")])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]),
                             "FUNCTION 禁止访问 GVL")

    def test_function_without_return_type(self):
        fn = POUDefinition(name="F", pou_kind="FUNCTION", language="ST",
                           code=[LoadConst(True, "BOOL")])
        main = _program([])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]), "缺 return_type")

    def test_function_exit_stack_contract(self):
        fn = POUDefinition(name="F", pou_kind="FUNCTION", language="ST",
                           return_type="REAL", code=[])   # 出口空栈 ≠ [REAL]
        main = _program([])
        self.assert_rejected(_task(pou=main, extra_pous=[fn]),
                             "FUNCTION 正常出口栈应恰为")

    def test_program_with_return_type(self):
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST",
                             return_type="INT", code=[])
        self.assert_rejected(_task(pou=main), "不应有 return_type")

    def test_duplicate_names_in_scope(self):
        main = _program([], locals_=[VarDecl("x", "BOOL"), VarDecl("x", "INT")])
        self.assert_rejected(_task(pou=main), "名称重复")

    def test_duplicate_gvl_names(self):
        gvl = _gvl() + [VarDecl("Start", "INT", section="VAR_GLOBAL")]
        self.assert_rejected(_task([], gvl=gvl), "GVL 变量名重复")

    def test_io_map_unknown_var_and_missing_policy(self):
        task = _task([])
        task.io_map = [IOMap("Ghost", "DI0", "IN"), IOMap("Motor", "DO0", "OUT")]
        self.assert_rejected(task, "未声明的 GVL", "缺 OutputPolicy")

    def test_duplicate_store_prefix(self):
        task = _task([])
        task.programs.append(ProgramInstance("Main", "PLC_PRG"))
        self.assert_rejected(task, "store_prefix 重复")


# ---------------------------------------------------------------------------
# 接入 L2 注册表后的库块管脚类型闭环（WP-20260723-018）
# ---------------------------------------------------------------------------
#
# 未接入注册表时，库块实例管脚解析为 "*"（类型未知、跳过核对，历史诚实
# 边界）；接入 `build_default_registry()` 后，`<inst>.<pin>` 必须按已注册
# BlockSchema 核验存在性、方向与 IEC 类型，`"*"` 与 legacy 路径都不得绕过。
# 这些断言只验证 Python 侧静态校验，不构成与 CODESYS 语义一致的证据。

class TestRegistryLibraryPins(_Base):
    def _reg(self):
        return build_default_registry()

    def _ton_task(self, code):
        main = _program(code,
                        instances=[InstanceDecl("TON1", "TON", kind="library")])
        return _task(pou=main)

    def assert_rejected_reg(self, task, *needles):
        with self.assertRaises(IRValidationError) as cm:
            validate_task(task, registry=self._reg())
        text = str(cm.exception)
        for needle in needles:
            self.assertIn(needle, text)
        return cm.exception

    def test_registered_library_pins_closed(self):
        """已注册库块：管脚存在、方向、IEC 类型全部核对通过。"""
        code = [
            LoadVar("Start", "BOOL"), StoreVar("TON1.IN", "BOOL"),
            LoadConst(1000, "TIME"), StoreVar("TON1.PT_ms", "TIME"),
            CallFb("TON1"),
            LoadVar("TON1.Q", "BOOL"), StoreVar("Motor", "BOOL"),
        ]
        validate_task(self._ton_task(code), registry=self._reg())

    def test_unknown_pin_rejected(self):
        code = [LoadVar("TON1.NOPE", "BOOL"), StoreVar("Motor", "BOOL")]
        self.assert_rejected_reg(self._ton_task(code), "不存在的管脚")

    def test_wrong_direction_rejected(self):
        # 读输入管脚（IN 是 VAR_INPUT，不可 LOAD）
        code_in = [LoadVar("TON1.IN", "BOOL"), StoreVar("Motor", "BOOL")]
        self.assert_rejected_reg(self._ton_task(code_in), "只读 VAR_OUTPUT")
        # 写输出管脚（Q 是 VAR_OUTPUT，不可 STORE）
        code_q = [LoadConst(True, "BOOL"), StoreVar("TON1.Q", "BOOL")]
        self.assert_rejected_reg(self._ton_task(code_q), "只写 VAR_INPUT")

    def test_pin_type_mismatch_rejected(self):
        # Q 声明 BOOL；指令按 REAL 读 → 类型闭环拒绝
        code = [LoadVar("TON1.Q", "REAL"), StoreVar("Setpoint", "REAL")]
        self.assert_rejected_reg(self._ton_task(code), "不一致")

    def test_no_star_bypass_when_registry_present(self):
        """同一 IR：无注册表时 '*' 跳过类型检查而被接受；接入注册表后按
        Schema 闭环拒绝——证明 '*'/legacy 路径不再绕过。"""
        code = [LoadVar("TON1.Q", "REAL"), StoreVar("Setpoint", "REAL")]
        validate_task(self._ton_task(code))                 # 无注册表：接受
        self.assert_rejected_reg(self._ton_task(code), "不一致")   # 有注册表：拒绝

    def test_unregistered_library_block_rejected(self):
        """库块类型未在 L2 注册表登记 → 失败关闭（诊断只引用 block_type
        字符串，不对不可信对象做危险字符串化）。"""
        main = _program([CallFb("G1")],
                        instances=[InstanceDecl("G1", "GHOST", kind="library")])
        self.assert_rejected_reg(_task(pou=main),
                                 "未在 L2 注册表登记", "GHOST")

    def test_registry_resolve_fail_closed(self):
        """缺变体 / 未知块解析一律显式报错，绝不静默降级到 engineering。"""
        reg = self._reg()
        with self.assertRaises(MissingVariantError):
            reg.resolve("APCM", "fidelity_f2")       # F2 缺变体不静默回退
        with self.assertRaises(UnknownBlockError):
            reg.resolve("GHOST", "engineering")


if __name__ == "__main__":
    unittest.main()
