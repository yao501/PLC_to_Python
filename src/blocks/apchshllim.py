"""业务块 APCHSHLLIM：幅值限幅（High/Low Limit）。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCHSHLLIM.txt``。

ST 原语义（逐条保留）::

    IF LL > HL THEN
        LL := HL;       (* 块内对参数错配的静默修正 *)
    END_IF
    IF (IN > HL) THEN  AV := HL;
    ELSIF (IN < LL) THEN  AV := LL;
    ELSE  AV := IN;
    END_IF

行为要点：

1. **无状态**：纯组合块，不携带跨周期状态，``dt_ms`` 不参与运算。
2. **块内静默修正**：``LL > HL`` 时块内自动把 ``LL`` 视作 ``HL``。
   这是源块自带的容错语义，**必须如实复现**，不允许 Python 侧改成
   ``raise ValueError`` 或 warning（属 02 规则"不擅自改变业务行为"）。
   该修正只影响 *本拍* ``AV`` 计算，不写回任何持久状态——下一拍 ``LL``
   仍是调用方传入的原值（与 ST 中 VAR_INPUT 值传递语义一致）。
3. **关于参数非负**：本块不约束 ``HL`` / ``LL`` 必须为某符号；任意符号
   组合都按上面的钳位规则处理。``HL`` 的合理性由配置装载层
   （``RUNTIME-PARAM-VALIDATION``）兜底，本块不做内嵌校验。

依赖：无（纯组合，不使用基础原语）。

参考 ``docs/RISKS.md`` 的 ``APCHSHLLIM-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict


class APCHSHLLIMOutput(TypedDict):
    AV: float


class APCHSHLLIM:
    """幅值限幅块。无跨周期状态。

    公开接口::

        step(dt_ms, *, IN, HL, LL) -> {"AV": ...}
    """

    def __init__(self) -> None:
        self.AV: float = 0.0

    def step(
        self,
        dt_ms: int,
        *,
        IN: float,
        HL: float,
        LL: float,
    ) -> APCHSHLLIMOutput:
        del dt_ms

        if LL > HL:
            LL = HL

        if IN > HL:
            self.AV = HL
        elif IN < LL:
            self.AV = LL
        else:
            self.AV = IN

        return {"AV": self.AV}
