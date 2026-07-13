"""业务块 APCHSACCUM：离散积算 / 单次回绕（accumulate）。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCHSACCUM.txt``。

ST 原语义（逐条保留，顺序不可调整）::

    IF AV >= MS OR AV < 0 THEN
        AV := IV;
    END_IF

    IF AV + MC * I1 < MS THEN
        AV := AV + MC * I1;
        SS := FALSE;
    ELSE
        AV := AV + MC * I1 - MS;
        SS := TRUE;
    END_IF

    IF (NOT preRS) AND RS THEN
        LR := AV;
        AV := IV;
        SS := FALSE;
    END_IF

    preRS := RS;

行为要点：

1. **离散积算，非 dt 积分**：每次 ``step`` 调用执行一次 ``AV := AV + MC*I1``，
   累加量与扫描周期长度无关。``dt_ms`` 仅为统一调度接口保留，**不得**进入
   累积公式（不乘 ``I1``），实现里显式 ``del dt_ms``（R6/R7）。
2. **执行顺序锁定**：① 先处理上一拍遗留的 ``AV >= MS`` 或 ``AV < 0``（置 ``IV``）；
   ② 再做本拍一次积算或**单次回绕**；③ 最后才检查 ``RS`` 上升沿。三步顺序
   不可调整。
3. **单次回绕**：本拍若 ``AV + MC*I1 >= MS`` 则只减一次 ``MS`` 并置 ``SS=True``；
   即便单拍输入跨越多个 ``MS``，也只减一次，剩余的 ``AV >= MS`` 留到**下一拍
   开头**才因第①步被置回 ``IV``。**严禁**用 ``%`` / while 循环 / 数学优化替代。
4. **负值修正延后**：负 ``I1`` 可令当拍 ``AV`` 变负；当拍不额外修正，**下一拍
   开头**才因 ``AV < 0`` 置回 ``IV``。
5. **RS 为上升沿复位**：仅在 ``(NOT preRS) AND RS`` 那一拍生效，且发生在**本拍
   积算/回绕之后**——``LR`` 保存的是积算后的 ``AV``，随后 ``AV := IV``、
   ``SS := False``。**不得**实现为电平复位。
6. **冷启动 ``AV = 0.0``**：源 ST 中 ``AV : LREAL``（VAR_OUTPUT RETAIN）无显式
   初值即 0.0；即使 ``IV`` 被配置为非零值，冷启动 ``AV`` 仍为 ``0.0``，``IV``
   只在第②/③/第①步指定位置生效。
7. **``MS`` 字面量**：按源 ST **可执行字面量** ``1.797693134862E+38`` 实现；源行尾
   注释写的 ``1.79769313486232E308`` 与字面量冲突，作为待确认源资料歧义登记
   （见 ``docs/RISKS.md::APCHSACCUM-AC3``），未取得原工程确认前**不修正**。
8. **``bPositiveAccum`` 保留但不启用**：源 ST 声明了该变量却未在 body 使用
   （注释意为"TRUE 时不累积负输入"）；本块保留属性以忠实复现，但**不**实现
   "只积正值"逻辑（见 ``APCHSACCUM-AC4``）。

依赖：无（不使用 TON/R_TRIG/BLINK 等基础原语，不导入第三方库）。

参考 ``docs/RISKS.md`` 的 ``APCHSACCUM-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict


class APCHSACCUMOutput(TypedDict):
    AV: float
    SS: bool


class APCHSACCUM:
    """离散积算 / 单次回绕块。携带跨周期（RETAIN）状态。

    公开接口::

        step(dt_ms, *, I1=0.0, RS=False) -> {"AV": ..., "SS": ...}

    构造参数（对应 ST ``VAR RETAIN`` 配置，非每拍输入）：

    * ``IV``：积算初值（``AV`` 越界 / 越下界 / ``RS`` 复位时恢复到的值）。
    * ``MS``：积算总量上限（达到/越过即回绕一次并置 ``SS``）。
    * ``MC``：仪表因子（每拍累加 ``MC * I1``）。
    """

    def __init__(
        self,
        *,
        IV: float = 0.0,
        MS: float = 1.797693134862e38,
        MC: float = 1.0,
    ) -> None:
        # VAR_OUTPUT RETAIN：冷启动均为零值，AV 不取 IV。
        self.AV: float = 0.0
        self.SS: bool = False

        # VAR RETAIN 配置参数（实例级，跨周期保持）。
        self.IV: float = IV
        self.MS: float = MS
        self.MC: float = MC

        # VAR RETAIN 内部状态。
        self.LR: float = 0.0
        self.preRS: bool = False
        # 源 ST 声明但 body 未使用；保留属性，不附加新语义（AC4）。
        self.bPositiveAccum: bool = False

    def step(
        self,
        dt_ms: int,
        *,
        I1: float = 0.0,
        RS: bool = False,
    ) -> APCHSACCUMOutput:
        del dt_ms

        if self.AV >= self.MS or self.AV < 0.0:
            self.AV = self.IV

        if self.AV + self.MC * I1 < self.MS:
            self.AV = self.AV + self.MC * I1
            self.SS = False
        else:
            self.AV = self.AV + self.MC * I1 - self.MS
            self.SS = True

        if (not self.preRS) and RS:
            self.LR = self.AV
            self.AV = self.IV
            self.SS = False

        self.preRS = RS

        return {
            "AV": self.AV,
            "SS": self.SS,
        }
