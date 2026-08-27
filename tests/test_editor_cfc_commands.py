"""Stage 4 无界面 CFC 原子编辑命令与撤销基础的反证（WP-20260827-147）。

先写反证：六类命令（add_node / remove_node / move_node / set_node_comment /
add_connection / remove_connection）正向、多步链式编辑、删除节点级联、auto / explicit
图、previous 连接、before/after/undo/redo 身份与隔离、输入 / 输出容器篡改、缺失 / 重复 /
非法目标、完整底层诊断收敛、恶意对象零观察、失败原子性，以及到冻结 ``CFCDocument`` /
``CFCModel`` / ``CFCOrderGraph`` 的邻接回归。

图 / 布局 / 连接的所有 CFC 语义（字段 / 枚举 / pin 方向 / 类型 / carrier / 反馈 / 定序 /
单驱动 / layout 对应关系）由冻结的 ``load_cfc_document`` → ``load_cfc_model`` 承载；本文件
只验证命令层不复制、不绕过、不放宽该单一真值来源，并把失败严格分层为命令边界稳定
``CFCEditError`` 与整文档 ``CFCDocumentError``。Python 反证不构成 CODESYS / PLC / HAL /
现场语义证明。
"""
import copy
import unittest

from src.runtime.cfc_model import (
    CFCConnection, CFCModel, CFCNode, CFCPin,
    SCHEMA_VERSION as MODEL_SCHEMA_VERSION,
)
from src.runtime.cfc_order import CFCOrderGraph, resolve_execution_order
from src.editor import (
    DOCUMENT_SCHEMA_VERSION,
    CFCLayoutEntry, CFCDocument, CFCDocumentError, load_cfc_document, dump_cfc_document,
    CFCEditDiagnostic, CFCEditError, CFCEditResult,
    add_node, remove_node, move_node, set_node_comment,
    add_connection, remove_connection,
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


def _layout(node_id, x, y, comment=""):
    return {"node_id": node_id, "x": x, "y": y, "comment": comment}


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


def _graph_fanout():
    """auto：多一个未驱动的 out2（IN REAL），便于新增 current/previous 连线。"""
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
            ]),
            _node("out1", "output", "MV", [_pin("out1.i", "IN", "IN", "REAL")]),
            _node("out2", "output", "MV2", [_pin("out2.i", "IN", "IN", "REAL")]),
        ],
        "connections": [
            _conn("in1", "in1.o", "b1", "b1.i"),
            _conn("b1", "b1.o", "out1", "out1.i"),
        ],
    }


def _doc(document_id, graph, layout, *, title="Flow", description=""):
    return {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "document_id": document_id,
        "title": title,
        "description": description,
        "graph": graph,
        "layout": layout,
    }


def _doc_auto():
    return _doc("doc-1", _graph_auto(),
                [_layout("in1", 0, 0, "source"),
                 _layout("b1", 100, 0, "filter"),
                 _layout("out1", 200, 0, "sink")])


def _doc_explicit():
    return _doc("doc-2", _graph_explicit(),
                [_layout("in1", 0, 0), _layout("out1", 50, 50, "target")])


def _doc_empty():
    graph = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "carrier": "user_defined",
        "execution_order_mode": "auto",
        "order_source": "user_defined",
        "nodes": [],
        "connections": [],
    }
    return _doc("doc-empty", graph, [])


def _doc_fanout():
    return _doc("doc-fan", _graph_fanout(),
                [_layout("in1", 0, 0), _layout("b1", 100, 0),
                 _layout("out1", 200, 0), _layout("out2", 200, 100)])


def _load_auto():
    return load_cfc_document(_doc_auto())


def _load_explicit():
    return load_cfc_document(_doc_explicit())


def _load_empty():
    return load_cfc_document(_doc_empty())


def _load_fanout():
    return load_cfc_document(_doc_fanout())


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


class _ExplodingCall:
    """被调用即抛 BaseException；用于检测是否错误调用直接构造的 method shell。"""

    def __call__(self, *args, **kwargs):  # pragma: no cover - 缺陷时才会触发
        raise KeyboardInterrupt("malicious callable observed")


class _CFCDocumentSubclass(CFCDocument):
    """公开边界必须拒绝的 CFCDocument 子类。"""

    pass


def _ecodes(err):
    return [d.code for d in err.errors]


def _node_ids(document):
    return [node.node_id for node in document.graph.nodes]


def _layout_ids(document):
    return [entry.node_id for entry in document.layout]


def _conn_tuples(document):
    return sorted(
        (c.source_node_id, c.source_pin_id, c.target_node_id, c.target_pin_id,
         c.read_mode)
        for c in document.graph.connections)


# ---------------------------------------------------------------------------
# add_node — 正向 / auto / explicit / empty
# ---------------------------------------------------------------------------
class AddNodePositiveTests(unittest.TestCase):
    def test_add_isolated_input_node_auto(self):
        doc = _load_auto()
        record = _node("in2", "input", "SP", [_pin("in2.o", "OUT", "OUT", "REAL")])
        result = add_node(doc, "in2", record, 300, 400, "second source")
        self.assertIsInstance(result, CFCEditResult)
        self.assertIsInstance(result.after, CFCDocument)
        self.assertEqual(_node_ids(result.after), ["b1", "in1", "in2", "out1"])
        entry = [e for e in result.after.layout if e.node_id == "in2"][0]
        self.assertEqual((entry.x, entry.y, entry.comment), (300, 400, "second source"))
        # 原文档不变
        self.assertEqual(_node_ids(doc), ["b1", "in1", "out1"])

    def test_add_node_into_empty_document(self):
        doc = _load_empty()
        record = _node("only", "input", "PV", [_pin("only.o", "OUT", "OUT", "INT")])
        result = add_node(doc, "only", record, 0, 0, "")
        self.assertEqual(_node_ids(result.after), ["only"])
        self.assertEqual(_layout_ids(result.after), ["only"])
        self.assertEqual(doc.graph.nodes, ())

    def test_add_explicit_node_with_unique_order(self):
        doc = _load_explicit()
        record = _node("mid", "input", "AUX",
                       [_pin("mid.o", "OUT", "OUT", "INT")], order=5)
        result = add_node(doc, "mid", record, 10, 10, "")
        self.assertEqual(_node_ids(result.after), ["in1", "mid", "out1"])
        mid = [n for n in result.after.graph.nodes if n.node_id == "mid"][0]
        self.assertEqual(mid.execution_order_id, 5)


# ---------------------------------------------------------------------------
# add_node — 命令边界失败关闭（CFCEditError）
# ---------------------------------------------------------------------------
class AddNodeCommandErrorTests(unittest.TestCase):
    def test_document_must_be_exact(self):
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCEditError) as ctx:
            add_node(_doc_auto(), "n", record, 0, 0, "")  # dict, 非 CFCDocument
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_node_id_must_be_nonempty_str(self):
        doc = _load_auto()
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCEditError) as ctx:
            add_node(doc, "", record, 0, 0, "")
        self.assertIn("INVALID_NODE_ID", _ecodes(ctx.exception))

    def test_node_must_be_exact_dict(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            add_node(doc, "n", ["not", "a", "dict"], 0, 0, "")
        self.assertIn("INVALID_NODE_RECORD", _ecodes(ctx.exception))

    def test_coord_bool_not_int(self):
        doc = _load_auto()
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCEditError) as ctx:
            add_node(doc, "n", record, True, 0, "")
        self.assertIn("INVALID_COORD", _ecodes(ctx.exception))

    def test_comment_must_be_str(self):
        doc = _load_auto()
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCEditError) as ctx:
            add_node(doc, "n", record, 0, 0, 5)
        self.assertIn("INVALID_COMMENT", _ecodes(ctx.exception))

    def test_malicious_node_id_zero_observation(self):
        doc = _load_auto()
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCEditError) as ctx:
            add_node(doc, _Exploding(), record, 0, 0, "")
        self.assertIn("INVALID_NODE_ID", _ecodes(ctx.exception))

    def test_malicious_coord_base_exception_zero_observation(self):
        doc = _load_auto()
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCEditError) as ctx:
            add_node(doc, "n", record, _ExplodingBaseException(), 0, "")
        self.assertIn("INVALID_COORD", _ecodes(ctx.exception))


# ---------------------------------------------------------------------------
# add_node — 文档边界失败关闭（CFCDocumentError，底层诊断收敛）
# ---------------------------------------------------------------------------
class AddNodeDocumentErrorTests(unittest.TestCase):
    def test_node_id_mismatch_yields_dangling_and_missing(self):
        doc = _load_auto()
        record = _node("zzz", "input", "PV", [_pin("zzz.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "www", record, 0, 0, "")  # 参数 id 与 record id 不一致
        codes = [d.code for d in ctx.exception.errors]
        self.assertIn("DANGLING_LAYOUT", codes)
        self.assertIn("MISSING_LAYOUT", codes)
        # 底层诊断完整收敛：与稳定排序序列逐字相等
        self.assertEqual(
            codes,
            [d.code for d in sorted(ctx.exception.errors,
                                    key=lambda d: d.sort_key())])

    def test_duplicate_node_id_rejected_by_document_boundary(self):
        doc = _load_auto()
        record = _node("in1", "input", "PV", [_pin("dup.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "in1", record, 5, 5, "")
        self.assertIn("GRAPH_DUPLICATE_NODE", [d.code for d in ctx.exception.errors])

    def test_invalid_kind_rejected_via_graph_prefix(self):
        doc = _load_auto()
        record = _node("n", "banana", "PV", [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "n", record, 0, 0, "")
        self.assertIn("GRAPH_INVALID_NODE_KIND", [d.code for d in ctx.exception.errors])

    def test_auto_graph_rejects_explicit_order(self):
        doc = _load_auto()
        record = _node("n", "input", "PV",
                       [_pin("n.o", "OUT", "OUT", "REAL")], order=3)
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "n", record, 0, 0, "")
        self.assertIn("GRAPH_INVALID_EXECUTION_ORDER_ID",
                      [d.code for d in ctx.exception.errors])

    def test_explicit_graph_rejects_missing_order(self):
        doc = _load_explicit()
        record = _node("n", "input", "PV", [_pin("n.o", "OUT", "OUT", "INT")])
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "n", record, 0, 0, "")
        self.assertIn("GRAPH_INVALID_EXECUTION_ORDER_ID",
                      [d.code for d in ctx.exception.errors])

    def test_explicit_graph_rejects_duplicate_order(self):
        doc = _load_explicit()
        record = _node("n", "input", "PV",
                       [_pin("n.o", "OUT", "OUT", "INT")], order=0)  # 与 in1 冲突
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "n", record, 0, 0, "")
        self.assertIn("GRAPH_DUPLICATE_EXECUTION_ORDER_ID",
                      [d.code for d in ctx.exception.errors])

    def test_malicious_node_field_zero_observation(self):
        doc = _load_auto()
        record = _node("n", "input", _Exploding(),
                       [_pin("n.o", "OUT", "OUT", "REAL")])
        with self.assertRaises(CFCDocumentError) as ctx:
            add_node(doc, "n", record, 0, 0, "")
        self.assertTrue(all(isinstance(d.message, str) for d in ctx.exception.errors))


# ---------------------------------------------------------------------------
# remove_node — 级联 / 缺失 / 非法
# ---------------------------------------------------------------------------
class RemoveNodeTests(unittest.TestCase):
    def test_remove_middle_node_cascades_connections(self):
        doc = _load_auto()
        result = remove_node(doc, "b1")
        self.assertEqual(_node_ids(result.after), ["in1", "out1"])
        self.assertEqual(result.after.graph.connections, ())
        self.assertEqual(_layout_ids(result.after), ["in1", "out1"])
        # 原文档保持三节点两连线
        self.assertEqual(_node_ids(doc), ["b1", "in1", "out1"])
        self.assertEqual(len(doc.graph.connections), 2)

    def test_remove_source_node_cascades(self):
        doc = _load_auto()
        result = remove_node(doc, "in1")
        self.assertEqual(_node_ids(result.after), ["b1", "out1"])
        # 只剩 b1->out1
        self.assertEqual(_conn_tuples(result.after),
                         [("b1", "b1.o", "out1", "out1.i", "current")])

    def test_remove_missing_node_fails_closed(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            remove_node(doc, "ghost")
        self.assertEqual(_ecodes(ctx.exception), ["MISSING_NODE"])

    def test_remove_node_invalid_id(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            remove_node(doc, "")
        self.assertIn("INVALID_NODE_ID", _ecodes(ctx.exception))

    def test_remove_node_document_must_be_exact(self):
        with self.assertRaises(CFCEditError) as ctx:
            remove_node(_doc_auto(), "in1")
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_remove_node_malicious_id_zero_observation(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            remove_node(doc, _Exploding())
        self.assertIn("INVALID_NODE_ID", _ecodes(ctx.exception))


# ---------------------------------------------------------------------------
# move_node / set_node_comment
# ---------------------------------------------------------------------------
class MoveNodeTests(unittest.TestCase):
    def test_move_only_changes_target_coords(self):
        doc = _load_auto()
        result = move_node(doc, "in1", 500, 600)
        moved = [e for e in result.after.layout if e.node_id == "in1"][0]
        self.assertEqual((moved.x, moved.y), (500, 600))
        # 其它 layout 不变
        other = [e for e in result.after.layout if e.node_id == "b1"][0]
        self.assertEqual((other.x, other.y, other.comment), (100, 0, "filter"))
        # graph 不变
        self.assertEqual(dump_cfc_document(doc)["graph"],
                         dump_cfc_document(result.after)["graph"])
        # 原文档不变
        orig = [e for e in doc.layout if e.node_id == "in1"][0]
        self.assertEqual((orig.x, orig.y), (0, 0))

    def test_move_missing_node(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            move_node(doc, "ghost", 1, 1)
        self.assertEqual(_ecodes(ctx.exception), ["MISSING_NODE"])

    def test_move_bool_coord_rejected(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            move_node(doc, "in1", True, 1)
        self.assertIn("INVALID_COORD", _ecodes(ctx.exception))

    def test_move_float_coord_rejected(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            move_node(doc, "in1", 1, 2.5)
        self.assertIn("INVALID_COORD", _ecodes(ctx.exception))

    def test_move_malicious_coord_zero_observation(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            move_node(doc, "in1", _Exploding(), 1)
        self.assertIn("INVALID_COORD", _ecodes(ctx.exception))


class SetNodeCommentTests(unittest.TestCase):
    def test_set_comment_only_changes_target(self):
        doc = _load_auto()
        result = set_node_comment(doc, "b1", "first order filter")
        entry = [e for e in result.after.layout if e.node_id == "b1"][0]
        self.assertEqual(entry.comment, "first order filter")
        # 坐标与其它项不变
        self.assertEqual((entry.x, entry.y), (100, 0))
        self.assertEqual(
            [e.comment for e in doc.layout if e.node_id == "b1"], ["filter"])

    def test_set_comment_missing_node(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            set_node_comment(doc, "ghost", "x")
        self.assertEqual(_ecodes(ctx.exception), ["MISSING_NODE"])

    def test_set_comment_non_str_rejected(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            set_node_comment(doc, "b1", 123)
        self.assertIn("INVALID_COMMENT", _ecodes(ctx.exception))

    def test_set_comment_malicious_zero_observation(self):
        doc = _load_auto()
        with self.assertRaises(CFCEditError) as ctx:
            set_node_comment(doc, "b1", _Exploding())
        self.assertIn("INVALID_COMMENT", _ecodes(ctx.exception))


# ---------------------------------------------------------------------------
# add_connection / remove_connection
# ---------------------------------------------------------------------------
class AddConnectionTests(unittest.TestCase):
    def test_add_current_connection(self):
        doc = _load_fanout()
        result = add_connection(doc, "b1", "b1.o", "out2", "out2.i", "current")
        self.assertIn(("b1", "b1.o", "out2", "out2.i", "current"),
                      _conn_tuples(result.after))
        self.assertEqual(len(doc.graph.connections), 2)

    def test_add_previous_connection_not_inferred(self):
        doc = _load_fanout()
        result = add_connection(doc, "b1", "b1.o", "out2", "out2.i", "previous")
        conn = [c for c in result.after.graph.connections
                if c.target_node_id == "out2"][0]
        self.assertEqual(conn.read_mode, "previous")

    def test_add_connection_document_must_be_exact(self):
        with self.assertRaises(CFCEditError) as ctx:
            add_connection(_doc_fanout(), "b1", "b1.o", "out2", "out2.i", "current")
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_add_connection_endpoint_type_rejected(self):
        doc = _load_fanout()
        with self.assertRaises(CFCEditError) as ctx:
            add_connection(doc, "b1", "b1.o", "out2", 3, "current")
        self.assertIn("INVALID_CONNECTION_ENDPOINT", _ecodes(ctx.exception))

    def test_add_connection_read_mode_type_rejected(self):
        doc = _load_fanout()
        with self.assertRaises(CFCEditError) as ctx:
            add_connection(doc, "b1", "b1.o", "out2", "out2.i", 1)
        self.assertIn("INVALID_READ_MODE", _ecodes(ctx.exception))

    def test_add_connection_malicious_endpoint_zero_observation(self):
        doc = _load_fanout()
        with self.assertRaises(CFCEditError) as ctx:
            add_connection(doc, "b1", "b1.o", _Exploding(), "out2.i", "current")
        self.assertIn("INVALID_CONNECTION_ENDPOINT", _ecodes(ctx.exception))

    def test_add_connection_bogus_read_mode_hits_document_boundary(self):
        doc = _load_fanout()
        with self.assertRaises(CFCDocumentError) as ctx:
            add_connection(doc, "b1", "b1.o", "out2", "out2.i", "sideways")
        self.assertIn("GRAPH_INVALID_CONNECTION",
                      [d.code for d in ctx.exception.errors])

    def test_add_connection_dangling_endpoint(self):
        doc = _load_fanout()
        with self.assertRaises(CFCDocumentError) as ctx:
            add_connection(doc, "b1", "b1.o", "ghost", "ghost.i", "current")
        self.assertIn("GRAPH_DANGLING_CONNECTION",
                      [d.code for d in ctx.exception.errors])

    def test_add_connection_multiple_drivers(self):
        doc = _load_fanout()
        # out1.i 已被 b1.o 驱动，再由 in1.o 驱动即多驱动（类型均 REAL）。
        with self.assertRaises(CFCDocumentError) as ctx:
            add_connection(doc, "in1", "in1.o", "out1", "out1.i", "current")
        self.assertIn("GRAPH_MULTIPLE_DRIVERS",
                      [d.code for d in ctx.exception.errors])

    def test_add_connection_wrong_direction(self):
        doc = _load_fanout()
        # 源指向 IN、目标指向 OUT，方向非法。
        with self.assertRaises(CFCDocumentError) as ctx:
            add_connection(doc, "out2", "out2.i", "b1", "b1.o", "current")
        self.assertIn("GRAPH_INVALID_CONNECTION_DIRECTION",
                      [d.code for d in ctx.exception.errors])


class RemoveConnectionTests(unittest.TestCase):
    def test_remove_exact_connection(self):
        doc = _load_fanout()
        result = remove_connection(doc, "b1", "b1.o", "out1", "out1.i", "current")
        self.assertEqual(
            _conn_tuples(result.after),
            [("in1", "in1.o", "b1", "b1.i", "current")])
        self.assertEqual(len(doc.graph.connections), 2)

    def test_remove_missing_connection_no_silent_noop(self):
        doc = _load_fanout()
        with self.assertRaises(CFCEditError) as ctx:
            remove_connection(doc, "b1", "b1.o", "out2", "out2.i", "current")
        self.assertEqual(_ecodes(ctx.exception), ["MISSING_CONNECTION"])

    def test_remove_connection_wrong_read_mode_is_miss(self):
        doc = _load_fanout()
        # 该连线以 current 存在；请求 previous 应零命中而非静默 no-op。
        with self.assertRaises(CFCEditError) as ctx:
            remove_connection(doc, "b1", "b1.o", "out1", "out1.i", "previous")
        self.assertEqual(_ecodes(ctx.exception), ["MISSING_CONNECTION"])

    def test_remove_connection_endpoint_type_rejected(self):
        doc = _load_fanout()
        with self.assertRaises(CFCEditError) as ctx:
            remove_connection(doc, "b1", "b1.o", "out1", None, "current")
        self.assertIn("INVALID_CONNECTION_ENDPOINT", _ecodes(ctx.exception))

    def test_remove_connection_malicious_endpoint_zero_observation(self):
        doc = _load_fanout()
        with self.assertRaises(CFCEditError) as ctx:
            remove_connection(doc, "b1", "b1.o", _Exploding(), "out1.i", "current")
        self.assertIn("INVALID_CONNECTION_ENDPOINT", _ecodes(ctx.exception))


# ---------------------------------------------------------------------------
# before/after/undo/redo 身份与隔离
# ---------------------------------------------------------------------------
class ResultSnapshotTests(unittest.TestCase):
    def test_before_is_original_after_is_new(self):
        doc = _load_auto()
        result = move_node(doc, "in1", 9, 9)
        self.assertIs(result.before, doc)
        self.assertIsInstance(result.after, CFCDocument)
        self.assertIsNot(result.after, doc)

    def test_undo_redo_return_stable_snapshots(self):
        doc = _load_auto()
        result = set_node_comment(doc, "in1", "changed")
        self.assertIs(result.undo(), result.before)
        self.assertIs(result.redo(), result.after)
        # 多次调用返回同一实例，快照不被修改
        self.assertIs(result.undo(), result.undo())
        self.assertIs(result.redo(), result.redo())

    def test_undo_redo_documents_differ(self):
        doc = _load_auto()
        result = move_node(doc, "in1", 77, 88)
        before_in1 = [e for e in result.undo().layout if e.node_id == "in1"][0]
        after_in1 = [e for e in result.redo().layout if e.node_id == "in1"][0]
        self.assertEqual((before_in1.x, before_in1.y), (0, 0))
        self.assertEqual((after_in1.x, after_in1.y), (77, 88))

    def test_result_after_to_json_is_isolated(self):
        doc = _load_auto()
        result = move_node(doc, "in1", 5, 5)
        snapshot = dump_cfc_document(result.after)
        mutated = dump_cfc_document(result.after)
        mutated["layout"].append({"node_id": "x", "x": 0, "y": 0, "comment": ""})
        mutated["title"] = "hacked"
        self.assertEqual(dump_cfc_document(result.after), snapshot)


# ---------------------------------------------------------------------------
# 直接构造壳体：命令 / CFCEditResult 的单一信任门禁
# ---------------------------------------------------------------------------
class DirectConstructionTrustBoundaryTests(unittest.TestCase):
    def _direct_document_with_graph(self, graph, layout=()):
        return CFCDocument(DOCUMENT_SCHEMA_VERSION, "direct", "Direct", "", graph, layout)

    def test_command_rejects_direct_document_with_malicious_graph_before_to_json(self):
        graph = CFCModel(MODEL_SCHEMA_VERSION, "user_defined", "auto", "user_defined", (), ())
        # 直接构造 exact CFCModel 后可被低层 object.__setattr__ 塞入恶意 method；命令绝不能
        # 走到 CFCDocument.to_json() -> graph.to_json()。
        object.__setattr__(graph, "to_json", _ExplodingCall())
        doc = self._direct_document_with_graph(graph)
        with self.assertRaises(CFCEditError) as ctx:
            move_node(doc, "ghost", 1, 2)
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_command_rejects_document_instance_to_json_monkey_patch(self):
        doc = _load_empty()
        object.__setattr__(doc, "to_json", _ExplodingCall())
        with self.assertRaises(CFCEditError) as ctx:
            move_node(doc, "ghost", 1, 2)
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_command_rejects_direct_document_with_malicious_layout_entry(self):
        safe_graph = _load_empty().graph
        bad_entry = CFCLayoutEntry(_ExplodingBaseException(), 0, 0, "")
        doc = self._direct_document_with_graph(safe_graph, (bad_entry,))
        with self.assertRaises(CFCEditError) as ctx:
            set_node_comment(doc, "ghost", "x")
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_command_rejects_direct_document_with_malicious_nested_model_shell(self):
        bad_pin = CFCPin("p", "OUT", "OUT", _ExplodingBaseException(), "p")
        node = CFCNode("n", "input", "PV", "", None, None, (bad_pin,))
        graph = CFCModel(
            MODEL_SCHEMA_VERSION, "user_defined", "auto", "user_defined", (node,), ())
        doc = self._direct_document_with_graph(
            graph, (CFCLayoutEntry("n", 0, 0, ""),))
        with self.assertRaises(CFCEditError) as ctx:
            remove_node(doc, "n")
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_DOCUMENT"])

    def test_result_rejects_non_document_before_after_without_observation(self):
        with self.assertRaises(CFCEditError) as ctx:
            CFCEditResult(_ExplodingBaseException(), _Exploding())
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_RESULT_SNAPSHOT"])

    def test_result_rejects_document_subclass_without_observation(self):
        doc = _load_empty()
        subclass = _CFCDocumentSubclass(
            doc.schema_version, doc.document_id, doc.title, doc.description,
            doc.graph, doc.layout)
        with self.assertRaises(CFCEditError) as ctx:
            CFCEditResult(subclass, doc)
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_RESULT_SNAPSHOT"])

    def test_result_rejects_exact_document_with_malicious_graph_shell(self):
        graph = CFCModel(MODEL_SCHEMA_VERSION, "user_defined", "auto", "user_defined", (), ())
        object.__setattr__(graph, "to_json", _ExplodingCall())
        doc = self._direct_document_with_graph(graph)
        with self.assertRaises(CFCEditError) as ctx:
            CFCEditResult(doc, _load_empty())
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_RESULT_SNAPSHOT"])

    def test_result_rejects_semantically_invalid_duplicate_node_snapshot(self):
        valid = _load_auto()
        first = valid.graph.nodes[0]
        invalid_graph = CFCModel(
            valid.graph.schema_version,
            valid.graph.carrier,
            valid.graph.execution_order_mode,
            valid.graph.order_source,
            (first, first),
            (),
        )
        invalid = self._direct_document_with_graph(
            invalid_graph, (valid.layout[0],))
        with self.assertRaises(CFCEditError) as ctx:
            CFCEditResult(invalid, invalid)
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_RESULT_SNAPSHOT"])

    def test_command_rejects_semantically_invalid_direct_document_before_edit(self):
        valid = _load_auto()
        first = valid.graph.nodes[0]
        invalid_graph = CFCModel(
            valid.graph.schema_version,
            valid.graph.carrier,
            valid.graph.execution_order_mode,
            valid.graph.order_source,
            (first, first),
            (),
        )
        invalid = self._direct_document_with_graph(
            invalid_graph, (valid.layout[0],))
        before = dump_cfc_document(invalid)
        with self.assertRaises(CFCDocumentError):
            remove_node(invalid, first.node_id)
        self.assertEqual(dump_cfc_document(invalid), before)

    def test_result_and_command_reject_invalid_direct_carrier(self):
        valid = _load_empty()
        invalid_graph = CFCModel(
            valid.graph.schema_version, "invalid_carrier",
            valid.graph.execution_order_mode, valid.graph.order_source,
            valid.graph.nodes, valid.graph.connections,
        )
        invalid = self._direct_document_with_graph(invalid_graph, valid.layout)
        with self.assertRaises(CFCEditError) as ctx:
            CFCEditResult(invalid, invalid)
        self.assertEqual(_ecodes(ctx.exception), ["INVALID_RESULT_SNAPSHOT"])
        with self.assertRaises(CFCDocumentError):
            move_node(invalid, "ghost", 1, 2)


# ---------------------------------------------------------------------------
# 输入 / 输出容器篡改
# ---------------------------------------------------------------------------
class ContainerTamperingTests(unittest.TestCase):
    def test_mutating_input_node_record_does_not_affect_result(self):
        doc = _load_auto()
        record = _node("in2", "input", "PV", [_pin("in2.o", "OUT", "OUT", "REAL")])
        result = add_node(doc, "in2", record, 1, 2, "src")
        snapshot = dump_cfc_document(result.after)
        # 命令返回后篡改传入 record，不得影响已构造的 after。
        record["node_id"] = "hacked"
        record["pins"].append(_pin("evil", "X", "OUT", "REAL"))
        self.assertEqual(dump_cfc_document(result.after), snapshot)

    def test_command_does_not_mutate_original_document_json(self):
        doc = _load_auto()
        before_json = dump_cfc_document(doc)
        remove_node(doc, "b1")
        self.assertEqual(dump_cfc_document(doc), before_json)


# ---------------------------------------------------------------------------
# 失败原子性
# ---------------------------------------------------------------------------
class FailureAtomicityTests(unittest.TestCase):
    def test_failed_add_leaves_document_and_payload_unchanged(self):
        doc = _load_auto()
        doc_json = dump_cfc_document(doc)
        record = _node("in1", "input", "PV", [_pin("dup.o", "OUT", "OUT", "REAL")])
        record_snapshot = copy.deepcopy(record)
        with self.assertRaises(CFCDocumentError):
            add_node(doc, "in1", record, 0, 0, "")  # duplicate id
        self.assertEqual(dump_cfc_document(doc), doc_json)
        self.assertEqual(record, record_snapshot)

    def test_failed_remove_connection_leaves_document_unchanged(self):
        doc = _load_fanout()
        doc_json = dump_cfc_document(doc)
        with self.assertRaises(CFCEditError):
            remove_connection(doc, "b1", "b1.o", "out2", "out2.i", "current")
        self.assertEqual(dump_cfc_document(doc), doc_json)


# ---------------------------------------------------------------------------
# 多步链式编辑
# ---------------------------------------------------------------------------
class ChainedEditTests(unittest.TestCase):
    def test_chained_commands_accumulate_without_touching_origin(self):
        d0 = _load_auto()
        d0_json = dump_cfc_document(d0)

        d1 = set_node_comment(d0, "in1", "renamed").after
        d2 = move_node(d1, "out1", 300, 300).after
        record = _node("in2", "input", "SP", [_pin("in2.o", "OUT", "OUT", "REAL")])
        d3 = add_node(d2, "in2", record, 400, 0, "aux").after

        self.assertEqual(_node_ids(d3), ["b1", "in1", "in2", "out1"])
        in1 = [e for e in d3.layout if e.node_id == "in1"][0]
        out1 = [e for e in d3.layout if e.node_id == "out1"][0]
        self.assertEqual(in1.comment, "renamed")
        self.assertEqual((out1.x, out1.y), (300, 300))
        # 起点文档全程不变
        self.assertEqual(dump_cfc_document(d0), d0_json)

    def test_add_then_connect_then_remove_connection(self):
        d0 = _load_fanout()
        d1 = add_connection(d0, "b1", "b1.o", "out2", "out2.i", "current").after
        self.assertEqual(len(d1.graph.connections), 3)
        d2 = remove_connection(d1, "b1", "b1.o", "out2", "out2.i", "current").after
        self.assertEqual(_conn_tuples(d2), _conn_tuples(d0))


# ---------------------------------------------------------------------------
# 邻接回归：命令产物仍走冻结投影 / 定序
# ---------------------------------------------------------------------------
class AdjacencyRegressionTests(unittest.TestCase):
    def test_command_output_projects_and_resolves(self):
        doc = _load_auto()
        # 追加一个不改变既有数据流的独立输入节点后仍可投影 / 定序。
        record = _node("in2", "input", "SP", [_pin("in2.o", "OUT", "OUT", "REAL")])
        after = add_node(doc, "in2", record, 300, 0, "").after
        order_graph = after.to_cfc_model().to_order_graph()
        self.assertIsInstance(order_graph, CFCOrderGraph)
        order = resolve_execution_order(order_graph)
        # 主链保持 in1->b1->out1，且新节点参与定序集合。
        self.assertEqual(order.index("in1") < order.index("b1"), True)
        self.assertEqual(order.index("b1") < order.index("out1"), True)
        self.assertIn("in2", order)

    def test_remove_then_round_trip_stable(self):
        doc = _load_auto()
        after = remove_node(doc, "b1").after
        reloaded = load_cfc_document(dump_cfc_document(after))
        self.assertEqual(dump_cfc_document(reloaded), dump_cfc_document(after))

    def test_edit_diagnostic_is_frozen_and_stable(self):
        d = CFCEditDiagnostic("A", "m")
        self.assertEqual(d.sort_key(), ("A", "m"))
        with self.assertRaises(Exception):
            d.code = "B"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
