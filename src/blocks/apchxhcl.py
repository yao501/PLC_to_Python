"""业务块 APCHXHCL（修正版 ST → Python 迁移）。

对应的 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCHXHCL1.txt``。

功能定位：
    信号处理模块 —— 故障检测 + 最近一分钟均值统计 + 一阶惯性滤波 + 故障均值冻结。

故障判据（与 ST 一致）：
    1. 持续不变化：``A > TL``（A = 连续 ``PV == PV_1`` 的扫描周期数）；
    2. 变化过大：``|PV - PV_1| > BHSLH``，由 ``TOF1`` 保持 ``TL`` 秒；
    3. 越界：``PV > PVH`` 或 ``PV < PVL``，由 ``TOF2`` 保持 ``TL`` 秒。

时序要点（与 ST 一致）：
    - 正常时每扫描周期都向 ``PV_TEMP[1] / FV_TEMP[1]`` 写入最新一拍并整体后移；
    - "故障首拍"（``R_TRIG3.Q=TRUE``）基于当前缓存做一次均值，之后**冻结**；
    - 故障持续期间数组、``PV_AVG / FV_AVG`` 都不更新；
    - ``A`` 在 ``EN=TRUE`` 的每一拍（无论故障与否）末尾更新；
    - ``PV_1 := PV`` 在 ``EN`` 分支之外、周期末尾执行（供下一拍用）。

依赖（严格复用，不重写）：

* :class:`~src.primitives.TOF`、:class:`~src.primitives.R_TRIG` —— 基础原语
* :func:`~src.compat.real_to_int`、:func:`~src.compat.real_to_time_ms`
  —— ST 类型转换兼容 helper（所有 ``REAL_TO_INT / REAL_TO_TIME`` 统一走此）

时间语义（严格按 00a 契约 R7 条）：
    - 主程序以 ``cycle_ms`` 周期推进，``dt_ms`` 只用于驱动 ``TOF1/TOF2``
      内部累积；``dt_ms`` **不替代** ``TB / TC / TL`` 等显式输入脚。
    - ``TB / TC / TL`` 是 ST 显式输入脚，按 PLC 输入脚语义取值；单位是
      **秒**，由 FB 源码决定。
    - ``TB`` 的业务语义是 "窗口分辨率"——``SAMPLE_N = 60 / TB`` 决定了
      "最近一分钟"窗口被切成多少个样本；``TB`` 同时也作为一阶 IIR 滤波
      离散化步长。
    - 调用方若希望 ``SAMPLE_N`` 准确对应 "每扫描周期一个样本"，可以传入
      ``TB = cycle_ms / 1000``——这是**业务配置决策**，不是契约强制。如
      业务需要 "若干扫描周期合并为一个业务样本"，``TB`` 取对应秒数同样合法。
    - 业务上推荐 ``60 / TB`` 为整数（详见
      :func:`~src.validation.check_tb_sample_n_integer`）。

风险契约（对应 ``docs/RISKS.md`` 中的条目，此处锁死行为）：

R1 —— ``A > TL`` 的混合单位
    ``TL`` 在 ``TOF1/TOF2.PT_ms`` 中按**秒**使用；在 ``A > TL`` 中按**源块
    周期阈值语义**保留。这是原 CODESYS FB 作者明确声明保留的怪异行为，
    本迁移**不改其语义**。业务调用方应把 ``TL`` 同时解读为：
    "TOF 的保持秒数" 和 "持续不变化的扫描周期数阈值"。
    如未来需要时间尺度统一，必须在上游 ST 同步修改，此处不擅动。

R3 —— 冷启动 / 刚使能即故障
    本块层的事实：

    * ``EN=FALSE`` 会清空历史缓存 ``PV_TEMP / FV_TEMP`` 并置 ``INIT_OK=False``；
    * 刚 ``EN=True`` 第一拍就进入故障（例如 ``PVH/PVL`` 配错、或现场信号
      进入瞬间即越界）时，``R_TRIG3.Q`` 会在首拍 TRUE，此时冻结的均值
      来自**全零或未填满窗口**的历史，该均值在业务上不具代表性；
    * 本块**不自行引入 warm-up / history_valid 门控语义**——这是 Runtime
      阶段 ``system_ready`` / ``startup_inhibit_ms`` / output gate 的职责。

    推荐由主程序在：
      ``system_ready AND 窗口累积充足`` 才放 ``EN:=True``
    实现业务层面的冷启动保护。
"""

from __future__ import annotations

from typing import TypedDict

from src.compat import real_to_int, real_to_time_ms
from src.primitives import R_TRIG, TOF


class APCHXHCLOutput(TypedDict):
    AV: float
    GZDV: bool
    PV_AVG: float
    FV_AVG: float


class APCHXHCL:
    """信号处理业务块（修正版）。

    跨周期状态（全部为 self.* 实例属性，保留原 ST 变量名）：

    * ``AV / GZDV / PV_AVG / FV_AVG``：公开输出
    * ``PV_1``：上一拍 PV 快照
    * ``Ok_1``：上一拍滤波输出
    * ``AV_TEMP``：本拍滤波中间值
    * ``PV_TEMP[1..500] / FV_TEMP[1..500]``：最近样本缓存（实际用 ``1..SAMPLE_N``）
    * ``SAMPLE_N``：最近一分钟窗口样本数
    * ``GZDV_RAW``：本拍原始故障判定
    * ``INIT_OK``：首拍初始化标志
    * ``A``：持续不变化计数器（周期数，上限 3600）
    * ``TOF1 / TOF2 / R_TRIG3``：子功能块实例

    公开接口::

        step(dt_ms, *, EN, PV, FV, PVH, PVL, BHSLH, TL, TC, KG, TB) -> dict
    """

    ARRAY_SIZE = 500

    def __init__(self) -> None:
        self.TOF1 = TOF()
        self.TOF2 = TOF()
        self.R_TRIG3 = R_TRIG()

        self.AV: float = 0.0
        self.GZDV: bool = False
        self.PV_AVG: float = 0.0
        self.FV_AVG: float = 0.0

        self.PV_1: float = 0.0
        self.Ok_1: float = 0.0
        self.AV_TEMP: float = 0.0

        self.PV_TEMP: list[float] = [0.0] * (self.ARRAY_SIZE + 1)
        self.FV_TEMP: list[float] = [0.0] * (self.ARRAY_SIZE + 1)

        self.SAMPLE_N: int = 1
        self.SUM: float = 0.0
        self.NUM: float = 0.0
        self.SUM1: float = 0.0
        self.NUM1: float = 0.0

        self.GZDV_RAW: bool = False
        self.INIT_OK: bool = False

        self.A: float = 0.0

    def step(
        self,
        dt_ms: int,
        *,
        EN: bool,
        PV: float,
        FV: float,
        PVH: float = 1_000_000.0,
        PVL: float = -100_000.0,
        BHSLH: float = 100_000.0,
        TL: float = 60.0,
        TC: float = 1.0,
        KG: float = 1.0,
        TB: float = 0.5,
    ) -> APCHXHCLOutput:
        TL_c = min(max(TL, 1.0), 290.0)

        if TB > 0.001:
            sample_n = real_to_int(60.0 / TB)
        else:
            sample_n = 1
        self.SAMPLE_N = min(max(sample_n, 1), 500)

        if EN:
            if not self.INIT_OK:
                self.PV_1 = PV
                self.Ok_1 = PV
                self.INIT_OK = True

            pt_ms = real_to_time_ms(TL_c * 1000.0)

            tof1_in = abs(PV - self.PV_1) > BHSLH
            self.TOF1.step(dt_ms, IN=tof1_in, PT_ms=pt_ms)

            tof2_in = (PV > PVH) or (PV < PVL)
            self.TOF2.step(dt_ms, IN=tof2_in, PT_ms=pt_ms)

            self.GZDV_RAW = (self.A > TL_c) or self.TOF1.Q or self.TOF2.Q
            self.R_TRIG3.step(CLK=self.GZDV_RAW)

            if not self.GZDV_RAW:
                for I in range(self.SAMPLE_N, 1, -1):
                    self.FV_TEMP[I] = self.FV_TEMP[I - 1]
                self.FV_TEMP[1] = FV

                for I in range(self.SAMPLE_N, 1, -1):
                    self.PV_TEMP[I] = self.PV_TEMP[I - 1]
                self.PV_TEMP[1] = PV

                self.NUM = 0.0
                self.SUM = 0.0
                for I in range(1, self.SAMPLE_N + 1):
                    if self.FV_TEMP[I] > 0.1:
                        self.SUM += self.FV_TEMP[I]
                        self.NUM += 1.0
                self.FV_AVG = self.SUM / max(self.NUM, 1.0)

                self.NUM1 = 0.0
                self.SUM1 = 0.0
                for I in range(1, self.SAMPLE_N + 1):
                    if self.PV_TEMP[I] != 0:
                        self.SUM1 += self.PV_TEMP[I]
                        self.NUM1 += 1.0
                self.PV_AVG = self.SUM1 / max(self.NUM1, 1.0)

            elif self.R_TRIG3.Q:
                self.NUM = 0.0
                self.SUM = 0.0
                for I in range(1, self.SAMPLE_N + 1):
                    if self.FV_TEMP[I] > 0.1:
                        self.SUM += self.FV_TEMP[I]
                        self.NUM += 1.0
                self.FV_AVG = self.SUM / max(self.NUM, 1.0)

                self.NUM1 = 0.0
                self.SUM1 = 0.0
                for I in range(1, self.SAMPLE_N + 1):
                    if self.PV_TEMP[I] != 0:
                        self.SUM1 += self.PV_TEMP[I]
                        self.NUM1 += 1.0
                self.PV_AVG = self.SUM1 / max(self.NUM1, 1.0)

            self.GZDV = self.GZDV_RAW

            new_A = (self.A + 1.0) if PV == self.PV_1 else 0.0
            self.A = min(new_A, 3600.0)

        else:
            self.TOF1.step(dt_ms, IN=False, PT_ms=0)
            self.TOF2.step(dt_ms, IN=False, PT_ms=0)

            self.GZDV = False
            self.GZDV_RAW = False
            self.INIT_OK = False

            for I in range(1, self.ARRAY_SIZE + 1):
                self.FV_TEMP[I] = 0.0
                self.PV_TEMP[I] = 0.0

            self.FV_AVG = 0.0
            self.PV_AVG = 0.0
            self.A = 0.0

        self.PV_1 = PV

        if self.GZDV:
            self.AV = self.PV_AVG
        else:
            if (TB + TC) > 0.001:
                self.AV_TEMP = (TC * self.Ok_1 + KG * TB * PV) / (TB + TC)
                if -1e10 < self.AV_TEMP < 1e10:
                    self.AV = self.AV_TEMP
                    self.Ok_1 = self.AV

        return {
            "AV": self.AV,
            "GZDV": self.GZDV,
            "PV_AVG": self.PV_AVG,
            "FV_AVG": self.FV_AVG,
        }
