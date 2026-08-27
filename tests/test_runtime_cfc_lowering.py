"""WP-20260803-068：无环 CFC 到正式 typed IR 的定向测试。"""
from __future__ import annotations

import unittest

from src.runtime.cfc_lowering import (
    CFCInputBinding, CFCNodeIR, CFCLoweringError, lower_cfc_task,
)
from src.runtime.cfc_order import CFCOrderEdge, CFCOrderGraph, CFCOrderNode
from src.runtime.ir import (
    CallStd, LoadConst, LoadPrev, LoadVar, POUDefinition, ProgramInstance,
    StdSig, StoreVar, Task, VarDecl,
)
from src.runtime.loader import IRValidationError


class _Boom(BaseException):
    """自定义 BaseException：任一被观察即逃逸，证明零观察边界（不被 except Exception 吞）。"""


class _ObservationTrap:
    """任何观察（repr/str/eq/ne/hash/bool）都抛 BaseException 的恶意对象。

    完整的先于 Loader 的验证器只允许对它做 ``type(x) is T`` 判定；一旦被
    ``%r`` 格式化、``==`` / ``in`` 比较、哈希或 ``bool()``，``_Boom`` 就会逃逸，
    assertRaises(CFCLoweringError) 随即失败——正是缺陷回归的红灯信号。
    """

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


def _graph(nodes=("A", "B"), edges=(("A", "B"),)):
    return CFCOrderGraph(tuple(CFCOrderNode(name) for name in nodes),
                         tuple(CFCOrderEdge(*edge) for edge in edges),
                         "user_defined", "auto", "user_defined")


def _task(graph):
    pou = POUDefinition(
        "Main", "PROGRAM", "CFC",
        locals=[VarDecl("InputA", "BOOL"), VarDecl("InputB", "BOOL")],
        source=graph, code=None,
    )
    return Task(
        programs=[ProgramInstance("Main", "PLC_PRG")],
        gvl=[VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
             VarDecl("Mid", "BOOL", section="VAR_GLOBAL"),
             VarDecl("Motor", "BOOL", section="VAR_GLOBAL")],
        pou_lib={"Main": pou},
    )


def _nodes(*, reverse=False):
    values = (
        CFCNodeIR("A", (CFCInputBinding(None, "Start", "InputA", "BOOL"),),
                  (LoadVar("InputA", "BOOL"), StoreVar("Mid", "BOOL"))),
        CFCNodeIR("B", (CFCInputBinding("A", "Mid", "InputB", "BOOL"),),
                  (LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"))),
    )
    return tuple(reversed(values)) if reverse else values


class TestCFCLowering(unittest.TestCase):
    def test_lowers_current_bindings_before_bodies_and_validates_cloned_task(self):
        graph = _graph()
        original = _task(graph)
        result = lower_cfc_task(graph, _nodes(), original, "Main")
        self.assertEqual(result.execution_order, ("A", "B"))
        self.assertEqual(result.code, (
            LoadVar("Start", "BOOL"), StoreVar("InputA", "BOOL"),
            LoadVar("InputA", "BOOL"), StoreVar("Mid", "BOOL"),
            LoadVar("Mid", "BOOL"), StoreVar("InputB", "BOOL"),
            LoadVar("InputB", "BOOL"), StoreVar("Motor", "BOOL"),
        ))
        self.assertIsNot(result.task, original)
        self.assertIsNone(original.pou_lib["Main"].code)
        self.assertIs(result.task.pou_lib["Main"].source, graph)

    def test_node_and_edge_permutations_are_stable(self):
        first_graph = _graph()
        first = lower_cfc_task(first_graph, _nodes(), _task(first_graph), "Main")
        second_graph = _graph(nodes=("B", "A"), edges=(("A", "B"),))
        second = lower_cfc_task(second_graph, _nodes(reverse=True), _task(second_graph), "Main")
        self.assertEqual(first.code, second.code)

    def test_graph_external_source_does_not_require_an_edge(self):
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (CFCInputBinding(None, "Start", "InputA", "BOOL"),),
                         (LoadVar("InputA", "BOOL"), StoreVar("Motor", "BOOL")))
        result = lower_cfc_task(graph, (node,), _task(graph), "Main")
        self.assertEqual(result.execution_order, ("A",))

    def test_accepts_typed_call_std_body(self):
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), CallStd("NOT", StdSig(("BOOL",), "BOOL")), StoreVar("Motor", "BOOL")))
        result = lower_cfc_task(graph, (node,), _task(graph), "Main")
        self.assertEqual(result.code[-1], StoreVar("Motor", "BOOL"))

    def test_fragment_and_edge_contract_fail_closed(self):
        graph = _graph()
        cases = (
            (_nodes()[:1], "MISSING_FRAGMENT"),
            (_nodes() + (CFCNodeIR("X"),), "UNKNOWN_FRAGMENT"),
            ((CFCNodeIR("A"), CFCNodeIR("B")), "EDGE_BINDING_MISMATCH"),
        )
        for nodes, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, nodes, _task(graph), "Main")
                self.assertIn(code, [error.code for error in raised.exception.errors])

    def test_feedback_and_load_prev_are_rejected(self):
        graph = _graph(nodes=("A",), edges=())
        marked = CFCOrderGraph((CFCOrderNode("A", feedback_marker=True),), (), "user_defined", "auto", "user_defined")
        with self.assertRaises(CFCLoweringError):
            lower_cfc_task(marked, (CFCNodeIR("A"),), _task(marked), "Main")
        with self.assertRaises(CFCLoweringError):
            lower_cfc_task(graph, (CFCNodeIR("A", body=(LoadPrev("Motor", "BOOL"),)),), _task(graph), "Main")

    def test_invalid_exact_binding_fields_are_rejected(self):
        class StringChild(str):
            pass

        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (CFCInputBinding(None, StringChild("Start"), "InputA", "BOOL", False),), ())
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_task(graph, (node,), _task(graph), "Main")
        self.assertIn("INVALID_BINDING", [error.code for error in raised.exception.errors])

    def test_ir_validation_errors_and_task_isolation_are_preserved(self):
        graph = _graph(nodes=("A",), edges=())
        bad = CFCNodeIR("A", (), (LoadVar("Missing", "BOOL"),))
        task_a, task_b = _task(graph), _task(graph)
        with self.assertRaises(IRValidationError):
            lower_cfc_task(graph, (bad,), task_a, "Main")
        good = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        self.assertIsNot(lower_cfc_task(graph, (good,), task_b, "Main").task, task_b)
        self.assertIsNone(task_a.pou_lib["Main"].code)
        self.assertIsNone(task_b.pou_lib["Main"].code)

    def test_unrelated_invalid_pou_lib_entry_fails_closed(self):
        # Round 4：完整的先于 Loader 的输入图验证器不能把未验证的兄弟 pou_lib 值
        # 交给冻结 Loader（其 `%r` 会观察恶意对象）。因此任何非 exact POUDefinition
        # 的兄弟值都在 clone/validate_task 之前失败关闭为稳定 CFCLoweringError，
        # 且不改写原 Task/兄弟条目。（本用例取代 Round 3 的“留给 IR 校验”边界——
        # Codex Round 3 已把未验证兄弟值判为必须返修项。）
        graph = _graph(nodes=("A",), edges=())
        task = _task(graph)
        broken = object()
        task.pou_lib["Broken"] = broken
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_task(graph, (node,), task, "Main")
        self.assertIn("INVALID_POU_LIB_VALUE",
                      [error.code for error in raised.exception.errors])
        self.assertIsNone(task.pou_lib["Main"].code)
        self.assertIs(task.pou_lib["Broken"], broken)

    def test_duplicate_current_target_pin_fails_closed(self):
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (
            CFCInputBinding(None, "Start", "InputA", "BOOL"),
            CFCInputBinding(None, "Mid", "InputA", "BOOL"),
        ), (LoadVar("InputA", "BOOL"), StoreVar("Motor", "BOOL")))
        original = _task(graph)
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_task(graph, (node,), original, "Main")
        self.assertIn("DUPLICATE_TARGET_BINDING",
                      [error.code for error in raised.exception.errors])
        self.assertIsNone(original.pou_lib["Main"].code)

    def test_current_input_permutation_produces_identical_code(self):
        graph = _graph(nodes=("A",), edges=())

        def fragments(reverse):
            bindings = (
                CFCInputBinding(None, "Start", "InputA", "BOOL"),
                CFCInputBinding(None, "Mid", "InputB", "BOOL"),
            )
            body = (LoadVar("InputA", "BOOL"), StoreVar("Motor", "BOOL"))
            return (CFCNodeIR("A", tuple(reversed(bindings)) if reverse else bindings, body),)

        first = lower_cfc_task(graph, fragments(False), _task(graph), "Main")
        second = lower_cfc_task(graph, fragments(True), _task(graph), "Main")
        self.assertEqual(first.code, second.code)

    def _explicit_reverse_case(self, order_a, order_b):
        graph = CFCOrderGraph(
            (CFCOrderNode("A", order_a), CFCOrderNode("B", order_b)),
            (CFCOrderEdge("A", "B"),),
            "plcopen_xml", "explicit", "exported",
        )
        pou = POUDefinition(
            "Main", "PROGRAM", "CFC",
            locals=[VarDecl("InB", "BOOL")], source=graph, code=None,
        )
        task = Task(
            programs=[ProgramInstance("Main", "PLC_PRG")],
            gvl=[VarDecl("Sig", "BOOL", section="VAR_GLOBAL")],
            pou_lib={"Main": pou},
        )
        nodes = (
            CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Sig", "BOOL"))),
            CFCNodeIR("B", (CFCInputBinding("A", "Sig", "InB", "BOOL"),),
                      (LoadVar("InB", "BOOL"), StoreVar("Sig", "BOOL"))),
        )
        return graph, nodes, task

    def test_reverse_explicit_current_dependency_fails_closed(self):
        # executionOrderId places source A (2) after target B (1): B would read
        # A's current output before A runs, so a LoadVar current edge must fail
        # closed rather than silently rely on a stale Store value.
        graph, nodes, task = self._explicit_reverse_case(2, 1)
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_task(graph, nodes, task, "Main")
        self.assertIn("CURRENT_DEPENDENCY_ORDER",
                      [error.code for error in raised.exception.errors])
        self.assertIsNone(task.pou_lib["Main"].code)

    def test_forward_explicit_current_dependency_lowers(self):
        graph, nodes, task = self._explicit_reverse_case(1, 2)
        result = lower_cfc_task(graph, nodes, task, "Main")
        self.assertEqual(result.execution_order, ("A", "B"))

    def test_malformed_task_container_fails_closed(self):
        # Codex 未预告反证：exact Task 的内部容器被换成错误类型（如 pou_lib=list）时，
        # clone/get 前必须失败关闭为稳定 CFCLoweringError，绝不泄漏 AttributeError。
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        for name, mutate in (
            ("pou_lib-list", lambda t: setattr(
                t, "pou_lib", [t.pou_lib["Main"]])),
            ("programs-tuple", lambda t: setattr(
                t, "programs", tuple(t.programs))),
            ("gvl-tuple", lambda t: setattr(t, "gvl", tuple(t.gvl))),
            ("io_map-tuple", lambda t: setattr(t, "io_map", tuple(t.io_map))),
        ):
            with self.subTest(name=name):
                task = _task(graph)
                mutate(task)
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, (node,), task, "Main")
                self.assertIn("INVALID_TASK_CONTAINER",
                              [error.code for error in raised.exception.errors])

    def test_malformed_target_pou_container_fails_closed(self):
        # Codex Round 2 未预告反证：目标 POU 的 interface/locals/instances 会被
        # _clone_pou 以 list(...) 迭代克隆；clone 前必须按 exact list 校验——tuple
        # 会被静默归一化、不可迭代对象会泄漏 TypeError，两者都必须失败关闭为稳定
        # CFCLoweringError 且不改写原 POU。
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        for field in ("interface", "locals", "instances"):
            for kind, value in (("tuple", ()), ("non-iterable", object())):
                with self.subTest(field=field, kind=kind):
                    task = _task(graph)
                    setattr(task.pou_lib["Main"], field, value)
                    with self.assertRaises(CFCLoweringError) as raised:
                        lower_cfc_task(graph, (node,), task, "Main")
                    self.assertIn("INVALID_TARGET_POU",
                                  [error.code for error in raised.exception.errors])
                    self.assertIsNone(task.pou_lib["Main"].code)

    def test_malicious_pou_lib_key_is_not_observed(self):
        # Codex Round 2 未预告反证：pou_lib 的键在 `.get(pou_name)` 之前必须按 exact
        # str 校验；与目标名同 hash、__eq__/__hash__/__repr__ 抛异常的恶意非 str 键
        # 必须失败关闭为稳定 CFCLoweringError，绝不在 `.get` 时被观察。
        class ExplodingKey:
            def __hash__(self):
                return hash("Main")

            def __eq__(self, other):  # pragma: no cover - 缺陷回归才触发
                raise AssertionError("pou_lib key must not be observed")

            def __repr__(self):  # pragma: no cover - 缺陷回归才触发
                raise AssertionError("pou_lib key must not be stringified")

        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        task = _task(graph)
        task.pou_lib = {ExplodingKey(): task.pou_lib["Main"]}
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_task(graph, (node,), task, "Main")
        self.assertIn("INVALID_POU_LIB_KEY",
                      [error.code for error in raised.exception.errors])

    def test_malicious_target_pou_identity_is_not_observed(self):
        # 目标 POU 的 name/language 在 clone/比较前必须先按 exact 类型校验，
        # 不得观察攻击者的 __eq__/__ne__/__repr__。
        class ExplodingIdentity:
            def __eq__(self, other):  # pragma: no cover - 缺陷回归才触发
                raise AssertionError("POU identity must not be observed")

            def __ne__(self, other):  # pragma: no cover - 缺陷回归才触发
                raise AssertionError("POU identity must not be observed")

            def __repr__(self):  # pragma: no cover - 缺陷回归才触发
                raise AssertionError("POU identity must not be stringified")

        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        for field in ("name", "language"):
            with self.subTest(field=field):
                task = _task(graph)
                setattr(task.pou_lib["Main"], field, ExplodingIdentity())
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, (node,), task, "Main")
                self.assertIn("INVALID_TARGET_POU",
                              [error.code for error in raised.exception.errors])
                self.assertIsNone(task.pou_lib["Main"].code)

    def test_malformed_target_pou_declaration_fails_closed(self):
        # Codex Round 3 ①：目标 POU 的 exact-list interface/locals/instances 里放入
        # 非 VarDecl/InstanceDecl 值对象时，冻结 Loader 会以 `.name` 等观察它并泄漏
        # 原始 AttributeError。完整预检必须在 clone/validate_task 之前逐元素按 exact
        # 类型失败关闭为稳定 CFCLoweringError，且不改写原 POU。
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        for field in ("interface", "locals", "instances"):
            with self.subTest(field=field):
                task = _task(graph)
                setattr(task.pou_lib["Main"], field, [_ObservationTrap()])
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, (node,), task, "Main")
                self.assertIn("INVALID_TARGET_POU",
                              [error.code for error in raised.exception.errors])
                self.assertIsNone(task.pou_lib["Main"].code)

    def test_malicious_task_scalar_and_element_not_observed(self):
        # Codex Round 3 ②：Task 标量（cycle_ms）、容器元素（programs 项）、兄弟 pou_lib
        # 值与目标 pou_kind 被置为恶意对象时，冻结 Loader 会 `%r` 格式化或 `==` / `in`
        # 比较它们并让自定义 BaseException 逃逸。完整预检必须先失败关闭为稳定 CFCLoweringError。
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        for name, mutate, code in (
            ("cycle_ms", lambda t: setattr(t, "cycle_ms", _ObservationTrap()),
             "INVALID_TASK_CYCLE"),
            ("programs-item", lambda t: setattr(t, "programs", [_ObservationTrap()]),
             "INVALID_PROGRAM"),
            ("sibling-value",
             lambda t: t.pou_lib.__setitem__("Broken", _ObservationTrap()),
             "INVALID_POU_LIB_VALUE"),
            ("target-pou_kind",
             lambda t: setattr(t.pou_lib["Main"], "pou_kind", _ObservationTrap()),
             "INVALID_TARGET_POU"),
        ):
            with self.subTest(name=name):
                task = _task(graph)
                mutate(task)
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, (node,), task, "Main")
                self.assertIn(code, [error.code for error in raised.exception.errors])

    def test_malicious_body_instruction_field_not_observed(self):
        # Codex Round 3 ③：body 指令（LoadVar/StoreVar）的 key/iec_type 字段被置为
        # 恶意对象时，冻结 Loader 会在栈类型模拟中观察它们并让 BaseException 逃逸。
        # 完整预检必须在发码/clone 前按 exact-type 校验指令字段并失败关闭。
        graph = _graph(nodes=("A",), edges=())
        for name, instruction in (
            ("loadvar-key", LoadVar(_ObservationTrap(), "BOOL")),
            ("loadvar-type", LoadVar("Motor", _ObservationTrap())),
            ("storevar-key", StoreVar(_ObservationTrap(), "BOOL")),
            ("storevar-type", StoreVar("Motor", _ObservationTrap())),
        ):
            with self.subTest(name=name):
                task = _task(graph)
                node = CFCNodeIR("A", (), (instruction,))
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, (node,), task, "Main")
                self.assertIn("INVALID_INSTRUCTION",
                              [error.code for error in raised.exception.errors])
                self.assertIsNone(task.pou_lib["Main"].code)

    def test_empty_name_var_decl_malicious_subfield_not_observed(self):
        # Codex Round 3 同源缺口的最后一处：exact VarDecl 的 name 若为空串，旧
        # 「type() is str」预检会放行；但冻结 Loader 的 _check_var_decl 在
        # `not decl.name` 分支 `%r` 格式化整个 VarDecl，会观察未校验的 Any/bool 子
        # 字段（initial/retain/persistent）并让恶意 __repr__/BaseException 逃逸。完整
        # 预检必须要求 name 为非空 exact str，使该 repr 路径不可达且子字段永不被观察。
        graph = _graph(nodes=("A",), edges=())
        node = CFCNodeIR("A", (), (LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")))
        gvl_task = _task(graph)
        gvl_task.gvl.append(
            VarDecl("", "BOOL", initial=_ObservationTrap(), section="VAR_GLOBAL"))
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_task(graph, (node,), gvl_task, "Main")
        self.assertIn("INVALID_GVL",
                      [error.code for error in raised.exception.errors])
        self.assertIsNone(gvl_task.pou_lib["Main"].code)
        for field in ("interface", "locals"):
            with self.subTest(field=field):
                task = _task(graph)
                getattr(task.pou_lib["Main"], field).append(
                    VarDecl("", "BOOL", initial=_ObservationTrap()))
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_task(graph, (node,), task, "Main")
                self.assertIn("INVALID_TARGET_POU",
                              [error.code for error in raised.exception.errors])
                self.assertIsNone(task.pou_lib["Main"].code)


if __name__ == "__main__":
    unittest.main()
