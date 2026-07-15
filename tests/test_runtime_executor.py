"""WP-20260714-004：正式 IR 执行器 / TypedValue 求值栈 / 用户 POU 调用帧测试。

对应任务书"最低测试要求"46 条逐条对号（注释标注"要求 N"）。
注意：F1 量化与整数中间位宽政策是**当前候选行为**（待真机黄金轨迹裁决），
这些 Python 测试只锁定当前实现，不构成与 CODESYS PLC 语义一致的证据。

运行期防御类测试通过"装载校验通过后篡改 code"的方式绕过 loader 构造非法
指令流——目的正是证明执行器不只信任 loader。
"""
from __future__ import annotations

import sys
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
    Executor,
    IECMathError,
    InstanceDecl,
    IRExecutionError,
    Jmp,
    JmpIfFalse,
    Label,
    LoadConst,
    LoadPrev,
    LoadVar,
    MissingLibraryAdapterError,
    MissingStdFunctionError,
    NumericMode,
    POUDefinition,
    ProgramInstance,
    StackSlot,
    StdSig,
    StoreKey,
    StoreTypeError,
    StoreVar,
    Task,
    TypedValue,
    UnOp,
    UnsupportedNumericModeError,
    VarDecl,
    build_runtime_store,
    persistent_key,
    quantize_real32,
)


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------

def _gvl():
    return [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("X", "REAL", section="VAR_GLOBAL"),
        VarDecl("Y", "REAL", section="VAR_GLOBAL"),
        VarDecl("N", "INT", section="VAR_GLOBAL"),
        VarDecl("M", "INT", section="VAR_GLOBAL"),
        VarDecl("BigA", "LINT", section="VAR_GLOBAL"),
        VarDecl("BigB", "LINT", section="VAR_GLOBAL"),
        VarDecl("W", "WORD", section="VAR_GLOBAL"),
        VarDecl("V", "WORD", section="VAR_GLOBAL"),
        VarDecl("UseBad", "BOOL", section="VAR_GLOBAL"),
    ]


def _prog(name, code, locals_=None, instances=None):
    return POUDefinition(name=name, pou_kind="PROGRAM", language="ST",
                         locals=locals_ or [], instances=instances or [],
                         code=code)


def _task(main_code=None, pous=None, programs=None, gvl=None):
    pou_lib = {}
    if main_code is not None:
        pou_lib["Main"] = _prog("Main", main_code)
    for p in (pous or []):
        pou_lib[p.name] = p
    progs = programs or [ProgramInstance("Main", "PLC_PRG")]
    return Task(programs=progs, gvl=gvl if gvl is not None else _gvl(),
                pou_lib=pou_lib)


def _run(task, mode=None, std=None, adapters=None, prev_overrides=None,
         store_setup=None):
    """建布局→(可选)预置 Store→取 prev 快照→执行；返回 (layout, executor)。"""
    layout = build_runtime_store(task)
    if store_setup:
        for k, v in store_setup.items():
            layout.store.write(k, v)
    prev = layout.store.snapshot()
    if prev_overrides:
        # 制造 prev ≠ 当前：临时写入→快照→恢复（当前 Store 不保留 overrides）
        prev = _snap_with(layout.store, prev_overrides)
    ex = Executor(task, layout, numeric_mode=mode, std_functions=std,
                  library_adapters=adapters)
    ex.execute_programs(prev if prev is not None else layout.store.snapshot())
    return layout, ex


def _snap_with(store, overrides):
    """先写 overrides 再取快照、随后恢复——制造 prev≠当前 的快照。"""
    olds = {k: store.read(k) for k in overrides}
    for k, v in overrides.items():
        store.write(k, v)
    snap = store.snapshot()
    for k, v in olds.items():
        store.write(k, v)
    return snap


F1 = NumericMode(mode="fidelity_f1")


# ---------------------------------------------------------------------------
# 基础执行（要求 1–9）
# ---------------------------------------------------------------------------

class TestBasicExecution(unittest.TestCase):
    def test_sequential_and_load_store(self):                # 要求 1、3
        code = [LoadConst(3, "INT"), StoreVar("N", "INT"),
                LoadVar("N", "INT"), LoadConst(4, "INT"),
                BinOp("ADD", "INT"), StoreVar("M", "INT")]
        layout, _ = _run(_task(code))
        self.assertEqual(layout.store.read("N"), 3)
        self.assertEqual(layout.store.read("M"), 7)

    def test_multi_program_order(self):                      # 要求 2
        p1 = _prog("P1", [LoadConst(1, "INT"), StoreVar("N", "INT")])
        p2 = _prog("P2", [LoadVar("N", "INT"), LoadConst(10, "INT"),
                          BinOp("MUL", "INT"), StoreVar("N", "INT")])
        task = _task(pous=[p1, p2],
                     programs=[ProgramInstance("P1", "A"),
                               ProgramInstance("P2", "B")])
        layout, _ = _run(task)
        self.assertEqual(layout.store.read("N"), 10)   # 先写 1 再 ×10（顺序敏感）
        task2 = _task(pous=[p1, p2],
                      programs=[ProgramInstance("P2", "B"),
                                ProgramInstance("P1", "A")])
        layout2, _ = _run(task2)
        self.assertEqual(layout2.store.read("N"), 1)   # 反序则最后写 1

    def test_load_prev_reads_snapshot(self):                 # 要求 4
        code = [LoadPrev("N", "INT"), StoreVar("M", "INT")]
        layout, _ = _run(_task(code), prev_overrides={"N": 42})
        self.assertEqual(layout.store.read("M"), 42)   # prev=42
        self.assertEqual(layout.store.read("N"), 0)    # 当前 N=0，未被冒充

    def test_program_local_shadows_gvl(self):                # 要求 5
        main = _prog("Main",
                     [LoadConst(9, "INT"), StoreVar("N", "INT"),   # 局部 N
                      LoadVar("N", "INT"), StoreVar("M", "INT")],  # M=GVL
                     locals_=[VarDecl("N", "INT")])
        layout, _ = _run(_task(pous=[main]))
        self.assertEqual(layout.store.read(persistent_key("PLC_PRG", "N")), 9)
        self.assertEqual(layout.store.read("N"), 0)    # GVL N 未被误写
        self.assertEqual(layout.store.read("M"), 9)

    def test_control_flow(self):                             # 要求 6
        code = [LoadVar("Start", "BOOL"), JmpIfFalse("else"),
                LoadConst(1, "INT"), StoreVar("N", "INT"), Jmp("end"),
                Label("else"), LoadConst(2, "INT"), StoreVar("N", "INT"),
                Label("end")]
        layout, _ = _run(_task(code), store_setup={"Start": True})
        self.assertEqual(layout.store.read("N"), 1)
        layout2, _ = _run(_task(code))                 # Start=False
        self.assertEqual(layout2.store.read("N"), 2)

    def test_program_exit_stack_must_be_empty(self):         # 要求 7
        task = _task([LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
        layout = build_runtime_store(task)
        task.pou_lib["Main"].code = [LoadConst(1, "INT")]    # 装载后篡改
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("出口栈应为空", str(cm.exception))

    def test_function_exit_contract(self):                   # 要求 8
        fn = POUDefinition(name="One", pou_kind="FUNCTION", language="ST",
                           return_type="INT", code=[LoadConst(1, "INT")])
        code = [CallFunc("One", (), "INT"), StoreVar("N", "INT")]
        layout, _ = _run(_task(code, pous=[fn]))
        self.assertEqual(layout.store.read("N"), 1)

    def test_runtime_defenses(self):                         # 要求 9
        cases = [
            ([BinOp("ADD", "INT"), StoreVar("N", "INT")], "栈下溢"),
            ([LoadConst(True, "BOOL"), StoreVar("N", "INT")], "类型"),
            ([Jmp("nowhere")], "未知标签"),
            (["not-an-instruction"], "未知指令"),
        ]
        for bad_code, needle in cases:
            task = _task([LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
            layout = build_runtime_store(task)
            task.pou_lib["Main"].code = bad_code         # 绕过 loader（见模块 docstring）
            ex = Executor(task, layout)
            with self.assertRaises(IRExecutionError) as cm:
                ex.execute_programs(layout.store.snapshot())
            self.assertIn(needle, str(cm.exception))
            self.assertEqual(cm.exception.pou, "Main")   # 上下文字段
            self.assertIsNotNone(cm.exception.pc)


# ---------------------------------------------------------------------------
# 运算与模式（要求 10–20）
# ---------------------------------------------------------------------------

class TestNumericOps(unittest.TestCase):
    def test_bool_and_bitstring_logic(self):                 # 要求 10
        code = [LoadVar("Start", "BOOL"), LoadVar("Motor", "BOOL"),
                BinOp("AND", "BOOL"), StoreVar("Motor", "BOOL"),
                LoadVar("W", "WORD"), LoadVar("V", "WORD"),
                BinOp("AND", "WORD"), StoreVar("W", "WORD")]
        layout, _ = _run(_task(code),
                         store_setup={"Start": True, "Motor": True,
                                      "W": 0b1100, "V": 0b1010})
        self.assertEqual(layout.store.read("Motor"), True)
        self.assertEqual(layout.store.read("W"), 0b1000)

    def test_compare_yields_bool(self):                      # 要求 11
        code = [LoadConst(2, "INT"), LoadConst(1, "INT"),
                BinOp("GT", "INT"), StoreVar("Motor", "BOOL")]
        layout, _ = _run(_task(code))
        self.assertIs(layout.store.read("Motor"), True)

    def test_neg_and_not(self):                              # 要求 12
        code = [LoadConst(5, "INT"), UnOp("NEG", "INT"), StoreVar("N", "INT"),
                LoadConst(0x00FF, "WORD"), UnOp("NOT", "WORD"),
                StoreVar("W", "WORD"),
                LoadVar("Start", "BOOL"), UnOp("NOT", "BOOL"),
                StoreVar("Motor", "BOOL")]
        layout, _ = _run(_task(code))
        self.assertEqual(layout.store.read("N"), -5)
        self.assertEqual(layout.store.read("W"), 0xFF00)
        self.assertIs(layout.store.read("Motor"), True)

    def test_int_div_truncates_toward_zero(self):            # 要求 13
        for a, b, want in [(7, 2, 3), (-7, 2, -3), (7, -2, -3), (-7, -2, 3)]:
            code = [LoadConst(a, "INT"), LoadConst(b, "INT"),
                    BinOp("DIV", "INT"), StoreVar("N", "INT")]
            layout, _ = _run(_task(code))
            self.assertEqual(layout.store.read("N"), want, (a, b))
        big = 2 ** 62 - 3
        code = [LoadConst(big, "LINT"), LoadConst(7, "LINT"),
                BinOp("DIV", "LINT"), StoreVar("BigA", "LINT")]
        layout, _ = _run(_task(code))
        q = abs(big) // 7
        self.assertEqual(layout.store.read("BigA"), q)   # 大整数不经 float

    def test_iec_mod_sign_follows_dividend(self):            # 要求 14
        for a, b, want in [(7, 2, 1), (-7, 2, -1), (7, -2, 1), (-7, -2, -1)]:
            code = [LoadConst(a, "INT"), LoadConst(b, "INT"),
                    BinOp("MOD", "INT"), StoreVar("N", "INT")]
            layout, _ = _run(_task(code))
            self.assertEqual(layout.store.read("N"), want, (a, b))
        self.assertEqual((-7) % 2, 1)   # 对照：Python % 与 IEC 不同

    def test_division_by_zero_propagates(self):              # 要求 15
        code = [LoadConst(1, "INT"), LoadConst(0, "INT"),
                BinOp("DIV", "INT"), StoreVar("N", "INT")]
        with self.assertRaises(IRExecutionError) as cm:
            _run(_task(code))
        self.assertIsInstance(cm.exception.cause, IECMathError)

    def test_engineering_no_quantization(self):              # 要求 16
        code = [LoadConst(0.1, "REAL"), StoreVar("X", "REAL")]
        layout, _ = _run(_task(code))
        self.assertEqual(layout.store.read("X"), 0.1)   # 不量化

    def test_f1_quantization_boundaries(self):               # 要求 17
        q = quantize_real32
        # LoadConst + StoreVar
        layout, _ = _run(_task([LoadConst(0.1, "REAL"), StoreVar("X", "REAL")]),
                         mode=F1)
        self.assertEqual(layout.store.read("X"), q(0.1))
        self.assertNotEqual(layout.store.read("X"), 0.1)
        # BinOp
        layout, _ = _run(_task([LoadConst(0.1, "REAL"), LoadConst(0.2, "REAL"),
                                BinOp("ADD", "REAL"), StoreVar("X", "REAL")]),
                         mode=F1)
        self.assertEqual(layout.store.read("X"), q(q(0.1) + q(0.2)))
        # UnOp
        layout, _ = _run(_task([LoadConst(0.1, "REAL"), UnOp("NEG", "REAL"),
                                StoreVar("X", "REAL")]), mode=F1)
        self.assertEqual(layout.store.read("X"), q(-q(0.1)))
        # Convert（16777217 不可 binary32 精确表示）
        layout, _ = _run(_task([LoadConst(16777217, "DINT"),
                                Convert("DINT", "REAL"), StoreVar("X", "REAL")]),
                         mode=F1)
        self.assertEqual(layout.store.read("X"), 16777216.0)
        # CallStd 返回
        layout, _ = _run(_task([LoadConst(-0.1, "REAL"),
                                CallStd("ABS", StdSig(("REAL",), "REAL")),
                                StoreVar("X", "REAL")]),
                         mode=F1, std={"ABS": abs})
        self.assertEqual(layout.store.read("X"), q(abs(q(-0.1))))
        # CallFunc 返回 + OUT / FB 输入输出
        fn = POUDefinition(
            name="Pass", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("I", "REAL", section="VAR_INPUT"),
                       VarDecl("O", "REAL", section="VAR_OUTPUT")],
            return_type="REAL",
            code=[LoadVar("I", "REAL"), StoreVar("O", "REAL"),
                  LoadVar("I", "REAL")])
        code = [CallFunc("Pass", (Binding("I", "IN", Const(0.1, "REAL"), "REAL"),
                                  Binding("O", "OUT", StoreKey("Y"), "REAL")),
                         "REAL"),
                StoreVar("X", "REAL")]
        layout, _ = _run(_task(code, pous=[fn]), mode=F1)
        self.assertEqual(layout.store.read("X"), q(0.1))
        self.assertEqual(layout.store.read("Y"), q(0.1))
        fb = POUDefinition(
            name="Echo", pou_kind="FUNCTION_BLOCK", language="ST",
            interface=[VarDecl("I", "REAL", section="VAR_INPUT"),
                       VarDecl("Q", "REAL", section="VAR_OUTPUT")],
            code=[LoadVar("I", "REAL"), StoreVar("Q", "REAL")])
        main = _prog("Main",
                     [CallFbInstance("E1", (
                         Binding("I", "IN", Const(0.1, "REAL"), "REAL"),
                         Binding("Q", "OUT", StoreKey("X"), "REAL"))) ],
                     instances=[InstanceDecl("E1", "Echo", kind="user_fb")])
        layout, _ = _run(_task(pous=[main, fb]), mode=F1)
        self.assertEqual(layout.store.read("X"), q(0.1))

    def test_intermediate_policy_candidates_differ(self):    # 要求 18
        # (30000+30000)/2：declared_width 中间回绕→-2768；native_width→30000
        code = [LoadConst(30000, "INT"), LoadConst(30000, "INT"),
                BinOp("ADD", "INT"), LoadConst(2, "INT"),
                BinOp("DIV", "INT"), StoreVar("N", "INT")]
        m_native = NumericMode(mode="fidelity_f1",
                               int_intermediate_policy="native_width")
        m_decl = NumericMode(mode="fidelity_f1",
                             int_intermediate_policy="declared_width")
        l1, _ = _run(_task(code), mode=m_native)
        l2, _ = _run(_task(code), mode=m_decl)
        self.assertEqual(l1.store.read("N"), 30000)
        self.assertEqual(l2.store.read("N"), -2768)
        self.assertNotEqual(l1.store.read("N"), l2.store.read("N"))

    def test_f2_rejected(self):                              # 要求 19
        with self.assertRaises(UnsupportedNumericModeError):
            NumericMode(mode="fidelity_f2")

    def test_unsupported_convert_rejected(self):             # 要求 20
        task = _task([LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")])
        layout = build_runtime_store(task)
        task.pou_lib["Main"].code = [LoadVar("Start", "BOOL"),
                                     Convert("BOOL", "INT"),
                                     StoreVar("N", "INT")]
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("尚未裁决/不支持", str(cm.exception))


# ---------------------------------------------------------------------------
# FUNCTION 调用（要求 21–31）
# ---------------------------------------------------------------------------

def _sub_fn():
    """Diff(A,B)=A-B：可暴露实参次序。"""
    return POUDefinition(
        name="Diff", pou_kind="FUNCTION", language="ST",
        interface=[VarDecl("A", "REAL", section="VAR_INPUT"),
                   VarDecl("B", "REAL", section="VAR_INPUT")],
        return_type="REAL",
        code=[LoadVar("A", "REAL"), LoadVar("B", "REAL"),
              BinOp("SUB", "REAL")])


class TestFunctionCalls(unittest.TestCase):
    def test_in_from_const_and_storekey(self):               # 要求 21、22、25
        fn = _sub_fn()
        code = [CallFunc("Diff", (Binding("A", "IN", Const(5.0, "REAL"), "REAL"),
                                  Binding("B", "IN", StoreKey("Y"), "REAL")),
                         "REAL"),
                StoreVar("X", "REAL")]
        layout, _ = _run(_task(code, pous=[fn]), store_setup={"Y": 2.0})
        self.assertEqual(layout.store.read("X"), 3.0)        # 返回值压回

    def test_stackslot_by_index_not_order(self):             # 要求 23、24
        fn = _sub_fn()
        # 栈：先压 1.0（底）再压 2.0（顶）；slot0=2.0、slot1=1.0
        bindings_a = (Binding("A", "IN", StackSlot(1), "REAL"),
                      Binding("B", "IN", StackSlot(0), "REAL"))
        bindings_b = tuple(reversed(bindings_a))             # 书写顺序调换
        for bindings in (bindings_a, bindings_b):
            code = [LoadConst(1.0, "REAL"), LoadConst(2.0, "REAL"),
                    CallFunc("Diff", bindings, "REAL"), StoreVar("X", "REAL")]
            layout, _ = _run(_task(code, pous=[fn]))
            self.assertEqual(layout.store.read("X"), -1.0)   # A=1.0,B=2.0 恒定
        # 消费数量正确：调用后栈只剩返回值（出口空栈由 StoreVar 后验证）

    def test_out_writes_back_storekey(self):                 # 要求 26
        fn = POUDefinition(
            name="Twice", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("I", "REAL", section="VAR_INPUT"),
                       VarDecl("O", "REAL", section="VAR_OUTPUT")],
            return_type="BOOL",
            code=[LoadVar("I", "REAL"), LoadConst(2.0, "REAL"),
                  BinOp("MUL", "REAL"), StoreVar("O", "REAL"),
                  LoadConst(True, "BOOL")])
        code = [CallFunc("Twice", (Binding("I", "IN", Const(3.0, "REAL"), "REAL"),
                                   Binding("O", "OUT", StoreKey("X"), "REAL")),
                         "BOOL"),
                StoreVar("Motor", "BOOL")]
        layout, _ = _run(_task(code, pous=[fn]))
        self.assertEqual(layout.store.read("X"), 6.0)

    def test_inout_true_alias(self):                         # 要求 27
        fn = POUDefinition(
            name="Inc", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("IO", "INT", section="VAR_IN_OUT")],
            return_type="BOOL",
            code=[LoadVar("IO", "INT"), LoadConst(1, "INT"),
                  BinOp("ADD", "INT"), StoreVar("IO", "INT"),
                  LoadConst(True, "BOOL")])
        code = [CallFunc("Inc", (Binding("IO", "INOUT", StoreKey("N"), "INT"),),
                         "BOOL"),
                StoreVar("Motor", "BOOL")]
        layout, _ = _run(_task(code, pous=[fn]), store_setup={"N": 10})
        self.assertEqual(layout.store.read("N"), 11)         # 写透调用方位置

    def test_function_var_reinitialized_each_call(self):     # 要求 28
        fn = POUDefinition(
            name="Cnt", pou_kind="FUNCTION", language="ST",
            locals=[VarDecl("c", "INT", initial=0)],
            return_type="INT",
            code=[LoadVar("c", "INT"), LoadConst(1, "INT"),
                  BinOp("ADD", "INT"), StoreVar("c", "INT"),
                  LoadVar("c", "INT")])
        code = [CallFunc("Cnt", (), "INT"), StoreVar("N", "INT"),
                CallFunc("Cnt", (), "INT"), StoreVar("M", "INT")]
        layout, _ = _run(_task(code, pous=[fn]))
        self.assertEqual(layout.store.read("N"), 1)
        self.assertEqual(layout.store.read("M"), 1)          # 无跨调用状态

    def test_nested_function_calls(self):                    # 要求 29
        inner = POUDefinition(
            name="Inner", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("I", "INT", section="VAR_INPUT")],
            return_type="INT",
            code=[LoadVar("I", "INT"), LoadConst(1, "INT"),
                  BinOp("ADD", "INT")])
        outer = POUDefinition(
            name="Outer", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("I", "INT", section="VAR_INPUT")],
            return_type="INT",
            code=[CallFunc("Inner", (Binding("I", "IN", StoreKey("I"), "INT"),),
                           "INT"),
                  LoadConst(10, "INT"), BinOp("MUL", "INT")])
        code = [CallFunc("Outer", (Binding("I", "IN", Const(4, "INT"), "INT"),),
                         "INT"), StoreVar("N", "INT")]
        layout, _ = _run(_task(code, pous=[inner, outer]))
        self.assertEqual(layout.store.read("N"), 50)         # (4+1)*10

    def test_out_inout_target_caller_frame(self):            # 要求 30
        inner = POUDefinition(
            name="Fill", pou_kind="FUNCTION", language="ST",
            interface=[VarDecl("O", "INT", section="VAR_OUTPUT"),
                       VarDecl("IO", "INT", section="VAR_IN_OUT")],
            return_type="BOOL",
            code=[LoadConst(7, "INT"), StoreVar("O", "INT"),
                  LoadVar("IO", "INT"), LoadConst(1, "INT"),
                  BinOp("ADD", "INT"), StoreVar("IO", "INT"),
                  LoadConst(True, "BOOL")])
        outer = POUDefinition(
            name="Outer", pou_kind="FUNCTION", language="ST",
            locals=[VarDecl("a", "INT", initial=0),
                    VarDecl("b", "INT", initial=100)],
            return_type="INT",
            code=[CallFunc("Fill", (Binding("O", "OUT", StoreKey("a"), "INT"),
                                    Binding("IO", "INOUT", StoreKey("b"), "INT")),
                           "BOOL"),
                  JmpIfFalse("skip"), Label("skip"),
                  LoadVar("a", "INT"), LoadVar("b", "INT"),
                  BinOp("ADD", "INT")])
        code = [CallFunc("Outer", (), "INT"), StoreVar("N", "INT")]
        layout, _ = _run(_task(code, pous=[inner, outer]))
        self.assertEqual(layout.store.read("N"), 7 + 101)    # 写进 Outer frame
        self.assertNotIn("a", layout.store)                  # 未误写全局 Store
        self.assertNotIn("b", layout.store)

    def test_frame_cleanup_after_callee_exception(self):     # 要求 31
        bad = POUDefinition(
            name="Bad", pou_kind="FUNCTION", language="ST",
            return_type="INT",
            code=[LoadConst(1, "INT"), LoadConst(0, "INT"),
                  BinOp("DIV", "INT")])
        code = [LoadVar("UseBad", "BOOL"), JmpIfFalse("ok"),
                CallFunc("Bad", (), "INT"), StoreVar("N", "INT"), Jmp("end"),
                Label("ok"), LoadConst(5, "INT"), StoreVar("N", "INT"),
                Label("end")]
        task = _task(code, pous=[bad])
        layout = build_runtime_store(task)
        layout.store.write("UseBad", True)
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertEqual(cm.exception.pou, "Bad")            # 上下文指向被调
        self.assertEqual(ex._active_frames, [])              # frame 已清理
        layout.store.write("UseBad", False)                  # 同一执行器再跑
        ex.execute_programs(layout.store.snapshot())
        self.assertEqual(layout.store.read("N"), 5)


# ---------------------------------------------------------------------------
# 用户 FB 调用（要求 32–40）
# ---------------------------------------------------------------------------

def _acc_fb():
    """Acc：VAR acc 跨调用累加 IN；VAR_TEMP t 每次清零后 +1 写 TQ。"""
    return POUDefinition(
        name="Acc", pou_kind="FUNCTION_BLOCK", language="ST",
        interface=[VarDecl("I", "INT", section="VAR_INPUT"),
                   VarDecl("Q", "INT", section="VAR_OUTPUT"),
                   VarDecl("TQ", "INT", section="VAR_OUTPUT"),
                   VarDecl("IO", "INT", section="VAR_IN_OUT")],
        locals=[VarDecl("acc", "INT", initial=0),
                VarDecl("t", "INT", section="VAR_TEMP")],
        code=[
            LoadVar("acc", "INT"), LoadVar("I", "INT"),
            BinOp("ADD", "INT"), StoreVar("acc", "INT"),
            LoadVar("acc", "INT"), StoreVar("Q", "INT"),
            LoadVar("t", "INT"), LoadConst(1, "INT"),
            BinOp("ADD", "INT"), StoreVar("t", "INT"),
            LoadVar("t", "INT"), StoreVar("TQ", "INT"),
            LoadVar("IO", "INT"), LoadConst(1, "INT"),
            BinOp("ADD", "INT"), StoreVar("IO", "INT"),
        ])


def _acc_call(inst, i_const):
    return CallFbInstance(inst, (
        Binding("I", "IN", Const(i_const, "INT"), "INT"),
        Binding("Q", "OUT", StoreKey("N"), "INT"),
        Binding("TQ", "OUT", StoreKey("M"), "INT"),
        Binding("IO", "INOUT", StoreKey("BigN"), "INT"),
    ))


class TestUserFbCalls(unittest.TestCase):
    def _task(self, main_code, instances):
        gvl = _gvl() + [VarDecl("BigN", "INT", section="VAR_GLOBAL")]
        main = _prog("Main", main_code, instances=instances)
        return _task(pous=[main, _acc_fb()], gvl=gvl)

    def test_fb_in_out_inout_var_and_temp(self):     # 要求 32、33、34、35、36
        task = self._task([_acc_call("A1", 5), _acc_call("A1", 5)],
                          [InstanceDecl("A1", "Acc", kind="user_fb")])
        layout, _ = _run(task)
        self.assertEqual(layout.store.read("N"), 10)   # VAR acc 跨调用保持:5+5
        self.assertEqual(layout.store.read("M"), 1)    # VAR_TEMP 每次清零:恒 1
        self.assertEqual(layout.store.read("BigN"), 2) # INOUT 别名写透两次
        self.assertEqual(
            layout.store.read(persistent_key("PLC_PRG.A1", "acc")), 10)

    def test_two_instances_isolated(self):                   # 要求 37
        task = self._task([_acc_call("A1", 5), _acc_call("A2", 3)],
                          [InstanceDecl("A1", "Acc", kind="user_fb"),
                           InstanceDecl("A2", "Acc", kind="user_fb")])
        layout, _ = _run(task)
        self.assertEqual(
            layout.store.read(persistent_key("PLC_PRG.A1", "acc")), 5)
        self.assertEqual(
            layout.store.read(persistent_key("PLC_PRG.A2", "acc")), 3)

    def test_nested_fb_paths(self):                          # 要求 38
        inner = POUDefinition(
            name="Leaf", pou_kind="FUNCTION_BLOCK", language="ST",
            interface=[VarDecl("I", "INT", section="VAR_INPUT"),
                       VarDecl("Q", "INT", section="VAR_OUTPUT")],
            locals=[VarDecl("s", "INT", initial=0)],
            code=[LoadVar("s", "INT"), LoadVar("I", "INT"),
                  BinOp("ADD", "INT"), StoreVar("s", "INT"),
                  LoadVar("s", "INT"), StoreVar("Q", "INT")])
        outer = POUDefinition(
            name="Wrap", pou_kind="FUNCTION_BLOCK", language="ST",
            interface=[VarDecl("I", "INT", section="VAR_INPUT"),
                       VarDecl("Q", "INT", section="VAR_OUTPUT")],
            locals=[VarDecl("q", "INT")],
            instances=[InstanceDecl("Sub", "Leaf", kind="user_fb")],
            code=[CallFbInstance("Sub", (
                      Binding("I", "IN", StoreKey("I"), "INT"),
                      Binding("Q", "OUT", StoreKey("q"), "INT"))),
                  LoadVar("q", "INT"), StoreVar("Q", "INT")])
        main = _prog("Main",
                     [CallFbInstance("W1", (
                         Binding("I", "IN", Const(4, "INT"), "INT"),
                         Binding("Q", "OUT", StoreKey("N"), "INT")))],
                     instances=[InstanceDecl("W1", "Wrap", kind="user_fb")])
        task = _task(pous=[main, outer, inner])
        layout, _ = _run(task)
        self.assertEqual(layout.store.read("N"), 4)
        self.assertEqual(
            layout.store.read(persistent_key("PLC_PRG.W1.Sub", "s")), 4)

    def test_call_creates_no_new_keys(self):                 # 要求 39
        task = self._task([_acc_call("A1", 5)],
                          [InstanceDecl("A1", "Acc", kind="user_fb")])
        layout = build_runtime_store(task)
        keys_before = set(layout.store.keys())
        ex = Executor(task, layout)
        ex.execute_programs(layout.store.snapshot())
        self.assertEqual(set(layout.store.keys()), keys_before)

    def test_unexpanded_instance_path_rejected(self):        # 要求 40
        task = self._task([_acc_call("A1", 5)],
                          [InstanceDecl("A1", "Acc", kind="user_fb")])
        layout = build_runtime_store(task)
        # 装载后篡改：调用不存在的实例路径
        task.pou_lib["Main"].code = [_acc_call("GHOST", 1)]
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("未由装载期展开", str(cm.exception))


# ---------------------------------------------------------------------------
# 注入边界（要求 41–46）
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """最小假 library adapter（正式 L2 RuntimeAdapter 属独立工作包）。"""

    def __init__(self, fail=False):
        self.pins = {"IN": False, "Q": False}
        self.steps = 0
        self.fail = fail

    def write_pin(self, pin, value):
        self.pins[pin] = value

    def read_pin(self, pin):
        return self.pins[pin]

    def step(self, dt_ms):
        if self.fail:
            raise RuntimeError("adapter 内部故障")
        self.steps += 1
        self.pins["Q"] = self.pins["IN"]


def _lib_task():
    main = _prog("Main",
                 [LoadVar("Start", "BOOL"), StoreVar("T1.IN", "BOOL"),
                  CallFb("T1"),
                  LoadVar("T1.Q", "BOOL"), StoreVar("Motor", "BOOL")],
                 instances=[InstanceDecl("T1", "TON", kind="library")])
    return _task(pous=[main])


class TestInjectionBoundaries(unittest.TestCase):
    def test_callstd_injection_and_type_check(self):         # 要求 41
        code = [LoadConst(-3, "INT"),
                CallStd("ABS", StdSig(("INT",), "INT")), StoreVar("N", "INT")]
        layout, _ = _run(_task(code), std={"ABS": abs})
        self.assertEqual(layout.store.read("N"), 3)
        # 返回类型不符 → 明确报错
        code2 = [LoadConst(-3, "INT"),
                 CallStd("ABS", StdSig(("INT",), "INT")), StoreVar("N", "INT")]
        with self.assertRaises(IRExecutionError) as cm:
            _run(_task(code2), std={"ABS": lambda v: float(abs(v))})
        self.assertIn("返回值", str(cm.exception))

    def test_missing_std_function(self):                     # 要求 42
        code = [LoadConst(-3, "INT"),
                CallStd("ABS", StdSig(("INT",), "INT")), StoreVar("N", "INT")]
        with self.assertRaises(MissingStdFunctionError):
            _run(_task(code))

    def test_callfb_delegation(self):                        # 要求 43
        adapter = _FakeAdapter()
        layout, _ = _run(_lib_task(), store_setup={"Start": True},
                         adapters={"PLC_PRG.T1": adapter})
        self.assertEqual(adapter.steps, 1)                   # 调用恰一次
        self.assertIs(layout.store.read("Motor"), True)      # 管脚读写经 adapter

    def test_missing_adapter(self):                          # 要求 44
        with self.assertRaises(MissingLibraryAdapterError):
            _run(_lib_task())

    def test_adapter_exception_wrapped_with_context(self):   # 要求 45
        with self.assertRaises(IRExecutionError) as cm:
            _run(_lib_task(), adapters={"PLC_PRG.T1": _FakeAdapter(fail=True)})
        self.assertIsInstance(cm.exception.cause, RuntimeError)
        self.assertEqual(cm.exception.pou, "Main")
        self.assertIn("PLC_PRG.T1", str(cm.exception))

    def test_no_prototype_dependency(self):                  # 要求 46
        import pathlib
        import subprocess
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        probe = ("import sys, src.runtime; "
                 "bad=[m for m in sys.modules if m.startswith('prototype_05')]; "
                 "sys.exit(1 if bad else 0)")
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=str(repo_root),
            env={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(repo_root)},
            capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         result.stderr.decode(errors="replace"))
        import src.runtime
        pkg = pathlib.Path(src.runtime.__file__).parent
        for py in pkg.glob("*.py"):
            for line in py.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith(("import ", "from ")):
                    self.assertNotIn("prototype_05", s, py.name)


# ---------------------------------------------------------------------------
# 装载后篡改的运行期防御（Codex Round 1 两条 + Round 2 两条最小反证固化）
# ---------------------------------------------------------------------------

def _fill_def():
    """Fill：向 OUT 形参 O 写 INT 7，返回 BOOL。"""
    return POUDefinition(
        name="Fill", pou_kind="FUNCTION", language="ST",
        interface=[VarDecl("O", "INT", section="VAR_OUTPUT")],
        return_type="BOOL",
        code=[LoadConst(7, "INT"), StoreVar("O", "INT"),
              LoadConst(True, "BOOL")])


def _outer_def(a_type, out_binding_type):
    """Outer：调用 Fill 把 OUT 写回自身 frame 局部 a；参数化以便装载后篡改。"""
    return POUDefinition(
        name="Outer", pou_kind="FUNCTION", language="ST",
        locals=[VarDecl("a", a_type)],
        return_type="INT",
        code=[CallFunc("Fill", (Binding("O", "OUT", StoreKey("a"),
                                        out_binding_type),), "BOOL"),
              JmpIfFalse("skip"), Label("skip"),
              LoadConst(0, "INT")])


def _addhalf_def(input_decl=None):
    """AddHalf(I: REAL): REAL := I + 0.5（Codex Round 2 反证①用）。"""
    return POUDefinition(
        name="AddHalf", pou_kind="FUNCTION", language="ST",
        interface=[input_decl or VarDecl("I", "REAL", section="VAR_INPUT")],
        return_type="REAL",
        code=[LoadVar("I", "REAL"), LoadConst(0.5, "REAL"),
              BinOp("ADD", "REAL")])


def _localhalf_def(a_decl):
    """LocalHalf(): REAL := a + 0.5，a 为局部 VAR（Codex Round 2 反证②用）。"""
    return POUDefinition(
        name="LocalHalf", pou_kind="FUNCTION", language="ST",
        locals=[a_decl], return_type="REAL",
        code=[LoadVar("a", "REAL"), LoadConst(0.5, "REAL"),
              BinOp("ADD", "REAL")])


class TestPostLoadTamperingDefenses(unittest.TestCase):
    """Codex Round 1/2 反证固化：装载校验通过后篡改 IR/声明，执行器须继续拦截。"""

    def test_tampered_callfunc_ret_type_rejected(self):
        # 反证①：篡改 CallFunc.ret_type（INT→REAL）——须抛带上下文的
        # IRExecutionError，而非漏出原始 StoreTypeError
        fn = POUDefinition(name="F", pou_kind="FUNCTION", language="ST",
                           return_type="INT", code=[LoadConst(1, "INT")])
        task = _task([CallFunc("F", (), "INT"), StoreVar("N", "INT")],
                     pous=[fn])
        layout = build_runtime_store(task)
        task.pou_lib["Main"].code = [CallFunc("F", (), "REAL"),
                                     StoreVar("X", "REAL")]    # 装载后篡改
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("ret_type", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Main")
        self.assertIsNotNone(cm.exception.pc)

    def test_tampered_caller_decl_out_writeback_rejected(self):
        # 反证②：篡改调用方 frame 局部 a 的声明类型（INT→REAL）——
        # OUT 写回不得被静默接受（Codex 复现时曾静默返回 OK）
        task = _task([CallFunc("Outer", (), "INT"), StoreVar("N", "INT")],
                     pous=[_fill_def(), _outer_def("INT", "INT")])
        layout = build_runtime_store(task)
        task.pou_lib["Outer"] = _outer_def("REAL", "INT")      # 装载后篡改声明
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("写回目标", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Outer")

    def test_cell_write_type_check_wraps_store_error(self):
        # WP-004 Round 2 曾锁定：声明与绑定一并篡改为 REAL 时由
        # _CellLoc.write 结构检查拦截并包装 StoreTypeError。WP-005 Round 2
        # 把 OUT 写回接入共享边界通道后，同一篡改在**更早**的写回原始值
        # 检查处被拦截（防御点按 Codex 意见前移：写回值先验原始结构，
        # 不以"来源应当受守"代替边界防御）；断言随之锁定新边界，
        # _CellLoc.write 的内层兜底在本测试末尾单独锁定
        task = _task([CallFunc("Outer", (), "INT"), StoreVar("N", "INT")],
                     pous=[_fill_def(), _outer_def("INT", "INT")])
        layout = build_runtime_store(task)
        task.pou_lib["Outer"] = _outer_def("REAL", "REAL")     # 装载后篡改
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("OUT 形参", str(cm.exception))
        self.assertIn("原始值", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Outer")
        self.assertIsNotNone(cm.exception.pc)
        # 内层兜底仍在：_CellLoc.write 对结构性错误值抛 StoreTypeError
        # （执行器路径现被外层边界先行拦截，故此处直接单元级锁定）
        from src.runtime.executor import _CellLoc
        cells = {"a": ["REAL", 0.0]}
        with self.assertRaises(StoreTypeError):
            _CellLoc(cells, "a").write(7)
        self.assertEqual(cells["a"][1], 0.0)                   # 未被写入

    def test_tampered_input_binding_seed_rejected(self):
        # Codex Round 2 反证①：装载后把 IN 绑定篡改为
        # Binding("I","IN",Const(1,"INT"),"REAL")——int 值 1 不得被静默
        # 播入 REAL frame cell（复现时曾静默算出 X=1.5）
        task = _task([CallFunc("AddHalf",
                               (Binding("I", "IN", Const(1.0, "REAL"),
                                        "REAL"),), "REAL"),
                      StoreVar("X", "REAL")],
                     pous=[_addhalf_def()])
        layout = build_runtime_store(task)
        task.pou_lib["Main"].code = [                          # 装载后篡改
            CallFunc("AddHalf",
                     (Binding("I", "IN", Const(1, "INT"), "REAL"),), "REAL"),
            StoreVar("X", "REAL")]
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        # WP-005 起，该篡改在**绑定求值边界**（早于 _seed_cell 播种兜底）
        # 即被拦截——Const 类型标签与绑定类型不一致；防御点前移是任务书
        # 要求（"必须在绑定求值或更早边界被拒绝"），本断言随之锁定新边界
        self.assertIn("Const 类型标签", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Main")
        self.assertIsNotNone(cm.exception.pc)
        self.assertEqual(layout.store.read("X"), 0.0)          # 未被静默写出

    def test_tampered_local_decl_seed_rejected(self):
        # Codex Round 2 反证②：装载后把 FUNCTION 局部声明篡改为
        # VarDecl("a","REAL", initial=1)——int 初值不得被静默播入
        # REAL frame cell（复现时曾静默算出 X=1.5）
        task = _task([CallFunc("LocalHalf", (), "REAL"),
                      StoreVar("X", "REAL")],
                     pous=[_localhalf_def(VarDecl("a", "REAL", initial=1.0))])
        layout = build_runtime_store(task)
        task.pou_lib["LocalHalf"] = _localhalf_def(            # 装载后篡改声明
            VarDecl("a", "REAL", initial=1))
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("播种", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Main")
        self.assertIsNotNone(cm.exception.pc)
        self.assertEqual(layout.store.read("X"), 0.0)          # 未被静默写出


# ---------------------------------------------------------------------------
# LoadPrev 边界补充
# ---------------------------------------------------------------------------

class TestLoadPrevBoundaries(unittest.TestCase):
    def test_prev_of_frame_var_rejected(self):
        fn = POUDefinition(
            name="F", pou_kind="FUNCTION", language="ST",
            locals=[VarDecl("v", "INT")], return_type="INT",
            code=[LoadConst(1, "INT")])
        code = [CallFunc("F", (), "INT"), StoreVar("N", "INT")]
        task = _task(code, pous=[fn])
        layout = build_runtime_store(task)
        # 装载后篡改 FUNCTION 体：LOAD_PREV frame 变量
        task.pou_lib["F"].code = [LoadPrev("v", "INT")]
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("没有上一拍语义", str(cm.exception))

    def test_prev_of_program_persistent_var(self):
        main = _prog("Main",
                     [LoadPrev("cnt", "INT"), LoadConst(1, "INT"),
                      BinOp("ADD", "INT"), StoreVar("cnt", "INT")],
                     locals_=[VarDecl("cnt", "INT", initial=0)])
        task = _task(pous=[main])
        layout = build_runtime_store(task)
        key = persistent_key("PLC_PRG", "cnt")
        ex = Executor(task, layout)
        prev = layout.store.snapshot()               # prev: cnt=0
        ex.execute_programs(prev)
        self.assertEqual(layout.store.read(key), 1)
        ex.execute_programs(prev)                    # 仍用旧快照 → 还是 0+1
        self.assertEqual(layout.store.read(key), 1)
        ex.execute_programs(layout.store.snapshot()) # 新快照 cnt=1 → 2
        self.assertEqual(layout.store.read(key), 2)


# ---------------------------------------------------------------------------
# WP-20260714-005：F1 量化前原始值结构校验与 TypedValue 边界防御
# ---------------------------------------------------------------------------

class TestRawValueBoundaryDefenses(unittest.TestCase):
    """Codex WP-004 Round 3 两条 F1 手工反证的固化 + 边界规则测试。

    规则：任何外部值/IR 常量/调用边界值必须在数值钩子（on_const/on_store）
    **之前**通过原始 Python 值结构检查；F1 量化不得把非法 int 洗成合法
    float。E 与 F1 共享同一结构检查（非 F1 特判）。
    """

    def _tampered_binding_task(self, const):
        task = _task([CallFunc("AddHalf",
                               (Binding("I", "IN", Const(1.0, "REAL"),
                                        "REAL"),), "REAL"),
                      StoreVar("X", "REAL")],
                     pous=[_addhalf_def()])
        layout = build_runtime_store(task)
        task.pou_lib["Main"].code = [                          # 装载后篡改
            CallFunc("AddHalf", (Binding("I", "IN", const, "REAL"),), "REAL"),
            StoreVar("X", "REAL")]
        return task, layout

    def test_f1_tampered_const_binding_rejected(self):         # 任务书测试 1
        # Codex 手工反证①固化：F1 下 Const(1,"INT")→REAL 形参
        task, layout = self._tampered_binding_task(Const(1, "INT"))
        ex = Executor(task, layout, numeric_mode=F1)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("Const 类型标签", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Main")
        self.assertIsNotNone(cm.exception.pc)
        self.assertEqual(layout.store.read("X"), 0.0)          # 未被写出/未洗白

    def test_f1_tampered_const_raw_value_rejected(self):
        # 变体：类型标签一致（REAL）但原始值是 int——原始值检查必须先于
        # on_const 拦截（此前 F1 会把 int 1 洗成 float 1.0 → X=1.5）
        task, layout = self._tampered_binding_task(Const(1, "REAL"))
        ex = Executor(task, layout, numeric_mode=F1)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("原始值", str(cm.exception))
        self.assertEqual(layout.store.read("X"), 0.0)

    def _bad_loadconst_task(self):
        fn = POUDefinition(name="BadReal", pou_kind="FUNCTION", language="ST",
                           return_type="REAL", code=[LoadConst(1.0, "REAL")])
        task = _task([CallFunc("BadReal", (), "REAL"),
                      LoadConst(0.5, "REAL"), BinOp("ADD", "REAL"),
                      StoreVar("X", "REAL")], pous=[fn])
        layout = build_runtime_store(task)
        task.pou_lib["BadReal"].code = [LoadConst(1, "REAL")]  # 装载后篡改
        return task, layout

    def test_f1_tampered_function_loadconst_rejected(self):    # 任务书测试 2
        # Codex 手工反证②固化：F1 下被调体 LoadConst(1,"REAL") 不得静默
        # 返回并参与运算得到 X=1.5
        task, layout = self._bad_loadconst_task()
        ex = Executor(task, layout, numeric_mode=F1)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("LOAD_CONST", str(cm.exception))
        self.assertEqual(cm.exception.pou, "BadReal")          # 上下文指向被调
        self.assertIsNotNone(cm.exception.pc)
        self.assertEqual(layout.store.read("X"), 0.0)          # X 保持默认

    def test_engineering_tampered_loadconst_rejected(self):    # 任务书测试 3
        # 同一非法 LoadConst 在 Engineering 下同样拒绝——非 F1 特判
        task, layout = self._bad_loadconst_task()
        ex = Executor(task, layout)                            # engineering
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("原始值", str(cm.exception))
        self.assertEqual(layout.store.read("X"), 0.0)

    def test_legal_loadconst_both_modes(self):                 # 任务书测试 4
        code = [LoadConst(1.0, "REAL"), StoreVar("X", "REAL")]
        for mode, expect in ((None, 1.0), (F1, quantize_real32(1.0))):
            layout, _ = _run(_task(code), mode=mode)
            self.assertEqual(layout.store.read("X"), expect)   # 修复不误伤合法路径

    def test_bool_not_accepted_as_int(self):                   # 任务书测试 5
        task = _task([LoadConst(1, "INT"), StoreVar("N", "INT")])
        layout = build_runtime_store(task)
        task.pou_lib["Main"].code = [LoadConst(True, "INT"),   # 装载后篡改
                                     StoreVar("N", "INT")]
        ex = Executor(task, layout)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("原始值", str(cm.exception))
        self.assertEqual(layout.store.read("N"), 0)

    def test_function_return_boundary_independent_defense(self):  # 任务书测试 6
        # 受控测试执行器子类：让被调体出口栈出现"REAL 标签、int 结构"的
        # TypedValue——返回边界必须在 on_store 之前拒绝（生产代码无后门）
        class _EvilExecutor(Executor):
            def _run(self, ctx):
                if ctx.pou.name == "GoodReal":
                    ctx.stack.append(TypedValue(1, "REAL"))    # 标签对、结构错
                    return
                super()._run(ctx)

        fn = POUDefinition(name="GoodReal", pou_kind="FUNCTION", language="ST",
                           return_type="REAL", code=[LoadConst(1.0, "REAL")])
        task = _task([CallFunc("GoodReal", (), "REAL"), StoreVar("X", "REAL")],
                     pous=[fn])
        layout = build_runtime_store(task)
        for mode in (None, F1):
            ex = _EvilExecutor(task, layout, numeric_mode=mode)
            with self.assertRaises(IRExecutionError) as cm:
                ex.execute_programs(layout.store.snapshot())
            self.assertIn("返回值", str(cm.exception))
            self.assertIn("原始值", str(cm.exception))
            self.assertEqual(cm.exception.pou, "Main")
            self.assertEqual(layout.store.read("X"), 0.0)      # F1 未洗白、未写出


# ---------------------------------------------------------------------------
# WP-20260714-005 Round 2：外部值/StoreKey/FB 拷入边界（Codex Round 1 反证固化）
# ---------------------------------------------------------------------------

class _IntPinAdapter:
    """管脚恒返回 Python int 的假 adapter——模拟外部值结构错误。"""

    def read_pin(self, pin):
        return 1                       # int；而 IR 声明该管脚为 REAL

    def write_pin(self, pin, value):
        pass

    def step(self, dt_ms):
        pass


class TestExternalAndStoreKeyBoundaryDefenses(unittest.TestCase):
    """Codex WP-005 Round 1 两条 F1 手工反证的固化 + FB 拷入边界。

    规则同 TestRawValueBoundaryDefenses：原始值结构检查先于任何数值钩子，
    E 与 F1 共享同一检查（非 F1 特判），目标不得被部分写入。
    """

    def test_adapter_pin_int_as_real_rejected(self):       # Codex 反证①固化
        # library adapter 管脚返回 int 1、IR 声明 REAL——即使 L2 未提供
        # 管脚声明类型（declared=None），LoadVar.type 已给出期望类型，
        # 必须在进入数值钩子/后续 StoreVar 之前拒绝（此前 F1 于 StoreVar
        # 的 on_store 把 int 洗成 float 静默写出 X=1.0）
        main = _prog("Main", [LoadVar("T1.Q", "REAL"), StoreVar("X", "REAL")],
                     instances=[InstanceDecl("T1", "TON", kind="library")])
        task = _task(pous=[main])
        for mode in (F1, None):                            # F1 与 E 同拒绝
            layout = build_runtime_store(task)
            ex = Executor(task, layout, numeric_mode=mode,
                          library_adapters={"PLC_PRG.T1": _IntPinAdapter()})
            with self.assertRaises(IRExecutionError) as cm:
                ex.execute_programs(layout.store.snapshot())
            self.assertIn("LOAD_VAR", str(cm.exception))
            self.assertIn("原始值", str(cm.exception))
            self.assertEqual(cm.exception.pou, "Main")
            self.assertIsNotNone(cm.exception.pc)
            self.assertEqual(layout.store.read("X"), 0.0)  # 未被洗白写出

    def _sink_task(self):
        """Sink.I: REAL <- StoreKey("X")（合法）；供装载后篡改。"""
        sink = POUDefinition(
            name="Sink", pou_kind="FUNCTION_BLOCK", language="ST",
            interface=[VarDecl("I", "REAL", section="VAR_INPUT")],
            locals=[VarDecl("t", "REAL", section="VAR_TEMP")],
            code=[LoadVar("I", "REAL"), StoreVar("t", "REAL")])
        main = _prog("Main",
                     [CallFbInstance("U", (
                         Binding("I", "IN", StoreKey("X"), "REAL"),))],
                     instances=[InstanceDecl("U", "Sink", kind="user_fb")])
        task = _task(pous=[main, sink])
        layout = build_runtime_store(task)
        return task, layout

    def test_tampered_storekey_int_location_rejected(self):  # Codex 反证②固化
        # 装载后把 IN×StoreKey 从 REAL 位置 X 篡改为 INT 位置 N（绑定仍
        # 声明 REAL）——此前 F1 把 int 0 经 on_store 洗成 0.0 静默写入
        # PLC_PRG.U.I；现须在绑定求值边界拒绝
        task, layout = self._sink_task()
        task.pou_lib["Main"].code = [CallFbInstance("U", (
            Binding("I", "IN", StoreKey("N"), "REAL"),))]   # 装载后篡改
        ex = Executor(task, layout, numeric_mode=F1)
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("StoreKey", str(cm.exception))
        self.assertIn("声明类型", str(cm.exception))
        self.assertEqual(cm.exception.pou, "Main")
        self.assertIsNotNone(cm.exception.pc)
        self.assertEqual(                                   # FB 持久 VAR_INPUT
            layout.store.read(persistent_key("PLC_PRG.U", "I")), 0.0)

    def test_tampered_fb_binding_type_rejected_at_copy_in(self):
        # 变体：绑定整体篡改为 Const(1,"INT")×"INT"（Const 分支自洽、能
        # 通过绑定求值）——FB 拷入边界必须再核对持久键声明类型 REAL 与
        # 绑定类型 INT 不一致并拒绝，目标不得被部分写入
        task, layout = self._sink_task()
        task.pou_lib["Main"].code = [CallFbInstance("U", (
            Binding("I", "IN", Const(1, "INT"), "INT"),))]  # 装载后篡改
        for mode in (F1, None):
            ex = Executor(task, layout, numeric_mode=mode)
            with self.assertRaises(IRExecutionError) as cm:
                ex.execute_programs(layout.store.snapshot())
            self.assertIn("FB IN 形参", str(cm.exception))
            self.assertEqual(cm.exception.pou, "Main")
            self.assertIsNotNone(cm.exception.pc)
            self.assertEqual(
                layout.store.read(persistent_key("PLC_PRG.U", "I")), 0.0)


class _RealPinAdapter:
    """管脚恒返回给定 float 的假 adapter——模拟库块 REAL 输出（float64 精度）。"""

    def __init__(self, q):
        self.q = q

    def read_pin(self, pin):
        return self.q

    def write_pin(self, pin, value):
        pass

    def step(self, dt_ms):
        pass


class TestLibraryPinRealQuantization(unittest.TestCase):
    """Codex WP-005 Round 2 必须返修固化：库块管脚 REAL 输出回收是
    IR_SPEC §5.3 边界 5 的量化边界——F1 下管脚值先量化到 binary32 再进入
    IR 世界，不得让未量化的 float64 直接参与后续 BINOP。

    F1 量化是当前候选行为（不承诺 bit-exact），本测试只锁定当前实现，
    不构成与 CODESYS PLC 语义一致的证据。
    """

    PIN_Q = 3444218515.250481         # Codex Round 2 复现值
    CONST = 2579544029.4030247

    def _pin_add_task(self):
        # CallFb 后回收 T1.Q，与 REAL 常量相加写 X（LoadVar 经 _PinLoc；
        # INOUT 别名指向管脚的读取落入同一 LoadVar 分支，共享本防御）
        main = _prog("Main",
                     [CallFb("T1"),
                      LoadVar("T1.Q", "REAL"),
                      LoadConst(self.CONST, "REAL"),
                      BinOp("ADD", "REAL"),
                      StoreVar("X", "REAL")],
                     instances=[InstanceDecl("T1", "TON", kind="library")])
        return _task(pous=[main])

    def test_pin_real_quantized_before_binop_f1(self):
        # 最小数值差异用例（Codex 非阻塞建议采纳，直接锁死双重舍入差异）：
        # 正确 = quantize(quantize(Q) + quantize(C)) = 6023762944.0；
        # 回退形态 = quantize(Q + quantize(C)) = 6023762432.0（管脚值未在
        # 回收边界量化、以 float64 直接参与 ADD）
        layout, _ = _run(self._pin_add_task(), mode=F1,
                         adapters={"PLC_PRG.T1": _RealPinAdapter(self.PIN_Q)})
        x = layout.store.read("X")
        self.assertEqual(x, 6023762944.0)
        self.assertNotEqual(x, 6023762432.0)          # 锁死回退形态
        self.assertEqual(x, quantize_real32(
            quantize_real32(self.PIN_Q) + quantize_real32(self.CONST)))

    def test_pin_real_not_quantized_engineering(self):
        # E 模式无量化：管脚 float64 原样参与运算——回收钩子是 F1 专属
        # 行为，不得误伤 E 模式（结构检查两模式仍共享）
        layout, _ = _run(self._pin_add_task(),
                         adapters={"PLC_PRG.T1": _RealPinAdapter(self.PIN_Q)})
        self.assertEqual(layout.store.read("X"), self.PIN_Q + self.CONST)

    def test_pin_recover_still_rejects_structure_error_f1(self):
        # 量化钩子不得洗白结构错误：F1 下 adapter 返回 int、IR 声明 REAL
        # 仍须在回收边界拒绝（结构检查先于量化，与既有反证同口径），
        # X 不得被部分写入
        main = _prog("Main", [LoadVar("T1.Q", "REAL"), StoreVar("X", "REAL")],
                     instances=[InstanceDecl("T1", "TON", kind="library")])
        task = _task(pous=[main])
        layout = build_runtime_store(task)
        ex = Executor(task, layout, numeric_mode=F1,
                      library_adapters={"PLC_PRG.T1": _IntPinAdapter()})
        with self.assertRaises(IRExecutionError) as cm:
            ex.execute_programs(layout.store.snapshot())
        self.assertIn("LOAD_VAR", str(cm.exception))
        self.assertIn("原始值", str(cm.exception))
        self.assertEqual(layout.store.read("X"), 0.0)


if __name__ == "__main__":
    unittest.main()
