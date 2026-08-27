"""Stage 4 无界面 CFC 编辑器包。

当前提供 Stage 4 前两个无界面构件：① 工程文档合同（``cfc_document``）安全保存平台新建
CFC 图、节点布局与注释，确定性 JSON 往返，并只通过冻结的 ``src.runtime`` CFC 模型投影
为 ``CFCModel``；② 原子编辑命令层（``cfc_commands``）在冻结文档之上提供节点 / 连线的
添加 / 删除、节点移动与注释更新，每个命令失败原子、成功返回带 before/after 快照的
:class:`CFCEditResult` 作为 undo/redo 基础。这里**不**导出完整历史栈、持久化或 UI；也
不修改或重导出 ``src.runtime`` 的 CFC 顶层合同，不做 ``src`` 根级导出。Python 侧的文档
与命令约定不构成 CODESYS / PLC 语义等价证据。
"""
from src.editor.cfc_document import (
    DOCUMENT_SCHEMA_VERSION,
    CFCLayoutEntry, CFCDocument,
    CFCDocumentDiagnostic, CFCDocumentError,
    load_cfc_document, dump_cfc_document,
)
from src.editor.cfc_commands import (
    CFCEditDiagnostic, CFCEditError, CFCEditResult,
    add_node, remove_node, move_node, set_node_comment,
    add_connection, remove_connection,
)

__all__ = [
    "DOCUMENT_SCHEMA_VERSION",
    "CFCLayoutEntry", "CFCDocument",
    "CFCDocumentDiagnostic", "CFCDocumentError",
    "load_cfc_document", "dump_cfc_document",
    "CFCEditDiagnostic", "CFCEditError", "CFCEditResult",
    "add_node", "remove_node", "move_node", "set_node_comment",
    "add_connection", "remove_connection",
]
