"""内部 CFC 模型 v1 与安全 Loader（WP-20260808-081）。

阶段 2 唯一的**内部 CFC 模型**：未来平台新建图、PLCopen 导入器与 ``.export`` 导入器
都先转换到这里，再进入已批准的定序 / lowering 内核（``src/runtime/cfc_order.py`` /
``cfc_lowering.py``）。本模块**不是** CODESYS 文件解析器：它只接受项目内部 schema v1
的不可信 payload，把它安全地物化成不可变的 ``CFCModel``，并提供稳定的 JSON 往返投影
与到现有 ``CFCOrderGraph`` 的只读投影。

设计纪律：

* ``payload`` 是项目内部工程契约，不冒充 IEC / CODESYS 官方序列化格式；
* 不可信边界只接受 exact 内建 JSON 形态（exact ``dict`` / ``list`` / ``str`` / ``bool`` /
  ``int`` / ``None``），``bool`` 不冒充整数，子类与自定义容器不被静默接受；
* 诊断只携带已验证的安全字符串 / 路径，绝不格式化、比较、哈希或真值测试不可信对象；
* ``read_mode=previous`` 只表示上游已明确分类的内部上一拍读取；Loader **不**根据序号、
  拓扑、名称或 carrier 自行猜测反馈边。``feedback_marker`` 只保存载体 / 编辑器提供的
  节点级元数据，与逐连线 ``read_mode`` 分层，本包不冻结两者映射。

Python 侧的模型与定序约定不构成 PLC / CODESYS 语义等价证据。
"""
from __future__ import annotations

from dataclasses import dataclass

from src.runtime.cfc_order import CFCOrderEdge, CFCOrderGraph, CFCOrderNode

#: 内部 schema v1 版本标识；任何其它值失败关闭。
SCHEMA_VERSION = "cfc-model-v1"

_ROOT_KEYS = frozenset(
    {"schema_version", "carrier", "execution_order_mode", "order_source",
     "nodes", "connections"})
_NODE_KEYS = frozenset(
    {"node_id", "kind", "type_name", "instance_name", "execution_order_id",
     "feedback_marker", "pins"})
_PIN_KEYS = frozenset(
    {"pin_id", "formal_name", "direction", "iec_type", "value_key"})
_CONNECTION_KEYS = frozenset(
    {"source_node_id", "source_pin_id", "target_node_id", "target_pin_id",
     "read_mode"})

_NODE_KINDS = frozenset({"input", "output", "block", "operator"})
_DIRECTIONS = frozenset({"IN", "OUT"})
_READ_MODES = frozenset({"current", "previous"})

#: 当前冻结的可执行 / 可保存载体组合（carrier, execution_order_mode, order_source）。
#: ``export_native`` 只能作为未验证 / 不可执行数据保存——内核（cfc_order）在定序时仍会
#: 拒绝它，本模块不绕过该门禁。
_CARRIER_COMBOS = frozenset({
    ("user_defined", "auto", "user_defined"),
    ("user_defined", "explicit", "user_defined"),
    ("plcopen_xml", "explicit", "exported"),
    ("export_native", "auto", "reconstructed"),
})


# ---------------------------------------------------------------------------
# 不可变数据对象
# ---------------------------------------------------------------------------
def _freeze_container(value) -> tuple:
    """构造期零观察容器规范化：exact ``tuple`` 原样保留（不可变、无调用方别名风险），
    exact ``list`` 复制成**新** ``tuple``（断开调用方可变别名），其它类型失败关闭。

    只做 ``type(x) is T`` 判定，绝不比较 / 迭代非内建容器；``frozen=True`` 只挡字段重新
    赋值，本函数补上容器别名这一窄边界，令 ``CFCNode.pins`` / ``CFCModel.nodes`` /
    ``CFCModel.connections`` 真正不可变。
    """
    if type(value) is tuple:
        return value
    if type(value) is list:
        return tuple(value)
    raise TypeError("container field must be an exact tuple or list")


@dataclass(frozen=True)
class CFCPin:
    pin_id: str
    formal_name: str
    direction: str
    iec_type: str
    value_key: str


@dataclass(frozen=True)
class CFCNode:
    node_id: str
    kind: str
    type_name: str
    instance_name: str
    execution_order_id: int | None
    feedback_marker: bool | None
    pins: tuple

    def __post_init__(self):
        # 断开调用方对 pins 容器的可变别名（见 _freeze_container）。
        object.__setattr__(self, "pins", _freeze_container(self.pins))


@dataclass(frozen=True)
class CFCConnection:
    source_node_id: str
    source_pin_id: str
    target_node_id: str
    target_pin_id: str
    read_mode: str


@dataclass(frozen=True)
class CFCModel:
    schema_version: str
    carrier: str
    execution_order_mode: str
    order_source: str
    nodes: tuple
    connections: tuple

    def __post_init__(self):
        # 断开调用方对 nodes / connections 容器的可变别名（见 _freeze_container）。
        object.__setattr__(self, "nodes", _freeze_container(self.nodes))
        object.__setattr__(self, "connections", _freeze_container(self.connections))

    def to_json(self) -> dict:
        """返回 JSON 兼容投影，保留 schema version、carrier / order provenance、节点、
        pins、connections、可选序号 / marker 与 read mode；每次调用返回全新容器。"""
        return {
            "schema_version": self.schema_version,
            "carrier": self.carrier,
            "execution_order_mode": self.execution_order_mode,
            "order_source": self.order_source,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "kind": node.kind,
                    "type_name": node.type_name,
                    "instance_name": node.instance_name,
                    "execution_order_id": node.execution_order_id,
                    "feedback_marker": node.feedback_marker,
                    "pins": [
                        {
                            "pin_id": pin.pin_id,
                            "formal_name": pin.formal_name,
                            "direction": pin.direction,
                            "iec_type": pin.iec_type,
                            "value_key": pin.value_key,
                        }
                        for pin in node.pins
                    ],
                }
                for node in self.nodes
            ],
            "connections": [
                {
                    "source_node_id": conn.source_node_id,
                    "source_pin_id": conn.source_pin_id,
                    "target_node_id": conn.target_node_id,
                    "target_pin_id": conn.target_pin_id,
                    "read_mode": conn.read_mode,
                }
                for conn in self.connections
            ],
        }

    def to_order_graph(self) -> CFCOrderGraph:
        """到现有 ``CFCOrderGraph`` 的只读投影。

        ``feedback_marker`` 的 ``None``（缺失 / 空语义，含全部 PLCopen 节点）安全归一为
        内核默认 ``False``；显式布尔 marker 原样传递。定序边只取 ``read_mode=current``
        的逐连线在**节点级**去重后的依赖对；``previous``（反馈）连线**不**构成前向定序约束，
        故不进入定序边。本投影不修改任何内核门禁：``export_native`` 组合仍由
        ``resolve_execution_order`` 拒绝。
        """
        nodes = tuple(
            CFCOrderNode(
                node.node_id,
                node.execution_order_id,
                False if node.feedback_marker is None else node.feedback_marker,
            )
            for node in self.nodes
        )
        seen: set = set()
        edges: list = []
        for conn in self.connections:
            if conn.read_mode != "current":
                continue
            pair = (conn.source_node_id, conn.target_node_id)
            if pair not in seen:
                seen.add(pair)
                edges.append(CFCOrderEdge(conn.source_node_id, conn.target_node_id))
        edges.sort(key=lambda edge: (edge.source, edge.target))
        return CFCOrderGraph(
            nodes, tuple(edges), self.carrier, self.execution_order_mode,
            self.order_source)


def dump_cfc_model(model: CFCModel) -> dict:
    """模块级 JSON 投影入口，等价于 :meth:`CFCModel.to_json`。"""
    return model.to_json()


# ---------------------------------------------------------------------------
# 诊断与聚合错误
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CFCModelDiagnostic:
    code: str
    message: str
    node_id: str | None = None
    pin_id: str | None = None

    def sort_key(self) -> tuple:
        """稳定排序键，令诊断顺序不依赖输入排列。"""
        return (self.code, self.node_id or "", self.pin_id or "", self.message)


class CFCModelError(ValueError):
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


def _diag(code, message, node_id=None, pin_id=None) -> CFCModelDiagnostic:
    return CFCModelDiagnostic(code, message, node_id, pin_id)


def _fields_ok(mapping, required, code, errors, *, node_id=None) -> bool:
    """校验 ``mapping`` 是 exact ``dict`` 且键集合与 ``required`` **精确一致**（零观察）。

    先逐键确认 exact ``str``（``type() is str`` 不触发 ``__hash__``），任一非 str 键即
    失败关闭且**不**做 ``set()`` 比较，避免恶意键的 ``__hash__`` / ``__eq__`` 逃逸。
    """
    if type(mapping) is not dict:
        errors.append(_diag(code, "record must be an exact dict", node_id))
        return False
    for key in mapping:
        if type(key) is not str:
            errors.append(_diag(code, "record keys must be exact str", node_id))
            return False
    if set(mapping) != required:
        errors.append(_diag(code, "record fields must match schema exactly", node_id))
        return False
    return True


def _validate_pin(raw, node_id, errors) -> CFCPin | None:
    if not _fields_ok(raw, _PIN_KEYS, "SCHEMA_PIN_FIELDS", errors, node_id=node_id):
        return None
    pin_id = raw["pin_id"]
    valid = True
    if not _is_nonempty_str(pin_id):
        errors.append(_diag("INVALID_PIN_ID", "pin_id must be a non-empty exact str",
                            node_id))
        valid = False
        pin_id = None
    for field_name in ("formal_name", "iec_type", "value_key"):
        if not _is_nonempty_str(raw[field_name]):
            errors.append(_diag("INVALID_PIN_FIELD",
                                "pin " + field_name + " must be a non-empty exact str",
                                node_id, pin_id))
            valid = False
    direction = raw["direction"]
    if type(direction) is not str or direction not in _DIRECTIONS:
        errors.append(_diag("INVALID_PIN_DIRECTION", "pin direction must be IN or OUT",
                            node_id, pin_id))
        valid = False
    if not valid:
        return None
    return CFCPin(pin_id, raw["formal_name"], direction, raw["iec_type"],
                  raw["value_key"])


def _validate_node(raw, carrier, mode, errors) -> CFCNode | None:
    if not _fields_ok(raw, _NODE_KEYS, "SCHEMA_NODE_FIELDS", errors):
        return None
    node_id = raw["node_id"]
    valid = True
    if not _is_nonempty_str(node_id):
        errors.append(_diag("INVALID_NODE_ID", "node_id must be a non-empty exact str"))
        valid = False
        node_id = None
    kind = raw["kind"]
    if type(kind) is not str or kind not in _NODE_KINDS:
        errors.append(_diag("INVALID_NODE_KIND",
                            "kind must be input/output/block/operator", node_id))
        valid = False
    if not _is_nonempty_str(raw["type_name"]):
        errors.append(_diag("INVALID_NODE_FIELD", "type_name must be a non-empty exact str",
                            node_id))
        valid = False
    if type(raw["instance_name"]) is not str:
        errors.append(_diag("INVALID_NODE_FIELD", "instance_name must be an exact str",
                            node_id))
        valid = False

    order_raw = raw["execution_order_id"]
    if mode == "auto":
        if order_raw is not None:
            errors.append(_diag("INVALID_EXECUTION_ORDER_ID",
                                "auto nodes must omit execution_order_id", node_id))
            valid = False
        order_id = None
    else:  # explicit
        if _is_exact_int(order_raw) and order_raw >= 0:
            order_id = order_raw
        else:
            errors.append(_diag("INVALID_EXECUTION_ORDER_ID",
                                "explicit nodes require a non-negative exact int", node_id))
            valid = False
            order_id = None

    marker_raw = raw["feedback_marker"]
    if marker_raw is None:
        marker = None
    elif carrier == "plcopen_xml":
        # 真实 PLCopen XML 无 feedback_marker：必须保持缺失 / 空语义，伪造 marker 失败关闭。
        errors.append(_diag("PLCOPEN_FEEDBACK_MARKER_FORBIDDEN",
                            "PLCopen nodes carry no feedback_marker; it must stay absent",
                            node_id))
        valid = False
        marker = None
    elif type(marker_raw) is bool:
        marker = marker_raw
    else:
        errors.append(_diag("INVALID_FEEDBACK_MARKER",
                            "feedback_marker must be an exact bool or None", node_id))
        valid = False
        marker = None

    pins_raw = raw["pins"]
    pins: list = []
    if type(pins_raw) is not list:
        errors.append(_diag("INVALID_PINS", "pins must be an exact list", node_id))
        valid = False
    else:
        seen_pin_ids: set = set()
        for pin_raw in pins_raw:
            pin = _validate_pin(pin_raw, node_id, errors)
            if pin is None:
                valid = False
                continue
            if pin.pin_id in seen_pin_ids:
                errors.append(_diag("DUPLICATE_PIN", "pin_id must be unique within a node",
                                    node_id, pin.pin_id))
                valid = False
                continue
            seen_pin_ids.add(pin.pin_id)
            pins.append(pin)

    if not valid:
        return None
    ordered_pins = tuple(sorted(pins, key=lambda pin: pin.pin_id))
    return CFCNode(node_id, kind, raw["type_name"], raw["instance_name"], order_id,
                   marker, ordered_pins)


def _validate_connection(raw, errors) -> CFCConnection | None:
    if not _fields_ok(raw, _CONNECTION_KEYS, "SCHEMA_CONNECTION_FIELDS", errors):
        return None
    fields = ("source_node_id", "source_pin_id", "target_node_id", "target_pin_id")
    values = [raw[name] for name in fields]
    valid = True
    for value in values:
        if not _is_nonempty_str(value):
            errors.append(_diag("INVALID_CONNECTION",
                                "connection endpoints must be non-empty exact str"))
            valid = False
            break
    read_mode = raw["read_mode"]
    if type(read_mode) is not str or read_mode not in _READ_MODES:
        errors.append(_diag("INVALID_CONNECTION", "read_mode must be current or previous"))
        valid = False
    if not valid:
        return None
    return CFCConnection(values[0], values[1], values[2], values[3], read_mode)


def load_cfc_model(payload) -> CFCModel:
    """把内部 schema v1 ``payload`` 安全物化成规范化、不可变的 :class:`CFCModel`。

    全程只做 ``type(x) is T`` 判定；任一非法结构记录固定文本、安全路径的稳定诊断并聚合，
    失败时不返回半成品。相同逻辑的输入排列产生等价模型与稳定诊断顺序；加载不修改调用方
    payload，两个模型实例不共享可变容器。
    """
    errors: list = []
    if not _fields_ok(payload, _ROOT_KEYS, "SCHEMA_ROOT_FIELDS", errors):
        raise CFCModelError(tuple(sorted(errors, key=CFCModelDiagnostic.sort_key)))

    schema_version = payload["schema_version"]
    if type(schema_version) is not str:
        errors.append(_diag("INVALID_SCHEMA_VERSION", "schema_version must be an exact str"))
    elif schema_version != SCHEMA_VERSION:
        errors.append(_diag("INVALID_SCHEMA_VERSION",
                            "schema_version must be " + SCHEMA_VERSION))

    carrier = payload["carrier"]
    mode = payload["execution_order_mode"]
    order_source = payload["order_source"]
    combo_typed = (type(carrier) is str and type(mode) is str and
                   type(order_source) is str)
    if not combo_typed:
        errors.append(_diag("INVALID_CARRIER_COMBO",
                            "carrier/execution_order_mode/order_source must be exact str"))
    elif (carrier, mode, order_source) not in _CARRIER_COMBOS:
        errors.append(_diag("INVALID_CARRIER_COMBO",
                            "unsupported carrier/execution_order_mode/order_source"))
    # mode 仅在组合合法时用于逐节点序号语义；否则按 auto 走（不会成功，errors 已非空）。
    node_mode = mode if (combo_typed and (carrier, mode, order_source) in _CARRIER_COMBOS) else "auto"
    node_carrier = carrier if combo_typed else None

    nodes_raw = payload["nodes"]
    if type(nodes_raw) is not list:
        errors.append(_diag("INVALID_NODES", "nodes must be an exact list"))
        nodes_raw = []
    connections_raw = payload["connections"]
    if type(connections_raw) is not list:
        errors.append(_diag("INVALID_CONNECTIONS", "connections must be an exact list"))
        connections_raw = []

    # 阶段一：先各自独立物化通过字段校验的节点，不让输入"第一个"隐式决定后续索引与诊断。
    materialised_nodes: list = []
    for node_raw in nodes_raw:
        node = _validate_node(node_raw, node_carrier, node_mode, errors)
        if node is not None:
            materialised_nodes.append(node)

    # 阶段二：以排列无关方式分组重复 node_id 与重复显式序号，再据此报稳定诊断。
    node_id_counts: dict = {}
    for node in materialised_nodes:
        node_id_counts[node.node_id] = node_id_counts.get(node.node_id, 0) + 1
    duplicate_node_ids = frozenset(
        node_id for node_id, count in node_id_counts.items() if count > 1)
    for node_id in duplicate_node_ids:
        errors.append(_diag("DUPLICATE_NODE", "node_id must be unique", node_id))

    order_id_counts: dict = {}
    for node in materialised_nodes:
        if node.execution_order_id is not None:
            order_id_counts[node.execution_order_id] = (
                order_id_counts.get(node.execution_order_id, 0) + 1)
    duplicate_order_ids = frozenset(
        order_id for order_id, count in order_id_counts.items() if count > 1)
    # 逐节点归因（而非仅"第二个遇到者"），故共享序号的每个节点都稳定出现在诊断里。
    for node in materialised_nodes:
        if node.execution_order_id in duplicate_order_ids:
            errors.append(_diag("DUPLICATE_EXECUTION_ORDER_ID",
                                "explicit execution_order_id must be unique",
                                node.node_id))

    # 只有 node_id 唯一的节点身份明确，才参与 pin 索引与连接语义；重复 node_id 身份歧义、
    # 整体排除，故引用它的连线在任一输入排列下都稳定报 DANGLING_CONNECTION。
    unique_nodes = [node for node in materialised_nodes
                    if node.node_id not in duplicate_node_ids]
    pin_index: dict = {}
    for node in unique_nodes:
        for pin in node.pins:
            pin_index[(node.node_id, pin.pin_id)] = pin

    valid_connections: list = []
    seen_targets: set = set()
    for conn_raw in connections_raw:
        conn = _validate_connection(conn_raw, errors)
        if conn is None:
            continue
        source = pin_index.get((conn.source_node_id, conn.source_pin_id))
        target = pin_index.get((conn.target_node_id, conn.target_pin_id))
        if source is None or target is None:
            errors.append(_diag("DANGLING_CONNECTION",
                                "connection endpoint does not name an existing pin",
                                conn.target_node_id, conn.target_pin_id))
            continue
        if source.direction != "OUT" or target.direction != "IN":
            errors.append(_diag("INVALID_CONNECTION_DIRECTION",
                                "connection source must be OUT and target must be IN",
                                conn.target_node_id, conn.target_pin_id))
        if source.iec_type != target.iec_type:
            errors.append(_diag("CONNECTION_TYPE_MISMATCH",
                                "connection endpoints must share the same IEC type",
                                conn.target_node_id, conn.target_pin_id))
        target_key = (conn.target_node_id, conn.target_pin_id)
        if target_key in seen_targets:
            errors.append(_diag("MULTIPLE_DRIVERS",
                                "one target pin may be driven by only one connection",
                                conn.target_node_id, conn.target_pin_id))
        else:
            seen_targets.add(target_key)
        valid_connections.append(conn)

    if errors:
        raise CFCModelError(tuple(sorted(errors, key=CFCModelDiagnostic.sort_key)))

    # 成功路径 errors 为空，故无重复 node_id，unique_nodes 即全部已物化节点。
    ordered_nodes = tuple(sorted(unique_nodes, key=lambda node: node.node_id))
    ordered_connections = tuple(sorted(
        valid_connections,
        key=lambda conn: (conn.source_node_id, conn.source_pin_id,
                          conn.target_node_id, conn.target_pin_id, conn.read_mode)))
    return CFCModel(schema_version, carrier, mode, order_source, ordered_nodes,
                    ordered_connections)
