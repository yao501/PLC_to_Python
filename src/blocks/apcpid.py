"""业务块 APCPID：变比例变积分 PID 调节器。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCPID.txt``。

**唯一事实来源是 ST 的实际执行顺序**（不是注释、不是提示词）。源 ST 的若干
"注释 vs 代码"冲突一律按实际代码实现，并在 ``docs/RISKS.md`` 登记
（``APCPID-CYCLE-1`` / ``APCPID-ORDER-1`` / ``APCPID-RM-1`` /
``APCPID-INPUT-1`` / ``APCPID-OUTRL-1`` / ``APCPID-ZZD-1``）。

整体结构（严格保留顺序）::

    KZQBDYZMK();
    IF (KZQBDYZMK.OK MOD 10000) = 0 THEN          (* 授权通过 *)
        (* 8.1 顶部参数处理：CYCLE/死区/SVH_SJ/SVL_SJ *)
        (*      TIi 用上一拍遗留 EK 判断；KD/TD 本拍局部修正；MU 实例改写 *)
        (*      LASTUKOUT:=AV; UK:=UK_1; PX/PTt *)
        (* 8.2 ATE/TS/preRM 状态切换（改写局部 rm） *)
        (* 8.3 偏差滤波 EK（0.9/0.1）+ AD 取反；微分 DEK + AD 取反 *)
        IF RM=3 OR RM=4 THEN ...  (* 跟踪：增量式/位置式 *)
        ELSIF RM=0 THEN ...       (* 手动：CASE MM；尾部 MM:=0 *)
        ELSE ...                  (* 自动：死区/积分分离/B1..C4/活动 DU_TEMP *)
                                  (*       增量式或位置式输出；末尾 OutRL 判定 *)
        END_IF
        (* 历史状态更新 DU_1/UK_1/EK_1.. *)
        PIDZZD1(...); PT1:=PIDZZD1.PT1; TI1:=PIDZZD1.TI1;
    ELSE                                          (* 授权失败 *)
        BD_ERROR1 := BD_ERROR1 + 1;
        IF BD_ERROR1 > 999999999 THEN BD_ERROR1 := 100000000; END_IF
    END_IF

关键契约：

1. **CYCLE 与 dt_ms 分离**（``APCPID-CYCLE-1``）：PID 公式使用源 ST 内部
   ``CYCLE``（默认 0.5 秒，绑定 500ms 任务）；``dt_ms`` 仅驱动外层授权
   ``KZQBDYZMK.step(dt_ms)`` 与嵌套 ``PIDZZD1.step(dt_ms, ...)``。**不**用
   ``dt_ms/1000`` 替换 ``CYCLE``、**不**因 ``dt_ms != 500`` 缩放公式。
2. **双层授权严格保序**（与 ``APCPIDZZD-GATE-1`` 一致）：每拍先调用一次外层
   ``KZQBDYZMK.step``，再读 ``OK % 10000``；通过才执行主 PID 逻辑，逻辑末尾
   调用一次 ``PIDZZD1.step``——后者内部又调用一次 ``KZQBDYZMK.step``，故授权
   通过的完整扫描共发生 **2 次** 授权调用，**不**去重/缓存。失败时只累加
   ``BD_ERROR1`` 并 ``return``，**不**调用 ``PIDZZD1``、**不**推进任何 PID 状态。
3. **VAR_INPUT 局部改写**（``APCPID-INPUT-1``）：``RM/SP/KD/TD`` 在源 ST 本拍
   内被改写，Python 实现为仅影响本 ``step()`` 余下路径及传入 ``PIDZZD1`` 的
   局部变量，**不**伪造为调用方下一拍输入。``CYCLE/MU/TIi/PTt`` 等 ``VAR``
   的改写必须持久化到下一拍。
4. **旧 EK 顺序**（``APCPID-ORDER-1``）：顶部 ``TIi`` 的 ``SVH_SJ`` 判断使用
   **上一拍**遗留的 ``EK``；本拍 ``EK`` 在其后才重算。
5. **RM 分支按实际代码**（``APCPID-RM-1``）：``RM=3/4`` 跟踪、``RM=0`` 手动，
   其余值（含 ``RM=2``、非法值）一律进入自动路径——与注释"非法保持前状态"
   冲突，按代码。
6. **OutRL 实际语义**（``APCPID-OUTRL-1``）：仅自动模式末尾以
   ``ABS(AV-AV_TEMP) > ABS(OutRL)`` 决定是否把 ``AV_TEMP`` 提交给 ``AV``；
   不是常规限速器。
7. **PIDZZD1 调用时序**（``APCPID-ZZD-1``）：在历史状态更新**之后**调用，新
   ``PT1/TI1`` 仅供**下一拍** PID 主计算使用；本拍 PID 用的是上拍遗留值。
8. **复用既有功能块**：``PIDZZD1`` 是真实的 :class:`APCPIDZZD` 实例，且与本块
   共享**同一个** :class:`LicenseContext`，不重写自整定/授权实现。
9. **严格阈值**：``>`` / ``<`` / ``>=`` / ``<=`` 一律按源码，不改边界、不加
   ``isfinite``/限幅/异常保护/``round``。
"""

from __future__ import annotations

from src.globals.license_context import LicenseContext

from .apcpidzzd import APCPIDZZD


class APCPID:
    """变比例变积分 PID 调节器功能块。携带跨扫描状态，必须注入
    :class:`LicenseContext`。

    构造::

        APCPID(license_context)

    单周期推进::

        step(dt_ms, *, SP, PV, IC=0.0, OC=0.0, TP, TS, RM, OutT, OutB,
             SADD, SSUB, PT, TI, KD=1.0, TD=0.0) -> None

    输出为实例属性 ``AV``。授权失败时不修改任何 PID 状态，只累加
    ``license_context.BD_ERROR1``。

    嵌套自整定实例 ``PIDZZD1`` 是真实 :class:`APCPIDZZD`，与本块共享同一
    ``LicenseContext``。
    """

    def __init__(self, license_context: LicenseContext) -> None:
        self._ctx = license_context

        # ===== VAR_OUTPUT：输出端，初值 0 =====
        self.AV: float = 0.0

        # ===== VAR（功能块实例参数，可配置、跨拍持久化；初值严格对应 ST）=====
        self.CYCLE: float = 0.5
        self.OutRH: float = 5.0
        self.OutRL: float = 0.0
        self.MU: float = 0.0
        self.MD: float = 0.0
        self.OutM: int = 0
        self.AD: int = 0
        self.TM: bool = False
        self.MI: float = 0.0
        self.MS: float = 0.0
        self.MM: int = 0
        self.ATE: bool = False
        self.PVMU: float = 0.0
        self.PVMD: float = 0.0
        self.DI: float = 0.0
        self.SVH: float = 30.0
        self.SVL: float = 0.5
        self.KP: float = 0.0
        self.KI: float = 0.0
        self.PT1K: float = 0.0
        self.TI1K: float = 0.0

        # ===== VAR（内部状态，默认零值）=====
        self.preRM: int = 0
        self.nowRM: int = 0  # 源声明但 body 未参与计算，保留默认状态不启用。
        self.UK_1: float = 0.0
        self.DU_1: float = 0.0
        self.EK_1: float = 0.0
        self.EK_2: float = 0.0
        self.DEK: float = 0.0
        self.DEK_1: float = 0.0
        self.DEK_2: float = 0.0
        self.PV_LAST: float = 0.0
        self.deadenter: int = 0  # 源声明但 body 未引用，保留默认零值。
        self.TIi: float = 0.0
        self.PX: float = 0.0
        self.PT1: float = 0.0
        self.TI1: float = 0.0

        self.UK: float = 0.0
        self.LASTUKOUT: float = 0.0
        self.EK: float = 0.0
        self.UKOUT: float = 0.0
        self.DUOUT: float = 0.0
        self.DU: float = 0.0
        self.DU_TEMP: float = 0.0
        self.SI: int = 0
        self.B1: float = 0.0
        self.B2: float = 0.0
        self.C1: float = 0.0
        self.C2: float = 0.0
        self.C3: float = 0.0
        self.C4: float = 0.0
        self.PTt: float = 0.0
        self.DI_SJ: float = 0.0
        self.SVH_SJ: float = 0.0
        self.SVL_SJ: float = 0.0
        self.AV_TEMP: float = 0.0
        self.EK_LAST: float = 0.0

        # 嵌套自整定：真实实例，复用同一 LicenseContext。
        self.PIDZZD1 = APCPIDZZD(license_context)

    def step(
        self,
        dt_ms: int,
        *,
        SP: float,
        PV: float,
        IC: float = 0.0,
        OC: float = 0.0,
        TP: float,
        TS: bool,
        RM: int,
        OutT: float,
        OutB: float,
        SADD: bool,
        SSUB: bool,
        PT: float,
        TI: float,
        KD: float = 1.0,
        TD: float = 0.0,
    ) -> None:
        # ---- 授权门控（严格保序，外层每拍调用一次）----
        self._ctx.KZQBDYZMK.step(dt_ms)
        if self._ctx.KZQBDYZMK.OK % 10000 != 0:
            # 验证未成功：只累加 BD_ERROR1，不动任何 PID 状态、不调用 PIDZZD1。
            self._ctx.BD_ERROR1 = self._ctx.BD_ERROR1 + 1
            if self._ctx.BD_ERROR1 > 999999999:
                self._ctx.BD_ERROR1 = 100000000.0
            return

        # VAR_INPUT 中会被源 ST 本拍局部改写的变量：用局部副本承载，
        # 仅影响本拍余下路径与传给 PIDZZD1 的参数（APCPID-INPUT-1）。
        rm = RM
        sp = SP
        kd = KD
        td = TD

        # ===== 主程序开始 =====

        # ---- 8.1 顶部参数处理 ----
        if self.CYCLE <= 0:
            self.CYCLE = 0.5

        self.DI_SJ = self.DI * 0.01 * abs(self.PVMU - self.PVMD)
        self.SVH_SJ = self.SVH * 0.01 * abs(self.PVMU - self.PVMD)
        self.SVL_SJ = self.SVL * 0.01 * abs(self.PVMU - self.PVMD)

        # 注意：这里使用的是上一拍遗留的 self.EK（本拍尚未重算）。APCPID-ORDER-1。
        if abs(self.EK) >= self.SVH_SJ:
            self.TIi = TI + abs(sp - PV) * self.KI + self.TI1
        else:
            self.TIi = TI + self.TI1

        if self.TIi <= 0:
            self.TIi = 0.001
        if kd <= 0:
            kd = 0.001
        if td < 0:
            td = 0.0
        if (self.MU - self.MD) == 0:
            self.MU = self.MD + 0.00001

        self.LASTUKOUT = self.AV
        self.UK = self.UK_1

        self.PX = PT + abs(sp - PV) * self.KP + self.PT1
        self.PTt = 0.01 * self.PX * (self.PVMU - self.PVMD) / (self.MU - self.MD)
        if self.PTt <= 0:
            self.PTt = 0.001

        # ---- 8.2 ATE / TS / preRM 自动跟踪状态切换（改写局部 rm）----
        if self.ATE:
            if TS:
                if rm != 4:
                    self.preRM = rm
                    rm = 4
            elif self.preRM != 4:
                rm = self.preRM
                self.preRM = 4

        # ---- 8.3 偏差与微分量计算 ----
        self.EK = 0.9 * self.EK_LAST + 0.1 * (PV + IC - sp)
        self.EK_LAST = self.EK  # 保留 AD 取反之前的滤波误差。
        if self.AD == 1:
            self.EK = -self.EK

        self.DEK = PV - self.PV_LAST
        if self.AD == 1:
            self.DEK = -self.DEK

        # ============================ 模式分支 ============================
        if rm == 3 or rm == 4:
            # ---- 9.1 跟踪模式 ----
            if self.OutM == 1:
                # 增量式
                self.DUOUT = TP
                if abs(self.DUOUT) > self.OutRH:
                    if self.DUOUT >= 0:
                        self.DUOUT = self.OutRH
                    else:
                        self.DUOUT = -self.OutRH
                self.AV = self.DUOUT
                self.AV_TEMP = self.AV
                self.DU = self.DUOUT - OC
            else:
                # 位置式
                self.UKOUT = TP
                self.UKOUT = min(OutT, self.UKOUT)
                self.UKOUT = max(OutB, self.UKOUT)
                if abs(self.UKOUT - self.LASTUKOUT) > self.OutRH:
                    if self.UKOUT > self.LASTUKOUT:
                        self.UKOUT = self.LASTUKOUT + self.OutRH
                    else:
                        self.UKOUT = self.LASTUKOUT - self.OutRH
                self.AV = self.UKOUT
                self.AV_TEMP = self.AV
                self.UK = self.UKOUT - OC
            if self.TM:
                sp = PV

        elif rm == 0:
            # ---- 9.2 手动模式 ----
            if self.OutM == 1:
                # 增量式
                if self.MM == 1:
                    self.DUOUT = self.MI
                elif self.MM == 2:
                    self.DUOUT = -self.MI
                elif self.MM == 3:
                    self.DUOUT = self.MS
                elif self.MM == 4:
                    self.DUOUT = -self.MS
                else:
                    self.DUOUT = 0.0

                if abs(self.DUOUT) > self.OutRH:
                    if self.DUOUT >= 0:
                        self.DUOUT = self.OutRH
                    else:
                        self.DUOUT = -self.OutRH
                self.AV = self.DUOUT
                self.AV_TEMP = self.AV
                self.DU = self.DUOUT - OC
            else:
                # 位置式
                if self.MM == 1:
                    self.UKOUT = self.MI
                elif self.MM == 2:
                    self.UKOUT = -self.MI
                elif self.MM == 3:
                    self.UKOUT = self.MS
                elif self.MM == 4:
                    self.UKOUT = -self.MS
                else:
                    self.UKOUT = 0.0

                self.UKOUT = self.LASTUKOUT + self.UKOUT
                self.UKOUT = min(OutT, self.UKOUT)
                self.UKOUT = max(OutB, self.UKOUT)
                if abs(self.UKOUT - self.LASTUKOUT) > self.OutRH:
                    if self.UKOUT > self.LASTUKOUT:
                        self.UKOUT = self.LASTUKOUT + self.OutRH
                    else:
                        self.UKOUT = self.LASTUKOUT - self.OutRH
                self.AV = self.UKOUT
                self.AV_TEMP = self.AV
                self.UK = self.UKOUT - OC

            self.MM = 0  # 仅手动模式尾部复位手动输出方式。
            if self.TM:
                sp = PV

        else:
            # ---- 9.3 自动模式（RM=1、RM=2、非法值均落此路径）----
            self.LASTUKOUT = self.AV_TEMP

            if abs(self.EK) <= self.DI_SJ:
                # 死区：不计算
                self.DU = 0.0
                self.UK = self.UK_1
            else:
                # 积分分离
                if abs(self.EK) <= self.SVL_SJ:
                    self.SI = 0
                else:
                    self.SI = 1

                # PID 中间量（C2/C3/C4 在 ST 中被计算但活动公式未使用，须保留）
                self.B1 = (td / self.CYCLE) / kd
                self.B2 = 1 + self.B1
                self.C1 = self.B1 / self.B2
                self.C2 = (1 + self.SI * self.CYCLE / self.TIi + td / self.CYCLE) / (
                    self.B2 * self.PTt
                )
                self.C3 = -(1 + 2 * td / self.CYCLE) / (self.B2 * self.PTt)
                self.C4 = (td / self.CYCLE) / (self.B2 * self.PTt)

                # 活动 PID 公式
                self.DU_TEMP = self.C1 * self.DU_1 + (1 - self.C1) * (1 / self.PTt) * (
                    (self.EK - self.EK_1)
                    + (self.SI * self.CYCLE / self.TIi) * self.EK
                    + (td / self.CYCLE) * (self.DEK - 2 * self.DEK_1 + self.DEK_2)
                )

                if (self.DU_TEMP < 10000000000) and (self.DU_TEMP > -10000000000):
                    self.DU = self.DU_TEMP
                else:
                    self.DU = 0.0

            if self.OutM == 1:
                # 9.4 自动增量式输出
                if abs(self.DU) > (self.OutRH - OC):
                    if self.DU >= 0:
                        self.DU = self.OutRH - OC
                    else:
                        self.DU = -self.OutRH + OC
                self.DUOUT = self.DU + OC
                self.AV_TEMP = self.DUOUT
                if SADD:
                    self.AV_TEMP = min(0.0, self.AV_TEMP)
                if SSUB:
                    self.AV_TEMP = max(0.0, self.AV_TEMP)
            else:
                # 9.5 自动位置式输出
                if abs(self.DU) > self.OutRH:
                    if self.DU >= 0:
                        self.DU = self.OutRH
                    else:
                        self.DU = -self.OutRH
                self.UK = self.UK_1 + self.DU
                self.UK = min(OutT - OC, self.UK)
                self.UK = max(OutB - OC, self.UK)
                self.UKOUT = self.UK + OC
                if abs(self.UKOUT - self.LASTUKOUT) > self.OutRH:
                    if self.UKOUT > self.LASTUKOUT:
                        self.UKOUT = self.LASTUKOUT + self.OutRH
                    else:
                        self.UKOUT = self.LASTUKOUT - self.OutRH

                if not (
                    (SADD and (self.UKOUT > self.LASTUKOUT))
                    or (SSUB and (self.UKOUT < self.LASTUKOUT))
                ):
                    self.AV_TEMP = self.UKOUT
                else:
                    # 禁止方向：保留旧 AV_TEMP，仅回写 UK。
                    self.UK = self.AV_TEMP - OC

            # 9.6 自动模式末尾 OutRL 输出更新判定（APCPID-OUTRL-1）
            if abs(self.AV - self.AV_TEMP) > abs(self.OutRL):
                self.AV = self.AV_TEMP

        # ===================== 历史状态更新（先于 PIDZZD1）=====================
        self.DU_1 = self.DU
        self.UK_1 = self.UK
        self.EK_2 = self.EK_1
        self.EK_1 = self.EK
        self.PV_LAST = PV
        self.DEK_2 = self.DEK_1
        self.DEK_1 = self.DEK

        # ===================== PID 自整定（历史更新之后才调用）================
        # 传入本拍经 ATE 处理后的局部 rm，以及可能被 TM 改写为 PV 的局部 sp。
        # 内层 APCPIDZZD 会再调用一次 KZQBDYZMK.step（授权通过总计 2 次）。
        self.PIDZZD1.step(
            dt_ms,
            AV=self.AV,
            SP=sp,
            PV=PV,
            PT=PT,
            TI=TI,
            RM=rm,
            PVMU=self.PVMU,
            PVMD=self.PVMD,
            MU=self.MU,
            MD=self.MD,
            SADD=SADD,
            SSUB=SSUB,
            PT1K=self.PT1K,
            TI1K=self.TI1K,
        )
        # 新 PT1/TI1 在 PID 主计算之后才拷回，故仅下一拍生效（APCPID-ZZD-1）。
        self.PT1 = self.PIDZZD1.PT1
        self.TI1 = self.PIDZZD1.TI1

        # ===== 主程序结束 =====
        return None
