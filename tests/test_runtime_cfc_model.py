"""内部 CFC 模型 v1 与安全 Loader 的反证（WP-20260808-081；WP-20260808-082 Round 2 返修）。

先写反证：正向四类 node kind、三类可执行载体 + `.export` 保存但不可执行、顺序/连接
规范化、JSON 往返、原 payload 不变、双实例隔离；以及根/嵌套容器子类、bool-as-int、
空/重复 ID、未知/缺失字段、非法枚举、悬空/反向/类型不匹配/重复驱动、恶意
``repr/str/eq/hash/bool``、多错误稳定聚合与**完整诊断排列无关**。

fixture 分两类且**互不冒充**：① 真实样本缩小 fixture（``_auto_payload`` /
``_plcopen_feedback_payload`` / ``_export_native_real_payload``）只表达两份已合并真实样本的
**已证实字段事实**——尤其真实 ``.export`` 的三个 ``IsFeedbackStart`` 均为 ``False``
（见 ``prototype_05/tests/test_import_trial.py``），故真实 export_native fixture 的节点级
marker 只保留观测到的 ``False`` / 缺失，绝不伪造 ``True``；② 合成/能力 fixture
（``_export_native_synthetic_marker_payload``）明确标注为**合成能力测试**，仅证明内部 schema
能无损保存节点级 ``True`` marker，**不冒充已证实真实样本事实**。本包不重新解析 XML，也不
冒充生产导入器或真机语义证明。
"""
import copy
import unittest

from src.runtime.cfc_model import (
    CFCConnection, CFCModel, CFCModelError, CFCNode, CFCPin, SCHEMA_VERSION,
    dump_cfc_model, load_cfc_model,
)
from src.runtime.cfc_order import CFCOrderError, CFCOrderGraph, resolve_execution_order


def _pin(pin_id, formal, direction, iec_type, value_key=None):
    return {
        "pin_id": pin_id,
        "formal_name": formal,
        "direction": direction,
        "iec_type": iec_type,
        "value_key": value_key if value_key is not None else pin_id,
    }


def _node(node_id, kind, type_name, pins, *, instance_name="", order=None, marker=None):
    return {
        "node_id": node_id,
        "kind": kind,
        "type_name": type_name,
        "instance_name": instance_name,
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
    """user_defined/auto/user_defined：input/block/output 三类节点，无显式序号。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "carrier": "user_defined",
        "execution_order_mode": "auto",
        "order_source": "user_defined",
        "nodes": [
            _node("in1", "input", "PV", [_pin("in1.o", "OUT", "OUT", "REAL")]),
            _node("b1", "block", "APCHSFOP", [
                _pin("b1.i", "IN", "IN", "REAL"),
                _pin("b1.o", "AV", "OUT", "REAL"),
            ], instance_name="FOP_1"),
            _node("out1", "output", "MV", [_pin("out1.i", "IN", "IN", "REAL")]),
        ],
        "connections": [
            _conn("in1", "in1.o", "b1", "b1.i"),
            _conn("b1", "b1.o", "out1", "out1.i"),
        ],
    }


def _plcopen_feedback_payload():
    """plcopen_xml/explicit/exported：显式 executionOrderId、connector 解引用后的连线、
    以及 *无* feedback_marker（真实样本二的已证实字段事实）。反馈以 read_mode=previous
    的逐连线分类表达（ADD.In2 回接 ADD 自身输出），marker 全部保持 None。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "carrier": "plcopen_xml",
        "execution_order_mode": "explicit",
        "order_source": "exported",
        "nodes": [
            _node("src", "input", "PV", [_pin("src.o", "OUT", "OUT", "INT")], order=0),
            _node("add", "operator", "ADD", [
                _pin("add.in1", "In1", "IN", "INT"),
                _pin("add.in2", "In2", "IN", "INT"),
                _pin("add.out", "Out", "OUT", "INT"),
            ], order=1),
            _node("sink", "output", "MV", [_pin("sink.i", "IN", "IN", "INT")], order=2),
        ],
        "connections": [
            _conn("src", "src.o", "add", "add.in1"),
            _conn("add", "add.out", "add", "add.in2", read_mode="previous"),
            _conn("add", "add.out", "sink", "sink.i"),
        ],
    }


def _export_native_real_payload():
    """真实样本缩小 fixture：export_native/auto/reconstructed，自动模式拓扑、无原始元素序号。
    真实 ``.export`` 的节点级 IsFeedbackStart **已观测为 False**（三个框全 False，见
    ``prototype_05/tests/test_import_trial.py`` 第 45 行），故 block 节点 marker 保留观测到的
    ``False``、input 节点无 marker（None）；不伪造 True。整图只能作为未验证/不可执行数据保存。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "carrier": "export_native",
        "execution_order_mode": "auto",
        "order_source": "reconstructed",
        "nodes": [
            _node("src", "input", "PV", [_pin("src.o", "OUT", "OUT", "REAL")]),
            _node("acc", "block", "APCHSACCUM", [
                _pin("acc.i", "I1", "IN", "REAL"),
                _pin("acc.o", "AV", "OUT", "REAL"),
            ], instance_name="ACC_1", marker=False),
        ],
        "connections": [
            _conn("src", "src.o", "acc", "acc.i"),
        ],
    }


def _export_native_synthetic_marker_payload():
    """合成/能力 fixture（**非真实样本事实**）：与真实缩小 fixture 同形，但把 block 节点级
    marker 置 ``True``，仅用于证明内部 schema 能无损保存节点级 True marker。真实 ``.export``
    样本三个 IsFeedbackStart 均为 False，本 fixture 不得被当作已证实真实样本内容。"""
    payload = _export_native_real_payload()
    payload["nodes"][1]["feedback_marker"] = True
    return payload


class Boom:
    """恶意对象：任何观察其 dunder 都抛异常。Loader 绝不能触发它们。"""

    def __eq__(self, other):
        raise AssertionError("__eq__ observed")

    def __ne__(self, other):
        raise AssertionError("__ne__ observed")

    def __hash__(self):
        raise AssertionError("__hash__ observed")

    def __repr__(self):
        raise AssertionError("__repr__ observed")

    def __str__(self):
        raise AssertionError("__str__ observed")

    def __bool__(self):
        raise AssertionError("__bool__ observed")


class LoadPositiveTests(unittest.TestCase):
    def test_auto_loads_and_projects(self):
        model = load_cfc_model(_auto_payload())
        self.assertIsInstance(model, CFCModel)
        self.assertEqual(model.carrier, "user_defined")
        self.assertEqual(tuple(n.node_id for n in model.nodes), ("b1", "in1", "out1"))
        graph = model.to_order_graph()
        self.assertIsInstance(graph, CFCOrderGraph)
        order = resolve_execution_order(graph)
        self.assertEqual(order[0], "in1")
        self.assertEqual(order[-1], "out1")

    def test_all_four_node_kinds_accepted(self):
        payload = _auto_payload()
        payload["nodes"].append(
            _node("op1", "operator", "GT", [
                _pin("op1.a", "In1", "IN", "REAL"),
                _pin("op1.q", "Out", "OUT", "BOOL"),
            ]))
        payload["connections"].append(_conn("b1", "b1.o", "op1", "op1.a"))
        model = load_cfc_model(payload)
        self.assertEqual({n.kind for n in model.nodes},
                         {"input", "block", "output", "operator"})

    def test_explicit_plcopen_feedback_loads(self):
        model = load_cfc_model(_plcopen_feedback_payload())
        self.assertEqual(model.execution_order_mode, "explicit")
        self.assertTrue(all(n.feedback_marker is None for n in model.nodes))
        self.assertEqual({n.node_id: n.execution_order_id for n in model.nodes},
                         {"src": 0, "add": 1, "sink": 2})
        previous = [c for c in model.connections if c.read_mode == "previous"]
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0].source_node_id, previous[0].target_node_id),
                         ("add", "add"))

    def test_explicit_projection_excludes_previous_and_orders(self):
        model = load_cfc_model(_plcopen_feedback_payload())
        graph = model.to_order_graph()
        # 反馈自环 (previous) 不进入定序边，投影无环。
        self.assertNotIn(("add", "add"), {(e.source, e.target) for e in graph.edges})
        order = resolve_execution_order(graph)
        self.assertEqual(order, ("src", "add", "sink"))

    def test_export_native_real_sample_saved_but_not_executable(self):
        model = load_cfc_model(_export_native_real_payload())
        # 真实样本已观测 IsFeedbackStart=False 被无损保存（不伪造 True）……
        self.assertEqual({n.node_id: n.feedback_marker for n in model.nodes},
                         {"acc": False, "src": None})
        # ……但投影到内核仍被现有门禁拒绝，不被绕过。
        graph = model.to_order_graph()
        with self.assertRaises(CFCOrderError) as ctx:
            resolve_execution_order(graph)
        self.assertEqual(
            [d.code for d in ctx.exception.errors], ["UNSUPPORTED_RECONSTRUCTION"])

    def test_export_native_synthetic_true_marker_saved_but_not_executable(self):
        # 合成能力 fixture：证明 schema 能无损保存节点级 True marker（非真实样本事实）。
        model = load_cfc_model(_export_native_synthetic_marker_payload())
        self.assertEqual({n.node_id: n.feedback_marker for n in model.nodes},
                         {"acc": True, "src": None})
        graph = model.to_order_graph()
        with self.assertRaises(CFCOrderError) as ctx:
            resolve_execution_order(graph)
        self.assertEqual(
            [d.code for d in ctx.exception.errors], ["UNSUPPORTED_RECONSTRUCTION"])


class RoundTripAndIsolationTests(unittest.TestCase):
    def test_json_round_trip_is_equivalent(self):
        for payload in (_auto_payload(), _plcopen_feedback_payload(),
                        _export_native_real_payload(),
                        _export_native_synthetic_marker_payload()):
            model = load_cfc_model(payload)
            dumped = model.to_json()
            self.assertEqual(load_cfc_model(dumped), model)
            self.assertEqual(dump_cfc_model(model), dumped)

    def test_dump_returns_fresh_container(self):
        model = load_cfc_model(_auto_payload())
        self.assertIsNot(model.to_json(), model.to_json())
        self.assertIsNot(model.to_json()["nodes"], model.to_json()["nodes"])

    def test_load_does_not_mutate_caller_payload(self):
        payload = _plcopen_feedback_payload()
        snapshot = copy.deepcopy(payload)
        load_cfc_model(payload)
        self.assertEqual(payload, snapshot)

    def test_two_instances_do_not_share_containers(self):
        payload = _auto_payload()
        a = load_cfc_model(payload)
        b = load_cfc_model(payload)
        self.assertEqual(a, b)
        self.assertIsNot(a.nodes, b.nodes)
        self.assertIsNot(a.connections, b.connections)

    def test_permutations_canonicalise_to_equal_models(self):
        base = _plcopen_feedback_payload()
        shuffled = _plcopen_feedback_payload()
        shuffled["nodes"] = list(reversed(shuffled["nodes"]))
        shuffled["connections"] = list(reversed(shuffled["connections"]))
        shuffled["nodes"][0]["pins"] = list(reversed(shuffled["nodes"][0]["pins"]))
        self.assertEqual(load_cfc_model(base), load_cfc_model(shuffled))
        self.assertEqual(load_cfc_model(base).to_json(),
                         load_cfc_model(shuffled).to_json())


class SchemaFailClosedTests(unittest.TestCase):
    def _codes(self, payload):
        with self.assertRaises(CFCModelError) as ctx:
            load_cfc_model(payload)
        return [d.code for d in ctx.exception.errors]

    def test_root_must_be_exact_dict(self):
        class SubDict(dict):
            pass
        payload = SubDict(_auto_payload())
        self.assertIn("SCHEMA_ROOT_FIELDS", self._codes(payload))

    def test_nodes_must_be_exact_list(self):
        class SubList(list):
            pass
        payload = _auto_payload()
        payload["nodes"] = SubList(payload["nodes"])
        self.assertIn("INVALID_NODES", self._codes(payload))

    def test_unknown_root_field_rejected(self):
        payload = _auto_payload()
        payload["surprise"] = 1
        self.assertIn("SCHEMA_ROOT_FIELDS", self._codes(payload))

    def test_missing_root_field_rejected(self):
        payload = _auto_payload()
        del payload["carrier"]
        self.assertIn("SCHEMA_ROOT_FIELDS", self._codes(payload))

    def test_non_str_root_key_rejected_without_observation(self):
        payload = dict(_auto_payload())
        payload[7] = "x"
        self.assertIn("SCHEMA_ROOT_FIELDS", self._codes(payload))

    def test_unknown_node_field_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["extra"] = 1
        self.assertIn("SCHEMA_NODE_FIELDS", self._codes(payload))

    def test_unknown_pin_field_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["pins"][0]["extra"] = 1
        self.assertIn("SCHEMA_PIN_FIELDS", self._codes(payload))

    def test_unknown_connection_field_rejected(self):
        payload = _auto_payload()
        payload["connections"][0]["extra"] = 1
        self.assertIn("SCHEMA_CONNECTION_FIELDS", self._codes(payload))

    def test_wrong_schema_version_rejected(self):
        payload = _auto_payload()
        payload["schema_version"] = "cfc-model-v0"
        self.assertIn("INVALID_SCHEMA_VERSION", self._codes(payload))

    def test_invalid_carrier_combo_rejected(self):
        payload = _auto_payload()
        payload["order_source"] = "exported"  # user_defined/auto/exported 非法
        self.assertIn("INVALID_CARRIER_COMBO", self._codes(payload))

    def test_invalid_node_kind_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["kind"] = "wormhole"
        self.assertIn("INVALID_NODE_KIND", self._codes(payload))

    def test_invalid_pin_direction_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["pins"][0]["direction"] = "SIDEWAYS"
        self.assertIn("INVALID_PIN_DIRECTION", self._codes(payload))

    def test_invalid_read_mode_rejected(self):
        payload = _auto_payload()
        payload["connections"][0]["read_mode"] = "future"
        self.assertIn("INVALID_CONNECTION", self._codes(payload))

    def test_empty_node_id_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["node_id"] = ""
        self.assertIn("INVALID_NODE_ID", self._codes(payload))

    def test_duplicate_node_id_rejected(self):
        payload = _auto_payload()
        payload["nodes"][1]["node_id"] = "in1"
        self.assertIn("DUPLICATE_NODE", self._codes(payload))

    def test_duplicate_pin_id_within_node_rejected(self):
        payload = _auto_payload()
        payload["nodes"][1]["pins"][1]["pin_id"] = "b1.i"
        self.assertIn("DUPLICATE_PIN", self._codes(payload))

    def test_duplicate_explicit_order_rejected(self):
        payload = _plcopen_feedback_payload()
        payload["nodes"][2]["execution_order_id"] = 1
        self.assertIn("DUPLICATE_EXECUTION_ORDER_ID", self._codes(payload))

    def test_auto_node_with_order_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["execution_order_id"] = 3
        self.assertIn("INVALID_EXECUTION_ORDER_ID", self._codes(payload))

    def test_explicit_node_without_order_rejected(self):
        payload = _plcopen_feedback_payload()
        payload["nodes"][0]["execution_order_id"] = None
        self.assertIn("INVALID_EXECUTION_ORDER_ID", self._codes(payload))

    def test_bool_as_execution_order_id_rejected(self):
        payload = _plcopen_feedback_payload()
        payload["nodes"][0]["execution_order_id"] = True
        self.assertIn("INVALID_EXECUTION_ORDER_ID", self._codes(payload))

    def test_negative_execution_order_id_rejected(self):
        payload = _plcopen_feedback_payload()
        payload["nodes"][0]["execution_order_id"] = -1
        self.assertIn("INVALID_EXECUTION_ORDER_ID", self._codes(payload))

    def test_plcopen_true_feedback_marker_forbidden(self):
        payload = _plcopen_feedback_payload()
        payload["nodes"][1]["feedback_marker"] = True
        self.assertIn("PLCOPEN_FEEDBACK_MARKER_FORBIDDEN", self._codes(payload))

    def test_non_bool_feedback_marker_rejected(self):
        payload = _export_native_real_payload()
        payload["nodes"][1]["feedback_marker"] = 1
        self.assertIn("INVALID_FEEDBACK_MARKER", self._codes(payload))


class ConnectionSemanticTests(unittest.TestCase):
    def _codes(self, payload):
        with self.assertRaises(CFCModelError) as ctx:
            load_cfc_model(payload)
        return [d.code for d in ctx.exception.errors]

    def test_dangling_connection_rejected(self):
        payload = _auto_payload()
        payload["connections"][0]["source_node_id"] = "ghost"
        self.assertIn("DANGLING_CONNECTION", self._codes(payload))

    def test_reverse_direction_rejected(self):
        payload = _auto_payload()
        # 把 source 指向一个 IN 管脚（反向）。
        payload["connections"][0] = _conn("b1", "b1.i", "out1", "out1.i")
        self.assertIn("INVALID_CONNECTION_DIRECTION", self._codes(payload))

    def test_target_must_be_input(self):
        payload = _auto_payload()
        # 把 target 指向一个 OUT 管脚。
        payload["connections"][0] = _conn("in1", "in1.o", "b1", "b1.o")
        self.assertIn("INVALID_CONNECTION_DIRECTION", self._codes(payload))

    def test_type_mismatch_rejected(self):
        payload = _auto_payload()
        payload["nodes"][0]["pins"][0]["iec_type"] = "INT"  # in1.o INT vs b1.i REAL
        self.assertIn("CONNECTION_TYPE_MISMATCH", self._codes(payload))

    def test_multiple_drivers_rejected(self):
        payload = _auto_payload()
        payload["connections"].append(_conn("in1", "in1.o", "b1", "b1.i"))
        self.assertIn("MULTIPLE_DRIVERS", self._codes(payload))


class MaliciousAndAggregationTests(unittest.TestCase):
    def _assert_fail_closed(self, payload):
        with self.assertRaises(CFCModelError) as ctx:
            load_cfc_model(payload)
        self.assertTrue(ctx.exception.errors)

    def test_malicious_schema_version(self):
        payload = _auto_payload()
        payload["schema_version"] = Boom()
        self._assert_fail_closed(payload)

    def test_malicious_node_kind(self):
        payload = _auto_payload()
        payload["nodes"][0]["kind"] = Boom()
        self._assert_fail_closed(payload)

    def test_malicious_execution_order_id(self):
        payload = _plcopen_feedback_payload()
        payload["nodes"][0]["execution_order_id"] = Boom()
        self._assert_fail_closed(payload)

    def test_malicious_feedback_marker(self):
        payload = _export_native_real_payload()
        payload["nodes"][1]["feedback_marker"] = Boom()
        self._assert_fail_closed(payload)

    def test_malicious_pin_value(self):
        payload = _auto_payload()
        payload["nodes"][0]["pins"][0]["value_key"] = Boom()
        self._assert_fail_closed(payload)

    def test_malicious_read_mode(self):
        payload = _auto_payload()
        payload["connections"][0]["read_mode"] = Boom()
        self._assert_fail_closed(payload)

    def _diagnostics(self, payload):
        with self.assertRaises(CFCModelError) as ctx:
            load_cfc_model(payload)
        errors = ctx.exception.errors
        tuples = [(d.code, d.node_id, d.pin_id, d.message) for d in errors]
        keys = [d.sort_key() for d in errors]
        return tuples, keys, str(ctx.exception)

    def test_multiple_errors_aggregate_and_sort_stably(self):
        payload = _auto_payload()
        payload["nodes"][0]["kind"] = "wormhole"
        payload["nodes"][1]["node_id"] = ""
        payload["connections"][0]["source_node_id"] = "ghost"

        first, first_keys, first_msg = self._diagnostics(copy.deepcopy(payload))
        self.assertGreaterEqual(len(first), 3)
        # 非恒真：诊断按 sort_key 稳定排序，其排序键序列必须实际等于自身排序结果。
        self.assertEqual(first_keys, sorted(first_keys))
        # 输入排列不改变完整诊断元组（code/node_id/pin_id/message）或异常字符串。
        permuted = copy.deepcopy(payload)
        permuted["nodes"] = list(reversed(permuted["nodes"]))
        permuted["connections"] = list(reversed(permuted["connections"]))
        permuted_diags, _permuted_keys, permuted_msg = self._diagnostics(permuted)
        self.assertEqual(permuted_diags, first)
        self.assertEqual(permuted_msg, first_msg)


class DiagnosticPermutationInvarianceTests(unittest.TestCase):
    """非法图的完整诊断（含 node_id 归因）与异常字符串必须与输入排列无关。"""

    def _diagnostics(self, payload):
        with self.assertRaises(CFCModelError) as ctx:
            load_cfc_model(payload)
        errors = ctx.exception.errors
        tuples = [(d.code, d.node_id, d.pin_id, d.message) for d in errors]
        return tuples, str(ctx.exception)

    def test_duplicate_order_attributes_all_nodes_regardless_of_permutation(self):
        base = _plcopen_feedback_payload()
        base["nodes"][2]["execution_order_id"] = 1   # add=1、sink=1 显式序号冲突
        swapped = copy.deepcopy(base)
        swapped["nodes"] = list(reversed(swapped["nodes"]))
        base_diags, base_msg = self._diagnostics(base)
        swap_diags, swap_msg = self._diagnostics(swapped)
        self.assertEqual(base_diags, swap_diags)
        self.assertEqual(base_msg, swap_msg)
        # 完整诊断归因到共享序号的两个节点，而不是仅"第二个遇到者"。
        dup = [d for d in base_diags if d[0] == "DUPLICATE_EXECUTION_ORDER_ID"]
        self.assertEqual(sorted(d[1] for d in dup), ["add", "sink"])

    def test_duplicate_node_with_dangling_is_permutation_invariant(self):
        def payload(nodes):
            return {
                "schema_version": SCHEMA_VERSION,
                "carrier": "user_defined",
                "execution_order_mode": "auto",
                "order_source": "user_defined",
                "nodes": nodes,
                "connections": [_conn("src", "src.o", "dup", "dup.q")],
            }
        src = _node("src", "input", "PV", [_pin("src.o", "OUT", "OUT", "REAL")])
        dup_p = _node("dup", "block", "A", [_pin("dup.p", "P", "IN", "REAL")])
        dup_q = _node("dup", "block", "B", [_pin("dup.q", "Q", "IN", "REAL")])
        forward, fmsg = self._diagnostics(payload([src, dup_p, dup_q]))
        reverse, rmsg = self._diagnostics(payload([src, dup_q, dup_p]))
        # 无论保留哪个同名节点，DANGLING 与 DUPLICATE_NODE 都必须同时稳定出现。
        self.assertEqual(forward, reverse)
        self.assertEqual(fmsg, rmsg)
        self.assertEqual({d[0] for d in forward},
                         {"DANGLING_CONNECTION", "DUPLICATE_NODE"})


class ImmutableConstructionTests(unittest.TestCase):
    """公开数据对象直接构造后不得保留调用方可变别名（WP-082 Round 3 必须返修 1）。

    ``@dataclass(frozen=True)`` 只挡字段重新赋值，挡不住容器别名变异：若 ``CFCNode.pins`` /
    ``CFCModel.nodes`` / ``CFCModel.connections`` 直接保存调用方传入的 ``list``，事后修改原
    列表会串改模型。合同要求这些字段在构造时规范化成不共享别名的不可变容器。"""

    def _pin_obj(self, pin_id, direction="IN"):
        return CFCPin(pin_id, pin_id, direction, "REAL", pin_id)

    def test_cfc_node_pins_does_not_alias_caller_list(self):
        pins = [self._pin_obj("p")]
        node = CFCNode("n", "block", "T", "", None, None, pins)
        self.assertIsInstance(node.pins, tuple)
        pins.append(self._pin_obj("q"))
        # 事后修改原列表不得改变节点的 pins。
        self.assertEqual(tuple(p.pin_id for p in node.pins), ("p",))

    def test_cfc_model_nodes_and_connections_do_not_alias_caller_list(self):
        node = CFCNode("n", "input", "T", "", None, None, (self._pin_obj("n.o", "OUT"),))
        nodes = [node]
        conns = [CFCConnection("n", "n.o", "n", "n.o", "current")]
        model = CFCModel(SCHEMA_VERSION, "user_defined", "auto", "user_defined",
                         nodes, conns)
        self.assertIsInstance(model.nodes, tuple)
        self.assertIsInstance(model.connections, tuple)
        before = model.to_json()
        # 事后修改原列表不得改变模型的节点 / 连线数量或 JSON 投影。
        nodes.append(node)
        conns.append(conns[0])
        self.assertEqual(model.to_json(), before)
        self.assertEqual(len(model.to_json()["nodes"]), 1)
        self.assertEqual(len(model.to_json()["connections"]), 1)


if __name__ == "__main__":
    unittest.main()
