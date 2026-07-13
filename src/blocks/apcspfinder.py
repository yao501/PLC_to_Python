"""业务块 APCSPFINDER：分析用设定值（SP）自动寻找功能块。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCSPFINDER.txt``。

**唯一事实来源是 ST 的实际执行顺序**（不是注释、不是提示词）。

**模块定位**：本块只用于自动参数推荐算法内部寻找分析用 ``SP_USE``，**不**参与
现场实际控制 SP 写入；**不**接 :class:`LicenseContext`、**不**新增授权门控、
**不**读系统时钟、**不**引入 TON/R_TRIG/累计器等依赖（``APCSPFINDER-ANALYSIS-1``）。

``SP_USE`` 优先级：人工 ``SP_MAN`` > 现场/主模块 ``SP_TAG`` > 自动寻找 ``SP_AUTO``；
当现场 SP 可疑且允许替代时，可用 ``SP_AUTO`` 作为分析 SP。

整体结构（严格保留顺序）::

    (* 基础限幅和阈值计算：每拍无条件执行 *)
    CYCLE_S := MAX(CYCLE, 0.001)
    PV_RANGE/OUT_RANGE（<=0 → 临时量程 100）
    PV_TH / AV_TH / SP_BAD_TH（ABS 优先；否则比例 MAX(K,0)；再给最小兜底）
    (* 复位 *)
    IF RESET THEN ... END_IF                     (* 不依赖 EN；不清 PV_1/AV_1/D_* *)
    (* 自动 SP 稳定段寻找 *)
    IF EN AND (NOT RESET) THEN
        首拍基准 PV_1/AV_1/INIT_DONE
        D_PV/D_AV
        IF SP_AUTO_EN AND SAMPLE_OK THEN
            稳定判定（先清零再同拍累计 / 不稳定清当前段，不清 SP_AUTO*）
            稳定段资格确认 + 自动 SP / 可信度更新
        ELSIF NOT SP_AUTO_EN THEN
            SP_AUTO_OK := FALSE; SP_AUTO_CONF := 0
        END_IF
        PV_1 := PV; AV_1 := AV                    (* 仅 EN 块内更新 *)
    END_IF
    (* 现场 SP 可信度提示：无条件 *)
    SP_TAG_BAD := FALSE
    IF SP_TAG_EN AND SP_AUTO_OK THEN SP_TAG_BAD := ABS(SP_TAG-SP_AUTO) > SP_BAD_TH; END_IF
    (* 确定最终分析用 SP：无条件，四级优先级 *)
    IF SP_MAN_EN ... ELSIF SP_TAG_EN AND NOT(...) ... ELSIF SP_AUTO_EN AND SP_AUTO_OK ... ELSE ...

关键契约：

1. **CYCLE 与 dt_ms 分离**（``APCSPFINDER-CYCLE-1``）：稳定段时间严格来自输入
   ``CYCLE`` 的 ``MAX(CYCLE, 0.001)``，``dt_ms`` 仅为统一 ``step`` 接口保留，
   **不**参与累计、**不**用 ``dt_ms/1000`` 替换 ``CYCLE``、**不**因 ``dt_ms != 500``
   缩放。``CYCLE`` 是本拍输入，不持久化改写。
2. **EN 只控制自动稳定段寻找**（``APCSPFINDER-EN-1``）：``EN=False`` 时基础阈值、
   ``RESET``、``SP_TAG_BAD``、最终 ``SP_USE`` 选择仍执行；**不**提前 ``return``。
   ``PV_1/AV_1`` 仅在 ``EN AND NOT RESET`` 块内更新。
3. **历史自动 SP 保留**（``APCSPFINDER-HOLD-1``）：已确认的 ``SP_AUTO/SP_AUTO_OK/
   SP_AUTO_CONF`` 在后续不稳定段、``SAMPLE_OK=False`` 时**不**被清除；仅 ``RESET``
   或 ``SP_AUTO_EN=False`` 的部分逻辑改变其可用性。
4. **RESET 不提前返回**（``APCSPFINDER-RESET-1``）：``RESET`` 清自动寻找内部状态，
   但当拍仍执行最终 ``SP_USE`` 优先级选择；``RESET`` 不重置 ``PV_1/AV_1/D_PV/D_AV/
   PV_TH/AV_TH`` 等未在源 RESET 段出现的变量。
5. **严格阈值**：``<=`` / ``>`` / ``>=`` 一律按源码，不改边界；不 ``round``、不额外
   限幅、不额外校验、不吞异常。
"""

from __future__ import annotations


class APCSPFINDER:
    """分析用设定值自动寻找功能块。携带跨扫描状态，无外部依赖。

    构造::

        APCSPFINDER()

    单周期推进::

        step(dt_ms, *, EN, RESET, CYCLE=0.5, SAMPLE_OK, SP_MAN=0.0,
             SP_MAN_EN=False, SP_TAG=0.0, SP_TAG_EN=True, SP_AUTO_EN=True,
             SP_AUTO_REPLACE_BAD_TAG=False, PV, AV, PVMU=100.0, PVMD=0.0,
             OUTT=100.0, OUTB=0.0, SP_STABLE_T=300.0, SP_CONF_T=900.0,
             PV_STABLE_K=0.002, AV_STABLE_K=0.001, PV_STABLE_ABS=0.0,
             AV_STABLE_ABS=0.0, SP_BAD_K=0.05, SP_BAD_ABS=0.0) -> None

    输出为实例属性 ``SP_USE / SP_VALID / SP_SOURCE / SP_REASON / SP_AUTO /
    SP_AUTO_OK / SP_AUTO_CONF / SP_TAG_BAD / SP_STABLE_T_OUT /
    SP_STABLE_PV_RANGE``。
    """

    def __init__(self) -> None:
        # ===== VAR_OUTPUT（初值 0 / False）=====
        self.SP_USE: float = 0.0
        self.SP_VALID: bool = False
        self.SP_SOURCE: int = 0
        self.SP_REASON: int = 0
        self.SP_AUTO: float = 0.0
        self.SP_AUTO_OK: bool = False
        self.SP_AUTO_CONF: float = 0.0
        self.SP_TAG_BAD: bool = False
        self.SP_STABLE_T_OUT: float = 0.0
        self.SP_STABLE_PV_RANGE: float = 0.0

        # ===== VAR（内部状态，默认零值，跨拍保留）=====
        self.INIT_DONE: bool = False
        self.CYCLE_S: float = 0.0
        self.PV_RANGE: float = 0.0
        self.OUT_RANGE: float = 0.0
        self.PV_TH: float = 0.0
        self.AV_TH: float = 0.0
        self.SP_BAD_TH: float = 0.0
        self.D_PV: float = 0.0
        self.D_AV: float = 0.0
        self.PV_1: float = 0.0
        self.AV_1: float = 0.0
        self.STABLE_ACTIVE: bool = False
        self.STABLE_T: float = 0.0
        self.STABLE_N: float = 0.0  # 源 ST 为 REAL，保持浮点。
        self.STABLE_PV_SUM: float = 0.0
        self.STABLE_PV_MAX: float = 0.0
        self.STABLE_PV_MIN: float = 0.0
        self.STABLE_SP_TEMP: float = 0.0
        self.STABLE_SPAN_K: float = 0.0

    def step(
        self,
        dt_ms: int,
        *,
        EN: bool,
        RESET: bool,
        CYCLE: float = 0.5,
        SAMPLE_OK: bool,
        SP_MAN: float = 0.0,
        SP_MAN_EN: bool = False,
        SP_TAG: float = 0.0,
        SP_TAG_EN: bool = True,
        SP_AUTO_EN: bool = True,
        SP_AUTO_REPLACE_BAD_TAG: bool = False,
        PV: float,
        AV: float,
        PVMU: float = 100.0,
        PVMD: float = 0.0,
        OUTT: float = 100.0,
        OUTB: float = 0.0,
        SP_STABLE_T: float = 300.0,
        SP_CONF_T: float = 900.0,
        PV_STABLE_K: float = 0.002,
        AV_STABLE_K: float = 0.001,
        PV_STABLE_ABS: float = 0.0,
        AV_STABLE_ABS: float = 0.0,
        SP_BAD_K: float = 0.05,
        SP_BAD_ABS: float = 0.0,
    ) -> None:
        # dt_ms 仅为统一接口保留；本块时间严格来自 CYCLE（APCSPFINDER-CYCLE-1）。
        del dt_ms

        # ---- 基础限幅和阈值计算（每拍无条件执行）----
        self.CYCLE_S = max(CYCLE, 0.001)
        self.PV_RANGE = abs(PVMU - PVMD)
        self.OUT_RANGE = abs(OUTT - OUTB)
        if self.PV_RANGE <= 0:
            self.PV_RANGE = 100
        if self.OUT_RANGE <= 0:
            self.OUT_RANGE = 100

        if PV_STABLE_ABS > 0:
            self.PV_TH = PV_STABLE_ABS
        else:
            self.PV_TH = self.PV_RANGE * max(PV_STABLE_K, 0)
        self.PV_TH = max(self.PV_TH, self.PV_RANGE * 0.0001)

        if AV_STABLE_ABS > 0:
            self.AV_TH = AV_STABLE_ABS
        else:
            self.AV_TH = self.OUT_RANGE * max(AV_STABLE_K, 0)
        self.AV_TH = max(self.AV_TH, self.OUT_RANGE * 0.0001)

        if SP_BAD_ABS > 0:
            self.SP_BAD_TH = SP_BAD_ABS
        else:
            self.SP_BAD_TH = self.PV_RANGE * max(SP_BAD_K, 0)
        self.SP_BAD_TH = max(self.SP_BAD_TH, self.PV_RANGE * 0.001)

        # ---- 复位处理（不依赖 EN）----
        if RESET:
            self.INIT_DONE = False
            self.STABLE_ACTIVE = False
            self.STABLE_T = 0.0
            self.STABLE_N = 0.0
            self.STABLE_PV_SUM = 0.0
            self.STABLE_PV_MAX = PV
            self.STABLE_PV_MIN = PV
            self.SP_AUTO = 0.0
            self.SP_AUTO_OK = False
            self.SP_AUTO_CONF = 0.0
            self.SP_TAG_BAD = False
            self.SP_STABLE_T_OUT = 0.0
            self.SP_STABLE_PV_RANGE = 0.0

        # ---- 自动 SP 稳定段寻找 ----
        if EN and (not RESET):
            if not self.INIT_DONE:
                self.PV_1 = PV
                self.AV_1 = AV
                self.INIT_DONE = True

            self.D_PV = abs(PV - self.PV_1)
            self.D_AV = abs(AV - self.AV_1)

            if SP_AUTO_EN and SAMPLE_OK:
                if (self.D_PV <= self.PV_TH) and (self.D_AV <= self.AV_TH):
                    if not self.STABLE_ACTIVE:
                        self.STABLE_ACTIVE = True
                        self.STABLE_T = 0.0
                        self.STABLE_N = 0.0
                        self.STABLE_PV_SUM = 0.0
                        self.STABLE_PV_MAX = PV
                        self.STABLE_PV_MIN = PV
                    self.STABLE_T = self.STABLE_T + self.CYCLE_S
                    self.STABLE_N = self.STABLE_N + 1
                    self.STABLE_PV_SUM = self.STABLE_PV_SUM + PV
                    self.STABLE_PV_MAX = max(self.STABLE_PV_MAX, PV)
                    self.STABLE_PV_MIN = min(self.STABLE_PV_MIN, PV)
                else:
                    self.STABLE_ACTIVE = False
                    self.STABLE_T = 0.0
                    self.STABLE_N = 0.0
                    self.STABLE_PV_SUM = 0.0
                    self.STABLE_PV_MAX = PV
                    self.STABLE_PV_MIN = PV

                self.SP_STABLE_T_OUT = self.STABLE_T
                self.SP_STABLE_PV_RANGE = abs(self.STABLE_PV_MAX - self.STABLE_PV_MIN)
                if (
                    (self.STABLE_N > 0)
                    and (self.STABLE_T >= max(SP_STABLE_T, 1))
                    and (
                        self.SP_STABLE_PV_RANGE
                        <= max(self.PV_TH * 5, self.PV_RANGE * 0.005)
                    )
                ):
                    self.STABLE_SP_TEMP = self.STABLE_PV_SUM / max(self.STABLE_N, 1)
                    self.SP_AUTO = self.STABLE_SP_TEMP
                    self.SP_AUTO_OK = True
                    self.STABLE_SPAN_K = 1 - (
                        self.SP_STABLE_PV_RANGE
                        / max(max(self.PV_TH * 5, self.PV_RANGE * 0.005), 0.001)
                    )
                    self.STABLE_SPAN_K = min(max(self.STABLE_SPAN_K, 0), 1)
                    self.SP_AUTO_CONF = min(
                        max(self.STABLE_T / max(max(SP_CONF_T, SP_STABLE_T), 1), 0), 1
                    )
                    self.SP_AUTO_CONF = min(
                        max(self.SP_AUTO_CONF * 0.7 + self.STABLE_SPAN_K * 0.3, 0), 1
                    )
            elif not SP_AUTO_EN:
                self.SP_AUTO_OK = False
                self.SP_AUTO_CONF = 0.0

            self.PV_1 = PV
            self.AV_1 = AV

        # ---- 现场 SP 可信度提示（无条件）----
        self.SP_TAG_BAD = False
        if SP_TAG_EN and self.SP_AUTO_OK:
            self.SP_TAG_BAD = abs(SP_TAG - self.SP_AUTO) > self.SP_BAD_TH

        # ---- 确定最终分析用 SP（无条件，四级优先级）----
        if SP_MAN_EN:
            self.SP_USE = SP_MAN
            self.SP_VALID = True
            self.SP_SOURCE = 1
            self.SP_REASON = 1
        elif SP_TAG_EN and not (
            SP_AUTO_REPLACE_BAD_TAG and self.SP_TAG_BAD and self.SP_AUTO_OK
        ):
            self.SP_USE = SP_TAG
            self.SP_VALID = True
            self.SP_SOURCE = 2
            if self.SP_TAG_BAD:
                self.SP_REASON = 5
            else:
                self.SP_REASON = 2
        elif SP_AUTO_EN and self.SP_AUTO_OK:
            self.SP_USE = self.SP_AUTO
            self.SP_VALID = True
            self.SP_SOURCE = 3
            if self.SP_TAG_BAD:
                self.SP_REASON = 6
            else:
                self.SP_REASON = 3
        else:
            self.SP_USE = PV
            self.SP_VALID = False
            self.SP_SOURCE = 0
            self.SP_REASON = 4

        return None
