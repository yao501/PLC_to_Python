"""PLC 基础原语功能块 Python 迁移版。

严格遵守 ``00a-runtime-contract``：时间单位统一为 **整数毫秒**。

用法示例::

    from src.primitives import TON

    ton = TON()
    # 周期 500 ms，PT 5 s，输入持续为真
    q, et_ms = ton.step(dt_ms=500, IN=True, PT_ms=5000)

全部原语按扫描周期模型设计：调用方每个周期调用一次 ``step(...)``，
类实例自动维护跨周期状态。``dt_ms`` 与 ``PT_ms`` 单位均为整数毫秒。
"""

from .edges import F_TRIG, R_TRIG
from .latches import RS, SR
from .timers import TOF, TON, TP

__all__ = [
    "TON",
    "TOF",
    "TP",
    "R_TRIG",
    "F_TRIG",
    "SR",
    "RS",
]
