"""只读 AI 交接状态工具。

``docs/AI_REVIEW_HANDOFF.md`` 始终是唯一权威来源；本包只生成可删除的
内存视图和 dry-run 运行记录。
"""

from .parser import HandoffParser, ParseResult, WorkPackage

__all__ = ["HandoffParser", "ParseResult", "WorkPackage"]
