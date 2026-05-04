"""业务块 APCHSRATELIM：速率限幅（Rate Limit）。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCHSRATELIM.txt``。

ST 原语义（逐条保留）::

    HL := ABS(HL);                   (* 块内强制把 HL 视作正幅值 *)
    LL := ABS(LL);                   (* 块内强制把 LL 视作正幅值 *)

    IF (IN - AV_1 > HL) THEN
        AV := AV_1 + HL;             (* 上升速率被钳到 +HL *)
    ELSIF (IN - AV_1 < -LL) THEN
        AV := AV_1 - LL;             (* 下降速率被钳到 -LL *)
    ELSE
        AV := IN;                    (* 区间内直通 *)
    END_IF
    AV_1 := AV;

接口语义（重要）::

    * ``HL`` = 单拍**上升**速率正幅值（注释 "AV增加限制值，HL>=0"）
    * ``LL`` = 单拍**下降**速率正幅值（注释 "AV减少限制值，LL>=0"）
    * 两者**都是正幅值**，不是"上下区间"。``HL=LL`` 即对称速率限幅。
    * 块内 ``ABS(HL) / ABS(LL)`` 是源块自带的容错——传入负值会被静默
      取绝对值，必须如实复现。该容错不写回输入参数（与 ST 中 VAR_INPUT
      值传递语义一致）。

行为要点（按 02 业务块规则 + 项目工程约定）：

1. **跨周期状态**：``AV_1``（前周期输出，原 ST VAR）。
2. **冷启动**：``AV_1 = 0``。**第一拍若 ``|IN| > HL/LL`` 会被钳到
   ``±HL/LL`` 之内，不会立刻直通 ``IN``**——这是源块语义，不是漏写
   初始化。如果业务希望首拍直通 IN，应当在上层 runtime 阶段做
   "首拍预设 ``AV_1 = IN``"（属 ``RUNTIME-*`` 范畴），本块不内嵌该
   优化。
3. **dt_ms 不参与运算**：源块以"每拍变化量"为单位限速，速率单位的
   实际含义由调用方按扫描周期约定（"每拍 ≤ HL"）。这是 R7 的典型
   场景：限幅幅值是显式输入脚，与 ``dt_ms`` 解耦。

依赖：无（纯组合 + 1 个状态变量，不使用基础原语）。

参考 ``docs/RISKS.md`` 的 ``APCHSRATELIM-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict


class APCHSRATELIMOutput(TypedDict):
    AV: float


class APCHSRATELIM:
    """速率限幅块。跨周期状态：``AV_1``（前周期输出，原 ST VAR，
    Python 侧以实例属性保持）。

    公开接口::

        step(dt_ms, *, IN, HL, LL) -> {"AV": ...}
    """

    def __init__(self) -> None:
        self.AV: float = 0.0
        self.AV_1: float = 0.0

    def step(
        self,
        dt_ms: int,
        *,
        IN: float,
        HL: float,
        LL: float,
    ) -> APCHSRATELIMOutput:
        del dt_ms

        HL = abs(HL)
        LL = abs(LL)

        delta = IN - self.AV_1
        if delta > HL:
            self.AV = self.AV_1 + HL
        elif delta < -LL:
            self.AV = self.AV_1 - LL
        else:
            self.AV = IN

        self.AV_1 = self.AV
        return {"AV": self.AV}
