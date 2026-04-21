"""业务块 APCSTATISTICS（修正版）：运行统计（min / max / running average）。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/statistics.txt``（修正版 ST 基线）。
对应任务书：``STATISTICS_修正版语义说明_与_Python改写任务书.md``。

本实现**不是**原始 STATISTICS.txt 的逐字复刻，而是采用修正版语义：

* 统一 ``MN/MX`` 的声明初值与 RESET 后赋值（均为 ``±REAL_MAX``）
* 删除原 ST 中未参与计算的死变量 ``SUM``
* 删除原 ST 中 ``COUNTER/2`` 防溢出分支（会造成平均值权重语义突变）
* ``COUNTER`` 在 ST 侧改为 ``ULINT``（Python 侧直接使用 ``int``）
* ``AVG`` 在 ST 侧改为 ``LREAL``（Python 侧使用原生 ``float``）
* ``AVG`` 改用 Welford 增量公式：``AVG += (IN - AVG) / N``
  （与原累计算术平均数学等价，但浮点稳定性更好）

时间语义：
    本块**不依赖 dt_ms**——运行统计只依赖调用次数而非真实时间。``step``
    签名仍保留 ``dt_ms: int`` 以与项目统一扫描入口兼容；方法体内忽略该参数。

RESET 语义（关键）：
    ``RESET=True`` 当拍**只清空状态，不采样当前 IN**。紧接的下一拍
    （``RESET=False, IN=x``）为第一个样本，此时 ``MN = MX = AVG = x``。

依赖：
    无（纯组合 + 状态累加，不使用任何基础原语块）。

参考 ``docs/RISKS.md`` 的 ``APCSTATISTICS-*`` 条目。
"""

from __future__ import annotations

from typing import TypedDict


REAL_MAX = 3.402823466e38
REAL_MIN = -3.402823466e38


class APCSTATISTICSOutput(TypedDict):
    MN: float
    MX: float
    AVG: float


class APCSTATISTICS:
    """运行统计业务块（修正版）。

    跨周期状态（全部 ``self.*`` 实例属性，保留 ST 变量名）：

    * ``MN / MX / AVG``：公开输出
    * ``COUNTER``：样本计数，对应 ST ``ULINT``

    公开接口::

        step(dt_ms, *, IN, RESET) -> {"MN": ..., "MX": ..., "AVG": ...}
    """

    def __init__(self) -> None:
        self.MN: float = REAL_MAX
        self.MX: float = REAL_MIN
        self.AVG: float = 0.0

        self.COUNTER: int = 0

    def step(
        self,
        dt_ms: int,
        *,
        IN: float,
        RESET: bool,
    ) -> APCSTATISTICSOutput:
        del dt_ms

        if RESET:
            self.AVG = 0.0
            self.COUNTER = 0
            self.MN = REAL_MAX
            self.MX = REAL_MIN
        else:
            if IN < self.MN:
                self.MN = IN
            if IN > self.MX:
                self.MX = IN

            self.COUNTER += 1
            self.AVG += (float(IN) - self.AVG) / float(self.COUNTER)

        return {"MN": self.MN, "MX": self.MX, "AVG": self.AVG}
