"""Stage 4 无界面 CFC 原子编辑命令与撤销基础（WP-20260827-147）。

在冻结的 :class:`CFCDocument`（``src.editor.cfc_document``）之上建立一层**无界面、
不可变、失败原子**的 CFC 编辑命令：节点与连线的添加 / 删除、节点移动与注释更新。每个
命令都从可信的 ``document.to_json()`` 容器构造候选，然后只调用冻结的
``load_cfc_document`` 做整文档原子校验；成功返回携带 before / after 两个不可变文档快照的
:class:`CFCEditResult`，为后续 undo/redo 历史提供安全基础。

设计纪律（与 ``src/editor/cfc_document.py`` / ``src/runtime/cfc_model.py`` 同源）：

* **单一真值来源**：图 / 布局 / 连接的所有语义（字段、枚举、pin 方向 / 类型、carrier、
  单驱动、read_mode、execution order、layout 对应关系）都交给冻结的
  ``load_cfc_document``；本模块**不**复制、放宽或绕过这些规则，也不据序号 / 拓扑 / 名称
  猜测 ``read_mode`` / ``feedback_marker`` / 反馈边。
* **不可信边界零观察**：命令控制参数只做 ``type(x) is T`` 判定，绝不格式化、比较、哈希或
  真值测试不可信对象；恶意子类或带 repr/str/eq/hash/bool/iter/BaseException 钩子的参数在
  被观察前失败关闭，诊断只携带固定文本、绝不携带不可信值。
* **失败原子**：候选从 ``document.to_json()`` 的全新容器构造，绝不原地修改传入文档、
  payload 或既有 :class:`CFCEditResult`；校验失败只抛稳定 :class:`CFCEditError` 或完整
  保留其原因的 ``CFCDocumentError``，不返回半成品、不泄漏中间态。
* **窄范围**：本包只做单命令可逆（before/after），**不**实现历史栈、命令合并、多命令
  事务、持久化或 UI；也不修改节点 pin/type/order/marker，不做工程导入。

Python 侧的命令与快照约定不构成 PLC / CODESYS 语义等价证据。
"""
from __future__ import annotations

from dataclasses import dataclass

from src.editor.cfc_document import (
    CFCLayoutEntry, CFCDocument, CFCDocumentError, load_cfc_document,
)
from src.runtime.cfc_model import CFCConnection, CFCModel, CFCNode, CFCPin


# ---------------------------------------------------------------------------
# 诊断与聚合错误
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CFCEditDiagnostic:
    """命令层稳定诊断：只携带固定 ``code`` / ``message``，绝不含不可信值。"""

    code: str
    message: str

    def sort_key(self) -> tuple:
        """稳定排序键，令聚合诊断顺序不依赖检查次序。"""
        return (self.code, self.message)


class CFCEditError(ValueError):
    """命令参数 / 目标定位不能安全成立时聚合的全部稳定诊断。

    与 ``CFCDocumentError`` 分层：本类只报告**命令边界**（参数类型、缺失 / 重复 / 歧义
    目标、no-op 删除）；候选整文档校验失败由冻结 ``load_cfc_document`` 抛出的
    ``CFCDocumentError`` 原样上抛，完整保留其底层诊断。
    """

    def __init__(self, errors: tuple):
        self.errors = errors
        super().__init__(
            "; ".join(f"{error.code}: {error.message}" for error in errors))


# ---------------------------------------------------------------------------
# 单命令结果（before/after 不可变快照，undo/redo 基础）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CFCEditResult:
    """单次编辑命令的不可变结果。

    只保存 exact :class:`CFCDocument` ``before`` / ``after`` 两个快照，不持有任何可变
    JSON 容器；:meth:`undo` 返回 ``before``、:meth:`redo` 返回 ``after``，使单命令可逆
    且不修改任一快照。本包**不**实现历史栈、命令合并、多命令事务或持久化。
    """

    before: CFCDocument
    after: CFCDocument

    def __post_init__(self):
        """拒绝绕过命令入口直接塞入的伪造 / 子类 / 可观察快照。

        ``frozen=True`` 只禁止之后重新赋值，不能证明调用方直接构造时给出的对象真是可安全
        投影的文档。因此 result 与六个命令共用同一结构信任门禁；在任何 ``to_json`` 调用前
        拒绝非 exact 文档、额外 monkey-patch 属性，以及其 graph / layout / CFC 内层壳体。
        """
        if (not _is_trusted_document_shell(self.before)
                or not _is_trusted_document_shell(self.after)):
            raise CFCEditError((_edit_diag(
                "INVALID_RESULT_SNAPSHOT",
                "before and after must be structurally trusted exact CFCDocument"),))
        # 结构可信只证明可以安全投影，不证明图、连接、carrier、layout 等语义已被
        # 文档 loader 接受。直接构造 dataclass 可以绕过 load_cfc_document；若把这种
        # 状态保存在 before/after，undo 就可能恢复一个系统从未接受过的非法文档。
        # 因此在零观察壳体门禁之后，统一交冻结 loader 做完整语义裁决。这里只丢弃
        # loader 返回的新对象，保留原快照 identity；不复制任何 CFC 业务规则。
        try:
            load_cfc_document(self.before.to_json())
            load_cfc_document(self.after.to_json())
        except CFCDocumentError:
            raise CFCEditError((_edit_diag(
                "INVALID_RESULT_SNAPSHOT",
                "before and after must be semantically valid CFCDocument snapshots"),)) \
                from None

    def undo(self) -> CFCDocument:
        return self.before

    def redo(self) -> CFCDocument:
        return self.after


# ---------------------------------------------------------------------------
# 零观察辅助（只做 type() 判定，绝不比较 / 格式化 / 哈希 / 真值测试不可信对象）
# ---------------------------------------------------------------------------
def _is_nonempty_str(value) -> bool:
    # ``type(value) is str`` 先短路，非 str 时不触发 ``!= ""``（str.__eq__ 才安全）。
    return type(value) is str and value != ""


def _is_exact_int(value) -> bool:
    # ``type(value) is int`` 已排除 ``bool``（``type(True) is bool``）。
    return type(value) is int


def _edit_diag(code, message) -> CFCEditDiagnostic:
    return CFCEditDiagnostic(code, message)


_DOCUMENT_FIELDS = frozenset(
    {"schema_version", "document_id", "title", "description", "graph", "layout"})
_MODEL_FIELDS = frozenset(
    {"schema_version", "carrier", "execution_order_mode", "order_source",
     "nodes", "connections"})
_NODE_FIELDS = frozenset(
    {"node_id", "kind", "type_name", "instance_name", "execution_order_id",
     "feedback_marker", "pins"})
_PIN_FIELDS = frozenset(
    {"pin_id", "formal_name", "direction", "iec_type", "value_key"})
_CONNECTION_FIELDS = frozenset(
    {"source_node_id", "source_pin_id", "target_node_id", "target_pin_id",
     "read_mode"})
_LAYOUT_FIELDS = frozenset({"node_id", "x", "y", "comment"})


def _has_exact_shell(value, expected_type, field_names) -> bool:
    """验证 exact dataclass 壳体，且不读取任意调用方对象的可观察字段。

    此函数只在 ``type(value) is expected_type`` 成立后使用 ``object.__getattribute__`` 读取
    Python dataclass 自身的内建 ``__dict__``。逐键确认 exact str 后才做集合比较，从而避免
    ``__hash__`` / ``__eq__`` 钩子；额外字段（包括实例级 ``to_json`` monkey patch）一律
    拒绝。这里是结构门禁，不重复任何 CFC 图 / 连接 / 枚举语义。
    """
    if type(value) is not expected_type:
        return False
    fields = object.__getattribute__(value, "__dict__")
    if type(fields) is not dict:
        return False
    for key in fields:
        if type(key) is not str:
            return False
    return set(fields) == field_names


def _exact_text_fields(value, field_names) -> bool:
    return all(type(object.__getattribute__(value, field)) is str
               for field in field_names)


def _is_trusted_document_shell(document) -> bool:
    """唯一的文档结构信任门禁。

    先用 exact 类型、exact ``tuple`` 与安全内建遍历确认 ``CFCDocument``、``CFCModel``、
    ``CFCNode``、``CFCPin``、``CFCConnection``、``CFCLayoutEntry`` 的完整对象壳体，**之后**
    才允许命令调用 ``CFCDocument.to_json``。它刻意不检查名称、pin 方向、IEC 类型、
    carrier、read_mode 或 execution order 等语义；这些仍只由冻结
    ``load_cfc_document`` 裁决。
    """
    if not _has_exact_shell(document, CFCDocument, _DOCUMENT_FIELDS):
        return False
    if not _exact_text_fields(
            document, ("schema_version", "document_id", "title", "description")):
        return False

    graph = object.__getattribute__(document, "graph")
    layout = object.__getattribute__(document, "layout")
    if not _has_exact_shell(graph, CFCModel, _MODEL_FIELDS):
        return False
    if not _exact_text_fields(
            graph, ("schema_version", "carrier", "execution_order_mode", "order_source")):
        return False
    nodes = object.__getattribute__(graph, "nodes")
    connections = object.__getattribute__(graph, "connections")
    if type(nodes) is not tuple or type(connections) is not tuple or type(layout) is not tuple:
        return False

    for node in nodes:
        if not _has_exact_shell(node, CFCNode, _NODE_FIELDS):
            return False
        if not _exact_text_fields(node, ("node_id", "kind", "type_name", "instance_name")):
            return False
        order = object.__getattribute__(node, "execution_order_id")
        marker = object.__getattribute__(node, "feedback_marker")
        pins = object.__getattribute__(node, "pins")
        if ((order is not None and type(order) is not int)
                or (marker is not None and type(marker) is not bool)
                or type(pins) is not tuple):
            return False
        for pin in pins:
            if not _has_exact_shell(pin, CFCPin, _PIN_FIELDS):
                return False
            if not _exact_text_fields(
                    pin, ("pin_id", "formal_name", "direction", "iec_type", "value_key")):
                return False

    for connection in connections:
        if not _has_exact_shell(connection, CFCConnection, _CONNECTION_FIELDS):
            return False
        if not _exact_text_fields(
                connection, ("source_node_id", "source_pin_id", "target_node_id",
                             "target_pin_id", "read_mode")):
            return False

    for entry in layout:
        if not _has_exact_shell(entry, CFCLayoutEntry, _LAYOUT_FIELDS):
            return False
        if (type(object.__getattribute__(entry, "node_id")) is not str
                or type(object.__getattribute__(entry, "x")) is not int
                or type(object.__getattribute__(entry, "y")) is not int
                or type(object.__getattribute__(entry, "comment")) is not str):
            return False
    return True


def _require_document(document) -> None:
    """文档必须是结构可信且经 loader 接受的 exact :class:`CFCDocument`。

    这是所有命令的首个门禁：只有完整的 exact document/model/node/pin/connection/layout
    壳体都成立后，才允许调用 ``document.to_json()``；投影随后立即交冻结
    ``load_cfc_document`` 做完整语义验证，防止直接构造 dataclass 绕过 loader 后被命令
    保存为 undo ``before``。验证结果只作证明，不替换原文档 identity。
    """
    if not _is_trusted_document_shell(document):
        raise CFCEditError(
            (_edit_diag("INVALID_DOCUMENT", "document must be an exact CFCDocument"),))
    load_cfc_document(document.to_json())


def _raise_edit(errors) -> None:
    if errors:
        raise CFCEditError(tuple(sorted(errors, key=CFCEditDiagnostic.sort_key)))


# ---------------------------------------------------------------------------
# 节点命令
# ---------------------------------------------------------------------------
def add_node(document, node_id, node, x, y, comment) -> CFCEditResult:
    """在同一候选中同时添加一个 graph 节点与其 layout。

    ``node`` 是 exact 节点 record（字段 / pin / kind / type / order / marker 由冻结
    ``load_cfc_document`` 裁决）；``node_id`` 只用于新 layout 项，必须与 record 的安全
    ``node_id`` 一致——不一致时统一文档边界以 DANGLING/MISSING layout 失败关闭。重复
    ``node_id``、非法 pin/type/kind/order/marker、explicit/auto 序号不匹配同样由文档边界
    失败关闭。
    """
    _require_document(document)
    errors: list = []
    if not _is_nonempty_str(node_id):
        errors.append(_edit_diag("INVALID_NODE_ID",
                                 "node_id must be a non-empty exact str"))
    if type(node) is not dict:
        errors.append(_edit_diag("INVALID_NODE_RECORD", "node must be an exact dict"))
    if not _is_exact_int(x) or not _is_exact_int(y):
        errors.append(_edit_diag("INVALID_COORD", "x and y must be exact int"))
    if type(comment) is not str:
        errors.append(_edit_diag("INVALID_COMMENT", "comment must be an exact str"))
    _raise_edit(errors)

    candidate = document.to_json()
    candidate["graph"]["nodes"].append(node)
    candidate["layout"].append(
        {"node_id": node_id, "x": x, "y": y, "comment": comment})
    after = load_cfc_document(candidate)
    return CFCEditResult(document, after)


def remove_node(document, node_id) -> CFCEditResult:
    """以明确的级联语义一次删除目标节点、其 layout 与所有入 / 出连线。

    目标缺失时稳定失败关闭；删除后仍由冻结 ``load_cfc_document`` 做整文档校验，保证
    layout 对应关系与连接语义不被破坏。
    """
    _require_document(document)
    if not _is_nonempty_str(node_id):
        raise CFCEditError(
            (_edit_diag("INVALID_NODE_ID", "node_id must be a non-empty exact str"),))

    candidate = document.to_json()
    nodes = candidate["graph"]["nodes"]
    # 候选节点 / layout / 连接的字段全部来自冻结文档投影，均为安全 str，可安全比较。
    if node_id not in {node["node_id"] for node in nodes}:
        raise CFCEditError(
            (_edit_diag("MISSING_NODE", "node_id names no existing graph node"),))

    candidate["graph"]["nodes"] = [n for n in nodes if n["node_id"] != node_id]
    candidate["graph"]["connections"] = [
        c for c in candidate["graph"]["connections"]
        if c["source_node_id"] != node_id and c["target_node_id"] != node_id]
    candidate["layout"] = [e for e in candidate["layout"] if e["node_id"] != node_id]
    after = load_cfc_document(candidate)
    return CFCEditResult(document, after)


def move_node(document, node_id, x, y) -> CFCEditResult:
    """只改变目标 layout 项的 ``x`` / ``y``；不重写 graph、其它 layout 或元数据。

    目标不存在、``x`` / ``y`` 非 exact int（``bool`` 不冒充 int）均失败关闭。
    """
    _require_document(document)
    errors: list = []
    if not _is_nonempty_str(node_id):
        errors.append(_edit_diag("INVALID_NODE_ID",
                                 "node_id must be a non-empty exact str"))
    if not _is_exact_int(x) or not _is_exact_int(y):
        errors.append(_edit_diag("INVALID_COORD", "x and y must be exact int"))
    _raise_edit(errors)

    candidate = document.to_json()
    layout = candidate["layout"]
    # 冻结文档保证每个节点恰有一项 layout；layout 命中即目标存在。
    if node_id not in {entry["node_id"] for entry in layout}:
        raise CFCEditError(
            (_edit_diag("MISSING_NODE", "node_id names no existing layout node"),))
    for entry in layout:
        if entry["node_id"] == node_id:
            entry["x"] = x
            entry["y"] = y
    after = load_cfc_document(candidate)
    return CFCEditResult(document, after)


def set_node_comment(document, node_id, comment) -> CFCEditResult:
    """只改变目标 layout 项的 ``comment``；不重写 graph、其它 layout 或元数据。

    目标不存在、``comment`` 非 exact str 均失败关闭。
    """
    _require_document(document)
    errors: list = []
    if not _is_nonempty_str(node_id):
        errors.append(_edit_diag("INVALID_NODE_ID",
                                 "node_id must be a non-empty exact str"))
    if type(comment) is not str:
        errors.append(_edit_diag("INVALID_COMMENT", "comment must be an exact str"))
    _raise_edit(errors)

    candidate = document.to_json()
    layout = candidate["layout"]
    if node_id not in {entry["node_id"] for entry in layout}:
        raise CFCEditError(
            (_edit_diag("MISSING_NODE", "node_id names no existing layout node"),))
    for entry in layout:
        if entry["node_id"] == node_id:
            entry["comment"] = comment
    after = load_cfc_document(candidate)
    return CFCEditResult(document, after)


# ---------------------------------------------------------------------------
# 连线命令
# ---------------------------------------------------------------------------
_CONN_FIELDS = (
    "source_node_id", "source_pin_id", "target_node_id", "target_pin_id")


def _require_connection_endpoints(source_node_id, source_pin_id, target_node_id,
                                  target_pin_id, read_mode) -> None:
    errors: list = []
    for value in (source_node_id, source_pin_id, target_node_id, target_pin_id):
        if not _is_nonempty_str(value):
            errors.append(_edit_diag(
                "INVALID_CONNECTION_ENDPOINT",
                "connection endpoints must be non-empty exact str"))
            break
    if not _is_nonempty_str(read_mode):
        errors.append(_edit_diag("INVALID_READ_MODE",
                                 "read_mode must be a non-empty exact str"))
    _raise_edit(errors)


def add_connection(document, source_node_id, source_pin_id, target_node_id,
                   target_pin_id, read_mode) -> CFCEditResult:
    """以五个 exact str 端点 / ``read_mode`` 构造一条候选连线。

    ``read_mode`` 必须显式给出——**不**自动推断 read_mode / feedback_marker / 反馈边。
    端点存在性、方向（source=OUT / target=IN）、IEC 类型一致、单驱动与 read_mode 枚举全部
    由冻结 ``load_cfc_document`` 裁决。
    """
    _require_document(document)
    _require_connection_endpoints(source_node_id, source_pin_id, target_node_id,
                                  target_pin_id, read_mode)
    candidate = document.to_json()
    candidate["graph"]["connections"].append({
        "source_node_id": source_node_id,
        "source_pin_id": source_pin_id,
        "target_node_id": target_node_id,
        "target_pin_id": target_pin_id,
        "read_mode": read_mode,
    })
    after = load_cfc_document(candidate)
    return CFCEditResult(document, after)


def remove_connection(document, source_node_id, source_pin_id, target_node_id,
                      target_pin_id, read_mode) -> CFCEditResult:
    """按五个 exact str 端点 / ``read_mode`` 精确定位并删除**恰好一条**连线。

    零命中或多命中均稳定失败关闭，绝不静默 no-op；删除后仍由冻结 ``load_cfc_document``
    整文档校验。
    """
    _require_document(document)
    _require_connection_endpoints(source_node_id, source_pin_id, target_node_id,
                                  target_pin_id, read_mode)
    candidate = document.to_json()
    connections = candidate["graph"]["connections"]

    def _matches(conn) -> bool:
        # 候选连接字段来自冻结文档投影，均为安全 str，可安全比较。
        return (conn["source_node_id"] == source_node_id
                and conn["source_pin_id"] == source_pin_id
                and conn["target_node_id"] == target_node_id
                and conn["target_pin_id"] == target_pin_id
                and conn["read_mode"] == read_mode)

    hits = [conn for conn in connections if _matches(conn)]
    if len(hits) == 0:
        raise CFCEditError(
            (_edit_diag("MISSING_CONNECTION",
                        "no connection matches the given endpoints"),))
    if len(hits) > 1:
        raise CFCEditError(
            (_edit_diag("AMBIGUOUS_CONNECTION",
                        "more than one connection matches the given endpoints"),))

    remaining: list = []
    removed = False
    for conn in connections:
        if not removed and _matches(conn):
            removed = True
            continue
        remaining.append(conn)
    candidate["graph"]["connections"] = remaining
    after = load_cfc_document(candidate)
    return CFCEditResult(document, after)


__all__ = [
    "CFCEditDiagnostic", "CFCEditError", "CFCEditResult",
    "add_node", "remove_node", "move_node", "set_node_comment",
    "add_connection", "remove_connection",
]
