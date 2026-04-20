"""PLC 双稳态锁存器：SR / RS。

按 IEC 61131-3 标准，这两个 FB 都是 *电平触发*（level-triggered），
而不是边沿触发——即便 CODESYS 在线文档中写的是 "Rising edge: Set Q1
to TRUE"，实际 ST 定义仍然是电平逻辑：
  ``SR``: ``Q1 := SET1 OR (Q1 AND NOT RESET)``      （Set 优先）
  ``RS``: ``Q1 := NOT RESET1 AND (SET OR Q1)``      （Reset 优先）

如果项目中真的需要边沿触发的 set/reset，请在外部叠加 ``R_TRIG`` 使用，
不要擅自改这两个 FB 的语义。
"""

from __future__ import annotations


class SR:
    """Set 优先双稳态锁存。``SET1`` 与 ``RESET`` 同时为真时，输出被置位。"""

    def __init__(self) -> None:
        self.Q1: bool = False

    def step(self, SET1: bool, RESET: bool) -> bool:
        self.Q1 = bool(SET1) or (self.Q1 and not RESET)
        return self.Q1


class RS:
    """Reset 优先双稳态锁存。``SET`` 与 ``RESET1`` 同时为真时，输出被复位。"""

    def __init__(self) -> None:
        self.Q1: bool = False

    def step(self, SET: bool, RESET1: bool) -> bool:
        self.Q1 = (not RESET1) and (bool(SET) or self.Q1)
        return self.Q1
