"""业务块 APCHSFOP：一阶惯性低通滤波（First-order IIR lag filter）。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/HSFOP.txt``。

数学模型：
    连续时间传递函数 ``G(s) = KG / (TC*s + 1)``，前向欧拉离散化后：

        AV[k] = (TC*AV[k-1] + KG*TB*IN[k]) / (TB + TC)

    等价形式（便于直觉）：

        α     = TB / (TB + TC)          # 滤波系数，∈ (0, 1)
        AV[k] = (1-α)*AV[k-1] + α*KG*IN[k]

    TC 越大 / TB 越小 → α 越小 → 滤波越强（响应越慢）。

原 ST 语义（逐条保留）：
    1. ``(TB + TC) > 0.001`` 才更新，否则**当拍整体跳过**，AV / Ok_1 保持。
    2. ``|AV_TEMP| < 1e10`` 才把新值写到 AV 与 Ok_1，否则**双冻结**。
    3. ``AV / Ok_1 / AV_TEMP`` 原 ST 带 ``RETAIN``，Python 侧以实例属性保持。
    4. 本块**无 EN / RESET 输入**，冷启动门控由主程序 ``system_ready`` 承担
       （见 00a 运行契约 / 03-main-program）。

时间语义（严格按 00a 契约 R7 条）：
    * ``TB / TC`` 是 **ST 显式输入脚**，按 PLC 输入脚语义取值：
      有外部赋值用外部值，无赋值用 FB 声明的默认值（``TB=0.5``、``TC=1.0``，单位**秒**）。
    * ``dt_ms`` 按 00a 契约传入但**不参与滤波公式**，也**不自动替代** ``TB``。
    * 调用方若希望 ``TB`` 代表的"每样本时间"与 runtime 扫描周期对齐，可以
      传入 ``cycle_ms / 1000``——这是业务配置决策，**不是契约强制**；
      ``TB`` 与扫描周期解耦的用法同样合法，滤波器按 ``TB`` 自身语义正确工作
      （相关讨论见 ``docs/RISKS.md::APCHSFOP-H5``）。

首拍行为（重要）：
    初值 ``Ok_1 = 0``，首拍 ``AV = α*KG*IN``，**不是 ``IN`` 本身**。从 0
    起爬，到 63.2% 稳态约需 ``TC/TB`` 拍。本块**不引入 INIT_OK 首拍初始化**
    以保持与原 ST 一致（APCHXHCL 在其内部使用本公式时用 INIT_OK，那是
    APCHXHCL 自己的设计）。

依赖：
    无（纯组合 + IIR 状态递推，不使用任何基础原语块）。

参考 ``docs/RISKS.md`` 的 ``APCHSFOP-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict


TB_PLUS_TC_MIN = 0.001
AV_LIMIT = 1e10


class APCHSFOPOutput(TypedDict):
    AV: float


class APCHSFOP:
    """一阶惯性滤波业务块。

    跨周期状态（全部 ``self.*``，保留 ST 变量名）：

    * ``AV``：公开输出（带 ``RETAIN`` 语义）
    * ``Ok_1``：上一拍输出，用于递推
    * ``AV_TEMP``：本拍中间结果（ST 里也声明为 RETAIN，Python 侧保留作调试观察用）

    公开接口::

        step(dt_ms, *, IN, TC, KG, TB) -> {"AV": ...}
    """

    def __init__(self) -> None:
        self.AV: float = 0.0
        self.Ok_1: float = 0.0
        self.AV_TEMP: float = 0.0

    def step(
        self,
        dt_ms: int,
        *,
        IN: float,
        TC: float,
        KG: float,
        TB: float,
    ) -> APCHSFOPOutput:
        del dt_ms

        denom = TB + TC
        if denom > TB_PLUS_TC_MIN:
            self.AV_TEMP = (TC * self.Ok_1 + KG * TB * IN) / denom
            if -AV_LIMIT < self.AV_TEMP < AV_LIMIT:
                self.AV = self.AV_TEMP
                self.Ok_1 = self.AV

        return {"AV": self.AV}
