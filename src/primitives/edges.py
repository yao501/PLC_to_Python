"""PLC 边沿检测器：R_TRIG / F_TRIG。

严格遵守 IEC 61131-3 定义：
  ``R_TRIG``: ``Q := CLK AND NOT M; M := CLK``（M 初值 FALSE）
  ``F_TRIG``: ``Q := NOT CLK AND M; M := CLK``（M 初值 TRUE）

注意：按 IEC 标准的初值约定，如果 ``CLK`` 在上电那一个扫描周期就为
TRUE（R_TRIG）或 FALSE（F_TRIG），会在第一个周期产生一次 ``Q = TRUE``
的"上电边沿"。如需抑制这种首周期边沿，调用方应在外部加 first-scan
mask 或构造时把 ``_CLK_prev`` 置成相反值。
"""

from __future__ import annotations


class R_TRIG:
    """上升沿检测。仅在 ``CLK`` 从 FALSE 变为 TRUE 的那个扫描周期 ``Q = TRUE``。"""

    def __init__(self) -> None:
        self.Q: bool = False
        self._CLK_prev: bool = False

    def step(self, CLK: bool) -> bool:
        self.Q = CLK and not self._CLK_prev
        self._CLK_prev = CLK
        return self.Q


class F_TRIG:
    """下降沿检测。仅在 ``CLK`` 从 TRUE 变为 FALSE 的那个扫描周期 ``Q = TRUE``。"""

    def __init__(self) -> None:
        self.Q: bool = False
        self._CLK_prev: bool = True

    def step(self, CLK: bool) -> bool:
        self.Q = (not CLK) and self._CLK_prev
        self._CLK_prev = CLK
        return self.Q
