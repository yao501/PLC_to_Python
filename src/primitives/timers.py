"""PLC 标准定时器：TON / TOF / TP。

严格遵守 ``00a-runtime-contract`` 规则：

* 时间单位统一为 **整数毫秒**（``dt_ms`` / ``PT_ms`` / ``ET_ms``）；
* 累积统一采用 ``self.ET_ms += dt_ms`` 的整数加法；
* 禁止浮点时间累积；禁止读取系统时钟；
* ``ET_ms`` 饱和到 ``PT_ms``。
"""

from __future__ import annotations


class TON:
    """接通延时定时器 (Timer On-delay)。

    每个扫描周期调用一次 ``step``：

    * ``IN`` 的上升沿启动计时；
    * ``IN`` 为真且 ``ET_ms < PT_ms``：``ET_ms`` 以 ``dt_ms`` 累加，``Q = FALSE``；
    * ``IN`` 为真且 ``ET_ms >= PT_ms``：``ET_ms`` 保持 ``PT_ms``，``Q = TRUE``；
    * ``IN`` 为假：立即 ``Q = FALSE``、``ET_ms = 0``。
    """

    def __init__(self) -> None:
        self.Q: bool = False
        self.ET_ms: int = 0

    def step(self, dt_ms: int, IN: bool, PT_ms: int) -> tuple[bool, int]:
        if IN:
            if self.ET_ms < PT_ms:
                self.ET_ms += dt_ms
                if self.ET_ms >= PT_ms:
                    self.ET_ms = PT_ms
            self.Q = self.ET_ms >= PT_ms
        else:
            self.Q = False
            self.ET_ms = 0
        return self.Q, self.ET_ms


class TOF:
    """断开延时定时器 (Timer Off-delay)。

    * ``IN`` 为真：``Q = TRUE``，``ET_ms = 0``；
    * ``IN`` 下降沿启动计时；
    * ``IN`` 为假且 ``ET_ms < PT_ms``：``ET_ms`` 累加，``Q`` 保持 ``TRUE``；
    * ``IN`` 为假且 ``ET_ms >= PT_ms``：``ET_ms`` 保持 ``PT_ms``，``Q = FALSE``。
    """

    def __init__(self) -> None:
        self.Q: bool = False
        self.ET_ms: int = 0

    def step(self, dt_ms: int, IN: bool, PT_ms: int) -> tuple[bool, int]:
        if IN:
            self.Q = True
            self.ET_ms = 0
        else:
            if self.ET_ms < PT_ms:
                self.ET_ms += dt_ms
                if self.ET_ms >= PT_ms:
                    self.ET_ms = PT_ms
            if self.ET_ms >= PT_ms:
                self.Q = False
        return self.Q, self.ET_ms


class TP:
    """脉冲定时器 (Pulse Timer)，IEC 标准的 *不可重触发* 行为。

    状态机：

    * IDLE  (``Q=0``, ``ET_ms=0``, ``_armed=True``)：等待 ``IN`` 上升沿；
    * PULSE (``Q=1``, ``ET_ms`` 累加)：脉冲期间 ``IN`` 的任何变化都被忽略；
    * HOLD  (``Q=0``, ``ET_ms=PT_ms``)：脉冲结束，等待 ``IN`` 回到低；
    * ``IN`` 回到低 → 回到 IDLE，``ET_ms`` 清零，FB 重新武装。
    """

    def __init__(self) -> None:
        self.Q: bool = False
        self.ET_ms: int = 0
        self._IN_prev: bool = False
        self._armed: bool = True

    def step(self, dt_ms: int, IN: bool, PT_ms: int) -> tuple[bool, int]:
        rising = IN and not self._IN_prev

        if self._armed and rising:
            self.Q = True
            self.ET_ms = 0
            self._armed = False

        if self.Q:
            self.ET_ms += dt_ms
            if self.ET_ms >= PT_ms:
                self.ET_ms = PT_ms
                self.Q = False

        if not self.Q and not IN:
            self._armed = True
            self.ET_ms = 0

        self._IN_prev = IN
        return self.Q, self.ET_ms
