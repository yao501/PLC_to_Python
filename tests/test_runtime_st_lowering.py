"""Stage 3 strict ST assignment-subset lowering tests (WP-20260810-096)."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import src.runtime.st_lowering as st_lowering_module
from src.runtime.descriptors import BlockSchema, Pin, build_default_registry
from src.runtime.descriptors import UnknownBlockError
from src.runtime.descriptors.business_basic import APCHSACCUM_SCHEMA
from src.runtime.ir import (
    BinOp, Binding, CallFb, CallFbInstance, CallFunc, CallStd, Const, Convert,
    InstanceDecl, Jmp,
    JmpIfFalse, Label, LoadConst, LoadPrev, LoadVar, POUDefinition,
    ProgramInstance,
    StackSlot, StdSig, StoreKey, StoreVar, Task, UnOp, VarDecl,
)
from src.runtime.parameters import build_runtime
from src.runtime.executor import IRExecutionError
from src.runtime.st_lowering import (
    STCompileError, STPOUCompileResult, compile_st_function, compile_st_function_block,
    compile_st_task,
)
from src.runtime.st_library_bindings import library_source_aliases, primitive_source_aliases


class _CatalogueProbe(BaseException):
    pass


class _HostilePou:
    calls = 0

    def __getattribute__(self, _name):
        type(self).calls += 1
        raise _CatalogueProbe("POU_FIELD_OBSERVED")


class _HostileField:
    """A scalar whose comparison and hash hooks raise a ``BaseException``.

    A fixed catalogue must classify such a field by exact identity type without
    ever invoking ``__eq__`` / ``__ne__`` / ``__hash__`` (which ``==`` / ``in`` /
    frozenset membership would trigger and leak the ``BaseException``).
    """

    def __eq__(self, _other):
        raise _CatalogueProbe("FIELD_COMPARED")

    __ne__ = __eq__

    def __hash__(self):
        raise _CatalogueProbe("FIELD_HASHED")


class _HostileBlockSchemaGetattribute:
    """Counter for temporary class-level ``BlockSchema.__getattribute__``.

    A catalogue validator must consume the frozen dataclass shell through
    ``object.__getattribute__`` only; ordinary instance reads are observable.
    """

    reads = 0


class _EqualStr(str):
    """A value-equal string subclass that library Schema fields must reject."""

    pass


class _PseudoPin:
    """A Pin-shaped object that must not cross the library catalogue boundary."""

    def __init__(self, pin):
        self.name = pin.name
        self.iec_type = pin.iec_type
        self.kind = pin.kind
        self.default = pin.default
        self.omit_policy = pin.omit_policy


class _HostileAliasCatalogue:
    """A non-dict alias carrier whose iteration/comparison/hash hooks must
    never run.

    Codex's WP-125 Round 1 counter-example returned such a carrier from
    ``library_source_aliases``; the previous ``_prepare_library_blocks`` called
    ``.items()`` on it before any exact-shell gate and leaked the raised
    ``BaseException``.  The fixed helper must reject it with an identity-only
    ``type`` check, so none of these hooks is ever observed.
    """

    items_calls = 0
    hook_calls = 0

    def items(self):
        type(self).items_calls += 1
        raise _CatalogueProbe("ALIASES_ITEMS_OBSERVED")

    def keys(self):
        type(self).hook_calls += 1
        raise _CatalogueProbe("ALIASES_KEYS_OBSERVED")

    def __iter__(self):
        type(self).hook_calls += 1
        raise _CatalogueProbe("ALIASES_ITER_OBSERVED")

    def __eq__(self, _other):
        type(self).hook_calls += 1
        raise _CatalogueProbe("ALIASES_COMPARED")

    __ne__ = __eq__

    def __hash__(self):
        type(self).hook_calls += 1
        raise _CatalogueProbe("ALIASES_HASHED")


class _HostileBlockKey:
    """A genuine-dict block key whose comparison hook must never run.

    It hashes to a constant so it can be stored, keeping the outer carrier an
    exact ``dict``; the fixed loop must reject its non-``str`` shape before any
    ``registry.resolve`` hash/compare touches it.
    """

    eq_calls = 0

    def __hash__(self):
        return 0

    def __eq__(self, _other):
        type(self).eq_calls += 1
        raise _CatalogueProbe("BLOCK_KEY_COMPARED")

    __ne__ = __eq__


class _HostileSchemaShell:
    """A resolved-Schema shell whose pin attributes are observed hooks.

    ``_prepare_library_blocks`` reads ``schema.inputs`` immediately after
    ``registry.resolve``; before the WP-141 Round 1 §4 fix a shell whose
    ``inputs`` property fired a custom ``BaseException`` leaked it past the
    catalogue contract.  The exact ``type(schema) is BlockSchema`` gate must
    reject the shell before any of these pin hooks is ever observed.
    """

    pin_reads = 0

    @property
    def inputs(self):
        type(self).pin_reads += 1
        raise _CatalogueProbe("SCHEMA_INPUTS_OBSERVED")

    @property
    def inouts(self):
        type(self).pin_reads += 1
        raise _CatalogueProbe("SCHEMA_INOUTS_OBSERVED")

    @property
    def outputs(self):
        type(self).pin_reads += 1
        raise _CatalogueProbe("SCHEMA_OUTPUTS_OBSERVED")


def _run(assembly, writes):
    trace = []
    previous = assembly.store.snapshot()
    for values in writes:
        for key in sorted(values):
            assembly.store.write(key, values[key])
        assembly.executor.execute_programs(previous)
        previous = assembly.store.snapshot()
        trace.append(previous.as_dict())
    return trace


class STLoweringPositiveTests(unittest.TestCase):
    def test_declarations_and_expression_code_are_exact(self):
        result = compile_st_task("""
            VAR_GLOBAL A:INT:=1; B:INT; Flag:BOOL; END_VAR
            VAR Temp:INT:=2; END_VAR
            B := A + Temp * 2;
            Flag := B > 3;
        """)
        self.assertEqual(result.code, (
            LoadVar("A", "INT"), LoadVar("TEMP", "INT"), LoadConst(2, "INT"),
            BinOp("MUL", "INT"), BinOp("ADD", "INT"), StoreVar("B", "INT"),
            LoadVar("B", "INT"), LoadConst(3, "INT"), BinOp("GT", "INT"),
            StoreVar("FLAG", "BOOL"),
            Label("__ST_RETURN_EPILOGUE"),
        ))
        self.assertIs(result.task.pou_lib["PLC_PRG"].source, result.unit)
        self.assertEqual([(item.name, item.initial) for item in result.task.gvl],
                         [("A", 1), ("B", None), ("FLAG", None)])
        self.assertEqual(result.task.pou_lib["PLC_PRG"].locals[0].initial, 2)

    def test_compile_build_runtime_and_manual_ir_are_multicycle_equivalent(self):
        source = """
            VAR_GLOBAL Input:INT; Output:INT; Positive:BOOL; END_VAR
            Output := Input * 2 + 1;
            Positive := Output > 0;
        """
        compiled = compile_st_task(source)
        manual_pou = POUDefinition(
            name="PLC_PRG", pou_kind="PROGRAM", language="ST",
            code=[
                LoadVar("INPUT", "INT"), LoadConst(2, "INT"), BinOp("MUL", "INT"),
                LoadConst(1, "INT"), BinOp("ADD", "INT"), StoreVar("OUTPUT", "INT"),
                LoadVar("OUTPUT", "INT"), LoadConst(0, "INT"), BinOp("GT", "INT"),
                StoreVar("POSITIVE", "BOOL"),
            ])
        manual = Task(
            programs=[ProgramInstance("PLC_PRG", "PLC_PRG")],
            gvl=[VarDecl("INPUT", "INT", section="VAR_GLOBAL"),
                 VarDecl("OUTPUT", "INT", section="VAR_GLOBAL"),
                 VarDecl("POSITIVE", "BOOL", section="VAR_GLOBAL")],
            pou_lib={"PLC_PRG": manual_pou})
        registry = build_default_registry()
        writes = ({"INPUT": -2}, {"INPUT": 0}, {"INPUT": 3})
        left = _run(build_runtime(compiled.task, registry), writes)
        right = _run(build_runtime(manual, registry), writes)
        self.assertEqual(
            [(row["OUTPUT"], row["POSITIVE"]) for row in left],
            [(-3, False), (1, True), (7, True)])
        self.assertEqual(left, right)

    def test_two_runtimes_from_one_task_are_isolated(self):
        task = compile_st_task(
            "VAR_GLOBAL I:INT; O:INT; END_VAR O:=I+1;").task
        registry = build_default_registry()
        left = build_runtime(task, registry)
        right = build_runtime(task, registry)
        self.assertIsNot(left.store, right.store)
        self.assertIsNot(left.executor, right.executor)
        self.assertEqual(_run(left, ({"I": 1}, {"I": 2}))[-1]["O"], 3)
        self.assertEqual(_run(right, ({"I": 10},))[0]["O"], 11)

    def test_bool_logic_negation_and_real_context(self):
        result = compile_st_task("""
            VAR_GLOBAL A:BOOL; B:BOOL; Q:BOOL; X:REAL; Y:REAL; END_VAR
            Q := NOT A OR B;
            Y := -X + 1.5;
        """)
        self.assertIn(UnOp("NOT", "BOOL"), result.code)
        self.assertIn(UnOp("NEG", "REAL"), result.code)
        self.assertIn(LoadConst(1.5, "REAL"), result.code)

    def test_persistent_local_shadow_and_temp_reset_follow_existing_runtime(self):
        task = compile_st_task("""
            VAR_GLOBAL X:INT:=10; O:INT; END_VAR
            VAR X:INT:=-1; Count:INT:=5; END_VAR
            VAR_TEMP Temp:INT; END_VAR
            Count:=Count+1; Temp:=Temp+1; O:=X+Count+Temp;
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({}, {}, {}))
        self.assertEqual([row["O"] for row in trace], [6, 7, 8])
        self.assertEqual([row["PLC_PRG.COUNT"] for row in trace], [6, 7, 8])
        self.assertEqual(runtime.store.read("X"), 10)

    def test_if_elsif_else_lowers_to_deterministic_validated_labels(self):
        result = compile_st_task("""
            VAR_GLOBAL A:INT; O:INT; END_VAR
            IF A < 0 THEN O:=-1;
            ELSIF A = 0 THEN O:=0;
            ELSE O:=1; END_IF;
        """)
        labels = [item.id for item in result.code if isinstance(item, Label)]
        self.assertEqual(labels, [
            "__ST_0002_IF_NEXT", "__ST_0003_IF_NEXT", "__ST_0001_IF_END",
            "__ST_RETURN_EPILOGUE"])
        self.assertEqual(sum(isinstance(item, JmpIfFalse) for item in result.code), 2)
        self.assertEqual(sum(isinstance(item, Jmp) for item in result.code), 2)
        self.assertEqual(result.code, (
            LoadVar("A", "INT"), LoadConst(0, "INT"), BinOp("LT", "INT"),
            JmpIfFalse("__ST_0002_IF_NEXT"),
            LoadConst(1, "INT"), UnOp("NEG", "INT"), StoreVar("O", "INT"),
            Jmp("__ST_0001_IF_END"), Label("__ST_0002_IF_NEXT"),
            LoadVar("A", "INT"), LoadConst(0, "INT"), BinOp("EQ", "INT"),
            JmpIfFalse("__ST_0003_IF_NEXT"),
            LoadConst(0, "INT"), StoreVar("O", "INT"),
            Jmp("__ST_0001_IF_END"), Label("__ST_0003_IF_NEXT"),
            LoadConst(1, "INT"), StoreVar("O", "INT"),
            Label("__ST_0001_IF_END"),
            Label("__ST_RETURN_EPILOGUE"),
        ))

    def test_if_runtime_paths_nested_and_no_else_persistence(self):
        task = compile_st_task("""
            VAR_GLOBAL A:INT; O:INT:=9; END_VAR
            IF A < 0 THEN O:=-1;
            ELSIF A = 0 THEN O:=0;
            ELSIF A > 10 THEN IF A > 20 THEN O:=3; ELSE O:=2; END_IF;
            ELSE O:=1; END_IF;
            IF A = 99 THEN O:=99; END_IF;
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({"A": -1}, {"A": 0}, {"A": 5},
                               {"A": 15}, {"A": 25}, {"A": 99}))
        self.assertEqual([row["O"] for row in trace], [-1, 0, 1, 2, 3, 99])

    def test_time_and_string_literals_decode_to_exact_engineering_values(self):
        result = compile_st_task("""
            VAR_GLOBAL Delay:TIME:=T#1D2H3M4S5MS;
            Maximum:TIME:=T#49D17H2M47S295MS;
            Text:STRING:='A$'B$$$41$L$N$P$R$Té'; END_VAR
        """)
        self.assertEqual(
            [(item.name, item.initial) for item in result.task.gvl],
            [("DELAY", 93_784_005), ("MAXIMUM", 4_294_967_295),
             ("TEXT", "A'B$A\n\n\f\r\té")])

    def test_time_string_assignment_comparison_and_if_execute_multicycle(self):
        task = compile_st_task("""
            VAR_GLOBAL Elapsed:TIME; Ready:BOOL; Name:STRING;
            Match:BOOL; State:STRING:='idle'; END_VAR
            Elapsed:=T#100S12MS;
            Ready:=Elapsed>=T#1M;
            Name:='RUN'; Match:=Name='RUN';
            IF Ready AND Match THEN State:='active'; ELSE State:='fault'; END_IF;
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({}, {}))
        self.assertEqual(
            [(row["ELAPSED"], row["READY"], row["NAME"], row["MATCH"],
              row["STATE"]) for row in trace],
            [(100_012, True, "RUN", True, "active")] * 2)

    def test_case_lists_ranges_else_and_nested_paths_execute(self):
        task = compile_st_task("""
            VAR_GLOBAL Mode:INT; Sub:INT; Out:INT:=99; END_VAR
            CASE Mode OF
              -2, 0: Out:=1;
              1..3: IF Sub=1 THEN Out:=2; ELSE Out:=3; END_IF;
              4: CASE Sub OF 1:Out:=4; ELSE Out:=5; END_CASE
              8: Out:=8;
              ELSE Out:=9;
            END_CASE
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, (
            {"MODE": -2, "SUB": 0}, {"MODE": 0},
            {"MODE": 2, "SUB": 1}, {"MODE": 3, "SUB": 0},
            {"MODE": 4, "SUB": 1}, {"MODE": 4, "SUB": 2},
            {"MODE": 8}, {"MODE": 7},
        ))
        self.assertEqual([row["OUT"] for row in trace], [1, 1, 2, 3, 4, 5, 8, 9])

    def test_case_generated_ir_is_deterministic_and_validated(self):
        source = "VAR_GLOBAL M:INT; O:INT; END_VAR CASE M OF 1,3:O:=1; 5..7:O:=2; ELSE O:=0; END_CASE"
        left = compile_st_task(source)
        right = compile_st_task(source)
        self.assertEqual(left.code, right.code)
        self.assertEqual(sum(isinstance(item, JmpIfFalse) for item in left.code), 3)
        self.assertIn(BinOp("AND", "BOOL"), left.code)
        self.assertEqual(len({item.id for item in left.code if isinstance(item, Label)}),
                         sum(isinstance(item, Label) for item in left.code))

    def test_case_runtime_matches_independent_handwritten_ir(self):
        compiled = compile_st_task("""
            VAR_GLOBAL M:INT; O:INT; END_VAR
            CASE M OF 1,3:O:=10; 5..7:O:=20; ELSE O:=0; END_CASE
        """).task
        manual_code = [
            LoadVar("M", "INT"), LoadConst(1, "INT"), BinOp("EQ", "INT"),
            JmpIfFalse("L1"), LoadConst(10, "INT"), StoreVar("O", "INT"),
            Jmp("END"), Label("L1"),
            LoadVar("M", "INT"), LoadConst(3, "INT"), BinOp("EQ", "INT"),
            JmpIfFalse("L2"), LoadConst(10, "INT"), StoreVar("O", "INT"),
            Jmp("END"), Label("L2"),
            LoadVar("M", "INT"), LoadConst(5, "INT"), BinOp("GE", "INT"),
            LoadVar("M", "INT"), LoadConst(7, "INT"), BinOp("LE", "INT"),
            BinOp("AND", "BOOL"), JmpIfFalse("ELSE"),
            LoadConst(20, "INT"), StoreVar("O", "INT"), Jmp("END"),
            Label("ELSE"), LoadConst(0, "INT"), StoreVar("O", "INT"),
            Label("END"),
        ]
        manual = Task(
            programs=[ProgramInstance("PLC_PRG", "PLC_PRG")],
            gvl=[VarDecl("M", "INT", section="VAR_GLOBAL"),
                 VarDecl("O", "INT", section="VAR_GLOBAL")],
            pou_lib={"PLC_PRG": POUDefinition(
                name="PLC_PRG", pou_kind="PROGRAM", language="ST",
                code=manual_code)})
        writes = tuple({"M": value} for value in (-3, 0, 1, 3, 5, 6, 7, 8))
        registry = build_default_registry()
        left = _run(build_runtime(compiled, registry), writes)
        right = _run(build_runtime(manual, registry), writes)
        self.assertEqual([row["O"] for row in left], [0, 0, 10, 10, 20, 20, 20, 0])
        self.assertEqual(left, right)

    def test_for_positive_negative_default_and_zero_trip_execute(self):
        task = compile_st_task("""
            VAR_GLOBAL I:INT; J:INT; Sum:INT; Down:INT; Empty:INT:=7; END_VAR
            FOR I:=1 TO 4 DO Sum:=Sum+I; END_FOR;
            FOR J:=5 TO 1 BY -2 DO Down:=Down+J; END_FOR;
            FOR I:=3 TO 1 DO Empty:=0; END_FOR;
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({}, {}))
        self.assertEqual(
            [(row["SUM"], row["DOWN"], row["EMPTY"]) for row in trace],
            [(10, 9, 7), (20, 18, 7)])

    def test_for_runtime_matches_independent_handwritten_ir(self):
        compiled = compile_st_task("""
            VAR_GLOBAL I:INT; Sum:INT; END_VAR
            FOR I:=1 TO 3 DO Sum:=Sum+I; END_FOR;
        """).task
        manual = Task(
            programs=[ProgramInstance("PLC_PRG", "PLC_PRG")],
            gvl=[VarDecl("I", "INT", section="VAR_GLOBAL"),
                 VarDecl("SUM", "INT", section="VAR_GLOBAL")],
            pou_lib={"PLC_PRG": POUDefinition(
                name="PLC_PRG", pou_kind="PROGRAM", language="ST", code=[
                    LoadConst(1, "INT"), StoreVar("I", "INT"), Label("CHECK"),
                    LoadVar("I", "INT"), LoadConst(3, "INT"), BinOp("LE", "INT"),
                    JmpIfFalse("END"), LoadVar("SUM", "INT"),
                    LoadVar("I", "INT"), BinOp("ADD", "INT"),
                    StoreVar("SUM", "INT"), LoadVar("I", "INT"),
                    LoadConst(1, "INT"), BinOp("ADD", "INT"),
                    StoreVar("I", "INT"), Jmp("CHECK"), Label("END"),
                ])})
        registry = build_default_registry()
        left = _run(build_runtime(compiled, registry), ({}, {}, {}))
        right = _run(build_runtime(manual, registry), ({}, {}, {}))
        self.assertEqual([row["SUM"] for row in left], [6, 12, 18])
        self.assertEqual(left, right)

    def test_while_continue_exit_zero_trip_and_multicycle_execute(self):
        task = compile_st_task("""
            VAR_GLOBAL Enabled:BOOL; I:INT; Sum:INT; END_VAR
            I:=0;
            WHILE Enabled AND I<5 DO
              I:=I+1;
              IF I=2 THEN CONTINUE; END_IF;
              IF I=4 THEN EXIT; END_IF;
              Sum:=Sum+I;
            END_WHILE;
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({"ENABLED": False}, {"ENABLED": True},
                               {"ENABLED": True}))
        self.assertEqual([(row["I"], row["SUM"]) for row in trace],
                         [(0, 0), (4, 4), (4, 8)])

    def test_return_uses_one_function_epilogue_after_nested_loop_control_flow(self):
        function = compile_st_function("""
            VAR_INPUT X:INT; END_VAR
            VAR I:INT; END_VAR
            FOR I:=1 TO 3 DO
              IF I=X THEN RETURN; END_IF;
              Pick:=I;
            END_FOR;
            Pick:=99;
        """, "Pick", "INT")
        labels = [instruction.id for instruction in function.code
                  if isinstance(instruction, Label)]
        self.assertEqual(labels.count("__ST_RETURN_EPILOGUE"), 1)
        epilogue = next(
            index for index, instruction in enumerate(function.code)
            if instruction == Label("__ST_RETURN_EPILOGUE"))
        self.assertEqual(function.code[epilogue + 1], LoadVar("PICK", "INT"))
        main = POUDefinition(
            name="MAIN", pou_kind="PROGRAM", language="ST", code=[
                CallFunc("PICK", (Binding("X", "IN", StoreKey("X"), "INT"),),
                         "INT"),
                StoreVar("O", "INT"),
                LoadConst(1, "INT"), StoreVar("AFTER", "INT"),
            ])
        task = Task(
            programs=[ProgramInstance("MAIN", "MAIN")],
            gvl=[VarDecl("X", "INT", section="VAR_GLOBAL"),
                 VarDecl("O", "INT", section="VAR_GLOBAL"),
                 VarDecl("AFTER", "INT", section="VAR_GLOBAL")],
            pou_lib={"MAIN": main, "PICK": function.pou})
        row = _run(build_runtime(task, build_default_registry()), ({"X": 2},))[0]
        self.assertEqual((row["O"], row["AFTER"]), (1, 1))

    def test_return_exits_nested_fb_and_program_without_exiting_its_caller(self):
        fb = compile_st_function_block("""
            VAR_INPUT Trigger:BOOL; END_VAR
            VAR_OUTPUT Q:INT; END_VAR
            VAR State:INT; END_VAR
            IF Trigger THEN
              CASE State OF
                0: WHILE State<2 DO State:=State+1; RETURN; END_WHILE;
                ELSE RETURN;
              END_CASE
            END_IF;
            State:=State+10; Q:=State;
        """, "Early")
        task = compile_st_task("""
            VAR_GLOBAL Trigger:BOOL; Fbo:INT; CallerAfter:INT; ProgramAfter:INT;
            END_VAR
            VAR B:Early; END_VAR
            B(Trigger:=Trigger,Q=>Fbo);
            CallerAfter:=Fbo+1;
            RETURN;
            ProgramAfter:=999;
        """, function_blocks=(fb,)).task
        for pou in (fb.pou, task.pou_lib["PLC_PRG"]):
            self.assertEqual(
                sum(instruction == Label("__ST_RETURN_EPILOGUE")
                    for instruction in pou.code), 1)
            self.assertGreaterEqual(
                sum(instruction == Jmp("__ST_RETURN_EPILOGUE")
                    for instruction in pou.code), 1)
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({"TRIGGER": True}, {"TRIGGER": False}))
        self.assertEqual(
            [(row["PLC_PRG.B.STATE"], row["FBO"], row["CALLERAFTER"],
              row["PROGRAMAFTER"]) for row in trace],
            [(1, 0, 1, 0), (11, 11, 12, 0)])

    def test_for_continue_targets_increment_and_nested_exit_is_innermost(self):
        task = compile_st_task("""
            VAR_GLOBAL I:INT; J:INT; Sum:INT; Inner:INT; END_VAR
            FOR I:=1 TO 4 DO
              IF I=2 THEN CONTINUE; END_IF;
              Sum:=Sum+I;
              FOR J:=1 TO 3 DO
                IF J=2 THEN EXIT; END_IF;
                Inner:=Inner+1;
              END_FOR;
            END_FOR;
        """).task
        row = _run(build_runtime(task, build_default_registry()), ({},))[0]
        self.assertEqual((row["SUM"], row["INNER"], row["I"]), (8, 3, 5))

    def test_while_runtime_matches_independent_handwritten_ir(self):
        compiled = compile_st_task("""
            VAR_GLOBAL I:INT; Sum:INT; END_VAR
            I:=0; WHILE I<3 DO I:=I+1; Sum:=Sum+I; END_WHILE;
        """).task
        manual = Task(
            programs=[ProgramInstance("PLC_PRG", "PLC_PRG")],
            gvl=[VarDecl("I", "INT", section="VAR_GLOBAL"),
                 VarDecl("SUM", "INT", section="VAR_GLOBAL")],
            pou_lib={"PLC_PRG": POUDefinition(
                name="PLC_PRG", pou_kind="PROGRAM", language="ST", code=[
                    LoadConst(0, "INT"), StoreVar("I", "INT"), Label("CHECK"),
                    LoadVar("I", "INT"), LoadConst(3, "INT"), BinOp("LT", "INT"),
                    JmpIfFalse("END"), LoadVar("I", "INT"), LoadConst(1, "INT"),
                    BinOp("ADD", "INT"), StoreVar("I", "INT"),
                    LoadVar("SUM", "INT"), LoadVar("I", "INT"),
                    BinOp("ADD", "INT"), StoreVar("SUM", "INT"),
                    Jmp("CHECK"), Label("END"),
                ])})
        writes = ({}, {}, {})
        registry = build_default_registry()
        left = _run(build_runtime(compiled, registry), writes)
        right = _run(build_runtime(manual, registry), writes)
        self.assertEqual([row["SUM"] for row in left], [6, 12, 18])
        self.assertEqual(left, right)

    def test_infinite_while_is_stopped_by_shared_executor_budget(self):
        task = compile_st_task("WHILE TRUE DO END_WHILE;").task
        runtime = build_runtime(task, build_default_registry())
        with patch("src.runtime.executor._MAX_INSTRUCTIONS_PER_EXECUTE", 9):
            with self.assertRaises(IRExecutionError) as caught:
                runtime.executor.execute_programs(runtime.store.snapshot())
        self.assertIn("指令预算已耗尽", str(caught.exception))
        self.assertIsNone(runtime.executor._instruction_budget_remaining)

    def test_eager_standard_calls_lower_to_exact_typed_ir(self):
        result = compile_st_task("""
            VAR_GLOBAL X:INT; A:INT; M:INT; C:INT; END_VAR
            A := aBs(X);
            M := MIN(X, 3, 2);
            C := LIMIT(0, MAX(X, 2), 10);
        """)
        self.assertEqual(result.code, (
            LoadVar("X", "INT"),
            CallStd("ABS", StdSig(("INT",), "INT")), StoreVar("A", "INT"),
            LoadVar("X", "INT"), LoadConst(3, "INT"), LoadConst(2, "INT"),
            CallStd("MIN", StdSig(("INT", "INT", "INT"), "INT")),
            StoreVar("M", "INT"),
            LoadConst(0, "INT"), LoadVar("X", "INT"), LoadConst(2, "INT"),
            CallStd("MAX", StdSig(("INT", "INT"), "INT")),
            LoadConst(10, "INT"),
            CallStd("LIMIT", StdSig(("INT", "INT", "INT"), "INT")),
            StoreVar("C", "INT"),
            Label("__ST_RETURN_EPILOGUE"),
        ))

    def test_eager_standard_calls_execute_through_default_runtime_catalogue(self):
        task = compile_st_task("""
            VAR_GLOBAL X:INT; A:INT; M:INT; C:INT; S:STRING; END_VAR
            A := ABS(X);
            M := MIN(X, 3, 2);
            C := LIMIT(0, MAX(X, 2), 10);
            S := MAX('A', 'Z', 'M');
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({"X": -7}, {"X": 20}))
        self.assertEqual(
            [(row["A"], row["M"], row["C"], row["S"]) for row in trace],
            [(7, -7, 2, "Z"), (20, 2, 10, "Z")])

    def test_standard_call_type_is_available_to_surrounding_expression(self):
        task = compile_st_task("""
            VAR_GLOBAL X:INT; Q:BOOL; END_VAR
            Q := ABS(X) > 3;
        """).task
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({"X": -2}, {"X": -5}))
        self.assertEqual([row["Q"] for row in trace], [False, True])

    def test_sel_lowers_to_lazy_typed_control_flow_with_exact_polarity(self):
        result = compile_st_task("""
            VAR_GLOBAL G:BOOL; A:INT; B:INT; O:INT; END_VAR
            O := SEL(G, A, B);
        """)
        self.assertEqual(result.code, (
            LoadVar("G", "BOOL"), JmpIfFalse("__ST_0001_SEL_IN0"),
            LoadVar("B", "INT"), Jmp("__ST_0002_SEL_END"),
            Label("__ST_0001_SEL_IN0"), LoadVar("A", "INT"),
            Label("__ST_0002_SEL_END"), StoreVar("O", "INT"),
            Label("__ST_RETURN_EPILOGUE"),
        ))
        self.assertFalse(any(
            isinstance(instruction, CallStd) and instruction.name == "SEL"
            for instruction in result.code))

        runtime = build_runtime(result.task, build_default_registry())
        trace = _run(runtime, (
            {"G": False, "A": 10, "B": 20},
            {"G": True, "A": 30, "B": 40},
        ))
        self.assertEqual([row["O"] for row in trace], [10, 40])

    def test_sel_does_not_execute_unselected_faulting_branch(self):
        task = compile_st_task("""
            VAR_GLOBAL G:BOOL; D0:INT; D1:INT; O:INT; END_VAR
            O := SEL(G, 100 / D0, 200 / D1);
        """).task
        safe = build_runtime(task, build_default_registry())
        trace = _run(safe, (
            {"G": False, "D0": 2, "D1": 0},
            {"G": True, "D0": 0, "D1": 4},
        ))
        self.assertEqual([row["O"] for row in trace], [50, 50])

        selected_fault = build_runtime(task, build_default_registry())
        selected_fault.store.write("G", False)
        selected_fault.store.write("D0", 0)
        selected_fault.store.write("D1", 1)
        with self.assertRaises(IRExecutionError):
            selected_fault.executor.execute_programs(
                selected_fault.store.snapshot())

    def test_nested_sel_and_eager_standard_function_compose(self):
        task = compile_st_task("""
            VAR_GLOBAL G0:BOOL; G1:BOOL; X:INT; O:INT; END_VAR
            O := ABS(SEL(G0, X, SEL(G1, -2, -3)));
        """).task
        left = build_runtime(task, build_default_registry())
        right = build_runtime(task, build_default_registry())
        self.assertEqual(_run(left, ({"G0": False, "X": -7},))[0]["O"], 7)
        self.assertEqual(
            [row["O"] for row in _run(right, (
                {"G0": True, "G1": False},
                {"G0": True, "G1": True},
            ))],
            [2, 3])

    def test_function_definition_compiles_and_executes_existing_call_frame_contract(self):
        compiled = compile_st_function("""
            VAR_INPUT X:INT; END_VAR
            VAR_OUTPUT Twice:INT; END_VAR
            VAR_IN_OUT Acc:INT; END_VAR
            VAR Bias:INT:=1; END_VAR
            Twice := X * 2;
            Acc := Acc + X;
            ClampAdd := LIMIT(0, Acc + Bias, 100);
        """, function_name="ClampAdd", return_type="INT")
        function = compiled.pou
        self.assertEqual(function.name, "CLAMPADD")
        self.assertEqual(function.pou_kind, "FUNCTION")
        self.assertEqual(function.return_type, "INT")
        self.assertEqual(
            [(item.name, item.section) for item in function.interface],
            [("X", "VAR_INPUT"), ("TWICE", "VAR_OUTPUT"),
             ("ACC", "VAR_IN_OUT")])
        self.assertEqual(
            [(item.name, item.initial) for item in function.locals],
            [("CLAMPADD", None), ("BIAS", 1)])
        self.assertEqual(compiled.code[-1], LoadVar("CLAMPADD", "INT"))

        main = POUDefinition(
            name="MAIN", pou_kind="PROGRAM", language="ST", code=[
                CallFunc("CLAMPADD", (
                    Binding("X", "IN", StoreKey("X"), "INT"),
                    Binding("TWICE", "OUT", StoreKey("TWICE"), "INT"),
                    Binding("ACC", "INOUT", StoreKey("ACC"), "INT"),
                ), "INT"),
                StoreVar("RESULT", "INT"),
            ])
        task = Task(
            programs=[ProgramInstance("MAIN", "MAIN")],
            gvl=[VarDecl("X", "INT", section="VAR_GLOBAL"),
                 VarDecl("TWICE", "INT", section="VAR_GLOBAL"),
                 VarDecl("ACC", "INT", section="VAR_GLOBAL"),
                 VarDecl("RESULT", "INT", section="VAR_GLOBAL")],
            pou_lib={"MAIN": main, "CLAMPADD": function})
        runtime = build_runtime(task, build_default_registry())
        trace = _run(runtime, ({"X": 2}, {"X": 3}))
        self.assertEqual(
            [(row["TWICE"], row["ACC"], row["RESULT"]) for row in trace],
            [(4, 2, 3), (6, 5, 6)])
        self.assertEqual(runtime.executor._active_frames, [])

    def test_function_default_result_and_two_runtime_frames_are_isolated(self):
        function = compile_st_function(
            "VAR_INPUT X:INT; END_VAR",
            function_name="NoAssign", return_type="INT").pou
        main = POUDefinition(
            name="MAIN", pou_kind="PROGRAM", language="ST",
            code=[CallFunc("NOASSIGN", (
                Binding("X", "IN", StoreKey("X"), "INT"),
            ), "INT"), StoreVar("O", "INT")])
        task = Task(
            programs=[ProgramInstance("MAIN", "MAIN")],
            gvl=[VarDecl("X", "INT", section="VAR_GLOBAL"),
                 VarDecl("O", "INT", section="VAR_GLOBAL")],
            pou_lib={"MAIN": main, "NOASSIGN": function})
        left = build_runtime(task, build_default_registry())
        right = build_runtime(task, build_default_registry())
        self.assertEqual(_run(left, ({"X": 9},))[0]["O"], 0)
        self.assertEqual(_run(right, ({"X": 99},))[0]["O"], 0)
        self.assertEqual(left.executor._active_frames, [])
        self.assertEqual(right.executor._active_frames, [])

    def test_program_source_positional_function_call_lowers_and_executes(self):
        add = compile_st_function("""
            VAR_INPUT A:INT; B:INT; END_VAR
            Add2 := A + B;
        """, "Add2", "INT")
        compiled = compile_st_task("""
            VAR_GLOBAL X:INT; Y:INT; O:INT; END_VAR
            O := Add2(X + 1, Y * 2);
        """, functions=(add,))
        self.assertEqual(compiled.code, (
            LoadVar("X", "INT"), LoadConst(1, "INT"), BinOp("ADD", "INT"),
            LoadVar("Y", "INT"), LoadConst(2, "INT"), BinOp("MUL", "INT"),
            CallFunc("ADD2", (
                Binding("A", "IN", StackSlot(1), "INT"),
                Binding("B", "IN", StackSlot(0), "INT"),
            ), "INT"),
            StoreVar("O", "INT"),
            Label("__ST_RETURN_EPILOGUE"),
        ))
        runtime = build_runtime(compiled.task, build_default_registry())
        trace = _run(runtime, ({"X": 2, "Y": 4}, {"X": -1, "Y": 3}))
        self.assertEqual([row["O"] for row in trace], [11, 6])

    def test_program_source_named_function_call_binds_out_and_inout(self):
        clamp = compile_st_function("""
            VAR_INPUT X:INT; END_VAR
            VAR_OUTPUT Twice:INT; END_VAR
            VAR_IN_OUT Acc:INT; END_VAR
            Twice:=X*2; Acc:=Acc+X; ClampAdd:=LIMIT(0,Acc,100);
        """, "ClampAdd", "INT")
        compiled = compile_st_task("""
            VAR_GLOBAL X:INT; Twice:INT; Acc:INT; Result:INT; END_VAR
            Result := ClampAdd(Twice=>Twice, Acc:=Acc, X:=X);
        """, functions=(clamp,))
        call = next(item for item in compiled.code if isinstance(item, CallFunc))
        self.assertEqual(call.bindings, (
            Binding("X", "IN", StackSlot(0), "INT"),
            Binding("TWICE", "OUT", StoreKey("TWICE"), "INT"),
            Binding("ACC", "INOUT", StoreKey("ACC"), "INT"),
        ))
        runtime = build_runtime(compiled.task, build_default_registry())
        trace = _run(runtime, ({"X": 4}, {"X": 7}))
        self.assertEqual(
            [(row["TWICE"], row["ACC"], row["RESULT"]) for row in trace],
            [(8, 4, 4), (14, 11, 11)])

    def test_function_catalogue_is_detached_from_compile_result(self):
        add = compile_st_function(
            "VAR_INPUT A:INT; END_VAR AddOne:=A+1;", "AddOne", "INT")
        compiled = compile_st_task(
            "VAR_GLOBAL X:INT; O:INT; END_VAR O:=AddOne(X);",
            functions=(add,))
        task_function = compiled.task.pou_lib["ADDONE"]
        self.assertIsNot(task_function, add.pou)
        self.assertIsNot(task_function.interface, add.pou.interface)
        self.assertIsNot(task_function.code, add.pou.code)
        add.pou.interface[0].name = "BROKEN"
        add.pou.code.clear()
        self.assertEqual(task_function.interface[0].name, "A")
        self.assertTrue(task_function.code)
        runtime = build_runtime(compiled.task, build_default_registry())
        self.assertEqual(_run(runtime, ({"X": 9},))[0]["O"], 10)

    def test_function_block_definition_executes_with_persistent_and_temp_state(self):
        accumulator = compile_st_function_block("""
            VAR_INPUT I:INT; END_VAR
            VAR_OUTPUT Q:INT; TQ:INT; END_VAR
            VAR_IN_OUT IO:INT; END_VAR
            VAR Acc:INT:=0; END_VAR
            VAR_TEMP Temp:INT; END_VAR
            Acc:=Acc+I; Q:=Acc;
            Temp:=Temp+1; TQ:=Temp;
            IO:=IO+1;
        """, "Accumulator")
        self.assertEqual(accumulator.pou.pou_kind, "FUNCTION_BLOCK")
        self.assertIsNone(accumulator.pou.return_type)
        self.assertEqual(
            [item.section for item in accumulator.pou.interface],
            ["VAR_INPUT", "VAR_OUTPUT", "VAR_OUTPUT", "VAR_IN_OUT"])
        self.assertEqual(
            [item.section for item in accumulator.pou.locals],
            ["VAR", "VAR_TEMP"])

        main = POUDefinition(
            name="MAIN", pou_kind="PROGRAM", language="ST",
            instances=[InstanceDecl("A", "ACCUMULATOR", kind="user_fb"),
                       InstanceDecl("B", "ACCUMULATOR", kind="user_fb")],
            code=[CallFbInstance("A", (
                Binding("I", "IN", StoreKey("I"), "INT"),
                Binding("Q", "OUT", StoreKey("Q"), "INT"),
                Binding("TQ", "OUT", StoreKey("TQ"), "INT"),
                Binding("IO", "INOUT", StoreKey("IO"), "INT"),
            )), CallFbInstance("B", (
                Binding("I", "IN", Const(10, "INT"), "INT"),
                Binding("Q", "OUT", StoreKey("Q2"), "INT"),
                Binding("TQ", "OUT", StoreKey("TQ2"), "INT"),
                Binding("IO", "INOUT", StoreKey("IO2"), "INT"),
            ))])
        task = Task(
            programs=[ProgramInstance("MAIN", "MAIN")],
            gvl=[VarDecl(name, "INT", section="VAR_GLOBAL")
                 for name in ("I", "Q", "TQ", "IO", "Q2", "TQ2", "IO2")],
            pou_lib={"MAIN": main, "ACCUMULATOR": accumulator.pou})
        left = build_runtime(task, build_default_registry())
        right = build_runtime(task, build_default_registry())
        left_trace = _run(left, ({"I": 2}, {"I": 3}))
        right_trace = _run(right, ({"I": 7},))
        self.assertEqual(
            [(row["Q"], row["TQ"], row["IO"]) for row in left_trace],
            [(2, 1, 1), (5, 1, 2)])
        self.assertEqual(
            [(row["Q2"], row["TQ2"], row["IO2"]) for row in left_trace],
            [(10, 1, 1), (20, 1, 2)])
        self.assertEqual(
            (right_trace[0]["Q"], right_trace[0]["TQ"], right_trace[0]["IO"]),
            (7, 1, 1))
        self.assertEqual(left.executor._active_frames, [])
        self.assertEqual(right.executor._active_frames, [])

    def test_program_source_declares_and_calls_user_fb_instances(self):
        accumulator = compile_st_function_block("""
            VAR_INPUT I:INT; END_VAR
            VAR_OUTPUT Q:INT; END_VAR
            VAR_IN_OUT IO:INT; END_VAR
            VAR State:INT; END_VAR
            State:=State+I; Q:=State; IO:=IO+1;
        """, "Accumulator")
        compiled = compile_st_task("""
            VAR_GLOBAL I:INT; QA:INT; QB:INT; IOA:INT; IOB:INT; END_VAR
            VAR A:Accumulator; B:Accumulator; END_VAR
            A(Q=>QA, I:=I+1, IO:=IOA);
            B(IO:=IOB, I:=10, Q=>QB);
        """, function_blocks=(accumulator,))
        self.assertEqual(
            [(item.name, item.block_type, item.kind)
             for item in compiled.task.pou_lib["PLC_PRG"].instances],
            [("A", "ACCUMULATOR", "user_fb"),
             ("B", "ACCUMULATOR", "user_fb")])
        self.assertEqual(compiled.code, (
            LoadVar("I", "INT"), LoadConst(1, "INT"), BinOp("ADD", "INT"),
            CallFbInstance("A", (
                Binding("I", "IN", StackSlot(0), "INT"),
                Binding("Q", "OUT", StoreKey("QA"), "INT"),
                Binding("IO", "INOUT", StoreKey("IOA"), "INT"),
            )),
            LoadConst(10, "INT"),
            CallFbInstance("B", (
                Binding("I", "IN", StackSlot(0), "INT"),
                Binding("Q", "OUT", StoreKey("QB"), "INT"),
                Binding("IO", "INOUT", StoreKey("IOB"), "INT"),
            )),
            Label("__ST_RETURN_EPILOGUE"),
        ))
        task_fb = compiled.task.pou_lib["ACCUMULATOR"]
        self.assertIsNot(task_fb, accumulator.pou)
        self.assertIsNot(task_fb.interface, accumulator.pou.interface)
        accumulator.pou.interface[0].name = "BROKEN"
        accumulator.pou.code.clear()
        self.assertEqual(task_fb.interface[0].name, "I")
        self.assertTrue(task_fb.code)
        left = build_runtime(compiled.task, build_default_registry())
        right = build_runtime(compiled.task, build_default_registry())
        trace = _run(left, ({"I": 1}, {"I": 2}))
        self.assertEqual(
            [(row["QA"], row["QB"], row["IOA"], row["IOB"])
             for row in trace],
            [(2, 10, 1, 1), (5, 20, 2, 2)])
        self.assertEqual(
            (_run(right, ({"I": 9},))[0]["QA"],
             left.store.read("QA")),
            (10, 5))

    def test_program_source_calls_ton_with_explicit_codesys_pin_aliases(self):
        compiled = compile_st_task("""
            VAR_GLOBAL Start:BOOL; Delay:TIME; Done:BOOL; Elapsed:TIME; END_VAR
            VAR Timer:TON; END_VAR
            Timer(PT:=Delay, IN:=Start, ET=>Elapsed, Q=>Done);
        """)
        self.assertEqual(
            [(item.name, item.block_type, item.kind)
             for item in compiled.task.pou_lib["PLC_PRG"].instances],
            [("TIMER", "TON", "library")])
        self.assertEqual(compiled.code, (
            LoadVar("DELAY", "TIME"), StoreVar("TIMER.PT_ms", "TIME"),
            LoadVar("START", "BOOL"), StoreVar("TIMER.IN", "BOOL"),
            CallFb("TIMER"),
            LoadVar("TIMER.Q", "BOOL"), StoreVar("DONE", "BOOL"),
            LoadVar("TIMER.ET_ms", "TIME"), StoreVar("ELAPSED", "TIME"),
            Label("__ST_RETURN_EPILOGUE"),
        ))
        runtime = build_runtime(compiled.task, build_default_registry())
        trace = _run(runtime, (
            {"START": True, "DELAY": 1000},
            {"START": True, "DELAY": 1000},
            {"START": False, "DELAY": 1000},
        ))
        self.assertEqual(
            [(row["DONE"], row["ELAPSED"]) for row in trace],
            [(False, 500), (True, 1000), (False, 0)])

    def test_eight_primitive_source_aliases_match_default_schemas_exactly(self):
        aliases = primitive_source_aliases()
        self.assertEqual(
            tuple(sorted(aliases)),
            ("BLINK", "F_TRIG", "RS", "R_TRIG", "SR", "TOF", "TON", "TP"))
        registry = build_default_registry()
        for block_type, mapping in aliases.items():
            with self.subTest(block_type=block_type):
                schema, _adapter = registry.resolve(block_type, "engineering")
                pins = tuple(schema.inputs) + tuple(schema.inouts) + tuple(schema.outputs)
                self.assertEqual(set(mapping.values()), {pin.name for pin in pins})
                self.assertEqual(len(mapping), len(set(mapping.values())))
                declarations = []
                arguments = []
                for index, (source_name, engineering_name) in enumerate(mapping.items()):
                    pin = schema.pin(engineering_name)
                    actual = "V%d" % index
                    declarations.append("%s:%s;" % (actual, pin.iec_type))
                    direction = "=>" if pin.kind == "VAR_OUTPUT" else ":="
                    arguments.append("%s%s%s" % (source_name, direction, actual))
                source = (
                    "VAR_GLOBAL %s END_VAR VAR FB:%s; END_VAR FB(%s);"
                    % (" ".join(declarations), block_type, ",".join(arguments)))
                result = compile_st_task(source)
                self.assertEqual(
                    sum(isinstance(item, CallFb) for item in result.code), 1)
        self.assertEqual(aliases["TON"]["PT"], "PT_ms")
        self.assertEqual(aliases["TON"]["ET"], "ET_ms")
        self.assertNotIn("PT_ms", aliases["TON"])

    def test_apccd_library_inout_lowers_read_call_and_writeback(self):
        source = """
            VAR_GLOBAL
                SP:REAL; PV:REAL; TS:BOOL; TC:REAL; TZ:REAL;
                CDH:REAL; CDL:REAL; TL:REAL; Z:REAL;
                AV:REAL; CD_BH:REAL;
            END_VAR
            VAR CD:APCCD; END_VAR
            CD(SP:=SP,PV:=PV,TS:=TS,TC:=TC,TZ:=TZ,CDH:=CDH,CDL:=CDL,
               TL:=TL,ZLOUT:=Z,AV=>AV,CD_BH=>CD_BH);
        """
        compiled = compile_st_task(source)
        self.assertIn(LoadVar("Z", "REAL"), compiled.code)
        self.assertIn(StoreVar("CD.ZLOUT", "REAL"), compiled.code)
        call_index = compiled.code.index(CallFb("CD"))
        self.assertEqual(
            compiled.code[call_index + 1:call_index + 3],
            (LoadVar("CD.ZLOUT", "REAL"), StoreVar("Z", "REAL")))

    def test_apcm_library_omission_and_inout_use_generic_lowering(self):
        source = """
            VAR_GLOBAL
                SP:REAL; PV:REAL; OC:REAL; TS:BOOL; TP:REAL; Z:REAL;
                AV:REAL; AV_P:REAL; AV_R:REAL; AV_GC:REAL;
                AV_J:REAL; AV_D:REAL; AV_C:REAL;
            END_VAR
            VAR M:APCM; END_VAR
            M(SP:=SP,PV:=PV,OC:=OC,TS:=TS,TP:=TP,ZLOUT:=Z,
              AV=>AV,AV_P=>AV_P,AV_R=>AV_R,AV_GC=>AV_GC,
              AV_J=>AV_J,AV_D=>AV_D,AV_C=>AV_C);
        """
        compiled = compile_st_task(source)
        self.assertIn(CallFb("M"), compiled.code)
        self.assertIn(StoreVar("M.ZLOUT", "REAL"), compiled.code)
        self.assertNotIn(StoreVar("M.RM", "INT"), compiled.code)
        self.assertNotIn(StoreVar("M.ZSYK", "REAL"), compiled.code)


class STLoweringFailureTests(unittest.TestCase):
    def assert_code(self, source, code, **kwargs):
        with self.assertRaises(STCompileError) as caught:
            compile_st_task(source, **kwargs)
        self.assertEqual(caught.exception.errors[0].code, code)
        return caught.exception

    def test_duplicate_undefined_and_case_insensitive_binding(self):
        self.assert_code("VAR_GLOBAL X:INT; x:INT; END_VAR", "DUPLICATE_DECLARATION")
        self.assert_code("VAR_GLOBAL X:INT; END_VAR Y:=X;", "UNDEFINED_NAME")
        result = compile_st_task("VAR_GLOBAL Mixed:INT; Out:INT; END_VAR out:=mIxEd;")
        self.assertEqual(result.code, (
            LoadVar("MIXED", "INT"), StoreVar("OUT", "INT"),
            Label("__ST_RETURN_EPILOGUE")))

    def test_ambiguous_and_mismatched_numeric_types_fail_closed(self):
        self.assert_code("VAR_GLOBAL Q:BOOL; END_VAR Q:=1<2;",
                         "AMBIGUOUS_LITERAL_TYPE")
        self.assert_code("VAR_GLOBAL Q:BOOL; END_VAR Q:=1;", "TYPE_MISMATCH")
        self.assert_code("VAR_GLOBAL X:INT; END_VAR X:=1.0;", "TYPE_MISMATCH")
        self.assert_code("VAR_GLOBAL X:INT; Y:REAL; END_VAR X:=Y;", "TYPE_MISMATCH")

    def test_unsupported_sections_targets_calls_and_control_flow(self):
        self.assert_code("VAR_INPUT X:INT; END_VAR", "UNSUPPORTED_DECLARATION_SECTION")
        self.assert_code("VAR_GLOBAL X:INT; END_VAR X.A:=1;",
                         "UNSUPPORTED_ASSIGNMENT_TARGET")
        self.assert_code("VAR_GLOBAL X:INT; END_VAR X:=A[1];",
                         "UNSUPPORTED_EXPRESSION")
        self.assert_code("F();", "UNSUPPORTED_CALL")
        self.assert_code(
            "VAR_GLOBAL X:INT; END_VAR IF TRUE THEN F(); END_IF;",
            "UNSUPPORTED_CALL")

    def test_standard_function_shape_and_type_failures_are_stable(self):
        prefix = "VAR_GLOBAL X:INT; Y:DINT; Q:BOOL; O:INT; END_VAR "
        self.assert_code(prefix + "O:=ABS();", "STANDARD_FUNCTION_ARITY")
        self.assert_code(prefix + "O:=ABS(X,X);", "STANDARD_FUNCTION_ARITY")
        self.assert_code(prefix + "O:=MIN(X);", "STANDARD_FUNCTION_ARITY")
        self.assert_code(prefix + "O:=LIMIT(0,X);", "STANDARD_FUNCTION_ARITY")
        self.assert_code(prefix + "O:=MIN(X,Y);", "TYPE_MISMATCH")
        self.assert_code(prefix + "O:=ABS(Q);", "INVALID_STANDARD_FUNCTION_TYPE")
        self.assert_code(prefix + "O:=ABS(IN:=X);", "UNSUPPORTED_CALL_ARGUMENT")
        self.assert_code(prefix + "O:=UNKNOWN(X);", "UNSUPPORTED_STANDARD_FUNCTION")

    def test_sel_shape_and_type_failures_are_stable(self):
        prefix = "VAR_GLOBAL G:BOOL; X:INT; Y:DINT; O:INT; END_VAR "
        self.assert_code(prefix + "O:=SEL(G,X);", "STANDARD_FUNCTION_ARITY")
        self.assert_code(prefix + "O:=SEL(X,X,O);", "NON_BOOL_SEL_GUARD")
        self.assert_code(prefix + "O:=SEL(G,X,Y);", "TYPE_MISMATCH")
        self.assert_code(prefix + "O:=SEL(G:=G,X,O);", "UNSUPPORTED_CALL_ARGUMENT")

    def test_function_definition_declaration_boundaries_fail_closed(self):
        function_cases = (
            ("VAR_GLOBAL X:INT; END_VAR", "UNSUPPORTED_FUNCTION_SECTION"),
            ("VAR_TEMP X:INT; END_VAR", "UNSUPPORTED_FUNCTION_SECTION"),
            ("VAR_INPUT X:INT:=1; END_VAR", "OPTIONAL_PARAMETER_DEFERRED"),
            ("VAR ClampAdd:INT; END_VAR", "FUNCTION_RESULT_NAME_CONFLICT"),
            ("VAR_INPUT X:TON; END_VAR", "UNSUPPORTED_DECLARATION_TYPE"),
        )
        for source, code in function_cases:
            with self.subTest(code=code):
                with self.assertRaises(STCompileError) as caught:
                    compile_st_function(
                        source, function_name="ClampAdd", return_type="INT")
                self.assertEqual(caught.exception.errors[0].code, code)

    def test_function_block_definition_boundaries_fail_closed(self):
        cases = (
            ("VAR_GLOBAL X:INT; END_VAR", "UNSUPPORTED_FB_SECTION"),
            ("VAR_INPUT X:INT:=1; END_VAR", "OPTIONAL_PARAMETER_DEFERRED"),
            ("VAR_INPUT X:TON; END_VAR", "UNSUPPORTED_DECLARATION_TYPE"),
            ("VAR X:INT; X:INT; END_VAR", "DUPLICATE_DECLARATION"),
            ("VAR_TEMP X:INT:=1; END_VAR", "UNSUPPORTED_TEMP_INITIALIZER"),
        )
        for source, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(STCompileError) as caught:
                    compile_st_function_block(source, "Accumulator")
                self.assertEqual(caught.exception.errors[0].code, code)
        for name in ("bad__name", "FUNCTION_BLOCK"):
            with self.subTest(name=name):
                with self.assertRaises(STCompileError) as caught:
                    compile_st_function_block("", name)
                self.assertEqual(caught.exception.errors[0].code, "INVALID_FB_NAME")

    def test_program_function_call_binding_failures_are_stable(self):
        pure = compile_st_function(
            "VAR_INPUT A:INT; B:INT; END_VAR Sum:=A+B;", "Sum", "INT")
        stateful = compile_st_function("""
            VAR_INPUT A:INT; END_VAR
            VAR_OUTPUT B:INT; END_VAR
            VAR_IN_OUT C:INT; END_VAR
            Mix:=A;
        """, "Mix", "INT")
        base = "VAR_GLOBAL A:INT; B:INT; C:INT; O:INT; END_VAR "
        cases = (
            (base + "O:=Sum(A:=A,B);", (pure,), "MIXED_CALL_ARGUMENT_STYLE"),
            (base + "O:=Sum(A:=A,A:=B);", (pure,), "DUPLICATE_CALL_FORMAL"),
            (base + "O:=Sum(A:=A,Z:=B);", (pure,), "UNKNOWN_CALL_FORMAL"),
            (base + "O:=Sum(A:=A);", (pure,), "MISSING_CALL_FORMAL"),
            (base + "O:=Mix(A:=A,B=>B);", (stateful,), "MISSING_CALL_FORMAL"),
            (base + "O:=Mix(A:=A,B:=B,C:=C);", (stateful,), "CALL_DIRECTION_MISMATCH"),
            (base + "O:=Mix(A:=A,B=>B,C:=1);", (stateful,), "CALL_ACTUAL_NOT_WRITABLE"),
        )
        for source, functions, code in cases:
            with self.subTest(code=code):
                self.assert_code(source, code, functions=functions)

        self.assert_code(base + "Sum(A,B);", "UNSUPPORTED_CALL", functions=(pure,))
        self.assert_code(
            "VAR_GLOBAL A:INT; Y:DINT; O:INT; END_VAR O:=Sum(A,Y);",
            "TYPE_MISMATCH", functions=(pure,))

    def test_program_function_catalogue_shell_failures_are_stable(self):
        function = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=[function])
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=(object(),))
        duplicate = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        self.assert_code(
            source, "DUPLICATE_FUNCTION_DEFINITION",
            functions=(function, duplicate))

        hostile = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        _HostilePou.calls = 0
        object.__setattr__(hostile, "pou", _HostilePou())
        self.assert_code(
            source, "INVALID_FUNCTION_CATALOGUE", functions=(hostile,))
        self.assertEqual(_HostilePou.calls, 0)

        malformed = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        malformed.pou.name = []
        self.assert_code(
            source, "INVALID_FUNCTION_CATALOGUE", functions=(malformed,))

        malformed = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        malformed.pou.interface[0].initial = object()
        self.assert_code(
            source, "INVALID_FUNCTION_CATALOGUE", functions=(malformed,))

        malformed = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        del malformed.pou.return_type
        self.assert_code(
            source, "INVALID_FUNCTION_CATALOGUE", functions=(malformed,))

        # The immutable code tuple is the catalogue authority; clearing the
        # convenience POU list must not erase the compiled FUNCTION body.
        detached = compile_st_function(
            "VAR_INPUT A:INT; END_VAR F:=A+1;", "F", "INT")
        detached.pou.code.clear()
        runtime = build_runtime(
            compile_st_task(source, functions=(detached,)).task,
            build_default_registry())
        self.assertEqual(_run(runtime, ({"A": 4},))[0]["O"], 5)

        for name, return_type, code in (
            ("bad__name", "INT", "INVALID_FUNCTION_NAME"),
            ("PROGRAM", "INT", "INVALID_FUNCTION_NAME"),
            ("Good", "TON", "INVALID_FUNCTION_RETURN_TYPE"),
        ):
            with self.subTest(name=name, return_type=return_type):
                with self.assertRaises(STCompileError) as caught:
                    compile_st_function(
                        "", function_name=name, return_type=return_type)
                self.assertEqual(caught.exception.errors[0].code, code)

    def test_program_user_fb_catalogue_and_call_failures_are_stable(self):
        fb = compile_st_function_block(
            "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Accumulator")
        base = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; D:DINT; END_VAR "
            "VAR A:Accumulator; END_VAR ")
        cases = (
            (base + "A(I,Q=>Q,IO:=IO);", "EXPLICIT_BINDING_REQUIRED"),
            (base + "A(I:=I,I:=I,Q=>Q,IO:=IO);", "DUPLICATE_CALL_FORMAL"),
            (base + "A(I:=I,Z=>Q,IO:=IO);", "UNKNOWN_CALL_FORMAL"),
            (base + "A(I:=I,Q=>Q);", "MISSING_CALL_FORMAL"),
            (base + "A(I=>I,Q=>Q,IO:=IO);", "CALL_DIRECTION_MISMATCH"),
            (base + "A(I:=I,Q:=Q,IO:=IO);", "CALL_DIRECTION_MISMATCH"),
            (base + "A(I:=I,Q=>Q,IO:=1);", "CALL_ACTUAL_NOT_WRITABLE"),
            (base + "A(I:=D,Q=>Q,IO:=IO);", "TYPE_MISMATCH"),
            ("VAR_GLOBAL A:Accumulator; END_VAR", "UNSUPPORTED_FB_INSTANCE_SECTION"),
            ("VAR A:Accumulator:=1; END_VAR", "UNSUPPORTED_FB_INSTANCE_INITIALIZER"),
            ("VAR A:UnknownFb; END_VAR", "UNSUPPORTED_DECLARATION_TYPE"),
        )
        for source, code in cases:
            with self.subTest(code=code):
                self.assert_code(source, code, function_blocks=(fb,))

        self.assert_code(base + "B(I:=I,Q=>Q,IO:=IO);", "UNSUPPORTED_CALL",
                         function_blocks=(fb,))
        self.assert_code(base, "INVALID_FB_CATALOGUE", function_blocks=[fb])
        self.assert_code(base, "INVALID_FB_CATALOGUE", function_blocks=(object(),))
        duplicate = compile_st_function_block(
            "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Accumulator")
        self.assert_code(base, "DUPLICATE_FB_DEFINITION",
                         function_blocks=(fb, duplicate))
        hostile = compile_st_function_block("", "HostileFb")
        _HostilePou.calls = 0
        object.__setattr__(hostile, "pou", _HostilePou())
        self.assert_code(base, "INVALID_FB_CATALOGUE",
                         function_blocks=(hostile,))
        self.assertEqual(_HostilePou.calls, 0)
        colliding_function = compile_st_function(
            "VAR_INPUT I:INT; END_VAR Accumulator:=I;", "Accumulator", "INT")
        self.assert_code(base, "FB_NAME_COLLISION", functions=(colliding_function,),
                         function_blocks=(fb,))

    def test_catalogue_hostile_scalar_fields_fail_closed_without_leaking(self):
        source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        # Hostile exact POU scalar fields: the fixed catalogue classifies them by
        # exact type and must never invoke their comparison/hash hooks (which
        # would leak a BaseException, as they did before the fix).
        for field in ("pou_kind", "language"):
            with self.subTest(field=field):
                func = compile_st_function(
                    "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
                setattr(func.pou, field, _HostileField())
                self.assert_code(
                    source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        # Hostile exact instruction scalar field: before the fix this reached the
        # loader's `type not in IEC_TYPES` membership and leaked; now the clone
        # rejects it up front without ever hashing the field.
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        object.__setattr__(func, "code", (LoadVar("A", _HostileField()),))
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

    def test_catalogue_added_fields_are_rejected(self):
        source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        object.__setattr__(func, "sneaky", 1)                 # added result field
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        func.pou.sneaky = 1                                   # added POU field
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        func.pou.interface[0].sneaky = 1                      # added declaration field
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        tampered = LoadVar("A", "INT")
        object.__setattr__(tampered, "sneaky", 1)             # added instruction field
        object.__setattr__(func, "code", (tampered,))
        self.assert_code(source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

    def test_catalogue_exact_illegal_scalar_values_are_rejected_by_catalogue(self):
        # WP-116 R1 item 1 / WP-117 R1 item 1: the clone helpers gate only the
        # exact Python *type* of each scalar.  A forged catalogue entry whose
        # scalar has the right type but an illegal *value* (IEC type, section or
        # binding mode) used to pass cloning and leak the Loader's
        # ``IRValidationError`` out of the final ``validate_task``.  It must now
        # fail closed as a stable ``STCompileError`` classified by the catalogue
        # kind (INVALID_FUNCTION_CATALOGUE vs INVALID_FB_CATALOGUE).
        func_source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"

        # (b) instruction IEC type: exact ``str`` value outside IEC_TYPES.
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        object.__setattr__(func, "code", tuple(
            LoadVar(ins.key, "EVIL")
            if isinstance(ins, LoadVar) and ins.key == "A" else ins
            for ins in func.code))
        self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

        # (c) binding mode: exact ``str`` value outside BINDING_MODES, reached
        # through a reconstructed CALL_FUNC binding.
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        object.__setattr__(func, "code", (
            CallFunc("F", (Binding("A", "EVIL", StoreKey("A"), "INT"),), "INT"),))
        self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

        # (a) FUNCTION declaration section: exact ``str`` value outside the
        # allowed sections.
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        func.pou.locals[0].section = "EVIL"
        self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))

        # (a') the same illegal-section defect in an FB must classify to the FB
        # catalogue code, proving stable FUNCTION/FB attribution.
        fb_call = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; END_VAR VAR A:Acc; END_VAR "
            "A(I:=I,Q=>Q,IO:=IO);")
        fb = compile_st_function_block(
            "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Acc")
        fb.pou.interface[0].section = "EVIL"
        self.assert_code(fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))

        # (b') the instruction IEC-type defect must classify to the FB catalogue
        # too, so FUNCTION/FB attribution is proven for the value-level scalar
        # families and not only the declaration section.  The forged FB body
        # keeps its exact ``str`` type but an IEC type outside ``IEC_TYPES``; it
        # only reads its own declared input, so the isolated catalogue-boundary
        # validation rejects it specifically on the illegal value rather than an
        # unresolved reference.
        fb = compile_st_function_block(
            "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Acc")
        object.__setattr__(fb, "code", tuple(
            LoadVar(ins.key, "EVIL")
            if isinstance(ins, LoadVar) and ins.key == "I" else ins
            for ins in fb.code))
        self.assert_code(fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))

    def test_catalogue_exact_illegal_scalar_values_reach_loader_boundary(self):
        # WP-118 Round 1 Codex item 1: the clone helpers gate only the exact
        # Python *type* of each catalogue scalar, and ``_validate_catalogue_pou``
        # only converts value faults the Loader itself rejects.  A forged
        # catalogue whose scalar keeps its exact type but carries a value the
        # Loader does *not* police -- a ``LoadConst`` value that cannot inhabit
        # its IEC type, an unsupported ``CALL_STD`` name, or a ``Const`` binding
        # actual typed outside ``IEC_TYPES`` -- used to flow straight into the
        # returned Task.  Each must now fail closed as a stable ``STCompileError``
        # classified by catalogue kind, rejected at the clone boundary (before
        # ``validate_task``), and the asserted diagnostic proves the value gate
        # itself fired rather than an incidental unresolved reference or stack
        # residue.  Every injected body below is otherwise stack-balanced and
        # reference-clean, so before the fix it compiled successfully.
        func_source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        fb_call = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; END_VAR VAR A:Acc; END_VAR "
            "A(I:=I,Q=>Q,IO:=IO);")

        def fresh_func():
            return compile_st_function(
                "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")

        def fresh_fb():
            return compile_st_function_block(
                "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
                "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Acc")

        # (1) LoadConst: an exact ``str`` value can never inhabit an INT constant.
        func = fresh_func()
        object.__setattr__(func, "code", tuple(
            LoadConst("x", "INT")
            if isinstance(ins, LoadVar) and ins.key == "A" else ins
            for ins in func.code))
        exc = self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        self.assertIn("constant value", exc.errors[0].message)

        fb = fresh_fb()
        object.__setattr__(fb, "code", tuple(
            LoadConst("x", "INT")
            if isinstance(ins, LoadVar) and ins.key == "I" else ins
            for ins in fb.code))
        exc = self.assert_code(
            fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
        self.assertIn("constant value", exc.errors[0].message)

        # (2) CALL_STD: an exact ``str`` name outside the supported eager set.
        func = fresh_func()
        object.__setattr__(func, "code", (
            LoadVar("A", "INT"),
            CallStd("WP118_UNKNOWN", StdSig(("INT",), "INT")),
            StoreVar("F", "INT"), LoadVar("F", "INT")))
        exc = self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        self.assertIn("standard function", exc.errors[0].message)

        fb = fresh_fb()
        object.__setattr__(fb, "code", (
            LoadVar("I", "INT"),
            CallStd("WP118_UNKNOWN", StdSig(("INT",), "INT")),
            StoreVar("Q", "INT")))
        exc = self.assert_code(
            fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
        self.assertIn("standard function", exc.errors[0].message)

        # (3) Const binding actual: an exact ``str`` type outside ``IEC_TYPES``.
        func = fresh_func()
        object.__setattr__(func, "code", (
            CallFunc(
                "F", (Binding("A", "IN", Const(1, "WP118_NOT_IEC"), "INT"),),
                "INT"),
            StoreVar("F", "INT"), LoadVar("F", "INT")))
        exc = self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        self.assertIn("binding constant value", exc.errors[0].message)

        fb = fresh_fb()
        object.__setattr__(fb, "code", (
            CallFunc(
                "F", (Binding("A", "IN", Const(1, "WP118_NOT_IEC"), "INT"),),
                "INT"),
            StoreVar("Q", "INT")))
        exc = self.assert_code(
            fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
        self.assertIn("binding constant value", exc.errors[0].message)

        # (4) Binding.mode: gated at the clone boundary so the FB counterexample
        # proves the mode itself is rejected rather than incidentally failing on
        # the unresolved ``CallFunc`` target (an FB has no sibling to resolve
        # against in the single-POU isolation task) or on stack residue.
        func = fresh_func()
        object.__setattr__(func, "code", (
            CallFunc("F", (Binding("A", "EVIL", StoreKey("A"), "INT"),), "INT"),
            StoreVar("F", "INT"), LoadVar("F", "INT")))
        exc = self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        self.assertIn("binding mode", exc.errors[0].message)

        fb = fresh_fb()
        object.__setattr__(fb, "code", (
            CallFunc("F", (Binding("A", "EVIL", StoreKey("A"), "INT"),), "INT"),
            StoreVar("Q", "INT")))
        exc = self.assert_code(
            fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
        self.assertIn("binding mode", exc.errors[0].message)

    def test_catalogue_declaration_initial_value_reaches_loader_boundary(self):
        # WP-118 Round 2 Codex item 1: ``_clone_var_decl`` gated only the exact
        # Python *shape* (and float finiteness) of a declaration's ``initial``.  A
        # non-None initial that keeps its exact type but cannot inhabit its declared
        # IEC type -- ``initial="WP118_BAD_INITIAL"`` with ``iec_type="INT"`` -- kept
        # its exact ``str`` type, so it passed cloning and was copied verbatim into
        # the returned Task; the Loader does not police catalogue initial values.
        # It must now fail closed at the clone boundary (before ``validate_task``)
        # as a stable ``STCompileError`` classified by catalogue kind, and the
        # asserted diagnostic proves the initial-value category gate itself fired
        # rather than the pre-existing exact-shape gate or an incidental fault.
        func_source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        fb_call = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; END_VAR VAR A:Acc; END_VAR "
            "A(I:=I,Q=>Q,IO:=IO);")

        def fresh_func():
            return compile_st_function(
                "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")

        def fresh_fb():
            return compile_st_function_block(
                "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
                "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Acc")

        # INT <- str, FUNCTION catalogue.
        func = fresh_func()
        self.assertEqual(func.pou.interface[0].iec_type, "INT")
        func.pou.interface[0].initial = "WP118_BAD_INITIAL"
        exc = self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        self.assertIn("initial value", exc.errors[0].message)

        # INT <- str, FB catalogue: same category defect must classify to the FB
        # code, proving stable FUNCTION/FB attribution for the initial-value gate.
        fb = fresh_fb()
        self.assertEqual(fb.pou.interface[0].iec_type, "INT")
        fb.pou.interface[0].initial = "WP118_BAD_INITIAL"
        exc = self.assert_code(
            fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
        self.assertIn("initial value", exc.errors[0].message)

        # Untrusted initial value object: the exact-type gate classifies it by
        # identity before the value gate runs, so its comparison/hash hooks are
        # never observed -- a leaked ``_CatalogueProbe`` (a ``BaseException``, not
        # an ``STCompileError``) would fail the ``assertRaises`` below.
        func = fresh_func()
        func.pou.interface[0].initial = _HostileField()
        self.assert_code(
            func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
        fb = fresh_fb()
        fb.pou.interface[0].initial = _HostileField()
        self.assert_code(fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))

    def test_catalogue_declaration_policy_invariants_fail_closed(self):
        # WP-118 Round 3 Codex items 1-2: the clone/prepare helpers gated only the
        # *category* of a declaration value, not the declaration-level *policy* the
        # front end enforces but the Loader does not.  A forged catalogue could
        # therefore carry (a) an interface / VAR_TEMP ``initial`` whose value is
        # IEC-consistent yet the section forbids (the front end rejects them as
        # ``OPTIONAL_PARAMETER_DEFERRED`` / ``UNSUPPORTED_TEMP_INITIALIZER``; an
        # accepted VAR_OUTPUT initial even changed a call's result), (b) a
        # ``retain`` / ``persistent`` flag no source syntax emits, or (c) a
        # non-empty / hostile ``instances`` container silently dropped.  Each must
        # now fail closed at the clone/prepare boundary as a stable catalogue
        # ``STCompileError`` classified by kind, and (a) must be attributed to the
        # section/initial policy rather than the pre-existing category gate.
        func_source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        fb_call = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; END_VAR VAR A:Acc; END_VAR "
            "A(I:=I,Q=>Q,IO:=IO);")

        def fresh_func():
            return compile_st_function(
                "VAR_INPUT A:INT; END_VAR VAR_OUTPUT B:INT; END_VAR "
                "VAR_IN_OUT C:INT; END_VAR F:=A;", "F", "INT")

        def fresh_fb():
            return compile_st_function_block(
                "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
                "VAR_IN_OUT IO:INT; END_VAR VAR S:INT; END_VAR "
                "VAR_TEMP T:INT; END_VAR "
                "T:=I; S:=S+T; Q:=S; IO:=IO+1;", "Acc")

        # (a) Interface (VAR_INPUT/VAR_OUTPUT/VAR_IN_OUT) same-category initial:
        # ``initial=9`` under INT passes the category gate but the section forbids
        # any initial, so the diagnostic must prove the section policy fired.
        for index in range(3):
            with self.subTest(catalogue="FUNCTION", interface_index=index):
                func = fresh_func()
                self.assertIn(
                    func.pou.interface[index].section,
                    {"VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"})
                func.pou.interface[index].initial = 9
                exc = self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
                self.assertIn("initial value", exc.errors[0].message)
            with self.subTest(catalogue="FB", interface_index=index):
                fb = fresh_fb()
                self.assertIn(
                    fb.pou.interface[index].section,
                    {"VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"})
                fb.pou.interface[index].initial = 9
                exc = self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
                self.assertIn("initial value", exc.errors[0].message)

        # (a') FB VAR_TEMP same-category initial: legal INT category, but VAR_TEMP
        # forbids an initial (the executor discards it), so it must fail closed.
        fb = fresh_fb()
        temp = next(d for d in fb.pou.locals if d.section == "VAR_TEMP")
        self.assertEqual(temp.iec_type, "INT")
        temp.initial = 9
        exc = self.assert_code(fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
        self.assertIn("initial value", exc.errors[0].message)

        # (b) RETAIN / PERSISTENT: no strict-subset source syntax emits either, so
        # a forged flag must fail closed rather than open an unimplemented
        # persistence contract; the diagnostic names the offending flags.
        for flag in ("retain", "persistent"):
            with self.subTest(catalogue="FUNCTION", flag=flag):
                func = fresh_func()
                setattr(func.pou.interface[0], flag, True)
                exc = self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
                self.assertIn(flag, exc.errors[0].message)
            with self.subTest(catalogue="FB", flag=flag):
                fb = fresh_fb()
                setattr(fb.pou.interface[0], flag, True)
                exc = self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))
                self.assertIn(flag, exc.errors[0].message)

        # (c) instances: a forged non-empty list (silently dropped before) and a
        # hostile non-list container (whose comparison/hash hooks must never run)
        # must both fail closed as a stable catalogue error.
        for payload in ([object()], _HostileField()):
            with self.subTest(catalogue="FUNCTION", instances=type(payload).__name__):
                func = fresh_func()
                func.pou.instances = payload
                self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE", functions=(func,))
            with self.subTest(catalogue="FB", instances=type(payload).__name__):
                fb = fresh_fb()
                fb.pou.instances = payload
                self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE", function_blocks=(fb,))

    def test_catalogue_unreachable_instruction_stream_fails_closed(self):
        # WP-118 Round 4 Codex item 1: ``_validate_catalogue_pou`` delegates the
        # allowed-value / reference checks to the real Loader, whose
        # reachability-driven worklist never value-checks a dead instruction that
        # sits after an unconditional ``Jmp`` with no other in-edge.  The clone
        # helpers gate only each scalar's exact Python type, so a forged catalogue
        # could smuggle an illegal exact-typed IEC type, operator, conversion or
        # standard-signature scalar, a dangling variable reference, or an illegal
        # binding / stack slot into the returned Task through unreachable code.
        # Each must now fail closed at the clone/prepare boundary (before
        # ``validate_task``) as a stable catalogue ``STCompileError`` classified by
        # kind, and the asserted diagnostic proves the whole-stream pre-check fired
        # rather than an incidental fault.  Every injected instruction below keeps
        # its exact Python type, so before the fix it cloned and -- being
        # unreachable -- slipped past ``validate_task`` into the Task.
        func_source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        fb_call = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; END_VAR VAR A:Acc; END_VAR "
            "A(I:=I,Q=>Q,IO:=IO);")

        def with_dead(code, instruction):
            # Prepend an unconditional jump over the injected instruction to its
            # trailing label, so the Loader's worklist never reaches (and never
            # value-checks) it, while the original body still validates unchanged.
            return (Jmp("__WP118_DEAD"), instruction, Label("__WP118_DEAD")) \
                + tuple(code)

        def dead_func(instruction):
            func = compile_st_function(
                "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
            object.__setattr__(func, "code", with_dead(func.code, instruction))
            return func

        def dead_fb(instruction):
            fb = compile_st_function_block(
                "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
                "VAR_IN_OUT IO:INT; END_VAR Q:=I;", "Acc")
            object.__setattr__(fb, "code", with_dead(fb.code, instruction))
            return fb

        cases = (
            # Illegal instruction IEC type on a dead LoadVar.
            (LoadVar("A", "WP118_BAD_TYPE"),
             "type is not a supported IEC type"),
            # Illegal operator on a dead BinOp.
            (BinOp("WP118_BAD_OP", "INT"), "unsupported operator"),
            # Illegal conversion IEC type on a dead Convert.
            (Convert("INT", "WP118_BAD_TYPE"),
             "conversion uses an unsupported IEC type"),
            # Illegal standard-signature scalar (legal ABS name, illegal param
            # type) on a dead CALL_STD.
            (CallStd("ABS", StdSig(("WP118_BAD_TYPE",), "INT")),
             "CALL_STD signature uses an unsupported IEC type"),
            # Dangling variable reference on a dead LoadVar.
            (LoadVar("WP118_MISSING", "INT"),
             "references an undeclared variable"),
            # Illegal binding / negative stack slot behind an unresolvable
            # CALL_FUNC: any call in a compiled catalogue POU is dead-code
            # contraband (no sibling POU / instance resolves in isolation), so the
            # negative StackSlot and dangling target both fail closed here.
            (CallFunc(
                "WP118_MISSING",
                (Binding("A", "IN", StackSlot(-1, False), "INT"),), "INT"),
             "must not call a function or FB instance"),
            # A dead CALL_FB_INSTANCE reference fails closed the same way.
            (CallFbInstance("WP118_MISSING", ()),
             "must not call a function or FB instance"),
        )
        for instruction, needle in cases:
            kindname = type(instruction).__name__
            with self.subTest(catalogue="FUNCTION", instruction=kindname):
                exc = self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE",
                    functions=(dead_func(instruction),))
                self.assertIn(needle, exc.errors[0].message)
            with self.subTest(catalogue="FB", instruction=kindname):
                exc = self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE",
                    function_blocks=(dead_fb(instruction),))
                self.assertIn(needle, exc.errors[0].message)

    def test_catalogue_unreachable_instruction_combined_semantics_fail_closed(self):
        # WP-20260812-119 (recovery of WP-118 Round 5): the whole-stream pre-check
        # gated only per-field set membership (type in IEC_TYPES, op in the operator
        # enum, each CALL_STD type in IEC_TYPES), not the *combined* semantics the
        # Loader enforces on reachable paths.  A forged catalogue could therefore
        # smuggle a dead instruction whose every field is individually legal yet
        # whose combination the Loader rejects -- an instruction IEC type that
        # disagrees with the referenced variable's declared type, an operator/type
        # pair the Loader forbids, or a standard-function signature that violates
        # arity / type matching -- past the reachability-driven worklist into the
        # returned Task.  Each must now fail closed at the pre-check (before
        # ``validate_task``) as a stable catalogue ``STCompileError`` classified by
        # kind, with a diagnostic proving the *combination* gate fired rather than
        # the pre-existing membership gate or an incidental fault.  Every injected
        # instruction below keeps exact-legal fields, so before the fix it cloned
        # and -- being unreachable -- slipped past ``validate_task`` and was
        # accepted.
        def with_dead(code, instruction):
            return (Jmp("__WP119_DEAD"), instruction, Label("__WP119_DEAD")) \
                + tuple(code)

        def dead_func(instruction):
            # Input ``A`` and result ``F`` are both declared ``INT``.
            func = compile_st_function(
                "VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
            object.__setattr__(func, "code", with_dead(func.code, instruction))
            return func

        def dead_fb(instruction):
            # Input ``A`` is declared ``INT``; the shared name lets a type-mismatch
            # case resolve the variable in both catalogues and fail on the mismatch
            # itself rather than on an undeclared reference.
            fb = compile_st_function_block(
                "VAR_INPUT A:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
                "VAR_IN_OUT IO:INT; END_VAR Q:=A;", "Acc")
            object.__setattr__(fb, "code", with_dead(fb.code, instruction))
            return fb

        func_source = "VAR_GLOBAL A:INT; O:INT; END_VAR O:=F(A);"
        fb_call = (
            "VAR_GLOBAL I:INT; Q:INT; IO:INT; END_VAR VAR X:Acc; END_VAR "
            "X(A:=I,Q=>Q,IO:=IO);")

        # (A) LoadVar / LoadPrev / StoreVar whose instruction IEC type disagrees
        # with the referenced variable's declared ``INT`` type.  Each replacement
        # type is itself a legal IEC type and ``A`` is declared, so only the
        # type-vs-declaration mismatch can reject it.
        type_needle = "does not match its declared variable type"
        type_cases = (
            LoadVar("A", "DINT"),
            LoadPrev("A", "REAL"),
            StoreVar("A", "DINT"),
        )
        for instruction in type_cases:
            kindname = type(instruction).__name__
            with self.subTest(group="declared-type", catalogue="FUNCTION",
                              instruction=kindname):
                exc = self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE",
                    functions=(dead_func(instruction),))
                self.assertIn(type_needle, exc.errors[0].message)
            with self.subTest(group="declared-type", catalogue="FB",
                              instruction=kindname):
                exc = self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE",
                    function_blocks=(dead_fb(instruction),))
                self.assertIn(type_needle, exc.errors[0].message)

        # (B) BinOp / UnOp whose operator and IEC type are each members of their
        # enums but whose *combination* the Loader rejects (arithmetic on BOOL, MOD
        # on REAL, NEG on an unsigned type, NOT on REAL).  They carry no variable
        # reference and the pre-check probes them on a wildcard stack, so the failure
        # is the op/type pair itself, never stack residue or a dangling reference.
        combo_needle = "operator and IEC type combination is unsupported"
        combo_cases = (
            BinOp("ADD", "BOOL"),
            BinOp("MOD", "REAL"),
            UnOp("NEG", "UINT"),
            UnOp("NOT", "REAL"),
        )
        for instruction in combo_cases:
            label = "%s(%s,%s)" % (type(instruction).__name__,
                                   instruction.op, instruction.type)
            with self.subTest(group="operator", catalogue="FUNCTION", case=label):
                exc = self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE",
                    functions=(dead_func(instruction),))
                self.assertIn(combo_needle, exc.errors[0].message)
            with self.subTest(group="operator", catalogue="FB", case=label):
                exc = self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE",
                    function_blocks=(dead_fb(instruction),))
                self.assertIn(combo_needle, exc.errors[0].message)

        # (C) CallStd naming a supported standard function with every signature type
        # a legal IEC type, yet an overall signature ``standard_signature_error``
        # rejects (ABS with zero or two args, ABS arg/result mismatch, MIN with one
        # arg).  Set membership alone would accept them; only the signature contract
        # can.
        sig_needle = "signature is invalid for its standard function"
        sig_cases = (
            CallStd("ABS", StdSig((), "INT")),
            CallStd("ABS", StdSig(("INT", "INT"), "INT")),
            CallStd("ABS", StdSig(("INT",), "DINT")),
            CallStd("MIN", StdSig(("INT",), "INT")),
        )
        for instruction in sig_cases:
            label = "%s%r->%s" % (instruction.name, instruction.sig.param_types,
                                  instruction.sig.return_type)
            with self.subTest(group="callstd", catalogue="FUNCTION", case=label):
                exc = self.assert_code(
                    func_source, "INVALID_FUNCTION_CATALOGUE",
                    functions=(dead_func(instruction),))
                self.assertIn(sig_needle, exc.errors[0].message)
            with self.subTest(group="callstd", catalogue="FB", case=label):
                exc = self.assert_code(
                    fb_call, "INVALID_FB_CATALOGUE",
                    function_blocks=(dead_fb(instruction),))
                self.assertIn(sig_needle, exc.errors[0].message)

    def test_legitimate_catalogue_dead_code_and_jump_graphs_still_compile(self):
        # The whole-stream catalogue pre-check must not false-kill legitimate
        # unreachable instructions or normal jump graphs: loop ``EXIT`` /
        # ``CONTINUE`` legally leave the statements that follow them dead (the
        # lowerer emits every body statement), and IF/FOR produce ``Jmp`` /
        # ``JmpIfFalse`` graphs.  A FUNCTION and an FB whose bodies contain such
        # dead code and jump graphs must still clone into the catalogue and run.
        func = compile_st_function(
            "VAR_INPUT A:INT; END_VAR VAR I:INT; END_VAR "
            "FOR I:=1 TO 3 DO IF A>0 THEN EXIT; END_IF; END_FOR; F:=A;",
            "F", "INT")
        fb = compile_st_function_block(
            "VAR_INPUT N:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR VAR I:INT; END_VAR "
            "FOR I:=1 TO 3 DO CONTINUE; Q:=N; END_FOR; Q:=N; IO:=IO+1;", "Acc")
        compiled = compile_st_task(
            "VAR_GLOBAL X:INT; O:INT; QA:INT; IOA:INT; END_VAR "
            "VAR A:Acc; END_VAR O:=F(X); A(N:=X, Q=>QA, IO:=IOA);",
            functions=(func,), function_blocks=(fb,))
        self.assertEqual(compiled.task.pou_lib["F"].return_type, "INT")
        self.assertIn("ACC", compiled.task.pou_lib)
        runtime = build_runtime(compiled.task, build_default_registry())
        trace = _run(runtime, ({"X": 4},))[0]
        self.assertEqual(trace["O"], 4)
        self.assertEqual(trace["QA"], 4)

    def test_legitimate_catalogue_scalar_values_still_compile(self):
        # The value-level catalogue gate must not reject well-formed compiled
        # FUNCTION/FB catalogues: legal enum/type/mode, a supported CALL_STD name
        # (ABS) and IEC-consistent constants must still clone and run.
        add = compile_st_function(
            "VAR_INPUT A:INT; B:INT; END_VAR Add2:=A+B;", "Add2", "INT")
        absinc = compile_st_function(
            "VAR_INPUT A:INT; END_VAR AbsInc:=ABS(A)+1;", "AbsInc", "INT")
        acc = compile_st_function_block(
            "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR VAR S:INT; END_VAR "
            "S:=S+I; Q:=S; IO:=IO+1;", "Acc")
        compiled = compile_st_task(
            "VAR_GLOBAL X:INT; Y:INT; O:INT; P:INT; QA:INT; IOA:INT; END_VAR "
            "VAR A:Acc; END_VAR "
            "O:=Add2(X, Y); P:=AbsInc(X); A(I:=X, Q=>QA, IO:=IOA);",
            functions=(add, absinc), function_blocks=(acc,))
        self.assertEqual(
            compiled.task.pou_lib["ADD2"].return_type, "INT")
        self.assertEqual(
            compiled.task.pou_lib["ABSINC"].return_type, "INT")
        self.assertEqual(
            [item.section for item in compiled.task.pou_lib["ACC"].interface],
            ["VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"])
        runtime = build_runtime(compiled.task, build_default_registry())
        trace = _run(runtime, ({"X": 2, "Y": 5},))[0]
        self.assertEqual(trace["O"], 7)
        self.assertEqual(trace["P"], 3)
        # A legal non-None initial (a VAR local with an IEC-consistent literal) and
        # legal ``None`` initials (all interface params) must survive the value
        # gate unchanged: the compiled catalogue preserves the seeded initial.
        seeded = compile_st_function_block(
            "VAR_INPUT I:INT; END_VAR VAR_OUTPUT Q:INT; END_VAR "
            "VAR_IN_OUT IO:INT; END_VAR VAR S:INT := 7; END_VAR "
            "VAR_TEMP T:INT; END_VAR "
            "T:=I; S:=S+T; Q:=S; IO:=IO+1;", "Seed")
        seeded_task = compile_st_task(
            "VAR_GLOBAL X:INT; QB:INT; IOB:INT; END_VAR VAR B:Seed; END_VAR "
            "B(I:=X, Q=>QB, IO:=IOB);",
            function_blocks=(seeded,)).task
        seed_pou = seeded_task.pou_lib["SEED"]
        seed_locals = {decl.name: decl for decl in seed_pou.locals}
        # A legal ``VAR`` non-None initial survives the section policy + category
        # gate, while a VAR_TEMP with no initial and all interface params keep
        # ``None`` -- the exact shapes the front end emits.
        self.assertEqual(seed_locals["S"].initial, 7)
        self.assertEqual(seed_locals["S"].section, "VAR")
        self.assertIsNone(seed_locals["T"].initial)
        self.assertEqual(seed_locals["T"].section, "VAR_TEMP")
        self.assertEqual(
            {decl.initial for decl in seed_pou.interface}, {None})
        # Default false RETAIN/PERSISTENT flags and an empty ``instances`` list
        # survive unchanged (the fail-closed gates only reject forged values).
        self.assertFalse(any(
            decl.retain or decl.persistent
            for decl in seed_pou.interface + seed_pou.locals))
        self.assertEqual(seed_pou.instances, [])

    def test_function_catalogue_source_is_disconnected_from_caller(self):
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        hostile_source = ["caller-owned"]
        func.pou.source = hostile_source
        task = compile_st_task(
            "VAR_GLOBAL X:INT; O:INT; END_VAR O:=F(X);",
            functions=(func,)).task
        task_function = task.pou_lib["F"]
        self.assertIsNone(task_function.source)
        hostile_source.append("mutated")
        self.assertIsNone(task_function.source)
        self.assertEqual(hostile_source, ["caller-owned", "mutated"])

    def test_failed_catalogue_compile_does_not_mutate_caller_input(self):
        func = compile_st_function("VAR_INPUT A:INT; END_VAR F:=A;", "F", "INT")
        names = [decl.name for decl in func.pou.interface]
        body = tuple(func.pou.code)
        return_type = func.pou.return_type
        with self.assertRaises(STCompileError):
            compile_st_task(
                "VAR_GLOBAL Y:DINT; O:INT; END_VAR O:=F(Y);", functions=(func,))
        self.assertEqual([decl.name for decl in func.pou.interface], names)
        self.assertEqual(tuple(func.pou.code), body)
        self.assertEqual(func.pou.return_type, return_type)

    def test_for_counter_indirect_writes_via_calls_fail_closed(self):
        mix = compile_st_function("""
            VAR_INPUT A:INT; END_VAR
            VAR_OUTPUT B:INT; END_VAR
            VAR_IN_OUT C:INT; END_VAR
            Mix:=A;
        """, "Mix", "INT")
        # FUNCTION VAR_OUTPUT actual bound to the active FOR counter.
        self.assert_code(
            "VAR_GLOBAL I:INT; A:INT; C:INT; O:INT; END_VAR "
            "FOR I:=1 TO 3 DO O:=Mix(A:=A,B=>I,C:=C); END_FOR;",
            "FOR_COUNTER_WRITE", functions=(mix,))
        # FUNCTION VAR_IN_OUT actual bound to the active FOR counter.
        self.assert_code(
            "VAR_GLOBAL I:INT; A:INT; B:INT; O:INT; END_VAR "
            "FOR I:=1 TO 3 DO O:=Mix(A:=A,B=>B,C:=I); END_FOR;",
            "FOR_COUNTER_WRITE", functions=(mix,))

        fb = compile_st_function_block("""
            VAR_INPUT N:INT; END_VAR
            VAR_OUTPUT Q:INT; END_VAR
            VAR_IN_OUT IO:INT; END_VAR
            Q:=N;
        """, "Acc")
        # User FB VAR_OUTPUT actual bound to the active FOR counter.
        self.assert_code(
            "VAR_GLOBAL I:INT; G:INT; END_VAR VAR A:Acc; END_VAR "
            "FOR I:=1 TO 3 DO A(N:=1,Q=>I,IO:=G); END_FOR;",
            "FOR_COUNTER_WRITE", function_blocks=(fb,))
        # User FB VAR_IN_OUT actual bound to the active FOR counter.
        self.assert_code(
            "VAR_GLOBAL I:INT; G:INT; END_VAR VAR A:Acc; END_VAR "
            "FOR I:=1 TO 3 DO A(N:=1,Q=>G,IO:=I); END_FOR;",
            "FOR_COUNTER_WRITE", function_blocks=(fb,))

    def test_primitive_library_fb_source_call_failures_are_stable(self):
        base = (
            "VAR_GLOBAL Start:BOOL; Delay:TIME; Done:BOOL; Elapsed:TIME; "
            "Wrong:INT; END_VAR VAR T:TON; END_VAR ")
        cases = (
            (base + "T(Start,Delay,Done,Elapsed);", "EXPLICIT_BINDING_REQUIRED"),
            (base + "T(IN:=Start,IN:=Start,PT:=Delay,Q=>Done,ET=>Elapsed);",
             "DUPLICATE_CALL_FORMAL"),
            (base + "T(IN:=Start,PT_ms:=Delay,Q=>Done,ET=>Elapsed);",
             "UNKNOWN_CALL_FORMAL"),
            (base + "T(IN:=Start,PT:=Delay,Q=>Done);", "MISSING_CALL_FORMAL"),
            (base + "T(IN=>Start,PT:=Delay,Q=>Done,ET=>Elapsed);",
             "CALL_DIRECTION_MISMATCH"),
            (base + "T(IN:=Start,PT:=Delay,Q:=Done,ET=>Elapsed);",
             "CALL_DIRECTION_MISMATCH"),
            (base + "T(IN:=Start,PT:=Delay,Q=>Done.X,ET=>Elapsed);",
             "CALL_ACTUAL_NOT_WRITABLE"),
            (base + "T(IN:=Start,PT:=Wrong,Q=>Done,ET=>Elapsed);",
             "TYPE_MISMATCH"),
            ("VAR_GLOBAL T:TON; END_VAR", "UNSUPPORTED_LIBRARY_INSTANCE_SECTION"),
            ("VAR T:TON:=1; END_VAR", "UNSUPPORTED_LIBRARY_INSTANCE_INITIALIZER"),
        )
        for source, code in cases:
            with self.subTest(code=code):
                self.assert_code(source, code)

        ton_function = compile_st_function(
            "VAR_INPUT X:INT; END_VAR TON:=X;", "TON", "INT")
        self.assert_code("", "FUNCTION_NAME_COLLISION", functions=(ton_function,))
        with patch(
                "src.runtime.st_lowering.library_source_aliases",
                return_value={"TON": {"IN": "IN"}}):
            self.assert_code("", "INVALID_LIBRARY_ALIAS_CONTRACT")

    def test_required_only_business_library_pin_contracts_fail_closed(self):
        # Every currently opened business source pin is required.  The cases use
        # explicit source syntax rather than deriving a spelling from Schema so
        # they remain a front-end contract test.
        blocks = (
            ("APCSTATISTICS", "S", """
                VAR_GLOBAL In:REAL; Reset:BOOL; Mn:REAL; Mx:REAL; Avg:LREAL;
                WrongBool:BOOL; WrongReal:REAL; END_VAR
                VAR S:APCSTATISTICS; END_VAR
            """, (
                ("IN", "IN:=In", "WrongBool"),
                ("RESET", "RESET:=Reset", "WrongReal"),
                ("MN", "MN=>Mn", "WrongBool"),
                ("MX", "MX=>Mx", "WrongBool"),
                ("AVG", "AVG=>Avg", "WrongReal"),
            )),
            ("APCHSFOP", "F", """
                VAR_GLOBAL In:REAL; Tc:REAL; Kg:REAL; Tb:REAL; Av:REAL;
                WrongBool:BOOL; END_VAR
                VAR F:APCHSFOP; END_VAR
            """, (
                ("IN", "IN:=In", "WrongBool"),
                ("TC", "TC:=Tc", "WrongBool"),
                ("KG", "KG:=Kg", "WrongBool"),
                ("TB", "TB:=Tb", "WrongBool"),
                ("AV", "AV=>Av", "WrongBool"),
            )),
            ("APCHSRATELIM", "R", """
                VAR_GLOBAL In:REAL; Hl:REAL; Ll:REAL; Av:REAL; WrongBool:BOOL;
                END_VAR
                VAR R:APCHSRATELIM; END_VAR
            """, (
                ("IN", "IN:=In", "WrongBool"),
                ("HL", "HL:=Hl", "WrongBool"),
                ("LL", "LL:=Ll", "WrongBool"),
                ("AV", "AV=>Av", "WrongBool"),
            )),
            ("APCHSHLLIM", "H", """
                VAR_GLOBAL In:REAL; Hl:REAL; Ll:REAL; Av:REAL; WrongBool:BOOL;
                END_VAR
                VAR H:APCHSHLLIM; END_VAR
            """, (
                ("IN", "IN:=In", "WrongBool"),
                ("HL", "HL:=Hl", "WrongBool"),
                ("LL", "LL:=Ll", "WrongBool"),
                ("AV", "AV=>Av", "WrongBool"),
            )),
            ("APCGCQ", "G", """
                VAR_GLOBAL In:REAL; Tc:REAL; Tz:REAL; K:REAL; Insp:REAL;
                           Gc1:REAL; Gc2:REAL; Outh:REAL; Outl:REAL; Outv:REAL;
                           Gcav:REAL; Jtav:REAL; Dtav:REAL; WrongBool:BOOL;
                           END_VAR
                VAR G:APCGCQ; END_VAR
            """, (
                ("IN", "IN:=In", "WrongBool"),
                ("TC", "TC:=Tc", "WrongBool"),
                ("TZ", "TZ:=Tz", "WrongBool"),
                ("K", "K:=K", "WrongBool"),
                ("INSP", "INSP:=Insp", "WrongBool"),
                ("GC1", "GC1:=Gc1", "WrongBool"),
                ("GC2", "GC2:=Gc2", "WrongBool"),
                ("OUTH", "OUTH:=Outh", "WrongBool"),
                ("OUTL", "OUTL:=Outl", "WrongBool"),
                ("OUTV", "OUTV:=Outv", "WrongBool"),
                ("GCAV", "GCAV=>Gcav", "WrongBool"),
                ("JTAV", "JTAV=>Jtav", "WrongBool"),
                ("DTAV", "DTAV=>Dtav", "WrongBool"),
            )),
        )
        before_aliases = library_source_aliases()
        before_keys = build_default_registry().keys()
        for block_type, instance, declarations, arguments in blocks:
            with self.subTest(block=block_type, check="all-pins-explicit"):
                self.assertEqual(
                    tuple(name for name, _argument, _wrong in arguments),
                    tuple(library_source_aliases()[block_type]))
            for missing, _argument, _wrong in arguments:
                source = declarations + "%s(%s);" % (
                    instance, ",".join(
                        argument for name, argument, _wrong in arguments
                        if name != missing))
                with self.subTest(block=block_type, pin=missing, check="missing"):
                    self.assert_code(source, "MISSING_CALL_FORMAL")
            for name, argument, wrong in arguments:
                if ":=" in argument:
                    wrong_direction = argument.replace(":=", "=>")
                else:
                    wrong_direction = argument.replace("=>", ":=")
                direction_source = declarations + "%s(%s);" % (
                    instance, ",".join(
                        wrong_direction if item_name == name else item_argument
                        for item_name, item_argument, _wrong in arguments))
                with self.subTest(block=block_type, pin=name, check="direction"):
                    self.assert_code(direction_source, "CALL_DIRECTION_MISMATCH")
                wrong_actual = argument.split(":=")[0] + ":=" + wrong \
                    if ":=" in argument else argument.split("=>")[0] + "=>" + wrong
                type_source = declarations + "%s(%s);" % (
                    instance, ",".join(
                        wrong_actual if item_name == name else item_argument
                        for item_name, item_argument, _wrong in arguments))
                with self.subTest(block=block_type, pin=name, check="type"):
                    self.assert_code(type_source, "TYPE_MISMATCH")
            unknown_source = declarations + "%s(UNKNOWN:=WrongBool,%s);" % (
                instance, ",".join(argument for _name, argument, _wrong in arguments))
            with self.subTest(block=block_type, check="unknown"):
                self.assert_code(unknown_source, "UNKNOWN_CALL_FORMAL")
        # WP-20260817-129: persist the WP-128 duplicate-formal counter-example as
        # a direct regression for APCHSHLLIM and APCGCQ.  Each case takes the
        # fully-valid required call and re-binds one already-legal source formal a
        # second time.  The duplicate scan (st_lowering.py library-FB path) fires
        # before the missing/direction/type checks, so every required pin stays
        # bound exactly once through the untouched arguments and the sole defect is
        # the duplicate itself; dropping the extra binding would compile cleanly.
        duplicate_targets = {"APCHSHLLIM", "APCGCQ"}
        for block_type, instance, declarations, arguments in blocks:
            if block_type not in duplicate_targets:
                continue
            valid = tuple(argument for _name, argument, _wrong in arguments)
            duplicate_source = declarations + "%s(%s);" % (
                instance, ",".join(valid + (valid[0],)))
            with self.subTest(block=block_type, check="duplicate"):
                self.assert_code(duplicate_source, "DUPLICATE_CALL_FORMAL")
        self.assertEqual(library_source_aliases(), before_aliases)
        self.assertEqual(build_default_registry().keys(), before_keys)
        self.assertTrue(build_default_registry().has("APCHSACCUM"))
        self.assertIsNotNone(compile_st_task("VAR A:APCHSACCUM; END_VAR").task)

    def test_use_default_business_library_pin_contracts_fail_closed(self):
        blocks = (
            ("APCHSACCUM", "A", """
                VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL;
                           WrongBool:BOOL; WrongReal:REAL; END_VAR
                VAR A:APCHSACCUM; END_VAR
            """, (
                ("I1", "I1:=I1", "WrongBool", False),
                ("RS", "RS:=Rs", "WrongReal", False),
                ("AV", "AV=>Av", "WrongReal", True),
                ("SS", "SS=>Ss", "WrongReal", True),
            )),
            ("APCHXHCL", "X", """
                VAR_GLOBAL En:BOOL; Pv:REAL; Fv:REAL; Pvh:REAL; Pvl:REAL;
                           Bhslh:REAL; Tl:REAL; Tc:REAL; Kg:REAL; Tb:REAL;
                           Av:REAL; Gzdv:BOOL; PvAvg:REAL; FvAvg:REAL;
                           WrongBool:BOOL; WrongReal:REAL; END_VAR
                VAR X:APCHXHCL; END_VAR
            """, (
                ("EN", "EN:=En", "WrongReal", True),
                ("PV", "PV:=Pv", "WrongBool", True),
                ("FV", "FV:=Fv", "WrongBool", True),
                ("PVH", "PVH:=Pvh", "WrongBool", False),
                ("PVL", "PVL:=Pvl", "WrongBool", False),
                ("BHSLH", "BHSLH:=Bhslh", "WrongBool", False),
                ("TL", "TL:=Tl", "WrongBool", False),
                ("TC", "TC:=Tc", "WrongBool", False),
                ("KG", "KG:=Kg", "WrongBool", False),
                ("TB", "TB:=Tb", "WrongBool", False),
                ("AV", "AV=>Av", "WrongBool", True),
                ("GZDV", "GZDV=>Gzdv", "WrongReal", True),
                ("PV_AVG", "PV_AVG=>PvAvg", "WrongBool", True),
                ("FV_AVG", "FV_AVG=>FvAvg", "WrongBool", True),
            )),
            ("APCSPFINDER", "F", """
                VAR_GLOBAL En:BOOL; Reset:BOOL; Cycle:REAL; SampleOk:BOOL;
                           SpMan:REAL; SpManEn:BOOL; SpTag:REAL; SpTagEn:BOOL;
                           SpAutoEn:BOOL; SpAutoReplaceBadTag:BOOL; Pv:REAL;
                           Av:REAL; Pvmu:REAL; Pvmd:REAL; Outt:REAL; Outb:REAL;
                           SpStableT:REAL; SpConfT:REAL; PvStableK:REAL;
                           AvStableK:REAL; PvStableAbs:REAL; AvStableAbs:REAL;
                           SpBadK:REAL; SpBadAbs:REAL; SpUse:REAL; SpValid:BOOL;
                           SpSource:INT; SpReason:INT; SpAuto:REAL; SpAutoOk:BOOL;
                           SpAutoConf:REAL; SpTagBad:BOOL; SpStableTOut:REAL;
                           SpStablePvRange:REAL; WrongBool:BOOL; WrongReal:REAL;
                           END_VAR
                VAR F:APCSPFINDER; END_VAR
            """, (
                ("EN", "EN:=En", "WrongReal", True),
                ("RESET", "RESET:=Reset", "WrongReal", True),
                ("CYCLE", "CYCLE:=Cycle", "WrongBool", False),
                ("SAMPLE_OK", "SAMPLE_OK:=SampleOk", "WrongReal", True),
                ("SP_MAN", "SP_MAN:=SpMan", "WrongBool", False),
                ("SP_MAN_EN", "SP_MAN_EN:=SpManEn", "WrongReal", False),
                ("SP_TAG", "SP_TAG:=SpTag", "WrongBool", False),
                ("SP_TAG_EN", "SP_TAG_EN:=SpTagEn", "WrongReal", False),
                ("SP_AUTO_EN", "SP_AUTO_EN:=SpAutoEn", "WrongReal", False),
                ("SP_AUTO_REPLACE_BAD_TAG", "SP_AUTO_REPLACE_BAD_TAG:=SpAutoReplaceBadTag", "WrongReal", False),
                ("PV", "PV:=Pv", "WrongBool", True),
                ("AV", "AV:=Av", "WrongBool", True),
                ("PVMU", "PVMU:=Pvmu", "WrongBool", False),
                ("PVMD", "PVMD:=Pvmd", "WrongBool", False),
                ("OUTT", "OUTT:=Outt", "WrongBool", False),
                ("OUTB", "OUTB:=Outb", "WrongBool", False),
                ("SP_STABLE_T", "SP_STABLE_T:=SpStableT", "WrongBool", False),
                ("SP_CONF_T", "SP_CONF_T:=SpConfT", "WrongBool", False),
                ("PV_STABLE_K", "PV_STABLE_K:=PvStableK", "WrongBool", False),
                ("AV_STABLE_K", "AV_STABLE_K:=AvStableK", "WrongBool", False),
                ("PV_STABLE_ABS", "PV_STABLE_ABS:=PvStableAbs", "WrongBool", False),
                ("AV_STABLE_ABS", "AV_STABLE_ABS:=AvStableAbs", "WrongBool", False),
                ("SP_BAD_K", "SP_BAD_K:=SpBadK", "WrongBool", False),
                ("SP_BAD_ABS", "SP_BAD_ABS:=SpBadAbs", "WrongBool", False),
                ("SP_USE", "SP_USE=>SpUse", "WrongBool", True),
                ("SP_VALID", "SP_VALID=>SpValid", "WrongReal", True),
                ("SP_SOURCE", "SP_SOURCE=>SpSource", "WrongReal", True),
                ("SP_REASON", "SP_REASON=>SpReason", "WrongReal", True),
                ("SP_AUTO", "SP_AUTO=>SpAuto", "WrongBool", True),
                ("SP_AUTO_OK", "SP_AUTO_OK=>SpAutoOk", "WrongReal", True),
                ("SP_AUTO_CONF", "SP_AUTO_CONF=>SpAutoConf", "WrongBool", True),
                ("SP_TAG_BAD", "SP_TAG_BAD=>SpTagBad", "WrongReal", True),
                ("SP_STABLE_T_OUT", "SP_STABLE_T_OUT=>SpStableTOut", "WrongBool", True),
                ("SP_STABLE_PV_RANGE", "SP_STABLE_PV_RANGE=>SpStablePvRange", "WrongBool", True),
            )),
            ("APCPIDZZD", "P", """
                VAR_GLOBAL Av:REAL; Sp:REAL; Pv:REAL; Pt:REAL; Ti:REAL; Rm:INT;
                           Pvmu:REAL; Pvmd:REAL; Mu:REAL; Md:REAL; Sadd:BOOL; Ssub:BOOL;
                           Pt1k:REAL; Ti1k:REAL; Pt1:REAL; Ti1:REAL;
                           WrongBool:BOOL; WrongReal:REAL; END_VAR
                VAR P:APCPIDZZD; END_VAR
            """, (
                ("AV", "AV:=Av", "WrongBool", True),
                ("SP", "SP:=Sp", "WrongBool", True),
                ("PV", "PV:=Pv", "WrongBool", True),
                ("PT", "PT:=Pt", "WrongBool", True),
                ("TI", "TI:=Ti", "WrongBool", True),
                ("RM", "RM:=Rm", "WrongReal", False),
                ("PVMU", "PVMU:=Pvmu", "WrongBool", True),
                ("PVMD", "PVMD:=Pvmd", "WrongBool", True),
                ("MU", "MU:=Mu", "WrongBool", True),
                ("MD", "MD:=Md", "WrongBool", True),
                ("SADD", "SADD:=Sadd", "WrongReal", True),
                ("SSUB", "SSUB:=Ssub", "WrongReal", True),
                ("PT1K", "PT1K:=Pt1k", "WrongBool", False),
                ("TI1K", "TI1K:=Ti1k", "WrongBool", False),
                ("PT1", "PT1=>Pt1", "WrongBool", True),
                ("TI1", "TI1=>Ti1", "WrongBool", True),
            )),
            ("APCPID", "P", """
                VAR_GLOBAL Sp:REAL; Pv:REAL; Ic:REAL; Oc:REAL; Tp:REAL; Ts:BOOL;
                           Rm:INT; Outt:REAL; Outb:REAL; Sadd:BOOL; Ssub:BOOL;
                           Pt:REAL; Ti:REAL; Kd:REAL; Td:REAL; Av:REAL;
                           WrongBool:BOOL; WrongReal:REAL; END_VAR
                VAR P:APCPID; END_VAR
            """, (
                ("SP", "SP:=Sp", "WrongBool", True),
                ("PV", "PV:=Pv", "WrongBool", True),
                ("IC", "IC:=Ic", "WrongBool", False),
                ("OC", "OC:=Oc", "WrongBool", False),
                ("TP", "TP:=Tp", "WrongBool", True),
                ("TS", "TS:=Ts", "WrongReal", True),
                ("RM", "RM:=Rm", "WrongReal", True),
                ("OutT", "OutT:=Outt", "WrongBool", True),
                ("OutB", "OutB:=Outb", "WrongBool", True),
                ("SADD", "SADD:=Sadd", "WrongReal", True),
                ("SSUB", "SSUB:=Ssub", "WrongReal", True),
                ("PT", "PT:=Pt", "WrongBool", True),
                ("TI", "TI:=Ti", "WrongBool", True),
                ("KD", "KD:=Kd", "WrongBool", False),
                ("TD", "TD:=Td", "WrongBool", False),
                ("AV", "AV=>Av", "WrongBool", True),
            )),
        )
        for block_type, instance, declarations, arguments in blocks:
            required = tuple(item for item in arguments if item[3])
            optional = tuple(item for item in arguments if not item[3])
            with self.subTest(block=block_type, check="required-bindings"):
                result = compile_st_task(declarations + "%s(%s);" % (
                    instance, ",".join(argument for _name, argument, _wrong, _required
                                       in required)))
                for name, _argument, _wrong, _required in optional:
                    schema, _adapter = build_default_registry().resolve(
                        block_type, "engineering")
                    self.assertNotIn(
                        StoreVar("%s.%s" % (instance, name), schema.pin(name).iec_type),
                        result.code)
            for name, _argument, _wrong, _required in required:
                source = declarations + "%s(%s);" % (
                    instance, ",".join(
                        argument for item_name, argument, _wrong, _required in required
                        if item_name != name))
                with self.subTest(block=block_type, pin=name, check="missing"):
                    self.assert_code(source, "MISSING_CALL_FORMAL")
            # Direction/type validation is a per-pin contract: a required pin
            # must not receive weaker coverage merely because it cannot be
            # omitted.  In particular this keeps the first ctor-injected
            # business block's inputs and outputs on the same fail-closed
            # matrix as its use_default pins.
            for name, argument, wrong, _required in arguments:
                wrong_direction = argument.replace(":=", "=>") \
                    if ":=" in argument else argument.replace("=>", ":=")
                direction_source = declarations + "%s(%s);" % (
                    instance, ",".join(
                        wrong_direction if item_name == name else item_argument
                        for item_name, item_argument, _wrong, _required in arguments))
                with self.subTest(block=block_type, pin=name, check="direction"):
                    self.assert_code(direction_source, "CALL_DIRECTION_MISMATCH")
                wrong_actual = argument.split(":=")[0] + ":=" + wrong \
                    if ":=" in argument else argument.split("=>")[0] + "=>" + wrong
                type_source = declarations + "%s(%s);" % (
                    instance, ",".join(
                        wrong_actual if item_name == name else item_argument
                        for item_name, item_argument, _wrong, _required in arguments))
                with self.subTest(block=block_type, pin=name, check="type"):
                    self.assert_code(type_source, "TYPE_MISMATCH")
            required_call = ",".join(
                argument for _name, argument, _wrong, _required in required)
            with self.subTest(block=block_type, check="unknown"):
                self.assert_code(
                    declarations + "%s(UNKNOWN:=WrongBool,%s);" %
                    (instance, required_call), "UNKNOWN_CALL_FORMAL")
            with self.subTest(block=block_type, check="duplicate"):
                self.assert_code(
                    declarations + "%s(%s,%s);" %
                    (instance, required_call, required[0][1]),
                    "DUPLICATE_CALL_FORMAL")

        registry = build_default_registry()
        schema, adapter = registry.resolve("APCHSACCUM", "engineering")
        registry._entries[("APCHSACCUM", "engineering")] = (
            replace(schema, inputs=(
                replace(schema.inputs[0], omit_policy="none_means_no_write"),
                schema.inputs[1],
            )), adapter)
        with patch("src.runtime.st_lowering.build_default_registry", return_value=registry), \
                patch("src.runtime.st_lowering.library_source_aliases", return_value={
                    "APCHSACCUM": {"I1": "I1", "RS": "RS", "AV": "AV", "SS": "SS"},
                }):
            result = compile_st_task("""
                VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR
                VAR A:APCHSACCUM; END_VAR
                A(RS:=Rs,AV=>Av,SS=>Ss);
            """)
            self.assertNotIn(StoreVar("A.I1", "REAL"), result.code)

    def test_library_schema_pin_policy_requires_exact_strings_and_direction(self):
        source = """
            VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR
            VAR A:APCHSACCUM; END_VAR
            A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);
        """
        self.assertIsNotNone(compile_st_task(source).task)

        for field, value, code in (
                ("kind", _EqualStr("VAR_INPUT"), "INVALID_LIBRARY_ALIAS_CONTRACT"),
                ("omit_policy", _EqualStr("use_default"),
                 "INVALID_LIBRARY_ALIAS_CONTRACT"),
                ("kind", "VAR_OUTPUT", "INVALID_LIBRARY_ALIAS_CONTRACT"),
                ("omit_policy", "invented", "INVALID_LIBRARY_ALIAS_CONTRACT")):
            registry = build_default_registry()
            schema, adapter = registry.resolve("APCHSACCUM", "engineering")
            schema = replace(
                schema,
                inputs=tuple(replace(pin) for pin in schema.inputs),
                outputs=tuple(replace(pin) for pin in schema.outputs),
                inouts=tuple(replace(pin) for pin in schema.inouts),
            )
            registry._entries[("APCHSACCUM", "engineering")] = (schema, adapter)
            object.__setattr__(schema.inputs[0], field, value)
            with self.subTest(field=field, value=value), \
                    patch("src.runtime.st_lowering.build_default_registry",
                          return_value=registry):
                self.assert_code(source, code)

        for field, code in (
                ("kind", "INVALID_LIBRARY_ALIAS_CONTRACT"),
                ("omit_policy", "INVALID_LIBRARY_ALIAS_CONTRACT"),
                ("name", "INVALID_LIBRARY_ALIAS_CONTRACT"),
                ("iec_type", "INVALID_LIBRARY_ALIAS_CONTRACT")):
            registry = build_default_registry()
            schema, adapter = registry.resolve("APCHSACCUM", "engineering")
            schema = replace(
                schema,
                inputs=tuple(replace(pin) for pin in schema.inputs),
                outputs=tuple(replace(pin) for pin in schema.outputs),
                inouts=tuple(replace(pin) for pin in schema.inouts),
            )
            registry._entries[("APCHSACCUM", "engineering")] = (schema, adapter)
            object.__delattr__(schema.inputs[0], field)
            with self.subTest(field=field, check="missing-instance-field"), \
                    patch("src.runtime.st_lowering.build_default_registry",
                          return_value=registry):
                self.assert_code(source, code)

        registry = build_default_registry()
        schema, adapter = registry.resolve("APCHSACCUM", "engineering")
        schema = replace(
            schema,
            inputs=tuple(replace(pin) for pin in schema.inputs),
            outputs=tuple(replace(pin) for pin in schema.outputs),
            inouts=tuple(replace(pin) for pin in schema.inouts),
        )
        registry._entries[("APCHSACCUM", "engineering")] = (schema, adapter)
        object.__setattr__(schema.inputs[0], "default", _HostileField())
        with patch("src.runtime.st_lowering.build_default_registry",
                   return_value=registry):
            self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

        registry = build_default_registry()
        schema, adapter = registry.resolve("APCHSACCUM", "engineering")
        schema = replace(
            schema,
            inputs=tuple(replace(pin) for pin in schema.inputs),
            outputs=tuple(replace(pin) for pin in schema.outputs),
            inouts=tuple(replace(pin) for pin in schema.inouts),
        )
        registry._entries[("APCHSACCUM", "engineering")] = (schema, adapter)
        object.__setattr__(
            schema, "inputs", (_PseudoPin(schema.inputs[0]), schema.inputs[1]))
        with patch("src.runtime.st_lowering.build_default_registry",
                   return_value=registry):
            self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

    def test_hostile_resolved_schema_shell_fails_closed_without_observation(self):
        """A resolved-Schema shell whose ``inputs`` / ``inouts`` / ``outputs``
        are observed hooks must converge to the catalogue
        ``INVALID_LIBRARY_ALIAS_CONTRACT`` ``STCompileError`` via the identity-
        only ``type(schema) is BlockSchema`` gate, before any pin attribute is
        read (Codex WP-141 Round 1 §4 zero-observation contract)."""
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")
        self.assertIsNotNone(compile_st_task(source).task)
        registry = build_default_registry()
        _schema, adapter = registry.resolve("APCHSACCUM", "engineering")
        registry._entries[("APCHSACCUM", "engineering")] = (
            _HostileSchemaShell(), adapter)
        _HostileSchemaShell.pin_reads = 0
        with patch("src.runtime.st_lowering.build_default_registry",
                   return_value=registry):
            self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")
        self.assertEqual(_HostileSchemaShell.pin_reads, 0)

    def test_exact_blockschema_shell_requires_complete_field_shape(self):
        """Exact ``BlockSchema`` objects with a missing or extra instance field
        fail at the catalogue boundary before any pin field is consumed.

        This complements the hostile non-``BlockSchema`` probe above: identity
        alone is insufficient when privileged mutation has made an otherwise
        exact carrier incomplete or shape-extended.
        """
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")
        for mutation in ("missing", "extra"):
            with self.subTest(mutation=mutation):
                registry = build_default_registry()
                schema, adapter = registry.resolve("APCHSACCUM", "engineering")
                schema = replace(
                    schema,
                    inputs=tuple(replace(pin) for pin in schema.inputs),
                    outputs=tuple(replace(pin) for pin in schema.outputs),
                    inouts=tuple(replace(pin) for pin in schema.inouts),
                )
                if mutation == "missing":
                    object.__delattr__(schema, "inputs")
                else:
                    object.__setattr__(schema, "unexpected_shell_field", None)
                registry._entries[("APCHSACCUM", "engineering")] = (
                    schema, adapter)
                with patch("src.runtime.st_lowering.build_default_registry",
                           return_value=registry):
                    self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

    def test_exact_blockschema_nonpin_carriers_fail_closed_without_hooks(self):
        """Every non-pin field has an exact frozen-dataclass carrier contract.

        This is deliberately table-driven from ``BlockSchema``'s dataclass
        fields.  It exercises wrong, hostile, missing and extra instance
        carriers without copying a business descriptor's pin rules.
        """
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")
        field_names = tuple(BlockSchema.__dataclass_fields__)
        nonpins = tuple(name for name in field_names
                        if name not in ("inputs", "outputs", "inouts"))
        wrong = {
            "block_type": 1,
            "variant": 1,
            "descriptor_version": 1,
            "state_vars": (),
            "retainable": (),
            "init_overridable": (),
            "hmi_writable": (),
            "output_access": (),
        }

        def mutable_schema():
            registry = build_default_registry()
            schema, adapter = registry.resolve("APCHSACCUM", "engineering")
            shell = replace(
                schema,
                inputs=tuple(replace(pin) for pin in schema.inputs),
                outputs=tuple(replace(pin) for pin in schema.outputs),
                inouts=tuple(replace(pin) for pin in schema.inouts),
            )
            registry._entries[("APCHSACCUM", "engineering")] = (shell, adapter)
            return registry, shell

        for field in nonpins:
            for carrier in (wrong[field], _HostileField()):
                with self.subTest(field=field, carrier=type(carrier).__name__):
                    registry, schema = mutable_schema()
                    object.__setattr__(schema, field, carrier)
                    with patch("src.runtime.st_lowering.build_default_registry",
                               return_value=registry):
                        self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

            with self.subTest(field=field, carrier="missing"):
                registry, schema = mutable_schema()
                object.__delattr__(schema, field)
                with patch("src.runtime.st_lowering.build_default_registry",
                           return_value=registry):
                    self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

        for field in ("state_vars", "retainable", "init_overridable", "hmi_writable"):
            with self.subTest(field=field, carrier="str-subclass-member"):
                registry, schema = mutable_schema()
                object.__setattr__(schema, field, frozenset({_EqualStr("STATE")}))
                with patch("src.runtime.st_lowering.build_default_registry",
                           return_value=registry):
                    self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

        registry, schema = mutable_schema()
        object.__setattr__(schema, "unexpected_shell_field", None)
        with patch("src.runtime.st_lowering.build_default_registry",
                   return_value=registry):
            self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

    def test_exact_blockschema_catalogue_uses_object_getattribute_only(self):
        """Class-level Schema hooks are rejected before Loader can observe them."""
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")
        guarded = frozenset(BlockSchema.__dataclass_fields__)
        original = BlockSchema.__getattribute__

        def observe(schema, name):
            if name in guarded:
                _HostileBlockSchemaGetattribute.reads += 1
                raise _CatalogueProbe("SCHEMA_FIELD_OBSERVED")
            return original(schema, name)

        _HostileBlockSchemaGetattribute.reads = 0
        registry = build_default_registry()
        with patch.object(BlockSchema, "__getattribute__", observe), \
                patch("src.runtime.st_lowering.build_default_registry",
                      return_value=registry):
            self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")
        self.assertEqual(_HostileBlockSchemaGetattribute.reads, 0)

    def test_catalogue_snapshot_rejects_original_pin_observation_and_context_drift(self):
        """The compiler must consume one trusted snapshot, not mutable sources.

        These are deliberately complete ``compile_st_task`` probes.  They lock
        both the pre-Lowering boundary and the subsequent Loader validation path:
        once an exact catalogue object has been observed, no original Pin/Schema
        field may be read again.
        """
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")

        # (A) A valid exact Pin with a class-level observer used to pass prepare
        # and leak when Lowering rebuilt its lookup table from original Pins.
        original_pin_getattribute = Pin.__getattribute__
        pin_reads = {"count": 0}

        def observe_pin(pin, name):
            if name in Pin.__dataclass_fields__:
                pin_reads["count"] += 1
                raise _CatalogueProbe("PIN_FIELD_OBSERVED")
            return original_pin_getattribute(pin, name)

        registry = build_default_registry()
        with patch.object(Pin, "__getattribute__", observe_pin), \
                patch("src.runtime.st_lowering.build_default_registry",
                      return_value=registry):
            self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")
        self.assertEqual(pin_reads["count"], 0)

    def test_catalogue_snapshot_rejects_schema_context_drift(self):
        """The schema snapshot is bound to its Registry key and engineering."""
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")
        # The resolved descriptor must belong to the exact Registry key and
        # engineering context selected by this compiler, rather than merely have
        # a structurally valid dataclass shell.
        for field, value in (("block_type", "TON"),
                             ("variant", "fidelity_f2")):
            with self.subTest(field=field):
                registry = build_default_registry()
                schema, adapter = registry.resolve("APCHSACCUM", "engineering")
                schema = replace(schema)
                object.__setattr__(schema, field, value)
                registry._entries[("APCHSACCUM", "engineering")] = (
                    schema, adapter)
                with patch("src.runtime.st_lowering.build_default_registry",
                           return_value=registry):
                    self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")

    def test_catalogue_class_getattribute_hooks_fail_closed_without_observation(self):
        """Exact Pin and BlockSchema class hooks reject with one diagnosis."""
        source = (
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);")
        for cls in (Pin, BlockSchema):
            with self.subTest(cls=cls.__name__):
                original = cls.__getattribute__
                reads = {"count": 0}
                guarded = frozenset(cls.__dataclass_fields__)

                def observe(instance, name, *, original=original,
                            guarded=guarded, reads=reads):
                    if name in guarded:
                        reads["count"] += 1
                        raise _CatalogueProbe("CLASS_FIELD_OBSERVED")
                    return original(instance, name)

                registry = build_default_registry()
                with patch.object(cls, "__getattribute__", observe), \
                        patch("src.runtime.st_lowering.build_default_registry",
                              return_value=registry):
                    self.assert_code(source, "INVALID_LIBRARY_ALIAS_CONTRACT")
                self.assertEqual(reads["count"], 0)

    def test_catalogue_snapshot_normal_22_entry_positive(self):
        """The frozen catalogue still exposes all 22 aliases to generic lowering."""
        registry, prepared = st_lowering_module._prepare_library_blocks()
        self.assertEqual(len(prepared), 22)
        self.assertEqual(set(prepared), set(library_source_aliases()))
        self.assertEqual(set(prepared), set(registry.block_types()))
        self.assertIsNotNone(compile_st_task(
            "VAR_GLOBAL I1:REAL; Rs:BOOL; Av:LREAL; Ss:BOOL; END_VAR "
            "VAR A:APCHSACCUM; END_VAR A(I1:=I1,RS:=Rs,AV=>Av,SS=>Ss);"
        ).task)

    def test_apccd_inout_source_contract_fails_closed(self):
        required = (
            ("SP", "REAL"), ("PV", "REAL"), ("TS", "BOOL"),
            ("TC", "REAL"), ("TZ", "REAL"), ("CDH", "REAL"),
            ("CDL", "REAL"), ("TL", "REAL"),
        )
        optional = (
            ("CD_K_J", "REAL"), ("CD_K_D", "REAL"),
            ("CD_K_FD", "REAL"), ("CD_GD", "REAL"),
            ("CD_K", "REAL"), ("AD", "BOOL"),
        )
        inouts = (("ZLOUT", "REAL"),)
        outputs = (("AV", "REAL"), ("CD_BH", "REAL"))
        all_pins = required + optional + inouts + outputs
        declarations = "VAR_GLOBAL " + " ".join(
            ("O_" if pin in outputs else "I_") + name + ":" + iec_type + ";"
            for pin in all_pins for name, iec_type in (pin,))
        declarations += " WrongBool:BOOL; WrongReal:REAL; END_VAR VAR CD:APCCD; END_VAR "

        def argument(pin):
            name, _iec_type = pin
            return (name + "=>O_" + name) if pin in outputs \
                else (name + ":=I_" + name)

        mandatory = required + inouts + outputs
        base = tuple(argument(pin) for pin in mandatory)
        full = tuple(argument(pin) for pin in all_pins)
        self.assertIsNotNone(compile_st_task(
            declarations + "CD(" + ",".join(base) + ");").task)

        for missing in mandatory:
            with self.subTest(pin=missing[0], check="missing"):
                self.assert_code(
                    declarations + "CD(" + ",".join(
                        argument(pin) for pin in mandatory if pin != missing) + ");",
                    "MISSING_CALL_FORMAL")
        for pin in all_pins:
            name, iec_type = pin
            original = argument(pin)
            reversed_direction = original.replace(":=", "=>") \
                if ":=" in original else original.replace("=>", ":=")
            with self.subTest(pin=name, check="direction"):
                self.assert_code(
                    declarations + "CD(" + ",".join(
                        reversed_direction if item == original else item
                        for item in full) + ");",
                    "CALL_DIRECTION_MISMATCH")
            wrong = "WrongReal" if iec_type == "BOOL" else "WrongBool"
            wrong_actual = (name + "=>" + wrong) if pin in outputs \
                else (name + ":=" + wrong)
            with self.subTest(pin=name, check="type"):
                self.assert_code(
                    declarations + "CD(" + ",".join(
                        wrong_actual if item == original else item
                        for item in full) + ");",
                    "TYPE_MISMATCH")
        self.assert_code(
            declarations + "CD(" + ",".join(
                "ZLOUT:=I_ZLOUT+1.0" if item.startswith("ZLOUT:=") else item
                for item in base) + ");",
            "CALL_ACTUAL_NOT_WRITABLE")
        self.assert_code(
            declarations + "CD(UNKNOWN:=WrongReal," + ",".join(base) + ");",
            "UNKNOWN_CALL_FORMAL")
        self.assert_code(
            declarations + "CD(" + ",".join(base) + "," + base[0] + ");",
            "DUPLICATE_CALL_FORMAL")

    def test_apcm_inout_and_omit_policy_source_contract_fails_closed(self):
        required = (
            ("SP", "REAL"), ("PV", "REAL"), ("OC", "REAL"),
            ("TS", "BOOL"), ("TP", "REAL"),
        )
        optional = (
            ("RM", "INT"), ("OUTT", "REAL"), ("OUTB", "REAL"),
            ("SADD", "BOOL"), ("SSUB", "BOOL"), ("ZLEN", "BOOL"),
            ("ZSYK", "REAL"),
        )
        inouts = (("ZLOUT", "REAL"),)
        outputs = (
            ("AV", "REAL"), ("AV_P", "REAL"), ("AV_R", "REAL"),
            ("AV_GC", "REAL"), ("AV_J", "REAL"), ("AV_D", "REAL"),
            ("AV_C", "REAL"),
        )
        all_pins = required + optional + inouts + outputs
        declarations = "VAR_GLOBAL " + " ".join(
            ("O_" if pin in outputs else "I_") + name + ":" + iec_type + ";"
            for pin in all_pins for name, iec_type in (pin,))
        declarations += " WrongBool:BOOL; WrongReal:REAL; END_VAR VAR M:APCM; END_VAR "

        def argument(pin):
            name, _type = pin
            return (name + "=>O_" + name) if pin in outputs \
                else (name + ":=I_" + name)

        mandatory = required + inouts + outputs
        base = tuple(argument(pin) for pin in mandatory)
        full = tuple(argument(pin) for pin in all_pins)
        minimal = compile_st_task(declarations + "M(" + ",".join(base) + ");")
        for name, iec_type in optional:
            self.assertNotIn(StoreVar("M." + name, iec_type), minimal.code)
        for missing in mandatory:
            with self.subTest(pin=missing[0], check="missing"):
                self.assert_code(
                    declarations + "M(" + ",".join(
                        argument(pin) for pin in mandatory if pin != missing) + ");",
                    "MISSING_CALL_FORMAL")
        for pin in all_pins:
            name, iec_type = pin
            original = argument(pin)
            reversed_direction = original.replace(":=", "=>") \
                if ":=" in original else original.replace("=>", ":=")
            with self.subTest(pin=name, check="direction"):
                self.assert_code(
                    declarations + "M(" + ",".join(
                        reversed_direction if item == original else item
                        for item in full) + ");",
                    "CALL_DIRECTION_MISMATCH")
            wrong = "WrongReal" if iec_type == "BOOL" else "WrongBool"
            wrong_actual = (name + "=>" + wrong) if pin in outputs \
                else (name + ":=" + wrong)
            with self.subTest(pin=name, check="type"):
                self.assert_code(
                    declarations + "M(" + ",".join(
                        wrong_actual if item == original else item
                        for item in full) + ");",
                    "TYPE_MISMATCH")
        self.assert_code(
            declarations + "M(UNKNOWN:=WrongReal," + ",".join(base) + ");",
            "UNKNOWN_CALL_FORMAL")
        self.assert_code(
            declarations + "M(" + ",".join(base) + "," + base[0] + ");",
            "DUPLICATE_CALL_FORMAL")

    def test_apcrsfnautopara_120_pin_source_contract_fails_closed(self):
        """Lock the large alias independently of the descriptor catalogue."""
        required = (
            ("EN", "BOOL"), ("RESET", "BOOL"), ("CALC_NOW", "BOOL"),
            ("SP", "REAL"), ("PV", "REAL"), ("AV", "REAL"),
            ("TP", "REAL"), ("TS", "BOOL"), ("RSF_LEVEL", "REAL"),
            ("RSF_LOCK_LEVEL_IN", "REAL"), ("RSF_STEP", "REAL"),
        )
        optional = (
            ("CYCLE", "REAL"), ("COLLECT_MODE", "INT"),
            ("SP_MAN", "REAL"), ("SP_MAN_EN", "BOOL"),
            ("SP_TAG_EN", "BOOL"), ("SP_AUTO_EN", "BOOL"),
            ("SP_AUTO_REPLACE_BAD_TAG", "BOOL"), ("SP_STABLE_T", "REAL"),
            ("SP_CONF_T", "REAL"), ("SP_PV_STABLE_ABS", "REAL"),
            ("SP_AV_STABLE_ABS", "REAL"), ("MU", "REAL"), ("MD", "REAL"),
            ("PHY_RANGE_EN", "BOOL"), ("PHY_MU", "REAL"),
            ("PHY_MD", "REAL"), ("WIN_T", "REAL"), ("MIN_WIN_T", "REAL"),
            ("MIN_STORE_EVENT", "REAL"), ("MIN_VALID_EVENT", "REAL"),
            ("HISTORY_N", "INT"), ("FUSE_MIN_N", "REAL"),
            ("FUSE_MIN_WEIGHT", "REAL"), ("SIM_SP_K", "REAL"),
            ("SIM_PV_K", "REAL"), ("SIM_AV_K", "REAL"),
            ("SIM_ERR_K", "REAL"), ("SIM_SP_ABS", "REAL"),
            ("SIM_PV_ABS", "REAL"), ("SIM_AV_ABS", "REAL"),
            ("SIM_ERR_ABS", "REAL"), ("SIM_RELAX_K", "REAL"),
            ("MAN_AV_MIN", "REAL"), ("AO_GAIN_K", "REAL"),
            ("REC_BLEND", "REAL"), ("TL_IN", "REAL"),
            ("TL1_IN", "REAL"), ("TL2_IN", "REAL"),
            ("TL3_IN", "REAL"), ("TL4_IN", "REAL"),
            ("E1_IN", "REAL"), ("E2_IN", "REAL"), ("E3_IN", "REAL"),
            ("E4_IN", "REAL"), ("AO1_IN", "REAL"),
            ("AO2_IN", "REAL"), ("AO3_IN", "REAL"),
            ("AO4_IN", "REAL"), ("RSF_LOCK_T_IN", "REAL"),
            ("RSF_HYS_IN", "REAL"), ("RSF_FAST_HYS_IN", "REAL"),
            ("RSF_TLOUT_K_IN", "REAL"), ("ZF_K_IN", "REAL"),
        )
        outputs = (
            ("RUNNING", "BOOL"), ("WINDOW_DONE", "BOOL"),
            ("FINAL_VALID", "BOOL"), ("FINAL_STRONG", "BOOL"),
            ("FINAL_WEAK", "BOOL"), ("MATCH_LEVEL", "INT"),
            ("DATA_REASON", "INT"), ("WINDOW_VALID", "BOOL"),
            ("SP_USE", "REAL"), ("SP_VALID", "BOOL"),
            ("SP_SOURCE", "INT"), ("SP_REASON", "INT"),
            ("SP_AUTO", "REAL"), ("SP_AUTO_OK", "BOOL"),
            ("SP_AUTO_CONF", "REAL"), ("SP_TAG_BAD", "BOOL"),
            ("SP_STABLE_T_OUT", "REAL"), ("HISTORY_COUNT", "REAL"),
            ("SIMILAR_COUNT", "REAL"), ("FUSE_WEIGHT", "REAL"),
            ("WINDOW_EVENT_N", "REAL"), ("WINDOW_T", "REAL"),
            ("AUTO_SAMPLE_T", "REAL"), ("MAN_EVENT_N", "REAL"),
            ("CROSS_COUNT", "REAL"), ("RSF_TRIGGER_N", "REAL"),
            ("RSF_LOCK_N", "REAL"), ("ERR_ABS_AVG", "REAL"),
            ("ERR_AREA_POS", "REAL"), ("ERR_AREA_NEG", "REAL"),
            ("ERR_PEAK_ABS", "REAL"), ("AVG_CROSS_T", "REAL"),
            ("PV_DELTA", "REAL"), ("AV_DELTA", "REAL"),
            ("NOISE_EST", "REAL"), ("PROCESS_GAIN", "REAL"),
            ("TL_REC", "REAL"), ("TL1_REC", "REAL"),
            ("TL2_REC", "REAL"), ("TL3_REC", "REAL"),
            ("TL4_REC", "REAL"), ("E1_REC", "REAL"),
            ("E2_REC", "REAL"), ("E3_REC", "REAL"),
            ("E4_REC", "REAL"), ("AO1_REC", "REAL"),
            ("AO2_REC", "REAL"), ("AO3_REC", "REAL"),
            ("AO4_REC", "REAL"), ("RSF_OK", "BOOL"),
            ("RSF_REASON", "INT"), ("RSF_LOCK_T_REC", "REAL"),
            ("RSF_HYS_REC", "REAL"), ("RSF_FAST_HYS_REC", "REAL"),
            ("RSF_TLOUT_K_REC", "REAL"), ("ZF_K_REC", "REAL"),
        )
        all_pins = required + optional + outputs
        declarations = "VAR_GLOBAL " + " ".join(
            "%s_%s:%s;" % ("O" if pin in outputs else "I", name, iec_type)
            for pin in all_pins for name, iec_type in (pin,))
        declarations += " WrongBool:BOOL; WrongReal:REAL; END_VAR "
        declarations += "VAR R:APCRSFNAUTOPARA; END_VAR "

        def argument(pin):
            name, _iec_type = pin
            if pin in outputs:
                return "%s=>O_%s" % (name, name)
            return "%s:=I_%s" % (name, name)

        base = tuple(argument(pin) for pin in required + outputs)
        full = tuple(argument(pin) for pin in all_pins)
        compiled = compile_st_task(declarations + "R(%s);" % ",".join(base))
        for name, iec_type in optional:
            self.assertNotIn(StoreVar("R.%s" % name, iec_type), compiled.code)

        for missing in required + outputs:
            with self.subTest(pin=missing[0], check="missing"):
                self.assert_code(
                    declarations + "R(%s);" % ",".join(
                        argument(pin) for pin in required + outputs if pin != missing),
                    "MISSING_CALL_FORMAL")

        for pin in all_pins:
            name, iec_type = pin
            original = argument(pin)
            reversed_direction = original.replace(":=", "=>") \
                if ":=" in original else original.replace("=>", ":=")
            with self.subTest(pin=name, check="direction"):
                self.assert_code(
                    declarations + "R(%s);" % ",".join(
                        reversed_direction if item == original else item for item in full),
                    "CALL_DIRECTION_MISMATCH")
            wrong = "WrongReal" if iec_type == "BOOL" else "WrongBool"
            wrong_actual = (name + "=>" + wrong) if pin in outputs else (name + ":=" + wrong)
            with self.subTest(pin=name, check="type"):
                self.assert_code(
                    declarations + "R(%s);" % ",".join(
                        wrong_actual if item == original else item for item in full),
                    "TYPE_MISMATCH")

        for omitted in optional:
            with self.subTest(pin=omitted[0], check="optional-omission"):
                self.assertIsNotNone(compile_st_task(
                    declarations + "R(%s);" % ",".join(
                        argument(pin) for pin in all_pins if pin != omitted)).task)
        self.assert_code(
            declarations + "R(UNKNOWN:=WrongBool,%s);" % ",".join(base),
            "UNKNOWN_CALL_FORMAL")
        self.assert_code(
            declarations + "R(%s,%s);" % (",".join(base), base[0]),
            "DUPLICATE_CALL_FORMAL")

    def test_apcmautopara_171_pin_source_contract_fails_closed(self):
        """Lock 84 optional inputs and 87 mandatory outputs independently."""
        inputs = (
            ("EN", "BOOL"), ("RESET", "BOOL"), ("CALC_NOW", "BOOL"),
            ("CYCLE", "REAL"), ("COLLECT_MODE", "INT"), ("SP", "REAL"),
            ("SP_MAN", "REAL"), ("SP_MAN_EN", "BOOL"), ("SP_TAG_EN", "BOOL"),
            ("SP_AUTO_EN", "BOOL"), ("SP_AUTO_REPLACE_BAD_TAG", "BOOL"), ("SP_STABLE_T", "REAL"),
            ("SP_CONF_T", "REAL"), ("SP_PV_STABLE_ABS", "REAL"), ("SP_AV_STABLE_ABS", "REAL"),
            ("PV", "REAL"), ("AV", "REAL"), ("RM", "INT"),
            ("TS", "BOOL"), ("PVMU", "REAL"), ("PVMD", "REAL"),
            ("MU", "REAL"), ("MD", "REAL"), ("OUTT", "REAL"),
            ("OUTB", "REAL"), ("WIN_T", "REAL"), ("MIN_WIN_T", "REAL"),
            ("MIN_STORE_EVENT", "REAL"), ("MIN_VALID_EVENT", "REAL"), ("HISTORY_N", "INT"),
            ("FUSE_MIN_N", "REAL"), ("FUSE_MIN_WEIGHT", "REAL"), ("SIM_SP_K", "REAL"),
            ("SIM_PV_K", "REAL"), ("SIM_AV_K", "REAL"), ("SIM_ERR_K", "REAL"),
            ("SIM_SP_ABS", "REAL"), ("SIM_PV_ABS", "REAL"), ("SIM_AV_ABS", "REAL"),
            ("SIM_ERR_ABS", "REAL"), ("SIM_RELAX_K", "REAL"), ("MAN_MERGE_T", "REAL"),
            ("MAN_RESP_T", "REAL"), ("MAN_RESP_T_MAX", "REAL"), ("MAN_AV_MIN", "REAL"),
            ("PT_IN", "REAL"), ("TI_IN", "REAL"), ("TD_IN", "REAL"),
            ("DI_IN", "REAL"), ("SVH_IN", "REAL"), ("SVL_IN", "REAL"),
            ("PID_FORMULA_EN", "BOOL"), ("PID_LAMBDA_K", "REAL"), ("PID_MODEL_L_K", "REAL"),
            ("PID_FORMULA_BLEND", "REAL"), ("TL_IN", "REAL"), ("TL1_IN", "REAL"),
            ("TL2_IN", "REAL"), ("TL3_IN", "REAL"), ("TL4_IN", "REAL"),
            ("E1_IN", "REAL"), ("E2_IN", "REAL"), ("E3_IN", "REAL"),
            ("E4_IN", "REAL"), ("AO1_IN", "REAL"), ("AO2_IN", "REAL"),
            ("AO3_IN", "REAL"), ("AO4_IN", "REAL"), ("RSF_LOCK_T_IN", "REAL"),
            ("TC_IN", "REAL"), ("TZ_IN", "REAL"), ("GC1_IN", "REAL"),
            ("GC2_IN", "REAL"), ("OUTH_IN", "REAL"), ("OUTL_IN", "REAL"),
            ("CD_GD_IN", "REAL"), ("CD_K_IN", "REAL"), ("CD_K_FD_IN", "REAL"),
            ("CD_K_J_IN", "REAL"), ("CD_K_D_IN", "REAL"), ("CDH_IN", "REAL"),
            ("CDL_IN", "REAL"), ("TC_CD_IN", "REAL"), ("TZ_CD_IN", "REAL"),
        )
        outputs = (
            ("RUNNING", "BOOL"), ("WINDOW_DONE", "BOOL"), ("FINAL_VALID", "BOOL"),
            ("FINAL_STRONG", "BOOL"), ("FINAL_WEAK", "BOOL"), ("MATCH_LEVEL", "INT"),
            ("WINDOW_VALID", "BOOL"), ("DATA_REASON", "INT"), ("SP_USE", "REAL"),
            ("SP_AUTO", "REAL"), ("SP_VALID", "BOOL"), ("SP_AUTO_OK", "BOOL"),
            ("SP_TAG_BAD", "BOOL"), ("SP_SOURCE", "INT"), ("SP_REASON", "INT"),
            ("SP_AUTO_CONF", "REAL"), ("SP_STABLE_T_OUT", "REAL"), ("PID_OK", "BOOL"),
            ("RSF_OK", "BOOL"), ("GC_OK", "BOOL"), ("CD_OK", "BOOL"),
            ("PID_REASON", "INT"), ("RSF_REASON", "INT"), ("GC_REASON", "INT"),
            ("CD_REASON", "INT"), ("HISTORY_COUNT", "REAL"), ("SIMILAR_COUNT", "REAL"),
            ("FUSE_WEIGHT", "REAL"), ("WINDOW_EVENT_N", "REAL"), ("WINDOW_T", "REAL"),
            ("AUTO_SAMPLE_T", "REAL"), ("MAN_EVENT_N", "REAL"), ("MAN_RESP_T_AUTO", "REAL"),
            ("MAN_RESP_T_USE", "REAL"), ("CROSS_COUNT", "REAL"), ("ERR_ABS_AVG", "REAL"),
            ("ERR_AREA_POS", "REAL"), ("ERR_AREA_NEG", "REAL"), ("ERR_PEAK_ABS", "REAL"),
            ("AVG_CROSS_T", "REAL"), ("PV_DELTA", "REAL"), ("AV_DELTA", "REAL"),
            ("NOISE_EST", "REAL"), ("PROCESS_GAIN", "REAL"), ("PT_REC", "REAL"),
            ("TI_REC", "REAL"), ("TD_REC", "REAL"), ("DI_REC", "REAL"),
            ("SVH_REC", "REAL"), ("SVL_REC", "REAL"), ("PID_FORMULA_VALID", "BOOL"),
            ("PT_FORMULA_REC", "REAL"), ("TI_FORMULA_REC", "REAL"), ("PID_MODEL_GAIN_REC", "REAL"),
            ("PID_MODEL_T_REC", "REAL"), ("PID_MODEL_L_REC", "REAL"), ("PID_MODEL_LAMBDA_REC", "REAL"),
            ("PID_FORMULA_BLEND_REC", "REAL"), ("TL_REC", "REAL"), ("TL1_REC", "REAL"),
            ("TL2_REC", "REAL"), ("TL3_REC", "REAL"), ("TL4_REC", "REAL"),
            ("E1_REC", "REAL"), ("E2_REC", "REAL"), ("E3_REC", "REAL"),
            ("E4_REC", "REAL"), ("AO1_REC", "REAL"), ("AO2_REC", "REAL"),
            ("AO3_REC", "REAL"), ("AO4_REC", "REAL"), ("RSF_LOCK_T_REC", "REAL"),
            ("TC_REC", "REAL"), ("TZ_REC", "REAL"), ("GC1_REC", "REAL"),
            ("GC2_REC", "REAL"), ("OUTH_REC", "REAL"), ("OUTL_REC", "REAL"),
            ("CD_GD_REC", "REAL"), ("CD_K_REC", "REAL"), ("CD_K_FD_REC", "REAL"),
            ("CD_K_J_REC", "REAL"), ("CD_K_D_REC", "REAL"), ("CDH_REC", "REAL"),
            ("CDL_REC", "REAL"), ("TC_CD_REC", "REAL"), ("TZ_CD_REC", "REAL"),
        )
        all_pins = inputs + outputs
        declarations = "VAR_GLOBAL " + " ".join(
            "%s_%s:%s;" % ("O" if pin in outputs else "I", name, iec_type)
            for pin in all_pins for name, iec_type in (pin,))
        declarations += " WrongBool:BOOL; WrongReal:REAL; END_VAR "
        declarations += "VAR A:APCMAUTOPARA; END_VAR "

        def argument(pin):
            name, _iec_type = pin
            return ("%s=>O_%s" if pin in outputs else "%s:=I_%s") % (name, name)

        base = tuple(argument(pin) for pin in outputs)
        full = tuple(argument(pin) for pin in all_pins)
        compiled = compile_st_task(declarations + "A(%s);" % ",".join(base))
        for name, iec_type in inputs:
            self.assertNotIn(StoreVar("A.%s" % name, iec_type), compiled.code)

        for missing in outputs:
            with self.subTest(pin=missing[0], check="missing-output"):
                self.assert_code(
                    declarations + "A(%s);" % ",".join(
                        argument(pin) for pin in outputs if pin != missing),
                    "MISSING_CALL_FORMAL")
        for omitted in inputs:
            with self.subTest(pin=omitted[0], check="optional-omission"):
                self.assertIsNotNone(compile_st_task(
                    declarations + "A(%s);" % ",".join(
                        argument(pin) for pin in all_pins if pin != omitted)).task)

        for pin in all_pins:
            name, iec_type = pin
            original = argument(pin)
            reversed_direction = original.replace(":=", "=>") \
                if ":=" in original else original.replace("=>", ":=")
            with self.subTest(pin=name, check="direction"):
                self.assert_code(
                    declarations + "A(%s);" % ",".join(
                        reversed_direction if item == original else item for item in full),
                    "CALL_DIRECTION_MISMATCH")
            wrong = "WrongReal" if iec_type == "BOOL" else "WrongBool"
            wrong_actual = (name + "=>" + wrong) if pin in outputs else (name + ":=" + wrong)
            with self.subTest(pin=name, check="type"):
                self.assert_code(
                    declarations + "A(%s);" % ",".join(
                        wrong_actual if item == original else item for item in full),
                    "TYPE_MISMATCH")
        self.assert_code(
            declarations + "A(UNKNOWN:=WrongBool,%s);" % ",".join(base),
            "UNKNOWN_CALL_FORMAL")
        self.assert_code(
            declarations + "A(%s,%s);" % (",".join(base), base[0]),
            "DUPLICATE_CALL_FORMAL")

    def test_hostile_alias_catalogue_carrier_fails_closed_without_leaking(self):
        # Codex WP-125 Round 1: the alias catalogue carrier itself was trusted.
        # A non-dict carrier whose ``items`` raises a ``BaseException`` must be
        # rejected by an identity-only ``type`` gate before ``.items()`` runs --
        # never leaking the probe and never invoking its iteration, comparison
        # or hash hooks.
        _HostileAliasCatalogue.items_calls = 0
        _HostileAliasCatalogue.hook_calls = 0
        with patch("src.runtime.st_lowering.library_source_aliases",
                   return_value=_HostileAliasCatalogue()):
            self.assert_code("", "INVALID_LIBRARY_ALIAS_CONTRACT")
        self.assertEqual(_HostileAliasCatalogue.items_calls, 0)
        self.assertEqual(_HostileAliasCatalogue.hook_calls, 0)

        # A genuine dict carrier with a non-``str`` block key must also fail
        # closed before ``registry.resolve`` hashes or compares that key.
        _HostileBlockKey.eq_calls = 0
        hostile_key_catalogue = {_HostileBlockKey(): {"IN": "IN"}}
        with patch("src.runtime.st_lowering.library_source_aliases",
                   return_value=hostile_key_catalogue):
            self.assert_code("", "INVALID_LIBRARY_ALIAS_CONTRACT")
        self.assertEqual(_HostileBlockKey.eq_calls, 0)

    def test_unregistered_str_block_key_fails_closed_without_leaking_registry_error(self):
        # Codex WP-125 Round 2: an exact ``dict`` carrier whose block key is an
        # exact ``str`` but is absent from the frozen engineering Registry must
        # fail closed at the ST alias boundary as INVALID_LIBRARY_ALIAS_CONTRACT.
        # Before the fix ``_prepare_library_blocks`` called ``registry.resolve``
        # directly, leaking the Registry-layer ``UnknownBlockError`` across the
        # ST compile boundary instead of a stable STCompileError.
        self.assertFalse(build_default_registry().has("NOT_A_REGISTERED_BLOCK"))
        unregistered_catalogue = {"NOT_A_REGISTERED_BLOCK": {"IN": "IN"}}
        with patch("src.runtime.st_lowering.library_source_aliases",
                   return_value=unregistered_catalogue):
            try:
                compile_st_task("")
            except UnknownBlockError as leaked:  # regression guard, not the path
                self.fail(
                    "registry.resolve leaked UnknownBlockError past the ST alias "
                    "boundary: %r" % (leaked,))
            except STCompileError as error:
                self.assertEqual(
                    error.errors[0].code, "INVALID_LIBRARY_ALIAS_CONTRACT")
            else:
                self.fail("unregistered block key did not fail closed")

    def test_if_condition_must_be_bool(self):
        self.assert_code(
            "VAR_GLOBAL X:INT; END_VAR IF X THEN X:=1; END_IF;",
            "NON_BOOL_CONDITION")
        self.assert_code(
            "VAR_GLOBAL X:INT; END_VAR IF 1 THEN X:=1; END_IF;",
            "NON_BOOL_CONDITION")
        self.assert_code(
            "VAR_GLOBAL X:INT; END_VAR IF F() THEN X:=1; END_IF;",
            "UNSUPPORTED_STANDARD_FUNCTION")

    def test_case_selector_labels_and_ranges_fail_closed(self):
        self.assert_code(
            "VAR_GLOBAL M:BOOL; END_VAR CASE M OF 0:M:=TRUE; END_CASE",
            "INVALID_CASE_SELECTOR_TYPE")
        self.assert_code(
            "VAR_GLOBAL M:INT; END_VAR CASE M+1 OF 1:M:=1; END_CASE",
            "UNSUPPORTED_CASE_SELECTOR")
        self.assert_code(
            "VAR_GLOBAL M:SINT; END_VAR CASE M OF 128:M:=1; END_CASE",
            "CASE_LABEL_RANGE")
        self.assert_code(
            "VAR_GLOBAL M:INT; END_VAR CASE M OF 3..1:M:=1; END_CASE",
            "REVERSED_CASE_RANGE")
        self.assert_code(
            "VAR_GLOBAL M:INT; END_VAR CASE M OF 1..3:M:=1; 3:M:=2; END_CASE",
            "OVERLAPPING_CASE_LABEL")

    def test_for_bounds_counter_writes_overflow_and_iteration_limit_fail_closed(self):
        self.assert_code(
            "VAR_GLOBAL I:BOOL; END_VAR FOR I:=0 TO 1 DO END_FOR;",
            "INVALID_FOR_COUNTER_TYPE")
        self.assert_code(
            "VAR_GLOBAL I:INT; X:INT; END_VAR FOR I:=X TO 3 DO END_FOR;",
            "UNSUPPORTED_FOR_BOUND")
        self.assert_code(
            "VAR_GLOBAL I:INT; END_VAR FOR I:=1 TO 3 BY 0 DO END_FOR;",
            "ZERO_FOR_INCREMENT")
        self.assert_code(
            "VAR_GLOBAL I:SINT; END_VAR FOR I:=120 TO 126 BY 10 DO END_FOR;",
            "FOR_COUNTER_OVERFLOW")
        self.assert_code(
            "VAR_GLOBAL I:DINT; END_VAR FOR I:=1 TO 100001 DO END_FOR;",
            "FOR_ITERATION_LIMIT")
        self.assert_code(
            "VAR_GLOBAL I:DINT; J:DINT; END_VAR FOR I:=1 TO 400 DO FOR J:=1 TO 300 DO END_FOR; END_FOR;",
            "FOR_NESTED_ITERATION_LIMIT")
        self.assert_code(
            "VAR_GLOBAL I:INT; END_VAR FOR I:=1 TO 3 DO I:=2; END_FOR;",
            "FOR_COUNTER_WRITE")
        self.assert_code(
            "VAR_GLOBAL I:INT; END_VAR FOR I:=1 TO 3 DO FOR I:=1 TO 2 DO END_FOR; END_FOR;",
            "ACTIVE_FOR_COUNTER_REUSE")

    def test_while_condition_and_loop_transfer_outside_loop_fail_closed(self):
        self.assert_code(
            "VAR_GLOBAL X:INT; END_VAR WHILE X DO END_WHILE;",
            "NON_BOOL_CONDITION")
        self.assert_code("EXIT;", "LOOP_CONTROL_OUTSIDE_LOOP")
        self.assert_code("CONTINUE;", "LOOP_CONTROL_OUTSIDE_LOOP")

    def test_initializer_boundary_is_explicit(self):
        self.assert_code("VAR_GLOBAL X:INT:=1+2; END_VAR", "NON_CONSTANT_INITIALIZER")
        self.assert_code("VAR_TEMP X:INT:=1; END_VAR", "UNSUPPORTED_TEMP_INITIALIZER")

    def test_time_range_encoding_and_literal_type_mismatch_fail_closed(self):
        self.assert_code(
            "VAR_GLOBAL T:TIME:=T#49D17H2M47S296MS; END_VAR",
            "TIME_LITERAL_RANGE")
        self.assert_code("VAR_GLOBAL T:TIME:='x'; END_VAR", "TYPE_MISMATCH")
        self.assert_code("VAR_GLOBAL S:STRING:=T#1S; END_VAR", "TYPE_MISMATCH")
        self.assert_code(
            "VAR_GLOBAL S:STRING:='中'; END_VAR",
            "STRING_ENCODING_UNRESOLVED")

    def test_operator_type_boundary_is_explicit(self):
        self.assert_code("VAR_GLOBAL Q:BOOL; END_VAR Q:=Q+Q;",
                         "INVALID_OPERATOR_TYPE")
        self.assert_code("VAR_GLOBAL X:REAL; END_VAR X:=X MOD X;",
                         "INVALID_OPERATOR_TYPE")
        self.assert_code("VAR_GLOBAL X:UINT; END_VAR X:=-X;",
                         "INVALID_OPERATOR_TYPE")

    def test_nonfinite_and_entry_configuration_are_rejected(self):
        self.assert_code("VAR_GLOBAL X:REAL; END_VAR X:=1.0E9999;",
                         "NONFINITE_LITERAL")
        self.assert_code("", "INVALID_PROGRAM_NAME", program_name="bad__name")
        self.assert_code("", "INVALID_PROGRAM_NAME", program_name="PROGRAM")
        self.assert_code("", "INVALID_CYCLE_MS", cycle_ms=True)

    def test_st_lowering_delegates_loader_combined_semantics_via_facade(self):
        # WP-20260813-121 import contract (counter-proof + fix): the whole-stream
        # catalogue pre-check must own no second operator/type/signature rule table
        # and must not reach into the Loader's private internals.  Prove it at the
        # AST level -- the ST module imports the Loader's single supported facade
        # ``validate_pou_instruction_semantics`` and no longer imports the private
        # ``_build_scope`` / ``_step`` or ``standard_signature_error``.  Before the
        # refactor ST imported ``_build_scope`` and ``_step`` from the Loader (and
        # ``standard_signature_error`` from standard_functions), so this assertion
        # fails against the pre-facade module and passes only once the delegation
        # is in place.
        import ast
        import inspect

        import src.runtime.st_lowering as st_module

        imported_names = set()
        tree = ast.parse(inspect.getsource(st_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)
        self.assertIn("validate_pou_instruction_semantics", imported_names)
        self.assertNotIn("_build_scope", imported_names)
        self.assertNotIn("_step", imported_names)
        self.assertNotIn("standard_signature_error", imported_names)
        # The bound module namespace must agree with the import list: the private
        # helpers are gone and the supported facade is present.
        self.assertTrue(
            hasattr(st_module, "validate_pou_instruction_semantics"))
        self.assertFalse(hasattr(st_module, "_build_scope"))
        self.assertFalse(hasattr(st_module, "_step"))
        self.assertFalse(hasattr(st_module, "standard_signature_error"))

    def test_return_does_not_bypass_whole_stream_catalogue_precheck(self):
        function = compile_st_function(
            "VAR_INPUT X:INT; END_VAR F:=X;", "F", "INT")
        # The malformed LoadVar is dead after the exact RETURN jump, but the
        # catalogue facade must still inspect it before a caller can receive it.
        forged = STPOUCompileResult(
            function.unit, function.pou, (
                Jmp("__ST_RETURN_EPILOGUE"),
                LoadVar("X", "DINT"),
                Label("__ST_RETURN_EPILOGUE"),
                LoadVar("F", "INT"),
            ))
        error = self.assert_code(
            "VAR_GLOBAL X:INT; O:INT; END_VAR O:=F(X);",
            "INVALID_FUNCTION_CATALOGUE", functions=(forged,))
        self.assertIn("does not match its declared variable type",
                      error.errors[0].message)


if __name__ == "__main__":
    unittest.main()
