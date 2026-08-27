"""WP-20260803-069：PLCopen 显式逐输入 feedback lowering 反证。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.runtime.descriptors import Registry, build_default_registry
from src.runtime.cfc_lowering import (
    CFCInputBinding,
    CFCNodeIR,
    CFCLoweringError,
    lower_cfc_feedback_task,
)
from src.runtime.cfc_order import (
    CFCOrderEdge,
    CFCOrderError,
    CFCOrderGraph,
    CFCOrderNode,
)
from src.runtime.ir import (
    InstanceDecl,
    LoadConst,
    LoadPrev,
    LoadVar,
    POUDefinition,
    ProgramInstance,
    StoreVar,
    Task,
    VarDecl,
)
from src.runtime.loader import IRValidationError


def _graph(nodes, edges, *, carrier="plcopen_xml", mode="explicit", source="exported"):
    return CFCOrderGraph(
        tuple(CFCOrderNode(*node) for node in nodes),
        tuple(CFCOrderEdge(*edge) for edge in edges),
        carrier,
        mode,
        source,
    )


def _task(graph):
    pou = POUDefinition(
        "Main",
        "PROGRAM",
        "CFC",
        locals=[
            VarDecl("IA", "BOOL"),
            VarDecl("IB", "BOOL"),
            VarDecl("IC", "BOOL"),
        ],
        source=graph,
        code=None,
    )
    return Task(
        programs=[ProgramInstance("Main", "PLC_PRG")],
        gvl=[
            VarDecl("X", "BOOL", section="VAR_GLOBAL"),
            VarDecl("Y", "BOOL", section="VAR_GLOBAL"),
            VarDecl("Z", "BOOL", section="VAR_GLOBAL"),
        ],
        pou_lib={"Main": pou},
    )


def _self_case(*, marker=False, feedback=True, body=(), carrier="plcopen_xml",
               mode="explicit", source="exported", source_node_id="A"):
    # 真实 PLCopen XML 没有 feedback_marker：节点 marker 默认保持 False，反馈证据
    # 只来自调用方逐 input 的 feedback=True 分类（WP-079 显式 feedback lowering 合同）。
    graph = _graph((("A", 1, marker),), (("A", "A"),),
                   carrier=carrier, mode=mode, source=source)
    nodes = (CFCNodeIR(
        "A",
        (CFCInputBinding(source_node_id, "X", "IA", "BOOL", feedback),),
        body,
    ),)
    return graph, nodes, _task(graph)


def _codes(error):
    return {item.code for item in error.errors}


class _ExplodingIdentity:
    """标识字段被观察时立刻爆炸，用于证明零观察边界。"""

    def __eq__(self, other):  # pragma: no cover - 只应在缺陷回归时触发
        raise AssertionError("POU identity field must not be observed")

    def __ne__(self, other):  # pragma: no cover - 只应在缺陷回归时触发
        raise AssertionError("POU identity field must not be observed")

    def __repr__(self):  # pragma: no cover - 只应在缺陷回归时触发
        raise AssertionError("POU identity field must not be stringified")


class _Boom(BaseException):
    """自定义 BaseException：任一被观察即逃逸，证明零观察边界（不被 except Exception 吞）。"""


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


class TestCFCFeedbackLowering(unittest.TestCase):
    def test_direct_feedback_uses_only_the_trusted_registry_copy(self):
        """源 Registry 只能被证明/复制，最终 Loader 不得再次调用其方法。"""
        graph, nodes, original = _self_case(body=(
            LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"),
        ))
        original.pou_lib["Main"].instances.append(
            InstanceDecl("Timer", "TON", "library"))
        source = build_default_registry()
        original_has = Registry.has
        receivers = []

        def guarded_has(receiver, *args):
            receivers.append("source" if receiver is source else "trusted")
            if receiver is source:  # pragma: no cover - 仅旧缺陷会触发
                raise _Boom("SOURCE_REGISTRY_HAS_OBSERVED")
            return original_has(receiver, *args)

        with patch.object(Registry, "has", guarded_has):
            result = lower_cfc_feedback_task(
                graph, nodes, original, "Main", source)

        self.assertTrue(receivers)
        self.assertNotIn("source", receivers)
        self.assertTrue(all(receiver == "trusted" for receiver in receivers))
        self.assertEqual(result.execution_order, ("A",))
        self.assertIsNone(original.pou_lib["Main"].code)

    def test_sample_backed_plcopen_self_feedback_uses_load_prev(self):
        graph, nodes, original = _self_case(body=(
            LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"),
        ))
        # 真实样本：PLCopen 节点无 feedback_marker，marker 保持默认 False。
        self.assertFalse(graph.nodes[0].feedback_marker)
        result = lower_cfc_feedback_task(graph, nodes, original, "Main")
        self.assertEqual(result.execution_order, ("A",))
        self.assertEqual(result.code[:2], (
            LoadPrev("X", "BOOL"), StoreVar("IA", "BOOL"),
        ))
        self.assertIs(result.task.pou_lib["Main"].source, graph)
        self.assertIsNone(original.pou_lib["Main"].code)

    def test_two_node_current_and_feedback_bindings_remain_distinct(self):
        graph = _graph(
            (("A", 1, False), ("B", 2, False)),
            (("A", "B"), ("B", "A")),
        )
        nodes = (
            CFCNodeIR("B", (
                CFCInputBinding("A", "X", "IB", "BOOL", False),
            ), (LoadVar("IB", "BOOL"), StoreVar("Y", "BOOL"))),
            CFCNodeIR("A", (
                CFCInputBinding("B", "Y", "IA", "BOOL", True),
            ), (LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"))),
        )
        result = lower_cfc_feedback_task(graph, nodes, _task(graph), "Main")
        self.assertEqual(result.execution_order, ("A", "B"))
        self.assertEqual(result.code[:2], (
            LoadPrev("Y", "BOOL"), StoreVar("IA", "BOOL"),
        ))
        self.assertIn(LoadVar("X", "BOOL"), result.code)

    def test_carrier_and_basic_feedback_failures_are_structured(self):
        cases = (
            ("user-auto", dict(carrier="user_defined", mode="auto",
                               source="user_defined"), None),
            ("export-native", dict(carrier="export_native"), None),
            ("no-feedback", {}, CFCInputBinding("A", "X", "IA", "BOOL", False)),
            ("external-feedback", {}, CFCInputBinding(None, "X", "IA", "BOOL", True)),
            ("non-exact-bool", {}, CFCInputBinding("A", "X", "IA", "BOOL", 1)),
        )
        for name, config, binding in cases:
            with self.subTest(name=name):
                graph, nodes, original = _self_case(**config)
                if binding is not None:
                    nodes = (CFCNodeIR("A", (binding,), ()),)
                with self.assertRaises(CFCLoweringError):
                    lower_cfc_feedback_task(graph, nodes, original, "Main")
                self.assertIsNone(original.pou_lib["Main"].code)

    def test_missing_edge_and_forbidden_marker_fail_closed(self):
        # PLCopen 图不得携带/伪造 feedback_marker；有 marker=True 的节点必须失败关闭，
        # 反馈证据只能来自调用方显式 input，与 marker 缺失无关。
        for name, graph, nodes, code in (
            ("missing-edge", _graph((("A", 1, False),), ()),
             (CFCNodeIR("A", (CFCInputBinding(
                 "A", "X", "IA", "BOOL", True),), ()),),
             "FEEDBACK_EDGE_MISMATCH"),
            ("fake-marker-on-feedback-node",
             _graph((("A", 1, True),), (("A", "A"),)),
             (CFCNodeIR("A", (CFCInputBinding(
                 "A", "X", "IA", "BOOL", True),), ()),),
             "FEEDBACK_MARKER_FORBIDDEN"),
            ("fake-marker-on-sibling",
             _graph((("A", 1, False), ("B", 2, True)), (("A", "A"),)),
             (CFCNodeIR("A", (CFCInputBinding(
                 "A", "X", "IA", "BOOL", True),), ()), CFCNodeIR("B")),
             "FEEDBACK_MARKER_FORBIDDEN"),
        ):
            with self.subTest(name=name):
                original = _task(graph)
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_feedback_task(graph, nodes, original, "Main")
                self.assertIn(code, _codes(raised.exception))
                self.assertIsNone(original.pou_lib["Main"].code)

    def test_mixed_pair_and_non_cyclic_feedback_are_rejected(self):
        self_graph, _, original = _self_case()
        mixed = (CFCNodeIR("A", (
            CFCInputBinding("A", "X", "IA", "BOOL", True),
            CFCInputBinding("A", "Y", "IB", "BOOL", False),
        )),)
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(self_graph, mixed, original, "Main")
        self.assertIn("MIXED_FEEDBACK_PAIR", _codes(raised.exception))

        non_cyclic = _graph(
            (("A", 1, False), ("B", 2, False)), (("B", "A"),)
        )
        nodes = (
            CFCNodeIR("A", (CFCInputBinding(
                "B", "Y", "IA", "BOOL", True),)),
            CFCNodeIR("B"),
        )
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(non_cyclic, nodes, _task(non_cyclic), "Main")
        self.assertIn("NON_CYCLIC_FEEDBACK", _codes(raised.exception))

    def test_feedback_source_cannot_precede_target_execution_order(self):
        graph = _graph(
            (("A", 2, False), ("B", 1, False)),
            (("A", "B"), ("B", "A")),
        )
        nodes = (
            CFCNodeIR("A", (CFCInputBinding(
                "B", "Y", "IA", "BOOL", True),)),
            CFCNodeIR("B", (CFCInputBinding(
                "A", "X", "IB", "BOOL", False),)),
        )
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(graph, nodes, _task(graph), "Main")
        self.assertIn("FEEDBACK_ORDER", _codes(raised.exception))

    def test_projection_with_an_unmarked_remaining_cycle_is_rejected(self):
        graph = _graph(
            (("A", 1, False), ("B", 2, False), ("C", 3, False)),
            (("C", "A"), ("A", "C"), ("A", "B"), ("B", "A")),
        )
        nodes = (
            CFCNodeIR("A", (
                CFCInputBinding("C", "Z", "IA", "BOOL", True),
                CFCInputBinding("B", "Y", "IB", "BOOL", False),
            )),
            CFCNodeIR("B", (CFCInputBinding(
                "A", "X", "IB", "BOOL", False),)),
            CFCNodeIR("C", (CFCInputBinding(
                "A", "X", "IC", "BOOL", False),)),
        )
        with self.assertRaises(CFCOrderError) as raised:
            lower_cfc_feedback_task(graph, nodes, _task(graph), "Main")
        self.assertIn("CYCLE", {item.code for item in raised.exception.errors})

    def test_body_load_prev_is_rejected_even_without_inputs(self):
        graph, _, original = _self_case()
        nodes = (CFCNodeIR("A", (), (LoadPrev("X", "BOOL"),)),)
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(graph, nodes, original, "Main")
        self.assertIn("FEEDBACK_UNSUPPORTED", _codes(raised.exception))

    def test_inherited_fragment_and_current_edge_gates_are_not_bypassed(self):
        graph = _graph(
            (("A", 1, False), ("B", 2, False)),
            (("A", "A"), ("A", "B")),
        )
        feedback_a = CFCNodeIR("A", (CFCInputBinding(
            "A", "X", "IA", "BOOL", True),))
        cases = (
            ("missing", (feedback_a,), "MISSING_FRAGMENT"),
            ("duplicate", (feedback_a, feedback_a, CFCNodeIR("B")),
             "DUPLICATE_FRAGMENT"),
            ("unknown", (feedback_a, CFCNodeIR("B"), CFCNodeIR("X")),
             "UNKNOWN_FRAGMENT"),
            ("edge-without-binding", (feedback_a, CFCNodeIR("B")),
             "EDGE_BINDING_MISMATCH"),
        )
        for name, nodes, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_feedback_task(graph, nodes, _task(graph), "Main")
                self.assertIn(code, _codes(raised.exception))

    def test_target_task_and_graph_structure_fail_before_clone(self):
        graph, nodes, original = _self_case()
        wrong_source = _task(graph)
        wrong_source.pou_lib["Main"].source = object()
        already_lowered = _task(graph)
        already_lowered.pou_lib["Main"].code = []

        class TaskChild(Task):
            pass

        for name, task in (
            ("wrong-source", wrong_source),
            ("already-lowered", already_lowered),
            ("task-subclass", TaskChild(
                programs=original.programs,
                gvl=original.gvl,
                pou_lib=original.pou_lib,
            )),
        ):
            with self.subTest(name=name):
                with self.assertRaises(CFCLoweringError):
                    lower_cfc_feedback_task(graph, nodes, task, "Main")

        bad_nodes = CFCOrderGraph(
            (object(),), (), "plcopen_xml", "explicit", "exported"
        )
        bad_edges = CFCOrderGraph(
            (CFCOrderNode("A", 1, False),), (object(),),
            "plcopen_xml", "explicit", "exported",
        )
        for bad_graph in (bad_nodes, bad_edges):
            task = _task(bad_graph)
            with self.assertRaises(CFCLoweringError):
                lower_cfc_feedback_task(bad_graph, nodes, task, "Main")

    def test_malformed_task_container_fails_closed(self):
        # Codex 未预告反证：exact Task 的内部容器被换成错误类型（如 pou_lib=list）
        # 时必须形成稳定 CFCLoweringError，绝不泄漏 AttributeError/TypeError/KeyError。
        graph, nodes, _ = _self_case()
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
                    lower_cfc_feedback_task(graph, nodes, task, "Main")
                self.assertIn("INVALID_TASK_CONTAINER", _codes(raised.exception))

    def test_malformed_target_pou_container_fails_closed(self):
        # Codex Round 2 未预告反证：目标 POU 的 interface/locals/instances 会被
        # _clone_pou 以 list(...) 迭代克隆；clone 前必须按 exact list 校验——tuple
        # 会被静默归一化、不可迭代对象会泄漏 TypeError，两者都必须失败关闭为稳定
        # CFCLoweringError 且不改写原 POU。
        graph, nodes, _ = _self_case()
        for field in ("interface", "locals", "instances"):
            for kind, value in (("tuple", ()), ("non-iterable", object())):
                with self.subTest(field=field, kind=kind):
                    task = _task(graph)
                    setattr(task.pou_lib["Main"], field, value)
                    with self.assertRaises(CFCLoweringError) as raised:
                        lower_cfc_feedback_task(graph, nodes, task, "Main")
                    self.assertIn("INVALID_TARGET_POU", _codes(raised.exception))
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

        graph, nodes, _ = _self_case()
        task = _task(graph)
        task.pou_lib = {ExplodingKey(): task.pou_lib["Main"]}
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(graph, nodes, task, "Main")
        self.assertIn("INVALID_POU_LIB_KEY", _codes(raised.exception))

    def test_malicious_target_pou_identity_is_not_observed(self):
        # 目标 POU 的标识字段（name/language）在 clone/比较前必须先按 exact 类型校验，
        # 不得观察攻击者的 __eq__/__ne__/__repr__。
        graph, nodes, _ = _self_case()
        for name, field in (("name", "name"), ("language", "language")):
            with self.subTest(name=name):
                task = _task(graph)
                setattr(task.pou_lib["Main"], field, _ExplodingIdentity())
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_feedback_task(graph, nodes, task, "Main")
                self.assertIn("INVALID_TARGET_POU", _codes(raised.exception))
                self.assertIsNone(task.pou_lib["Main"].code)

    def test_ir_validation_failures_preserve_original_task_and_sibling(self):
        # 第一部分（Round 4）：非 exact POUDefinition 的兄弟 pou_lib 值现在在
        # clone/validate_task 之前失败关闭为稳定 CFCLoweringError（完整验证器不把
        # 未验证的兄弟值交给 `%r` 会观察它的冻结 Loader），且不改写原 Task/兄弟条目。
        graph, nodes, original = _self_case()
        broken = object()
        original.pou_lib["Broken"] = broken
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(graph, nodes, original, "Main")
        self.assertIn("INVALID_POU_LIB_VALUE", _codes(raised.exception))
        self.assertIsNone(original.pou_lib["Main"].code)
        self.assertIs(original.pou_lib["Broken"], broken)

        # 第二部分：类型合法但语义非法的 IR（未知变量）仍由冻结 Loader 透传
        # IRValidationError——完整验证器只挡不可信类型，不吞掉合法的 Loader 语义诊断。
        invalid_body = (CFCNodeIR("A", (CFCInputBinding(
            "A", "X", "IA", "BOOL", True),),
            (LoadVar("Missing", "BOOL"),)),)
        clean = _task(graph)
        with self.assertRaises(IRValidationError):
            lower_cfc_feedback_task(graph, invalid_body, clean, "Main")
        self.assertIsNone(clean.pou_lib["Main"].code)

    def test_equivalent_input_permutations_produce_identical_code(self):
        first = _graph(
            (("A", 1, False), ("B", 2, False)),
            (("A", "B"), ("B", "A")),
        )
        second = _graph(
            (("B", 2, False), ("A", 1, False)),
            (("B", "A"), ("A", "B")),
        )

        def fragments():
            return (
                CFCNodeIR("A", (CFCInputBinding(
                    "B", "Y", "IA", "BOOL", True),)),
                CFCNodeIR("B", (CFCInputBinding(
                    "A", "X", "IB", "BOOL", False),)),
            )

        left = lower_cfc_feedback_task(first, fragments(), _task(first), "Main")
        right = lower_cfc_feedback_task(
            second, tuple(reversed(fragments())), _task(second), "Main"
        )
        self.assertEqual(left.execution_order, right.execution_order)
        self.assertEqual(left.code, right.code)

    def test_multiple_feedback_inputs_on_one_pair_have_canonical_pin_order(self):
        graph = _graph((("A", 1, False),), (("A", "A"),))

        def node(bindings):
            return (CFCNodeIR("A", tuple(bindings)),)

        first_bindings = (
            CFCInputBinding("A", "Y", "IB", "BOOL", True),
            CFCInputBinding("A", "X", "IA", "BOOL", True),
        )
        left = lower_cfc_feedback_task(
            graph, node(first_bindings), _task(graph), "Main"
        )
        right = lower_cfc_feedback_task(
            graph, node(reversed(first_bindings)), _task(graph), "Main"
        )
        self.assertEqual(left.code, right.code)
        self.assertEqual(left.code, (
            LoadPrev("X", "BOOL"), StoreVar("IA", "BOOL"),
            LoadPrev("Y", "BOOL"), StoreVar("IB", "BOOL"),
        ))

    def test_duplicate_target_pin_binding_is_rejected(self):
        graph = _graph((("A", 1, False),), (("A", "A"),))
        nodes = (CFCNodeIR("A", (
            CFCInputBinding("A", "X", "IA", "BOOL", True),
            CFCInputBinding("A", "Y", "IA", "BOOL", True),
        )),)
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(graph, nodes, _task(graph), "Main")
        self.assertIn("DUPLICATE_TARGET_BINDING", _codes(raised.exception))

    def test_reversed_non_feedback_current_edge_fails_closed(self):
        # Feedback (C->A) is well-formed (source order 3 does not precede target
        # order 1) and the projection is acyclic, but the non-feedback current
        # edge B->A is reversed (B runs after A).  The current-dependency order
        # gate must still fail closed rather than trust a stale Store value.
        graph = _graph(
            (("A", 1, False), ("B", 2, False), ("C", 3, False)),
            (("C", "A"), ("B", "A"), ("A", "C")),
        )
        pou = POUDefinition(
            "Main", "PROGRAM", "CFC",
            locals=[VarDecl("IA", "BOOL"), VarDecl("IB", "BOOL"),
                    VarDecl("IC", "BOOL")],
            source=graph, code=None,
        )
        task = Task(
            programs=[ProgramInstance("Main", "PLC_PRG")],
            gvl=[VarDecl("X", "BOOL", section="VAR_GLOBAL"),
                 VarDecl("Y", "BOOL", section="VAR_GLOBAL"),
                 VarDecl("Z", "BOOL", section="VAR_GLOBAL")],
            pou_lib={"Main": pou},
        )
        nodes = (
            CFCNodeIR("A", (
                CFCInputBinding("C", "Z", "IA", "BOOL", True),
                CFCInputBinding("B", "Y", "IB", "BOOL", False),
            )),
            CFCNodeIR("B", ()),
            CFCNodeIR("C", (CFCInputBinding("A", "X", "IC", "BOOL", False),)),
        )
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(graph, nodes, task, "Main")
        self.assertIn("CURRENT_DEPENDENCY_ORDER", _codes(raised.exception))
        self.assertIsNone(task.pou_lib["Main"].code)

    def test_malformed_target_pou_declaration_fails_closed(self):
        # Codex Round 3 ①（feedback 入口）：目标 POU 的 exact-list
        # interface/locals/instances 里放入非 VarDecl/InstanceDecl 值对象时，必须逐
        # 元素按 exact 类型失败关闭为稳定 CFCLoweringError，绝不泄漏 AttributeError。
        graph, nodes, _ = _self_case()
        for field in ("interface", "locals", "instances"):
            with self.subTest(field=field):
                task = _task(graph)
                setattr(task.pou_lib["Main"], field, [_ObservationTrap()])
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_feedback_task(graph, nodes, task, "Main")
                self.assertIn("INVALID_TARGET_POU", _codes(raised.exception))
                self.assertIsNone(task.pou_lib["Main"].code)

    def test_malicious_task_scalar_and_element_not_observed(self):
        # Codex Round 3 ②（feedback 入口）：Task 标量/容器元素/兄弟 pou_lib 值/目标
        # pou_kind 被置为恶意对象时必须先失败关闭为稳定 CFCLoweringError，绝不观察。
        graph, nodes, _ = _self_case()
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
                    lower_cfc_feedback_task(graph, nodes, task, "Main")
                self.assertIn(code, _codes(raised.exception))

    def test_malicious_body_instruction_field_not_observed(self):
        # Codex Round 3 ③（feedback 入口）：body 指令的 key/iec_type 字段被置为恶意
        # 对象时必须在发码/clone 前按 exact-type 校验并失败关闭为稳定 CFCLoweringError。
        graph, _, _ = _self_case()
        for name, instruction in (
            ("loadvar-key", LoadVar(_ObservationTrap(), "BOOL")),
            ("loadvar-type", LoadVar("X", _ObservationTrap())),
            ("storevar-key", StoreVar(_ObservationTrap(), "BOOL")),
            ("storevar-type", StoreVar("X", _ObservationTrap())),
        ):
            with self.subTest(name=name):
                task = _task(graph)
                node = CFCNodeIR(
                    "A", (CFCInputBinding("A", "X", "IA", "BOOL", True),),
                    (instruction,))
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_feedback_task(graph, (node,), task, "Main")
                self.assertIn("INVALID_INSTRUCTION", _codes(raised.exception))
                self.assertIsNone(task.pou_lib["Main"].code)

    def test_empty_name_var_decl_malicious_subfield_not_observed(self):
        # Codex Round 3 同源缺口的最后一处（feedback 入口）：exact VarDecl 的 name
        # 若为空串，旧「type() is str」预检会放行；但冻结 Loader 的 _check_var_decl 在
        # `not decl.name` 分支 `%r` 格式化整个 VarDecl，会观察未校验的 Any/bool 子字段
        # （initial/retain/persistent）并让恶意 __repr__/BaseException 逃逸。完整预检
        # 必须要求 name 为非空 exact str，使该 repr 路径不可达且子字段永不被观察。
        base_graph, base_nodes, _ = _self_case(body=(
            LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"),
        ))
        gvl_task = _task(base_graph)
        gvl_task.gvl.append(
            VarDecl("", "BOOL", initial=_ObservationTrap(), section="VAR_GLOBAL"))
        with self.assertRaises(CFCLoweringError) as raised:
            lower_cfc_feedback_task(base_graph, base_nodes, gvl_task, "Main")
        self.assertIn("INVALID_GVL", _codes(raised.exception))
        self.assertIsNone(gvl_task.pou_lib["Main"].code)
        for field in ("interface", "locals"):
            with self.subTest(field=field):
                task = _task(base_graph)
                getattr(task.pou_lib["Main"], field).append(
                    VarDecl("", "BOOL", initial=_ObservationTrap()))
                with self.assertRaises(CFCLoweringError) as raised:
                    lower_cfc_feedback_task(base_graph, base_nodes, task, "Main")
                self.assertIn("INVALID_TARGET_POU", _codes(raised.exception))
                self.assertIsNone(task.pou_lib["Main"].code)


if __name__ == "__main__":
    unittest.main()
