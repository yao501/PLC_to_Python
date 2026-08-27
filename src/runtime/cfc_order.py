"""CFC 定序视图（WP-20260803-067）。

这里只处理已经抽取出的节点依赖与载体顺序元数据；不解析 CFC、不 lower IR，
也不把反馈标记转换为 ``LoadPrev``。Python 侧的定序约定不构成 PLC 语义证据。
"""
from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush


@dataclass(frozen=True)
class CFCOrderNode:
    node_id: str
    execution_order_id: int | None = None
    feedback_marker: bool = False


@dataclass(frozen=True)
class CFCOrderEdge:
    source: str
    target: str


@dataclass(frozen=True)
class CFCOrderGraph:
    nodes: tuple
    edges: tuple
    carrier: str
    execution_order_mode: str
    order_source: str


@dataclass(frozen=True)
class CFCOrderDiagnostic:
    code: str
    message: str
    node_id: str | None = None
    edge: CFCOrderEdge | None = None

    def sort_key(self) -> tuple[str, str, str, str, str]:
        """公开的稳定排序键，避免诊断依赖输入排列。"""
        source = "" if self.edge is None else self.edge.source
        target = "" if self.edge is None else self.edge.target
        return (self.code, self.node_id or "", source, target, self.message)


class CFCOrderError(ValueError):
    """输入不能安全定序时聚合的全部稳定诊断。"""

    def __init__(self, errors: tuple[CFCOrderDiagnostic, ...]):
        self.errors = errors
        super().__init__("; ".join(f"{error.code}: {error.message}" for error in errors))


_AUTO_USER_DEFINED = ("user_defined", "auto", "user_defined")
_EXPLICIT_PLCOOPEN = ("plcopen_xml", "explicit", "exported")
_EXPLICIT_USER_DEFINED = ("user_defined", "explicit", "user_defined")


def _diagnostic(code: str, message: str, *, node_id: str | None = None,
                edge: CFCOrderEdge | None = None) -> CFCOrderDiagnostic:
    return CFCOrderDiagnostic(code, message, node_id, edge)


def _raise_if_errors(errors: list[CFCOrderDiagnostic]) -> None:
    if errors:
        raise CFCOrderError(tuple(sorted(errors, key=CFCOrderDiagnostic.sort_key)))


def resolve_execution_order(graph: CFCOrderGraph) -> tuple[str, ...]:
    """返回不可变的 CFC 节点执行顺序，或失败关闭并给出聚合诊断。

    平台新建自动图使用 Kahn 排序，所有同时 ready 的节点按 Python ``str``
    Unicode 码点升序。显式序号绝不被拓扑重写。`.export` 的两个分支均拒绝。
    """
    errors: list[CFCOrderDiagnostic] = []
    if type(graph) is not CFCOrderGraph:
        raise CFCOrderError((_diagnostic("INVALID_GRAPH", "graph must be a CFCOrderGraph"),))

    carrier_valid = type(graph.carrier) is str
    mode_valid = type(graph.execution_order_mode) is str
    source_valid = type(graph.order_source) is str
    if not carrier_valid:
        errors.append(_diagnostic("INVALID_CARRIER", "carrier must be an exact str"))
    if not mode_valid:
        errors.append(_diagnostic("INVALID_ORDER_MODE", "execution_order_mode must be an exact str"))
    if not source_valid:
        errors.append(_diagnostic("INVALID_ORDER_SOURCE", "order_source must be an exact str"))
    config = (graph.carrier, graph.execution_order_mode, graph.order_source)
    config_valid = carrier_valid and mode_valid and source_valid
    if config_valid and config == ("export_native", "auto", "reconstructed"):
        errors.append(_diagnostic(
            "UNSUPPORTED_RECONSTRUCTION",
            "export_native CFC ordering is unsupported until its carrier branch is verified",
        ))
    elif config_valid and config not in {_AUTO_USER_DEFINED, _EXPLICIT_PLCOOPEN, _EXPLICIT_USER_DEFINED}:
        errors.append(_diagnostic(
            "UNSUPPORTED_CARRIER_MODE",
            "unsupported carrier/execution_order_mode/order_source combination",
        ))

    if type(graph.nodes) is not tuple:
        errors.append(_diagnostic("INVALID_NODES", "nodes must be a tuple"))
        nodes: tuple = ()
    else:
        nodes = graph.nodes
    if type(graph.edges) is not tuple:
        errors.append(_diagnostic("INVALID_EDGES", "edges must be a tuple"))
        edges: tuple = ()
    else:
        edges = graph.edges

    valid_nodes: list[CFCOrderNode] = []
    node_ids: set[str] = set()
    seen_node_ids: set[str] = set()
    explicit = config_valid and config in {_EXPLICIT_PLCOOPEN, _EXPLICIT_USER_DEFINED}
    auto = config_valid and config == _AUTO_USER_DEFINED
    for node in nodes:
        if type(node) is not CFCOrderNode:
            errors.append(_diagnostic("INVALID_NODE", "node must be a CFCOrderNode"))
            continue
        node_id_valid = type(node.node_id) is str and bool(node.node_id)
        if not node_id_valid:
            errors.append(_diagnostic("INVALID_NODE_ID", "node_id must be a non-empty exact str"))
        elif node.node_id in seen_node_ids:
            errors.append(_diagnostic("DUPLICATE_NODE", "node_id must be unique", node_id=node.node_id))
        else:
            seen_node_ids.add(node.node_id)
            node_ids.add(node.node_id)
            valid_nodes.append(node)
        if type(node.feedback_marker) is not bool:
            errors.append(_diagnostic("INVALID_FEEDBACK_MARKER", "feedback_marker must be an exact bool",
                                      node_id=node.node_id if node_id_valid else None))
        order_id = node.execution_order_id
        if explicit and (type(order_id) is not int or order_id < 0):
            errors.append(_diagnostic("INVALID_ORDER_ID", "explicit execution_order_id must be a non-negative exact int",
                                      node_id=node.node_id if node_id_valid else None))
        elif auto and order_id is not None:
            errors.append(_diagnostic("INVALID_ORDER_ID", "auto execution_order_id must be None",
                                      node_id=node.node_id if node_id_valid else None))

    if explicit:
        seen_order_ids: set[int] = set()
        for node in valid_nodes:
            order_id = node.execution_order_id
            if type(order_id) is int and order_id >= 0:
                if order_id in seen_order_ids:
                    errors.append(_diagnostic("DUPLICATE_ORDER_ID", "explicit execution_order_id must be unique",
                                              node_id=node.node_id))
                seen_order_ids.add(order_id)

    valid_edges: list[CFCOrderEdge] = []
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if type(edge) is not CFCOrderEdge or type(edge.source) is not str or type(edge.target) is not str:
            errors.append(_diagnostic("INVALID_EDGE", "edge endpoints must be exact str values"))
            continue
        pair = (edge.source, edge.target)
        if pair in seen_edges:
            errors.append(_diagnostic("DUPLICATE_EDGE", "duplicate edge", edge=edge))
        else:
            seen_edges.add(pair)
            valid_edges.append(edge)
        if edge.source not in node_ids or edge.target not in node_ids:
            errors.append(_diagnostic("DANGLING_EDGE", "edge endpoint does not name a node", edge=edge))
        if edge.source == edge.target:
            errors.append(_diagnostic("SELF_EDGE", "self edges are not supported", edge=edge))

    _raise_if_errors(errors)

    adjacency: dict[str, list[str]] = {node.node_id: [] for node in valid_nodes}
    indegree: dict[str, int] = {node.node_id: 0 for node in valid_nodes}
    for edge in valid_edges:
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1
    ready: list[str] = []
    for node_id, degree in indegree.items():
        if degree == 0:
            heappush(ready, node_id)
    result: list[str] = []
    while ready:
        node_id = heappop(ready)
        result.append(node_id)
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heappush(ready, target)
    if len(result) != len(valid_nodes):
        cyclic = tuple(sorted(node_id for node_id, degree in indegree.items() if degree > 0))
        raise CFCOrderError((_diagnostic("CYCLE", "graph contains a cycle: " + ", ".join(cyclic)),))
    if explicit:
        return tuple(node.node_id for node in sorted(valid_nodes, key=lambda node: node.execution_order_id))
    return tuple(result)
