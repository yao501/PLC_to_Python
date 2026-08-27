"""Stage 4 无界面 CFC 编辑文档模型 v1 与安全投影的反证（WP-20260827-146）。

先写反证：auto / explicit user_defined 正向图、空 / 多节点图、布局与注释、节点与布局
排列确定性、JSON 往返、直接构造别名封闭、恶意根 / graph / layout / scalar 零观察、
完整多错误稳定聚合、失败原子性，以及到冻结 ``CFCModel`` / ``CFCOrderGraph`` 的真实投影。

``graph`` 的所有 CFC 语义（字段 / 枚举 / 连接 / carrier / 反馈 / 定序）由冻结的
``load_cfc_model`` 承载，本文件只验证编辑文档合同不复制、不绕过、不放宽该单一真值来源，
并额外冻结编辑器新建图 carrier 边界（拒绝 ``plcopen_xml`` / ``export_native``）。
Python 反证不构成 CODESYS / PLC / HAL / 现场语义证明。
"""
import copy
import unittest

from src.runtime.cfc_model import (
    CFCModel, SCHEMA_VERSION as MODEL_SCHEMA_VERSION,
)
from src.runtime.cfc_order import CFCOrderGraph, resolve_execution_order
from src.editor import (
    DOCUMENT_SCHEMA_VERSION,
    CFCLayoutEntry, CFCDocument,
    CFCDocumentDiagnostic, CFCDocumentError,
    load_cfc_document, dump_cfc_document,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
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


def _graph_auto():
    """user_defined/auto/user_defined：input/block/output 三节点，无显式序号。"""
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
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


def _graph_explicit():
    """user_defined/explicit/user_defined：带显式序号的两节点图。"""
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
        "carrier": "user_defined",
        "execution_order_mode": "explicit",
        "order_source": "user_defined",
        "nodes": [
            _node("in1", "input", "PV", [_pin("in1.o", "OUT", "OUT", "INT")], order=0),
            _node("out1", "output", "MV", [_pin("out1.i", "IN", "IN", "INT")], order=1),
        ],
        "connections": [
            _conn("in1", "in1.o", "out1", "out1.i"),
        ],
    }


def _graph_empty():
    """合法空图（无节点、无连线）。"""
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
        "carrier": "user_defined",
        "execution_order_mode": "auto",
        "order_source": "user_defined",
        "nodes": [],
        "connections": [],
    }


def _graph_plcopen():
    """plcopen_xml/explicit/exported：底层模型合法，但不是编辑器新建图。"""
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
        "carrier": "plcopen_xml",
        "execution_order_mode": "explicit",
        "order_source": "exported",
        "nodes": [
            _node("src", "input", "PV", [_pin("src.o", "OUT", "OUT", "INT")], order=0),
            _node("sink", "output", "MV", [_pin("sink.i", "IN", "IN", "INT")], order=1),
        ],
        "connections": [
            _conn("src", "src.o", "sink", "sink.i"),
        ],
    }


def _layout(node_id, x, y, comment=""):
    return {"node_id": node_id, "x": x, "y": y, "comment": comment}


def _doc_auto():
    return {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": "doc-1",
        "title": "Flow",
        "description": "",
        "graph": _graph_auto(),
        "layout": [
            _layout("in1", 0, 0, "source"),
            _layout("b1", 100, 0, "first order filter"),
            _layout("out1", 200, 0, "sink"),
        ],
    }


def _doc_explicit():
    return {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": "doc-2",
        "title": "Explicit",
        "description": "explicit order graph",
        "graph": _graph_explicit(),
        "layout": [
            _layout("in1", 0, 0),
            _layout("out1", 50, 50, "target"),
        ],
    }


def _doc_empty():
    return {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": "doc-empty",
        "title": "Empty",
        "description": "",
        "graph": _graph_empty(),
        "layout": [],
    }


class _Exploding:
    """任一观察钩子被调用即抛出，用于证明零观察失败关闭。"""

    def __repr__(self):  # pragma: no cover - 只应在缺陷时触发
        raise AssertionError("repr observed")

    def __str__(self):  # pragma: no cover
        raise AssertionError("str observed")

    def __eq__(self, other):  # pragma: no cover
        raise AssertionError("eq observed")

    def __hash__(self):  # pragma: no cover
        raise AssertionError("hash observed")

    def __bool__(self):  # pragma: no cover
        raise AssertionError("bool observed")

    def __iter__(self):  # pragma: no cover
        raise AssertionError("iter observed")


class _ExplodingBaseException:
    """观察钩子抛 BaseException（不被普通 except 捕获），仍必须零观察。"""

    def __eq__(self, other):  # pragma: no cover
        raise KeyboardInterrupt("eq observed")

    def __hash__(self):  # pragma: no cover
        raise KeyboardInterrupt("hash observed")

    def __bool__(self):  # pragma: no cover
        raise KeyboardInterrupt("bool observed")


def _codes(err):
    return [d.code for d in err.errors]


# ---------------------------------------------------------------------------
# 正向：auto / explicit / empty
# ---------------------------------------------------------------------------
class LoadPositiveTests(unittest.TestCase):
    def test_auto_graph_document(self):
        doc = load_cfc_document(_doc_auto())
        self.assertIsInstance(doc, CFCDocument)
        self.assertEqual(doc.schema_version, DOCUMENT_SCHEMA_VERSION)
        self.assertEqual(doc.document_id, "doc-1")
        self.assertEqual(doc.title, "Flow")
        self.assertEqual(doc.description, "")
        self.assertIsInstance(doc.graph, CFCModel)
        self.assertEqual([e.node_id for e in doc.layout], ["b1", "in1", "out1"])
        self.assertTrue(all(isinstance(e, CFCLayoutEntry) for e in doc.layout))

    def test_explicit_graph_document(self):
        doc = load_cfc_document(_doc_explicit())
        self.assertEqual(doc.graph.execution_order_mode, "explicit")
        self.assertEqual([e.node_id for e in doc.layout], ["in1", "out1"])
        target = [e for e in doc.layout if e.node_id == "out1"][0]
        self.assertEqual((target.x, target.y, target.comment), (50, 50, "target"))

    def test_empty_graph_document(self):
        doc = load_cfc_document(_doc_empty())
        self.assertEqual(doc.graph.nodes, ())
        self.assertEqual(doc.layout, ())

    def test_layout_entry_fields(self):
        doc = load_cfc_document(_doc_auto())
        b1 = [e for e in doc.layout if e.node_id == "b1"][0]
        self.assertEqual((b1.x, b1.y, b1.comment), (100, 0, "first order filter"))


# ---------------------------------------------------------------------------
# 投影：CFCModel / CFCOrderGraph（复用冻结单一真值来源）
# ---------------------------------------------------------------------------
class ProjectionTests(unittest.TestCase):
    def test_to_cfc_model_returns_stored_frozen_model(self):
        doc = load_cfc_document(_doc_auto())
        model = doc.to_cfc_model()
        self.assertIs(model, doc.graph)
        self.assertIsInstance(model, CFCModel)
        self.assertEqual([n.node_id for n in model.nodes], ["b1", "in1", "out1"])

    def test_projection_to_order_graph_and_resolve(self):
        doc = load_cfc_document(_doc_auto())
        order_graph = doc.to_cfc_model().to_order_graph()
        self.assertIsInstance(order_graph, CFCOrderGraph)
        self.assertEqual(resolve_execution_order(order_graph), ("in1", "b1", "out1"))

    def test_explicit_projection_preserves_declared_order(self):
        doc = load_cfc_document(_doc_explicit())
        order_graph = doc.to_cfc_model().to_order_graph()
        self.assertEqual(resolve_execution_order(order_graph), ("in1", "out1"))

    def test_projection_does_not_mutate_document(self):
        doc = load_cfc_document(_doc_auto())
        before = dump_cfc_document(doc)
        doc.to_cfc_model().to_order_graph()
        self.assertEqual(dump_cfc_document(doc), before)


# ---------------------------------------------------------------------------
# 确定性：排列无关 + JSON 往返
# ---------------------------------------------------------------------------
class DeterminismTests(unittest.TestCase):
    def test_node_and_layout_permutations_equivalent(self):
        base = _doc_auto()
        permuted = _doc_auto()
        permuted["graph"]["nodes"].reverse()
        permuted["graph"]["connections"].reverse()
        permuted["layout"].reverse()
        self.assertEqual(
            dump_cfc_document(load_cfc_document(base)),
            dump_cfc_document(load_cfc_document(permuted)),
        )

    def test_json_round_trip(self):
        doc = load_cfc_document(_doc_auto())
        dumped = dump_cfc_document(doc)
        reloaded = load_cfc_document(dumped)
        self.assertEqual(dump_cfc_document(reloaded), dumped)

    def test_json_round_trip_explicit(self):
        doc = load_cfc_document(_doc_explicit())
        self.assertEqual(
            dump_cfc_document(load_cfc_document(dump_cfc_document(doc))),
            dump_cfc_document(doc),
        )

    def test_to_json_returns_fresh_containers(self):
        doc = load_cfc_document(_doc_auto())
        first = doc.to_json()
        second = doc.to_json()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNot(first["layout"], second["layout"])
        self.assertIsNot(first["graph"], second["graph"])
        first["layout"].append({"node_id": "x", "x": 0, "y": 0, "comment": ""})
        first["title"] = "mutated"
        self.assertEqual(doc.title, "Flow")
        self.assertEqual(len(doc.to_json()["layout"]), 3)

    def test_two_instances_do_not_share_state(self):
        doc1 = load_cfc_document(_doc_auto())
        doc2 = load_cfc_document(_doc_auto())
        self.assertIsNot(doc1.layout, doc2.layout)
        self.assertIsNot(doc1.graph, doc2.graph)
        self.assertEqual(dump_cfc_document(doc1), dump_cfc_document(doc2))

    def test_diagnostic_order_independent_of_layout_permutation(self):
        payload_a = _doc_auto()
        payload_a["layout"] = [
            _layout("in1", 0, 0),
            _layout("ghost", 1, 1),   # dangling
            # b1 / out1 missing
        ]
        payload_b = _doc_auto()
        payload_b["layout"] = [
            _layout("ghost", 1, 1),
            _layout("in1", 0, 0),
        ]
        with self.assertRaises(CFCDocumentError) as ctx_a:
            load_cfc_document(payload_a)
        with self.assertRaises(CFCDocumentError) as ctx_b:
            load_cfc_document(payload_b)
        self.assertEqual(
            [d.sort_key() for d in ctx_a.exception.errors],
            [d.sort_key() for d in ctx_b.exception.errors],
        )


# ---------------------------------------------------------------------------
# 直接构造别名封闭
# ---------------------------------------------------------------------------
class DirectConstructionTests(unittest.TestCase):
    def test_layout_list_frozen_to_tuple(self):
        model = load_cfc_document(_doc_auto()).graph
        mutable = [CFCLayoutEntry("in1", 0, 0, "")]
        doc = CFCDocument(DOCUMENT_SCHEMA_VERSION, "d", "t", "", model, mutable)
        self.assertIsInstance(doc.layout, tuple)
        mutable.append(CFCLayoutEntry("x", 1, 1, ""))
        self.assertEqual(len(doc.layout), 1)

    def test_frozen_fields_reject_assignment(self):
        doc = load_cfc_document(_doc_auto())
        with self.assertRaises(Exception):
            doc.title = "other"
        with self.assertRaises(Exception):
            doc.layout = ()

    def test_non_container_layout_rejected(self):
        model = load_cfc_document(_doc_auto()).graph
        with self.assertRaises(TypeError):
            CFCDocument(DOCUMENT_SCHEMA_VERSION, "d", "t", "", model, "nope")


# ---------------------------------------------------------------------------
# 编辑器 carrier 边界：拒绝 plcopen_xml / export_native
# ---------------------------------------------------------------------------
class EditorCarrierTests(unittest.TestCase):
    def test_plcopen_graph_rejected(self):
        payload = _doc_auto()
        payload["graph"] = _graph_plcopen()
        payload["layout"] = [_layout("src", 0, 0), _layout("sink", 1, 0)]
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("GRAPH_NOT_EDITOR_CARRIER", _codes(ctx.exception))

    def test_export_native_graph_rejected(self):
        payload = _doc_auto()
        payload["graph"] = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "carrier": "export_native",
            "execution_order_mode": "auto",
            "order_source": "reconstructed",
            "nodes": [_node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])],
            "connections": [],
        }
        payload["layout"] = [_layout("n", 0, 0)]
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("GRAPH_NOT_EDITOR_CARRIER", _codes(ctx.exception))

    def test_invalid_graph_combo_reported_via_graph_prefix(self):
        payload = _doc_auto()
        payload["graph"]["order_source"] = "exported"  # user_defined/auto/exported 非法
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("GRAPH_INVALID_CARRIER_COMBO", _codes(ctx.exception))


# ---------------------------------------------------------------------------
# 结构 / scalar 校验
# ---------------------------------------------------------------------------
class SchemaValidationTests(unittest.TestCase):
    def test_root_must_be_exact_dict(self):
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(["not", "a", "dict"])
        self.assertEqual(_codes(ctx.exception), ["SCHEMA_DOCUMENT_FIELDS"])

    def test_missing_root_field(self):
        payload = _doc_auto()
        del payload["title"]
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertEqual(_codes(ctx.exception), ["SCHEMA_DOCUMENT_FIELDS"])

    def test_unknown_root_field(self):
        payload = _doc_auto()
        payload["extra"] = 1
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertEqual(_codes(ctx.exception), ["SCHEMA_DOCUMENT_FIELDS"])

    def test_wrong_schema_version(self):
        payload = _doc_auto()
        payload["schema_version"] = "cfc-document-v2"
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_DOCUMENT_SCHEMA_VERSION", _codes(ctx.exception))

    def test_empty_document_id_rejected(self):
        payload = _doc_auto()
        payload["document_id"] = ""
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_DOCUMENT_ID", _codes(ctx.exception))

    def test_empty_title_rejected(self):
        payload = _doc_auto()
        payload["title"] = ""
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_DOCUMENT_TITLE", _codes(ctx.exception))

    def test_non_str_description_rejected(self):
        payload = _doc_auto()
        payload["description"] = 5
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_DOCUMENT_DESCRIPTION", _codes(ctx.exception))

    def test_layout_must_be_list(self):
        payload = _doc_auto()
        payload["layout"] = {"in1": [0, 0]}
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT", _codes(ctx.exception))

    def test_layout_entry_field_mismatch(self):
        payload = _doc_empty()
        payload["layout"] = [{"node_id": "in1", "x": 0, "y": 0}]  # missing comment
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("SCHEMA_LAYOUT_FIELDS", _codes(ctx.exception))

    def test_layout_coord_bool_not_int(self):
        payload = _doc_auto()
        payload["layout"][0]["x"] = True
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT_COORD", _codes(ctx.exception))

    def test_layout_coord_float_rejected(self):
        payload = _doc_auto()
        payload["layout"][1]["y"] = 1.5
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT_COORD", _codes(ctx.exception))

    def test_layout_comment_must_be_str(self):
        payload = _doc_auto()
        payload["layout"][0]["comment"] = 0
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT_COMMENT", _codes(ctx.exception))


# ---------------------------------------------------------------------------
# 布局对应关系：缺失 / 悬空 / 重复
# ---------------------------------------------------------------------------
class LayoutCorrespondenceTests(unittest.TestCase):
    def test_missing_layout(self):
        payload = _doc_auto()
        payload["layout"] = [_layout("in1", 0, 0), _layout("b1", 1, 0)]  # out1 缺
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        missing = [d for d in ctx.exception.errors if d.code == "MISSING_LAYOUT"]
        self.assertEqual([d.node_id for d in missing], ["out1"])

    def test_dangling_layout(self):
        payload = _doc_auto()
        payload["layout"].append(_layout("ghost", 9, 9))
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        dangling = [d for d in ctx.exception.errors if d.code == "DANGLING_LAYOUT"]
        self.assertEqual([d.node_id for d in dangling], ["ghost"])

    def test_duplicate_layout(self):
        payload = _doc_auto()
        payload["layout"].append(_layout("in1", 5, 5))
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        dup = [d for d in ctx.exception.errors if d.code == "DUPLICATE_LAYOUT"]
        self.assertEqual([d.node_id for d in dup], ["in1"])

    def test_complete_multi_error_sequence(self):
        payload = _doc_auto()
        payload["schema_version"] = "bad"
        payload["document_id"] = ""
        payload["layout"] = [
            _layout("in1", 0, 0),
            _layout("in1", 1, 1),   # duplicate
            _layout("ghost", 2, 2),  # dangling
            # b1 / out1 missing
        ]
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        codes = _codes(ctx.exception)
        self.assertIn("INVALID_DOCUMENT_SCHEMA_VERSION", codes)
        self.assertIn("INVALID_DOCUMENT_ID", codes)
        self.assertIn("DUPLICATE_LAYOUT", codes)
        self.assertIn("DANGLING_LAYOUT", codes)
        self.assertIn("MISSING_LAYOUT", codes)
        # 稳定聚合：与已排序序列逐字相等
        self.assertEqual(
            codes,
            [d.code for d in sorted(
                ctx.exception.errors, key=CFCDocumentDiagnostic.sort_key)],
        )


# ---------------------------------------------------------------------------
# 恶意对象零观察 + 失败原子性
# ---------------------------------------------------------------------------
class MaliciousObjectTests(unittest.TestCase):
    def test_malicious_root_dict_subclass(self):
        class EvilDict(dict):
            def keys(self):  # pragma: no cover
                raise AssertionError("keys observed")

            def __iter__(self):  # pragma: no cover
                raise AssertionError("iter observed")

        evil = EvilDict(_doc_auto())
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(evil)
        self.assertEqual(_codes(ctx.exception), ["SCHEMA_DOCUMENT_FIELDS"])

    def test_malicious_root_field_value(self):
        payload = _doc_auto()
        payload["title"] = _Exploding()
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_DOCUMENT_TITLE", _codes(ctx.exception))

    def test_malicious_graph_value(self):
        payload = _doc_auto()
        payload["graph"] = _Exploding()
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertTrue(all(isinstance(d.message, str) for d in ctx.exception.errors))
        self.assertTrue(any(d.code.startswith("GRAPH_") for d in ctx.exception.errors))

    def test_malicious_layout_container(self):
        payload = _doc_empty()
        payload["layout"] = _Exploding()
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT", _codes(ctx.exception))

    def test_malicious_layout_entry(self):
        payload = _doc_empty()
        payload["layout"] = [_Exploding()]
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("SCHEMA_LAYOUT_FIELDS", _codes(ctx.exception))

    def test_malicious_scalar_int_subclass(self):
        class EvilInt(int):
            def __eq__(self, other):  # pragma: no cover
                raise AssertionError("eq observed")

            def __hash__(self):  # pragma: no cover
                raise AssertionError("hash observed")

        payload = _doc_auto()
        payload["layout"][0]["x"] = EvilInt(3)
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT_COORD", _codes(ctx.exception))

    def test_base_exception_hooks_not_observed(self):
        payload = _doc_auto()
        payload["layout"][0]["comment"] = _ExplodingBaseException()
        with self.assertRaises(CFCDocumentError) as ctx:
            load_cfc_document(payload)
        self.assertIn("INVALID_LAYOUT_COMMENT", _codes(ctx.exception))

    def test_failure_atomicity_payload_unchanged(self):
        payload = _doc_auto()
        payload["schema_version"] = "bad"
        payload["layout"].append(_layout("ghost", 1, 1))
        snapshot = copy.deepcopy(payload)
        with self.assertRaises(CFCDocumentError):
            load_cfc_document(payload)
        self.assertEqual(payload, snapshot)

    def test_success_does_not_mutate_payload(self):
        payload = _doc_auto()
        snapshot = copy.deepcopy(payload)
        load_cfc_document(payload)
        self.assertEqual(payload, snapshot)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
