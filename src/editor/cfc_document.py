"""Stage 4 无界面 CFC 编辑文档模型 v1 与安全投影（WP-20260827-146）。

Stage 4 的第一个**无界面工程文档合同**：安全地保存平台**新建** CFC 图、节点布局
与注释，做确定性 JSON 往返，并只通过冻结的 ``load_cfc_model`` 把图投影为不可变的
``CFCModel``。本模块**不是** CODESYS 文件解析器，也不实现任何编辑命令、undo/redo、
文件系统持久化或 UI：它只接受项目内部 schema v1 的不可信 payload，把它安全物化成
不可变的 :class:`CFCDocument`。

设计纪律（与 ``src/runtime/cfc_model.py`` 同源）：

* ``payload`` 是项目内部工程契约，不冒充 IEC / CODESYS 官方序列化格式；
* 不可信边界只接受 exact 内建 JSON 形态（exact ``dict`` / ``list`` / ``str`` /
  ``bool`` / ``int``），``bool`` 不冒充整数，子类与自定义容器不被静默接受；
* 诊断只携带已验证的安全字符串，绝不格式化、比较、哈希或真值测试不可信对象；
* **图语义只有一个真值来源**：``graph`` 完全交给冻结的 ``load_cfc_model`` 校验、
  规范化与投影；本模块不复制节点 / 管脚 / 连接 / carrier / 反馈或定序规则，也不据
  序号 / 拓扑 / 名称猜测 ``read_mode`` 或 ``feedback_marker``；
* 编辑器只保存**平台新建图**：``carrier`` 与 ``order_source`` 必须为
  ``user_defined``、``execution_order_mode`` 只能是 ``auto`` 或 ``explicit``；
  ``plcopen_xml`` / ``export_native`` 即使能通过底层模型也不得冒充编辑器新建图。

Python 侧的文档与投影约定不构成 PLC / CODESYS 语义等价证据。
"""
from __future__ import annotations

from dataclasses import dataclass

from src.runtime.cfc_model import CFCModel, CFCModelError, load_cfc_model

#: 内部文档 schema v1 版本标识；任何其它值失败关闭。
DOCUMENT_SCHEMA_VERSION = "cfc-document-v1"

_ROOT_KEYS = frozenset(
    {"schema_version", "document_id", "title", "description", "graph", "layout"})
_LAYOUT_KEYS = frozenset({"node_id", "x", "y", "comment"})

#: 编辑器新建图冻结的 carrier / order_source / mode 边界。
_EDITOR_CARRIER = "user_defined"
_EDITOR_ORDER_SOURCE = "user_defined"
_EDITOR_MODES = frozenset({"auto", "explicit"})


# ---------------------------------------------------------------------------
# 不可变数据对象
# ---------------------------------------------------------------------------
def _freeze_layout(value) -> tuple:
    """构造期零观察容器规范化：exact ``tuple`` 原样保留，exact ``list`` 复制成**新**
    ``tuple`` 以断开调用方可变别名，其它类型失败关闭。

    只做 ``type(x) is T`` 判定，绝不迭代非内建容器；``frozen=True`` 只挡字段重新赋值，
    本函数补上容器别名这一窄边界，令 :attr:`CFCDocument.layout` 真正不可变。
    """
    if type(value) is tuple:
        return value
    if type(value) is list:
        return tuple(value)
    raise TypeError("layout field must be an exact tuple or list")


@dataclass(frozen=True)
class CFCLayoutEntry:
    node_id: str
    x: int
    y: int
    comment: str


@dataclass(frozen=True)
class CFCDocument:
    schema_version: str
    document_id: str
    title: str
    description: str
    graph: CFCModel
    layout: tuple

    def __post_init__(self):
        # 断开调用方对 layout 容器的可变别名（见 _freeze_layout）。
        object.__setattr__(self, "layout", _freeze_layout(self.layout))

    def to_json(self) -> dict:
        """返回 JSON 兼容投影：文档元数据、经冻结模型的 ``graph`` 投影、逐节点 layout；
        每次调用返回全新容器（``graph`` 也委托 :meth:`CFCModel.to_json`）。"""
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "title": self.title,
            "description": self.description,
            "graph": self.graph.to_json(),
            "layout": [
                {
                    "node_id": entry.node_id,
                    "x": entry.x,
                    "y": entry.y,
                    "comment": entry.comment,
                }
                for entry in self.layout
            ],
        }

    def to_cfc_model(self) -> CFCModel:
        """到冻结 :class:`CFCModel` 的只读投影。

        直接返回加载时由冻结 ``load_cfc_model`` 物化的不可变模型实例；不复制节点 /
        管脚 / 连接、不修改文档或模型、不重复任何 CFC 校验。调用方可继续用
        ``model.to_order_graph()`` 得到 ``CFCOrderGraph``（同为冻结代码路径）。
        """
        return self.graph


def dump_cfc_document(document: CFCDocument) -> dict:
    """模块级 JSON 投影入口，等价于 :meth:`CFCDocument.to_json`。"""
    return document.to_json()


# ---------------------------------------------------------------------------
# 诊断与聚合错误
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CFCDocumentDiagnostic:
    code: str
    message: str
    node_id: str | None = None
    pin_id: str | None = None

    def sort_key(self) -> tuple:
        """稳定排序键，令诊断顺序不依赖 nodes / layout 输入排列。"""
        return (self.code, self.node_id or "", self.pin_id or "", self.message)


class CFCDocumentError(ValueError):
    """payload 不能安全物化时聚合的全部稳定诊断。"""

    def __init__(self, errors: tuple):
        self.errors = errors
        super().__init__(
            "; ".join(f"{error.code}: {error.message}" for error in errors))


# ---------------------------------------------------------------------------
# 零观察辅助（只做 type() 判定，绝不比较 / 格式化 / 哈希 / 真值测试不可信对象）
# ---------------------------------------------------------------------------
def _is_nonempty_str(value) -> bool:
    return type(value) is str and value != ""


def _is_exact_int(value) -> bool:
    # ``type(value) is int`` 已排除 ``bool``（``type(True) is bool``）。
    return type(value) is int


def _diag(code, message, node_id=None, pin_id=None) -> CFCDocumentDiagnostic:
    return CFCDocumentDiagnostic(code, message, node_id, pin_id)


def _fields_ok(mapping, required, code, errors) -> bool:
    """校验 ``mapping`` 是 exact ``dict`` 且键集合与 ``required`` **精确一致**（零观察）。

    先逐键确认 exact ``str``（``type() is str`` 不触发 ``__hash__``），任一非 str 键即
    失败关闭且**不**做 ``set()`` 比较，避免恶意键的 ``__hash__`` / ``__eq__`` 逃逸。
    这是通用结构门禁（非 CFC 语义规则），故本模块自持，不从 cfc_model 借用私有实现。
    """
    if type(mapping) is not dict:
        errors.append(_diag(code, "record must be an exact dict"))
        return False
    for key in mapping:
        if type(key) is not str:
            errors.append(_diag(code, "record keys must be exact str"))
            return False
    if set(mapping) != required:
        errors.append(_diag(code, "record fields must match schema exactly"))
        return False
    return True


def _validate_layout_entry(raw, errors) -> CFCLayoutEntry | None:
    if not _fields_ok(raw, _LAYOUT_KEYS, "SCHEMA_LAYOUT_FIELDS", errors):
        return None
    node_id = raw["node_id"]
    valid = True
    if not _is_nonempty_str(node_id):
        errors.append(_diag("INVALID_LAYOUT_NODE_ID",
                            "layout node_id must be a non-empty exact str"))
        valid = False
        node_id = None
    if not _is_exact_int(raw["x"]):
        errors.append(_diag("INVALID_LAYOUT_COORD",
                            "layout x must be an exact int", node_id))
        valid = False
    if not _is_exact_int(raw["y"]):
        errors.append(_diag("INVALID_LAYOUT_COORD",
                            "layout y must be an exact int", node_id))
        valid = False
    if type(raw["comment"]) is not str:
        errors.append(_diag("INVALID_LAYOUT_COMMENT",
                            "layout comment must be an exact str", node_id))
        valid = False
    if not valid:
        return None
    return CFCLayoutEntry(node_id, raw["x"], raw["y"], raw["comment"])


def load_cfc_document(payload) -> CFCDocument:
    """把内部 schema v1 ``payload`` 安全物化成规范化、不可变的 :class:`CFCDocument`。

    全程只做 ``type(x) is T`` 判定；``graph`` 完全委托冻结 ``load_cfc_model``。任一非法
    结构记录固定文本、安全路径的稳定诊断并聚合，失败时不返回半成品、不修改调用方
    payload、不触及任何既有文档 / 模型。相同逻辑的输入排列产生等价文档与稳定诊断顺序。
    """
    errors: list = []
    if not _fields_ok(payload, _ROOT_KEYS, "SCHEMA_DOCUMENT_FIELDS", errors):
        raise CFCDocumentError(
            tuple(sorted(errors, key=CFCDocumentDiagnostic.sort_key)))

    schema_version = payload["schema_version"]
    if type(schema_version) is not str:
        errors.append(_diag("INVALID_DOCUMENT_SCHEMA_VERSION",
                            "schema_version must be an exact str"))
    elif schema_version != DOCUMENT_SCHEMA_VERSION:
        errors.append(_diag("INVALID_DOCUMENT_SCHEMA_VERSION",
                            "schema_version must be " + DOCUMENT_SCHEMA_VERSION))

    document_id = payload["document_id"]
    if not _is_nonempty_str(document_id):
        errors.append(_diag("INVALID_DOCUMENT_ID",
                            "document_id must be a non-empty exact str"))

    title = payload["title"]
    if not _is_nonempty_str(title):
        errors.append(_diag("INVALID_DOCUMENT_TITLE",
                            "title must be a non-empty exact str"))

    if type(payload["description"]) is not str:
        errors.append(_diag("INVALID_DOCUMENT_DESCRIPTION",
                            "description must be an exact str"))

    # graph：唯一真值来源是冻结 load_cfc_model；其诊断安全并入文档诊断（GRAPH_ 前缀）。
    model = None
    node_ids: frozenset = frozenset()
    try:
        model = load_cfc_model(payload["graph"])
    except CFCModelError as exc:
        for diag in exc.errors:
            errors.append(_diag("GRAPH_" + diag.code, diag.message,
                                diag.node_id, diag.pin_id))
    if model is not None:
        node_ids = frozenset(node.node_id for node in model.nodes)
        if (model.carrier != _EDITOR_CARRIER
                or model.order_source != _EDITOR_ORDER_SOURCE
                or model.execution_order_mode not in _EDITOR_MODES):
            errors.append(_diag(
                "GRAPH_NOT_EDITOR_CARRIER",
                "editor documents only accept carrier=user_defined, "
                "order_source=user_defined and execution_order_mode auto or explicit"))

    # layout：先各自独立做字段 / scalar 校验，通过者进入排列无关的对应关系检查。
    layout_entries: list = []
    layout_raw = payload["layout"]
    if type(layout_raw) is not list:
        errors.append(_diag("INVALID_LAYOUT", "layout must be an exact list"))
    else:
        for entry_raw in layout_raw:
            entry = _validate_layout_entry(entry_raw, errors)
            if entry is not None:
                layout_entries.append(entry)

    # 以排列无关方式分组重复 layout node_id，再据此报稳定诊断。
    layout_counts: dict = {}
    for entry in layout_entries:
        layout_counts[entry.node_id] = layout_counts.get(entry.node_id, 0) + 1
    duplicate_layout_ids = frozenset(
        node_id for node_id, count in layout_counts.items() if count > 1)
    for node_id in duplicate_layout_ids:
        errors.append(_diag("DUPLICATE_LAYOUT",
                            "node_id must have exactly one layout entry", node_id))

    # 只有 graph 成功物化才知道确切节点集合，才能判定悬空 / 缺失对应关系。
    if model is not None:
        layout_ids = frozenset(layout_counts)
        for node_id in layout_ids - node_ids:
            errors.append(_diag("DANGLING_LAYOUT",
                                "layout entry names a node absent from the graph",
                                node_id))
        for node_id in node_ids - layout_ids:
            errors.append(_diag("MISSING_LAYOUT",
                                "graph node has no layout entry", node_id))

    if errors:
        raise CFCDocumentError(
            tuple(sorted(errors, key=CFCDocumentDiagnostic.sort_key)))

    # 成功路径 errors 为空：graph 已物化、无重复 layout、每个节点恰有一项 layout。
    ordered_layout = tuple(
        sorted(layout_entries, key=lambda entry: entry.node_id))
    return CFCDocument(schema_version, document_id, title,
                       payload["description"], model, ordered_layout)
