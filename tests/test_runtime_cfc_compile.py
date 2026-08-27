"""WP-20260809-085：阶段 2 CFC 内部模型到 typed IR Task 的安全编译入口反证。

本入口 :func:`compile_cfc_task` 把 exact ``cfc-model-v1`` payload 经冻结
``load_cfc_model`` 物化成唯一不可变模型，自动从 pins/connections 派生
``CFCInputBinding``，再投影到冻结定序 / lowering 内核并返回经 ``validate_task``
验证的正式 typed IR ``Task``。测试只证明当前内部模型→typed IR 的工程行为，不构成
PLC/CODESYS 或现场语义证明。
"""
from __future__ import annotations

import unittest

from src.runtime.cfc_lowering import (
    CFCCompileResult,
    CFCInputBinding,
    CFCNodeBody,
    CFCNodeIR,
    CFCLoweringError,
    compile_cfc_task,
    lower_cfc_feedback_task,
    lower_cfc_task,
)
from src.runtime.cfc_model import CFCModel, CFCModelError, load_cfc_model
from src.runtime.cfc_order import CFCOrderError, CFCOrderNode
from src.runtime.ir import (
    BinOp, Binding, CallFb, CallFbInstance, CallFunc, CallStd, Const, Convert,
    InstanceDecl,
    IOMap, Jmp, JmpIfFalse, Label, LoadConst, LoadPrev, LoadVar,
    POUDefinition,
    ProgramInstance,
    StackSlot, StdSig, StoreKey, StoreVar, UnOp,
    Task,
    VarDecl,
)
from src.runtime.descriptors.registry import Registry
from src.runtime.descriptors.model import BlockSchema, Pin, RuntimeAdapter
from src.runtime.descriptors.representative import build_default_registry
from src.runtime.loader import IRValidationError


# ---------------------------------------------------------------------------
# payload / task 构造辅助
# ---------------------------------------------------------------------------
def _pin(pin_id, formal, direction, value_key, iec="BOOL"):
    return {
        "pin_id": pin_id,
        "formal_name": formal,
        "direction": direction,
        "iec_type": iec,
        "value_key": value_key,
    }


def _node(node_id, kind, pins, *, order=None, marker=None, type_name="T",
          instance=""):
    return {
        "node_id": node_id,
        "kind": kind,
        "type_name": type_name,
        "instance_name": instance,
        "execution_order_id": order,
        "feedback_marker": marker,
        "pins": pins,
    }


def _conn(sn, sp, tn, tp, read_mode="current"):
    return {
        "source_node_id": sn,
        "source_pin_id": sp,
        "target_node_id": tn,
        "target_pin_id": tp,
        "read_mode": read_mode,
    }


def _auto_payload():
    """user_defined/auto 无环图：IN_A -> A -> B（全 current）。"""
    return {
        "schema_version": "cfc-model-v1",
        "carrier": "user_defined",
        "execution_order_mode": "auto",
        "order_source": "user_defined",
        "nodes": [
            _node("IN_A", "input", [_pin("po", "OUT", "OUT", "Start")]),
            _node("A", "block",
                  [_pin("i", "IN", "IN", "InputA"),
                   _pin("o", "OUT", "OUT", "Mid")]),
            _node("B", "block",
                  [_pin("i", "IN", "IN", "InputB"),
                   _pin("o", "OUT", "OUT", "Motor")]),
        ],
        "connections": [
            _conn("IN_A", "po", "A", "i"),
            _conn("A", "o", "B", "i"),
        ],
    }


def _auto_bodies():
    return (
        CFCNodeBody("IN_A", ()),
        CFCNodeBody("A", (LoadVar("InputA", "BOOL"), StoreVar("Mid", "BOOL"))),
        CFCNodeBody("B", (LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"))),
    )


def _explicit_payload():
    """user_defined/explicit：与 _auto 同拓扑但显式序号 0/1/2。"""
    payload = _auto_payload()
    payload["execution_order_mode"] = "explicit"
    payload["nodes"][0]["execution_order_id"] = 0
    payload["nodes"][1]["execution_order_id"] = 1
    payload["nodes"][2]["execution_order_id"] = 2
    return payload


def _feedback_payload():
    """plcopen_xml/explicit/exported 双节点反馈：A<->B，B->A 为 previous。"""
    return {
        "schema_version": "cfc-model-v1",
        "carrier": "plcopen_xml",
        "execution_order_mode": "explicit",
        "order_source": "exported",
        "nodes": [
            _node("A", "block",
                  [_pin("i", "IN", "IN", "IA"),
                   _pin("o", "OUT", "OUT", "X")], order=1),
            _node("B", "block",
                  [_pin("i", "IN", "IN", "IB"),
                   _pin("o", "OUT", "OUT", "Y")], order=2),
        ],
        "connections": [
            _conn("A", "o", "B", "i", "current"),
            _conn("B", "o", "A", "i", "previous"),
        ],
    }


def _feedback_bodies():
    return (
        CFCNodeBody("A", (LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"))),
        CFCNodeBody("B", (LoadVar("IB", "BOOL"), StoreVar("Y", "BOOL"))),
    )


def _task(payload, *, locals_=("InputA", "InputB"),
          gvl=("Start", "Mid", "Motor"), pou_name="Main"):
    pou = POUDefinition(
        pou_name, "PROGRAM", "CFC",
        locals=[VarDecl(name, "BOOL") for name in locals_],
        source=payload, code=None,
    )
    return Task(
        programs=[ProgramInstance(pou_name, "PLC_PRG")],
        gvl=[VarDecl(name, "BOOL", section="VAR_GLOBAL") for name in gvl],
        pou_lib={pou_name: pou},
    )


def _feedback_task(payload):
    return _task(payload, locals_=("IA", "IB"), gvl=("X", "Y"))


def _task_with_mutable_records(payload):
    """合法 Task 图，刻意包含 target 之外的可变记录与容器。"""
    task = _task(payload)
    task.gvl[0].initial = {"history": [1]}
    task.io_map.append(IOMap("Start", "%IX0.0", "IN"))
    task.pou_lib["Main"].instances.append(InstanceDecl(
        "LIB", "TON", ctor_args={"seed": [1]},
        init_overrides={"PT": {"ms": 500}}, retain={"Q"}))
    task.pou_lib["Sibling"] = POUDefinition(
        "Sibling", "FUNCTION_BLOCK", "ST",
        locals=[VarDecl("SiblingLocal", "BOOL")],
        instances=[InstanceDecl(
            "SiblingLib", "TON", ctor_args={"seed": [2]},
            init_overrides={"PT": {"ms": 250}}, retain={"ET"})],
        code=[],
    )
    return task


def _function_task(payload):
    """为 ``Binding.actual=Const`` 反证添加一个可验证的 FUNCTION。"""
    task = _task(payload, locals_=("InputA", "InputB", "Flag"))
    task.pou_lib["Fn"] = POUDefinition(
        "Fn", "FUNCTION", "ST",
        interface=[VarDecl("arg", "INT", section="VAR_INPUT")],
        return_type="BOOL", code=[LoadConst(True, "BOOL")],
    )
    return task


class _Boom(BaseException):
    """自定义 BaseException：任一被观察即逃逸，证明零观察边界。"""


class _ObservationTrap:
    """任何观察（repr/str/eq/ne/hash/bool）都抛 BaseException 的恶意对象。"""

    def __repr__(self):  # pragma: no cover - 缺陷回归才触发
        raise _Boom("repr observed")

    def __str__(self):  # pragma: no cover - 缺陷回归才触发
        raise _Boom("str observed")

    def __eq__(self, other):  # pragma: no cover - 缺陷回归才触发
        raise _Boom("eq observed")

    def __ne__(self, other):  # pragma: no cover - 缺陷回归才触发
        raise _Boom("ne observed")

    def __hash__(self):  # pragma: no cover - 缺陷回归才触发
        raise _Boom("hash observed")

    def __bool__(self):  # pragma: no cover - 缺陷回归才触发
        raise _Boom("bool observed")


def _codes(error):
    return {item.code for item in error.errors}


class TestCFCCompileEntry(unittest.TestCase):
    # --- 必须新增测试 1：自动图与手工 lowering 逐值一致 -------------------
    def test_auto_graph_matches_manual_lowering(self):
        payload = _auto_payload()
        result = compile_cfc_task(payload, _auto_bodies(), _task(payload), "Main")
        self.assertIsInstance(result, CFCCompileResult)
        self.assertIsInstance(result.model, CFCModel)
        # Kahn 定序：IN_A -> A -> B（同层按 str 升序），IN_A body/inputs 为空，
        # 故发出的 code 等同经典手工 current lowering。
        self.assertEqual(result.execution_order, ("IN_A", "A", "B"))
        expected = (
            LoadVar("Start", "BOOL"), StoreVar("InputA", "BOOL"),
            LoadVar("InputA", "BOOL"), StoreVar("Mid", "BOOL"),
            LoadVar("Mid", "BOOL"), StoreVar("InputB", "BOOL"),
            LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"),
        )
        self.assertEqual(result.code, expected)

        # 手工调用冻结 lowering 内核，与入口结果 execution order/code 完全一致。
        model = load_cfc_model(payload)
        graph = model.to_order_graph()
        fragments = (
            CFCNodeIR("A", (CFCInputBinding("IN_A", "Start", "InputA", "BOOL"),),
                      (LoadVar("InputA", "BOOL"), StoreVar("Mid", "BOOL"))),
            CFCNodeIR("B", (CFCInputBinding("A", "Mid", "InputB", "BOOL"),),
                      (LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"))),
            CFCNodeIR("IN_A", (), ()),
        )
        # 冻结内核要求目标 POU.source 就是它接收的定序图。
        manual = lower_cfc_task(graph, fragments, _task(graph), "Main")
        self.assertEqual(result.execution_order, manual.execution_order)
        self.assertEqual(result.code, manual.code)
        # model 作为 provenance 结果字段被保留（等价于 Loader 物化模型）。
        self.assertEqual(result.model.to_json(), load_cfc_model(payload).to_json())

    # --- 必须新增测试 2：explicit 保序 + 三类排列稳定 --------------------
    def test_explicit_order_and_permutations_are_stable(self):
        base_payload = _explicit_payload()
        base = compile_cfc_task(
            base_payload, _auto_bodies(), _task(base_payload), "Main")
        self.assertEqual(base.execution_order, ("IN_A", "A", "B"))

        # 连接排列
        conn_perm = _explicit_payload()
        conn_perm["connections"].reverse()
        # 节点排列
        node_perm = _explicit_payload()
        node_perm["nodes"].reverse()
        # body 描述排列
        bodies_perm = tuple(reversed(_auto_bodies()))

        for name, payload, bodies in (
            ("connections", conn_perm, _auto_bodies()),
            ("nodes", node_perm, _auto_bodies()),
            ("bodies", _explicit_payload(), bodies_perm),
        ):
            with self.subTest(name=name):
                other = compile_cfc_task(payload, bodies, _task(payload), "Main")
                self.assertEqual(other.execution_order, base.execution_order)
                self.assertEqual(other.code, base.code)

    # --- 必须新增测试 3：PLCopen 显式反馈只 lower 为 LoadPrev -------------
    def test_plcopen_feedback_uses_load_prev_only_for_previous_edges(self):
        payload = _feedback_payload()
        result = compile_cfc_task(
            payload, _feedback_bodies(), _feedback_task(payload), "Main")
        self.assertEqual(result.execution_order, ("A", "B"))
        # B->A 是 previous → LoadPrev("Y")；A->B 是 current → LoadVar("X")。
        self.assertEqual(result.code[:2], (LoadPrev("Y", "BOOL"), StoreVar("IA", "BOOL")))
        self.assertIn(LoadVar("X", "BOOL"), result.code)
        self.assertNotIn(LoadPrev("X", "BOOL"), result.code)
        # 与手工反馈 lowering 一致（目标 POU.source 绑定同一反馈全边图）。
        model = load_cfc_model(payload)
        fb_graph = _model_feedback_graph_for_test(model)
        manual = lower_cfc_feedback_task(
            fb_graph,
            (
                CFCNodeIR("A", (CFCInputBinding("B", "Y", "IA", "BOOL", True),),
                          (LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"))),
                CFCNodeIR("B", (CFCInputBinding("A", "X", "IB", "BOOL", False),),
                          (LoadVar("IB", "BOOL"), StoreVar("Y", "BOOL"))),
            ),
            _feedback_task(fb_graph), "Main")
        self.assertEqual(result.code, manual.code)

    # --- 必须新增测试 4：多类失败关闭 + 原 Task 零修改 -------------------
    def test_unsupported_and_structural_failures_fail_closed(self):
        # export_native/auto/reconstructed 由冻结内核透传 CFCOrderError。
        export_payload = _auto_payload()
        export_payload["carrier"] = "export_native"
        export_payload["order_source"] = "reconstructed"
        export_task = _task(export_payload)
        with self.assertRaises(CFCOrderError):
            compile_cfc_task(export_payload, _auto_bodies(), export_task, "Main")
        self.assertIsNone(export_task.pou_lib["Main"].code)

        # user-defined previous（内核不支持的 user feedback）→ 失败关闭。
        user_fb = _auto_payload()
        user_fb["connections"][1]["read_mode"] = "previous"
        user_fb_task = _task(user_fb)
        with self.assertRaises(CFCLoweringError) as raised:
            compile_cfc_task(user_fb, _auto_bodies(), user_fb_task, "Main")
        self.assertIn("UNSUPPORTED_FEEDBACK_CARRIER", _codes(raised.exception))
        self.assertIsNone(user_fb_task.pou_lib["Main"].code)

        # 缺失 / 多余 / 重复 body。
        payload = _auto_payload()
        for name, bodies, code in (
            ("missing", _auto_bodies()[:2], "MISSING_BODY"),
            ("unknown", _auto_bodies() + (CFCNodeBody("Z", ()),), "UNKNOWN_BODY"),
            ("duplicate", _auto_bodies() + (CFCNodeBody("A", ()),), "DUPLICATE_BODY"),
        ):
            with self.subTest(name=name):
                task = _task(payload)
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, bodies, task, "Main")
                self.assertIn(code, _codes(raised.exception))
                self.assertIsNone(task.pou_lib["Main"].code)

        # 非法 body 指令由 lowering 门禁透传 CFCLoweringError。
        bad_body = (
            CFCNodeBody("IN_A", ()),
            CFCNodeBody("A", (LoadPrev("Mid", "BOOL"),)),
            CFCNodeBody("B", (LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"))),
        )
        task = _task(payload)
        with self.assertRaises(CFCLoweringError) as raised:
            compile_cfc_task(payload, bad_body, task, "Main")
        self.assertIn("FEEDBACK_UNSUPPORTED", _codes(raised.exception))
        self.assertIsNone(task.pou_lib["Main"].code)

        # 目标 POU 身份不匹配（source 不是 payload）。
        mismatch_task = _task(payload)
        mismatch_task.pou_lib["Main"].source = _auto_payload()  # 另一个 dict
        with self.assertRaises(CFCLoweringError) as raised:
            compile_cfc_task(payload, _auto_bodies(), mismatch_task, "Main")
        self.assertIn("INVALID_TARGET_POU", _codes(raised.exception))
        self.assertIsNone(mismatch_task.pou_lib["Main"].code)

        # registry/IR 校验失败（body 写未声明变量）。
        ir_payload = _auto_payload()
        ir_bodies = (
            CFCNodeBody("IN_A", ()),
            CFCNodeBody("A", (LoadVar("Missing", "BOOL"), StoreVar("Mid", "BOOL"))),
            CFCNodeBody("B", (LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"))),
        )
        ir_task = _task(ir_payload)
        with self.assertRaises(IRValidationError):
            compile_cfc_task(ir_payload, ir_bodies, ir_task, "Main")
        self.assertIsNone(ir_task.pou_lib["Main"].code)

    # --- 必须新增测试 5：零观察 + 别名隔离 ------------------------------
    def test_malicious_inputs_are_not_observed(self):
        payload = _auto_payload()
        # 恶意 payload（dict 子类）→ 由 load_cfc_model 透传 CFCModelError，零观察。

        class DictChild(dict):
            pass

        child = DictChild(payload)
        child_task = _task(child)
        with self.assertRaises(CFCModelError):
            compile_cfc_task(child, _auto_bodies(), child_task, "Main")

        # 恶意 bodies 容器 / 元素。
        for name, bodies in (
            ("bodies-not-tuple", list(_auto_bodies())),
            ("body-not-exact", _auto_bodies() + (_ObservationTrap(),)),
            ("body-id-trap", _auto_bodies() + (CFCNodeBody(_ObservationTrap(), ()),)),
            ("body-container-trap",
             (CFCNodeBody("IN_A", _ObservationTrap()),) + _auto_bodies()[1:]),
        ):
            with self.subTest(name=name):
                task = _task(payload)
                with self.assertRaises(CFCLoweringError):
                    compile_cfc_task(payload, bodies, task, "Main")
                self.assertIsNone(task.pou_lib["Main"].code)

        # 恶意 Task 标量。
        task = _task(payload)
        task.cycle_ms = _ObservationTrap()
        with self.assertRaises(CFCLoweringError) as raised:
            compile_cfc_task(payload, _auto_bodies(), task, "Main")
        self.assertIn("INVALID_TASK_CYCLE", _codes(raised.exception))

    def test_result_isolated_from_caller_mutation_and_between_calls(self):
        payload = _auto_payload()
        bodies = _auto_bodies()
        task = _task(payload)
        result = compile_cfc_task(payload, bodies, task, "Main")
        original_code = result.code

        # 成功后修改原 payload 容器不改变结果。
        payload["nodes"].append(_node("EXTRA", "block", []))
        payload["connections"].clear()
        self.assertEqual(result.code, original_code)

        # 两次独立编译结果不共享可变容器。
        second_payload = _auto_payload()
        second = compile_cfc_task(
            second_payload, _auto_bodies(), _task(second_payload), "Main")
        self.assertIsNot(result.task, second.task)
        self.assertIsNot(result.task.pou_lib, second.task.pou_lib)
        self.assertIsNot(result.model, second.model)
        self.assertEqual(result.code, second.code)

    # --- WP-086 红灯：完整 Task 图的深层可变别名 -----------------------
    def test_result_task_deeply_isolates_all_mutable_records(self):
        payload = _auto_payload()
        task = _task_with_mutable_records(payload)
        result = compile_cfc_task(payload, _auto_bodies(), task, "Main")
        second = compile_cfc_task(payload, _auto_bodies(), task, "Main")

        # 每个声明记录、声明列表、POU、实例及其 dict/set 容器均须断开。
        for compiled in (result.task, second.task):
            self.assertIsNot(compiled.programs[0], task.programs[0])
            self.assertIsNot(compiled.gvl[0], task.gvl[0])
            self.assertIsNot(compiled.gvl[0].initial, task.gvl[0].initial)
            self.assertIsNot(compiled.io_map[0], task.io_map[0])
            self.assertIsNot(compiled.pou_lib["Main"], task.pou_lib["Main"])
            self.assertIsNot(compiled.pou_lib["Main"].locals,
                             task.pou_lib["Main"].locals)
            self.assertIsNot(compiled.pou_lib["Main"].locals[0],
                             task.pou_lib["Main"].locals[0])
            self.assertIsNot(compiled.pou_lib["Main"].instances[0],
                             task.pou_lib["Main"].instances[0])
            self.assertIsNot(compiled.pou_lib["Main"].instances[0].ctor_args,
                             task.pou_lib["Main"].instances[0].ctor_args)
            self.assertIsNot(compiled.pou_lib["Main"].instances[0].retain,
                             task.pou_lib["Main"].instances[0].retain)
            self.assertIsNot(compiled.pou_lib["Sibling"], task.pou_lib["Sibling"])
            self.assertIsNot(compiled.pou_lib["Sibling"].code,
                             task.pou_lib["Sibling"].code)
            self.assertIsNot(compiled.pou_lib["Sibling"].instances[0],
                             task.pou_lib["Sibling"].instances[0])

        self.assertIsNot(result.task.gvl[0], second.task.gvl[0])
        self.assertIsNot(result.task.pou_lib["Sibling"].instances[0].ctor_args,
                         second.task.pou_lib["Sibling"].instances[0].ctor_args)

        # 原 Task 的事后修改不能污染任一成功结果（含 sibling）。
        task.gvl[0].name = "Changed"
        task.gvl[0].initial["history"].append(99)
        task.pou_lib["Main"].locals[0].name = "ChangedLocal"
        task.pou_lib["Sibling"].code.append(LoadConst(False, "BOOL"))
        task.pou_lib["Sibling"].instances[0].ctor_args["seed"].append(99)
        task.pou_lib["Sibling"].instances[0].retain.add("Q")
        for compiled in (result.task, second.task):
            self.assertEqual(compiled.gvl[0].name, "Start")
            self.assertEqual(compiled.gvl[0].initial, {"history": [1]})
            self.assertEqual(compiled.pou_lib["Main"].locals[0].name, "InputA")
            self.assertEqual(compiled.pou_lib["Sibling"].code, [])
            self.assertEqual(compiled.pou_lib["Sibling"].instances[0].ctor_args,
                             {"seed": [2]})
            self.assertEqual(compiled.pou_lib["Sibling"].instances[0].retain,
                             {"ET"})

    # --- WP-086 红灯：LoadConst / Binding.actual=Const 字面量门禁 -------
    def test_instruction_and_binding_literals_fail_closed_without_observation(self):
        class IntChild(int):
            pass

        literal_cases = (
            ("bool-as-int", True, "INT"),
            ("wrong-type", "not-bool", "BOOL"),
            ("out-of-range", 128, "SINT"),
            ("nan", float("nan"), "REAL"),
            ("infinity", float("inf"), "LREAL"),
            ("scalar-subclass", IntChild(1), "INT"),
            ("base-exception", _ObservationTrap(), "BOOL"),
        )
        expected = [("INVALID_LITERAL_VALUE",
                     "literal value must be an exact built-in scalar valid for its IEC type",
                     "A")]

        for name, value, iec_type in literal_cases:
            with self.subTest(site="LoadConst", name=name):
                payload = _auto_payload()
                bodies = (
                    CFCNodeBody("IN_A", ()),
                    CFCNodeBody("A", (
                        LoadConst(value, iec_type),
                        StoreVar("ConstOut", iec_type),
                    )),
                    CFCNodeBody("B", (LoadVar("InputB", "BOOL"),
                                      StoreVar("Motor", "BOOL"))),
                )
                task = _task(payload, locals_=("InputA", "InputB", "ConstOut"))
                task.pou_lib["Main"].locals[2].iec_type = iec_type
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, bodies, task, "Main")
                self.assertEqual(
                    [(item.code, item.message, item.node_id)
                     for item in raised.exception.errors], expected)
                self.assertIsNone(task.pou_lib["Main"].code)

            with self.subTest(site="Binding.actual=Const", name=name):
                payload = _auto_payload()
                bodies = (
                    CFCNodeBody("IN_A", ()),
                    CFCNodeBody("A", (
                        CallFunc("Fn", (Binding("arg", "IN", Const(value, iec_type),
                                                iec_type),), "BOOL"),
                        StoreVar("Flag", "BOOL"),
                    )),
                    CFCNodeBody("B", (LoadVar("InputB", "BOOL"),
                                      StoreVar("Motor", "BOOL"))),
                )
                task = _function_task(payload)
                task.pou_lib["Fn"].interface[0].iec_type = iec_type
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, bodies, task, "Main")
                self.assertEqual(
                    [(item.code, item.message, item.node_id)
                     for item in raised.exception.errors], expected)
                self.assertIsNone(task.pou_lib["Main"].code)

    # --- WP-086 红灯：Registry 信任边界与失败原子性 ---------------------
    def test_registry_boundary_rejects_ducks_and_preserves_empty_registry_rule(self):
        payload = _auto_payload()

        class RegistryChild(Registry):
            def has(self, *args):  # pragma: no cover - 正确实现绝不调用
                raise _Boom("Registry subclass method observed")

        class DuckRegistry:
            def has(self, *args):  # pragma: no cover - 正确实现绝不调用
                raise _Boom("duck method observed")

        for name, registry in (
            ("duck", DuckRegistry()),
            ("subclass", RegistryChild()),
            ("malformed-exact", Registry()),
        ):
            with self.subTest(name=name):
                if name == "malformed-exact":
                    registry._entries = _ObservationTrap()
                task = _task_with_mutable_records(payload)
                original_sibling = task.pou_lib["Sibling"]
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, _auto_bodies(), task, "Main", registry)
                self.assertEqual(
                    [(item.code, item.message, item.node_id)
                     for item in raised.exception.errors],
                    [("INVALID_REGISTRY", "registry must be None or an exact safe Registry", None)])
                self.assertIsNone(task.pou_lib["Main"].code)
                self.assertIs(task.pou_lib["Sibling"], original_sibling)

        # 合法 exact 空 Registry 不走结构错误；它仍由冻结 Loader 报未注册库块。
        empty_task = _task_with_mutable_records(payload)
        with self.assertRaises(IRValidationError):
            compile_cfc_task(payload, _auto_bodies(), empty_task, "Main", Registry())
        self.assertIsNone(empty_task.pou_lib["Main"].code)
        self.assertEqual(empty_task.pou_lib["Sibling"].code, [])

    # --- WP-086 Iteration 2 红灯：完整 Task 字段与有界配置复制 ----------
    def test_config_clone_contract_rejects_omissions_cycles_and_limits(self):
        payload = _auto_payload()

        for field_name, value, expected in (
            ("retain", _ObservationTrap(), "VarDecl.retain must be an exact bool"),
            ("persistent", _ObservationTrap(), "VarDecl.persistent must be an exact bool"),
        ):
            with self.subTest(field=field_name):
                task = _task(payload)
                setattr(task.gvl[0], field_name, value)
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, _auto_bodies(), task, "Main")
                self.assertEqual([(x.code, x.message, x.node_id) for x in raised.exception.errors],
                                 [("INVALID_GVL", expected, None)])

        with self.subTest(field="channel"):
            task = _task(payload)
            task.io_map.append(IOMap("Start", _ObservationTrap(), "IN"))
            with self.assertRaises(CFCLoweringError) as raised:
                compile_cfc_task(payload, _auto_bodies(), task, "Main")
            self.assertEqual([(x.code, x.message, x.node_id) for x in raised.exception.errors],
                             [("INVALID_IO_MAP", "IOMap.channel must be an exact str", None)])

        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        cross_list = []
        cross_dict = {"list": cross_list}
        cross_list.append(cross_dict)
        for name, value in (("list-cycle", cyclic_list), ("dict-cycle", cyclic_dict),
                            ("cross-cycle", cross_list),
                            ("opaque-leaf", _ObservationTrap())):
            with self.subTest(config=name):
                task = _task(payload)
                task.gvl[0].initial = value
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, _auto_bodies(), task, "Main")
                self.assertEqual(len(raised.exception.errors), 1)
                self.assertEqual(raised.exception.errors[0].code, "INVALID_CONFIG_VALUE")

        with self.subTest(config="ctor-args-cross-cycle"):
            task = _task(payload)
            ctor_list = []
            ctor_dict = {"list": ctor_list}
            ctor_list.append(ctor_dict)
            task.pou_lib["Main"].instances.append(
                InstanceDecl("TON1", "TON", ctor_args={"cycle": ctor_list}))
            with self.assertRaises(CFCLoweringError) as raised:
                compile_cfc_task(payload, _auto_bodies(), task, "Main")
            self.assertEqual([(x.code, x.message, x.node_id) for x in raised.exception.errors],
                             [("INVALID_CONFIG_VALUE", "config containers must be acyclic", None)])

        def nested(depth):
            value = None
            for _ in range(depth):
                value = [value]
            return value

        for depth in (32, 33):
            with self.subTest(depth=depth):
                task = _task(payload)
                task.gvl[0].initial = nested(depth)
                if depth == 32:
                    self.assertIsInstance(
                        compile_cfc_task(payload, _auto_bodies(), task, "Main"),
                        CFCCompileResult)
                else:
                    with self.assertRaises(CFCLoweringError) as raised:
                        compile_cfc_task(payload, _auto_bodies(), task, "Main")
                    self.assertEqual(raised.exception.errors[0].code, "INVALID_CONFIG_VALUE")

        for size in (4095, 4096):  # root list counts as one of the 4096 nodes.
            with self.subTest(node_budget=size):
                task = _task(payload)
                task.gvl[0].initial = [None] * size
                if size == 4095:
                    self.assertIsInstance(
                        compile_cfc_task(payload, _auto_bodies(), task, "Main"),
                        CFCCompileResult)
                else:
                    with self.assertRaises(CFCLoweringError) as raised:
                        compile_cfc_task(payload, _auto_bodies(), task, "Main")
                    self.assertEqual(raised.exception.errors[0].code, "INVALID_CONFIG_VALUE")

    def test_config_clone_preserves_shared_dag_but_detaches_results(self):
        payload = _auto_payload()
        child = [1, {"leaf": True}]
        shared = {"left": child, "right": child}
        task = _task(payload)
        task.gvl[0].initial = shared
        first = compile_cfc_task(payload, _auto_bodies(), task, "Main")
        second = compile_cfc_task(payload, _auto_bodies(), task, "Main")
        for result in (first, second):
            copied = result.task.gvl[0].initial
            self.assertIs(copied["left"], copied["right"])
            self.assertIsNot(copied, shared)
            self.assertIsNot(copied["left"], child)
        self.assertIsNot(first.task.gvl[0].initial, second.task.gvl[0].initial)
        child.append("caller-mutation")
        self.assertEqual(first.task.gvl[0].initial["left"], [1, {"leaf": True}])
        self.assertEqual(second.task.gvl[0].initial["left"], [1, {"leaf": True}])

    # --- WP-086 Iteration 2 红灯：Registry entry 的递归可信副本 ---------
    def test_registry_entries_are_proven_then_rebuilt_without_hooks(self):
        payload = _auto_payload()

        class Block:
            pass

        def adapter(instance, dt_ms, inputs, refs):
            raise _Boom("adapter must not execute during compile")

        good_schema = BlockSchema(
            "SAFE", inputs=(Pin("IN", "BOOL"),),
            outputs=(Pin("OUT", "BOOL", "VAR_OUTPUT"),),
            output_access={"OUT": "attr:OUT"})
        good_adapter = RuntimeAdapter(Block, adapter)
        populated = Registry()
        populated.register(good_schema, good_adapter)

        # 合法 populated/empty Registry 均保持原有 Loader 行为；前者不执行 adapter。
        populated_task = _task(payload)
        populated_task.pou_lib["Main"].instances.append(InstanceDecl("SAFE1", "SAFE"))
        self.assertIsInstance(
            compile_cfc_task(payload, _auto_bodies(), populated_task, "Main", populated),
            CFCCompileResult)
        with self.assertRaises(IRValidationError):
            compile_cfc_task(payload, _auto_bodies(), _task_with_mutable_records(payload),
                             "Main", Registry())

        for name, mutate in (
            ("pin-field", lambda: object.__setattr__(good_schema.inputs[0], "name", _ObservationTrap())),
            ("schema-field", lambda: object.__setattr__(good_schema, "inputs", (_ObservationTrap(),))),
            ("adapter-field", lambda: object.__setattr__(good_adapter, "ctor_args", (_ObservationTrap(),))),
        ):
            with self.subTest(name=name):
                schema = BlockSchema(
                    "SAFE", inputs=(Pin("IN", "BOOL"),),
                    outputs=(Pin("OUT", "BOOL", "VAR_OUTPUT"),),
                    output_access={"OUT": "attr:OUT"})
                runtime_adapter = RuntimeAdapter(Block, adapter)
                registry = Registry()
                registry.register(schema, runtime_adapter)
                if name == "pin-field":
                    object.__setattr__(schema.inputs[0], "name", _ObservationTrap())
                elif name == "schema-field":
                    object.__setattr__(schema, "inputs", (_ObservationTrap(),))
                else:
                    object.__setattr__(runtime_adapter, "ctor_args", (_ObservationTrap(),))
                task = _task(payload)
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, _auto_bodies(), task, "Main", registry)
                self.assertEqual([(x.code, x.message, x.node_id) for x in raised.exception.errors],
                                 [("INVALID_REGISTRY", "registry must be None or an exact safe Registry", None)])
                self.assertIsNone(task.pou_lib["Main"].code)

    # --- WP-088：所有实际读取 dataclass 的 exact-instance shell ------------
    def test_instance_shell_proof_rejects_missing_and_extra_fields(self):
        """每个入口实际读取的 dataclass family 都不得回落 class default。"""
        payload = _auto_payload()

        def assert_rejected(mutate, *, direct=False):
            task = _task(payload)
            if direct:
                graph = load_cfc_model(payload).to_order_graph()
                with self.assertRaises(CFCLoweringError):
                    lower_cfc_task(graph, (
                        CFCNodeIR("IN_A", (), ()),
                        CFCNodeIR("A", (CFCInputBinding("IN_A", "Start", "InputA", "BOOL"),), ()),
                        CFCNodeIR("B", (CFCInputBinding("A", "Mid", "InputB", "BOOL"),), ()),
                    ), task, "Main")
                return
            mutate(task)
            with self.assertRaises(CFCLoweringError):
                compile_cfc_task(payload, _auto_bodies(), task, "Main")

        families = (
            lambda task: task.__dict__.__setitem__("extra", None),
            lambda task: task.programs[0].__dict__.pop("store_prefix"),
            lambda task: task.pou_lib["Main"].__dict__.__setitem__("extra", None),
            lambda task: task.gvl[0].__dict__.pop("retain"),
            lambda task: (task.io_map.append(IOMap("Start", "%IX0.0", "IN")),
                          task.io_map[0].__dict__.__setitem__("extra", None)),
            lambda task: (task.pou_lib["Main"].instances.append(InstanceDecl("T", "TON")),
                          task.pou_lib["Main"].instances[0].__dict__.pop("retain")),
        )
        for mutate in families:
            with self.subTest(family=mutate):
                assert_rejected(mutate)

        # CFC fragment/body/binding and direct graph/node/edge are separately
        # untrusted direct-lowering inputs, not Loader-owned model objects.
        task = _task(payload)
        bodies = list(_auto_bodies())
        bodies[0].__dict__["extra"] = None
        with self.assertRaises(CFCLoweringError):
            compile_cfc_task(payload, tuple(bodies), task, "Main")
        graph = load_cfc_model(payload).to_order_graph()
        graph.__dict__["extra"] = None
        with self.assertRaises(CFCLoweringError):
            lower_cfc_task(graph, (), _task(graph), "Main")

        def direct_with(fragment_mutation=None, graph_mutation=None):
            graph = load_cfc_model(payload).to_order_graph()
            fragments = [
                CFCNodeIR("IN_A", (), ()),
                CFCNodeIR("A", (CFCInputBinding("IN_A", "Start", "InputA", "BOOL"),), ()),
                CFCNodeIR("B", (CFCInputBinding("A", "Mid", "InputB", "BOOL"),), ()),
            ]
            if fragment_mutation:
                fragment_mutation(fragments)
            if graph_mutation:
                graph_mutation(graph)
            with self.assertRaises(CFCLoweringError):
                lower_cfc_task(graph, tuple(fragments), _task(graph), "Main")

        direct_with(lambda fs: fs[1].__dict__.pop("body"))
        direct_with(lambda fs: fs[1].inputs[0].__dict__.__setitem__("extra", None))
        direct_with(graph_mutation=lambda graph: graph.nodes[0].__dict__.pop("feedback_marker"))
        direct_with(graph_mutation=lambda graph: graph.edges[0].__dict__.__setitem__("extra", None))

    def test_all_instruction_and_nested_shells_reject_before_loader(self):
        """The formal instruction whitelist has no field-default escape hatch."""
        payload = _auto_payload()
        instructions = (
            LoadVar("InputA", "BOOL"), LoadConst(True, "BOOL"),
            LoadPrev("InputA", "BOOL"), StoreVar("InputA", "BOOL"),
            BinOp("AND", "BOOL"), UnOp("NOT", "BOOL"), Convert("BOOL", "BOOL"),
            CallStd("ABS", StdSig(("INT",), "INT")), CallFb("T"),
            CallFunc("Fn", (), "BOOL"), CallFbInstance("FB", ()),
            Jmp("L"), JmpIfFalse("L"), Label("L"),
        )
        for instruction in instructions:
            with self.subTest(instruction=type(instruction).__name__):
                instruction.__dict__["extra"] = None
                bodies = (CFCNodeBody("IN_A", ()), CFCNodeBody("A", (instruction,)),
                          CFCNodeBody("B", ()))
                with self.assertRaises(CFCLoweringError):
                    compile_cfc_task(payload, bodies, _task(payload), "Main")

        nested_cases = (
            (CallStd("ABS", StdSig(("INT",), "INT")), lambda ins: ins.sig.__dict__.pop("return_type")),
            (CallFunc("Fn", (Binding("a", "IN", StoreKey("InputA"), "BOOL"),), "BOOL"),
             lambda ins: ins.bindings[0].__dict__.__setitem__("extra", None)),
            (CallFunc("Fn", (Binding("a", "IN", StoreKey("InputA"), "BOOL"),), "BOOL"),
             lambda ins: ins.bindings[0].actual.__dict__.pop("key")),
            (CallFunc("Fn", (Binding("a", "IN", StackSlot(0), "BOOL"),), "BOOL"),
             lambda ins: ins.bindings[0].actual.__dict__.__setitem__("extra", None)),
            (CallFunc("Fn", (Binding("a", "IN", Const(True, "BOOL"), "BOOL"),), "BOOL"),
             lambda ins: ins.bindings[0].actual.__dict__.pop("type")),
        )
        for instruction, mutate in nested_cases:
            with self.subTest(nested=type(instruction).__name__):
                mutate(instruction)
                bodies = (CFCNodeBody("IN_A", ()), CFCNodeBody("A", (instruction,)),
                          CFCNodeBody("B", ()))
                with self.assertRaises(CFCLoweringError):
                    compile_cfc_task(payload, bodies, _function_task(payload), "Main")

    def test_config_roots_count_every_slot_and_fail_fast(self):
        payload = _auto_payload()
        # root + dict key + dict value + set member are all occurrence slots.
        task = _task(payload)
        task.pou_lib["Main"].instances.append(InstanceDecl(
            "T", "TON", ctor_args={"values": {None}}))
        self.assertIsInstance(
            compile_cfc_task(payload, _auto_bodies(), task, "Main"), CFCCompileResult)

        for root_name in ("ctor_args", "init_overrides"):
            with self.subTest(root=root_name):
                task = _task(payload)
                kwargs = {root_name: {"many": [None] * 4095}}
                task.pou_lib["Main"].instances.append(InstanceDecl("T", "TON", **kwargs))
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, _auto_bodies(), task, "Main")
                self.assertEqual(
                    [(x.code, x.message) for x in raised.exception.errors],
                    [("INVALID_CONFIG_VALUE", "config value exceeds the per-root node budget")])

        # Unsupported first child must stop the root before its later tail.
        task = _task(payload)
        task.gvl[0].initial = [_ObservationTrap(), [None] * 5000]
        with self.assertRaises(CFCLoweringError) as raised:
            compile_cfc_task(payload, _auto_bodies(), task, "Main")
        self.assertEqual(
            [(x.code, x.message) for x in raised.exception.errors],
            [("INVALID_CONFIG_VALUE", "config value must use exact supported leaves or containers")])

    def test_registry_carrier_shell_and_default_catalog_rebuild(self):
        payload = _auto_payload()

        class Block:
            pass

        def adapter(instance, dt_ms, inputs, refs):
            raise _Boom("must not execute")

        def registry_with_schema():
            schema = BlockSchema("SAFE", outputs=(Pin("OUT", "BOOL", "VAR_OUTPUT"),),
                                 output_access={"OUT": "attr:OUT"})
            registry = Registry()
            registry.register(schema, RuntimeAdapter(Block, adapter))
            return registry, schema

        for mutation in (
            lambda registry, schema: object.__setattr__(schema.output_access, "_pairs", (("OUT", "attr:OUT"), ("OUT", "attr:OUT"))),
            lambda registry, schema: object.__setattr__(schema.output_access, "_pairs", (("OUT", _ObservationTrap()),)),
            lambda registry, schema: registry.__dict__.__setitem__("extra", None),
            lambda registry, schema: schema.__dict__.__setitem__("extra", None),
            lambda registry, schema: schema.outputs[0].__dict__.pop("kind"),
            lambda registry, schema: registry._entries[("SAFE", "engineering")][1].__dict__.__setitem__("extra", None),
        ):
            with self.subTest(mutation=mutation):
                registry, schema = registry_with_schema()
                mutation(registry, schema)
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, _auto_bodies(), _task(payload), "Main", registry)
                self.assertEqual(_codes(raised.exception), {"INVALID_REGISTRY"})

        default = build_default_registry()
        self.assertEqual(len(default.keys()), 22)
        task = _task(payload)
        task.pou_lib["Main"].instances.append(InstanceDecl("T", "TON"))
        self.assertIsInstance(
            compile_cfc_task(payload, _auto_bodies(), task, "Main", default), CFCCompileResult)

    # --- 必须新增测试 6：多错误诊断 + 入口不掩盖内核门禁 -----------------
    def test_multiple_body_errors_aggregate_stably(self):
        payload = _auto_payload()
        bodies = (CFCNodeBody("A", ()),)  # 缺 IN_A / B（多缺失聚合）
        for order in (bodies, tuple(reversed(bodies))):
            with self.subTest():
                task = _task(payload)
                with self.assertRaises(CFCLoweringError) as raised:
                    compile_cfc_task(payload, order, task, "Main")
                self.assertEqual(
                    [(item.code, item.message, item.node_id)
                     for item in raised.exception.errors],
                    [("MISSING_BODY", "model node has no body", "B"),
                     ("MISSING_BODY", "model node has no body", "IN_A")])

    def test_entry_does_not_mask_current_dependency_order_gate(self):
        # explicit 逆序：source A(2) 在 target B(1) 之后 → 内核 CURRENT_DEPENDENCY_ORDER。
        payload = _explicit_payload()
        payload["nodes"][1]["execution_order_id"] = 2  # A
        payload["nodes"][2]["execution_order_id"] = 1  # B
        payload["nodes"][0]["execution_order_id"] = 0  # IN_A
        task = _task(payload)
        with self.assertRaises(CFCLoweringError) as raised:
            compile_cfc_task(payload, _auto_bodies(), task, "Main")
        self.assertIn("CURRENT_DEPENDENCY_ORDER", _codes(raised.exception))
        self.assertIsNone(task.pou_lib["Main"].code)


# 供测试 3 手工构造反馈全边图（与入口内部 _model_feedback_graph 等价）。
def _model_feedback_graph_for_test(model):
    from src.runtime.cfc_order import CFCOrderEdge, CFCOrderGraph
    nodes = tuple(
        CFCOrderNode(n.node_id, n.execution_order_id,
                     False if n.feedback_marker is None else n.feedback_marker)
        for n in model.nodes
    )
    seen = set()
    edges = []
    for conn in model.connections:
        pair = (conn.source_node_id, conn.target_node_id)
        if pair not in seen:
            seen.add(pair)
            edges.append(CFCOrderEdge(conn.source_node_id, conn.target_node_id))
    edges.sort(key=lambda edge: (edge.source, edge.target))
    return CFCOrderGraph(nodes, tuple(edges), model.carrier,
                         model.execution_order_mode, model.order_source)


if __name__ == "__main__":
    unittest.main()
