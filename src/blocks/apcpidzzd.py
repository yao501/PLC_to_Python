"""业务块 APCPIDZZD：PID 自整定模块。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCPIDZZD.txt``。

**唯一事实来源是 ST 的实际执行顺序**（不是注释、不是提示词）。源 ST 的两处
"注释 vs 代码"冲突按实际代码实现，并在 ``docs/RISKS.md`` 登记
（``APCPIDZZD-COMMENT-1`` / ``APCPIDZZD-COMMENT-2``）。

整体结构（严格保留顺序）::

    KZQBDYZMK();
    IF (KZQBDYZMK.OK MOD 10000) = 0 THEN          (* 授权通过 *)
        IF PT1K<=0 THEN PT1:=0; END_IF            (* 9.1 顶部清零 *)
        IF TI1K<=0 THEN TI1:=0; END_IF
        IF RM=1 THEN                              (* 9.2 自动状态 *)
            PVMAXDI := ABS(PVMU-PVMD)*0.02;
            TON1(IN:=ABS(PV-SP)>ABS(PVMU-PVMD)*0.005, PT:=T#5S);
            TON2(IN:=ABS(PV-SP)<ABS(PVMU-PVMD)*0.005, PT:=T#5S);
            (* 9.3 死区长期停留清除 *)
            (* 9.4 TON1.Q 真：积算 + 长时间偏差 R_TRIG *)
            (* 9.5 TON1.Q 假：结束一次正/负积算（两个独立 IF） *)
            (* 9.6 收敛识别三档 + 9.7 理论 TI 三档 + 9.8 时间均值 *)
            PT1:=MIN(MAX(PT1,-0.7*PT),PT*3);      (* 9.9 末尾限幅 *)
            TI1:=MIN(MAX(TI1,-0.7*TI),TI*3);
        ELSE                                      (* 9.10 非自动状态精确复位 *)
            JS_Z/JS_F 清零; ZJSBZ:=FALSE; FJSBZ:=FALSE; JSSJ:=0; JSSJ2:=0;
        END_IF
    ELSE                                          (* 授权失败 *)
        BD_ERROR5 := BD_ERROR5 + 1;
        IF BD_ERROR5 > 999999999 THEN BD_ERROR5 := 100000000; END_IF
    END_IF

关键契约：

1. **扫描周期语义绑定 500ms**：源中 ``SQSJ/JSSJ/JSSJ2`` 以**固定 ``+0.5`` 秒**
   推进（不是 ``dt_ms/1000``）。``dt_ms`` 仅驱动 ``TON1/TON2``（``PT=5000ms``）。
   语义只在 ``dt_ms=500`` 下验证；非 500 不拒绝、不静默缩放，仅登记风险
   （``APCPIDZZD-CYCLE-1``）。
2. **授权门控严格保序**：每拍先 ``KZQBDYZMK.step`` 一次，再读 ``OK % 10000``；
   通过才执行全部自整定逻辑；失败只累加 ``BD_ERROR5``，**不**重置/推进/限幅
   任何自整定状态。
3. **复用既有功能块**：``TON`` / ``R_TRIG`` / ``APCHSACCUM`` / ``APCHSHLLIM`` /
   ``LicenseContext.KZQBDYZMK``，不重写、不复制实现。
4. **``HSACCUM1(RS:=TRUE)`` 保留上次 ``I1``**：CODESYS 中省略输入脚保持上次值；
   既有 ``APCHSACCUM.step`` 默认 ``I1=0.0``，故本块记忆上次传入的
   ``ABS(PV-SP)`` 并在复位调用时显式回传，等价复现 CODESYS 输入保持语义
   （不修改 ``APCHSACCUM`` 本体）。由此在 TON1 延时窗口内会出现"积算值按保留
   ``I1`` 持续增长"——这是**源级语义推导**（依据 ST 调用路径 + CODESYS 输入
   保持 + 已锁定 ``APCHSACCUM`` 源逻辑），**尚未以真实 SoftPLC 实机轨迹做黄金
   对照**（见 ``docs/RISKS.md::APCPIDZZD-ACCUM-1``）。
5. **离散积算非 dt 积分**：积算面积是每拍 ``ABS(PV-SP)`` 的离散累计。
6. **两个独立 IF 不合并**：``PV>SP`` / ``PV<SP``、``ZJSBZ`` / ``FJSBZ`` 均为独立
   ``IF``，不得改成 ``elif`` 改变边界语义。
7. **严格阈值**：``>`` / ``<`` 不改成 ``>=`` / ``<=``。

参考 ``docs/RISKS.md`` 的 ``APCPIDZZD-*`` 条目。
"""

from __future__ import annotations

from src.globals.license_context import LicenseContext
from src.primitives.edges import R_TRIG
from src.primitives.timers import TON

from .apchsaccum import APCHSACCUM
from .apchshllim import APCHSHLLIM

# TON1 / TON2 的 PT 初值 = 5 秒（源 ST ``T#5S``）。
PT_5S_MS = 5000


def _new_3x3() -> list[list[float]]:
    """1-based 4x4 容器（索引 0 弃用），表达 ST ``ARRAY[1..3,1..3] OF REAL``。"""
    return [[0.0] * 4 for _ in range(4)]


class APCPIDZZD:
    """PID 自整定功能块。携带跨扫描状态，必须注入 :class:`LicenseContext`。

    构造::

        APCPIDZZD(license_context)

    单周期推进::

        step(dt_ms, *, AV, SP, PV, PT, TI, RM=1, PVMU, PVMD, MU, MD,
             SADD, SSUB, PT1K=1.0, TI1K=1.0) -> None

    输出为实例属性 ``PT1`` / ``TI1``。授权失败时不修改任何自整定状态，只
    累加 ``license_context.BD_ERROR5``。
    """

    def __init__(self, license_context: LicenseContext) -> None:
        self._ctx = license_context

        # VAR_OUTPUT：自适应增量，初值 0。
        self.PT1: float = 0.0
        self.TI1: float = 0.0

        # 复用既有功能块实例（跨周期状态各自保持）。
        self.TON1 = TON()
        self.TON2 = TON()
        self.HSACCUM1 = APCHSACCUM()
        self.HLLIM1 = APCHSHLLIM()
        self.R_TRIG1 = R_TRIG()
        self.R_TRIG2 = R_TRIG()

        # 正/负积算历史数组（1-based）：
        #   [1,*]=积算面积历史  [2,*]=积算时间历史  [3,*]=期间最大偏差历史
        self.JS_Z = _new_3x3()
        self.JS_F = _new_3x3()

        # VAR 状态（初值严格对应 ST 声明）。
        self.ZJSBZ: bool = True
        self.FJSBZ: bool = True
        self.SLL11: float = 0.0
        self.SLL12: float = 0.0
        self.SLL21: float = 0.0
        self.SLL22: float = 0.0
        self.JSSJ: float = 0.0
        self.JSSJ2: float = 0.0
        self.ZDPC: float = 0.0
        self.SBCGBZ: bool = False
        self.PVMAXDI: float = 0.0
        self.JSTI: float = 0.0
        self.JSSJZ: float = 20.0
        self.JSSJF: float = 20.0
        self.SQSJ: float = 0.0
        self.N: int = 0
        self.M: int = 0

        # 复现 CODESYS "HSACCUM1 的 I1 输入脚保持上次值"：记忆上次显式传入的
        # ABS(PV-SP)，复位调用 (RS:=TRUE) 时回传，而不是默认 0。冷启动 = 0.0。
        self._hsaccum_last_I1: float = 0.0

    def step(
        self,
        dt_ms: int,
        *,
        AV: float,
        SP: float,
        PV: float,
        PT: float,
        TI: float,
        RM: int = 1,
        PVMU: float,
        PVMD: float,
        MU: float,
        MD: float,
        SADD: bool,
        SSUB: bool,
        PT1K: float = 1.0,
        TI1K: float = 1.0,
    ) -> None:
        # ---- 授权门控（严格保序，每拍调用一次）----
        self._ctx.KZQBDYZMK.step(dt_ms)
        if self._ctx.KZQBDYZMK.OK % 10000 != 0:
            # 验证未成功：只累加 BD_ERROR5，不动任何自整定状态。
            self._ctx.BD_ERROR5 = self._ctx.BD_ERROR5 + 1
            if self._ctx.BD_ERROR5 > 999999999:
                self._ctx.BD_ERROR5 = 100000000.0
            return

        # ===== 主程序开始 =====

        # 9.1 PT1K / TI1K 顶部处理（仅本拍此处清零；后续识别分支仍可能改写）。
        if PT1K <= 0:
            self.PT1 = 0.0
        if TI1K <= 0:
            self.TI1 = 0.0

        if RM == 1:
            # 9.2 自动状态
            self.PVMAXDI = abs(PVMU - PVMD) * 0.02
            self.TON1.step(dt_ms, abs(PV - SP) > abs(PVMU - PVMD) * 0.005, PT_5S_MS)
            self.TON2.step(dt_ms, abs(PV - SP) < abs(PVMU - PVMD) * 0.005, PT_5S_MS)

            # 9.3 死区长期停留清除
            if self.TON2.Q:
                self.SQSJ = self.SQSJ + 0.5
            else:
                self.SQSJ = 0.0
            if self.SQSJ > max((self.JSSJZ + self.JSSJF) / 4, 10):
                for n in range(1, 4):
                    for m in range(1, 4):
                        self.JS_Z[n][m] = 0.0
                        self.JS_F[n][m] = 0.0
                self.SQSJ = 0.0

            if self.TON1.Q:
                # 9.4 积算 + 长时间偏差处理
                if PV > SP:
                    self.ZJSBZ = True
                    self.FJSBZ = False
                if PV < SP:
                    self.FJSBZ = True
                    self.ZJSBZ = False

                self._hsaccum_last_I1 = abs(PV - SP)
                self.HSACCUM1.step(dt_ms, I1=self._hsaccum_last_I1, RS=False)
                self.JSSJ = self.JSSJ + 0.5
                self.ZDPC = max(self.ZDPC, abs(PV - SP))

                if (
                    AV < (MU - (MU - MD) * 0.01)
                    and AV > (MD + (MU - MD) * 0.01)
                    and not SADD
                    and not SSUB
                ):
                    self.JSSJ2 = self.JSSJ2 + 0.5
                else:
                    self.JSSJ2 = 0.0

                self.R_TRIG1.step(self.JSSJ2 > (self.JSSJZ + self.JSSJF) * 2.5)
                if self.R_TRIG1.Q:
                    self.TI1 = max((TI + self.TI1) * 0.8 - TI, -0.7 * TI)
                self.R_TRIG2.step(self.JSSJ2 > (self.JSSJZ + self.JSSJF) * 4)
                if self.R_TRIG2.Q:
                    self.TI1 = max((TI + self.TI1) * 0.8 - TI, -0.7 * TI)
            else:
                # 9.5 结束一次正/负积算（两个独立 IF，不合并）
                if self.ZJSBZ:
                    if self.JSSJ > 5:
                        self._shift_and_identify(self.JS_Z, PT, TI, PT1K, TI1K, dt_ms)
                        self._update_mean_z(dt_ms)
                if self.FJSBZ:
                    if self.JSSJ > 5:
                        self._shift_and_identify(self.JS_F, PT, TI, PT1K, TI1K, dt_ms)
                        self._update_mean_f(dt_ms)

                # 在写入当前面积之后才复位 HSACCUM1（保留上次 I1 输入语义）。
                self.HSACCUM1.step(dt_ms, I1=self._hsaccum_last_I1, RS=True)
                self.JSSJ = 0.0
                self.JSSJ2 = 0.0
                self.ZDPC = 0.0
                self.ZJSBZ = False
                self.FJSBZ = False

            # 9.9 自动状态末尾限幅
            self.PT1 = min(max(self.PT1, -0.7 * PT), PT * 3)
            self.TI1 = min(max(self.TI1, -0.7 * TI), TI * 3)
        else:
            # 9.10 非自动状态：仅精确复位以下状态，其余一律保留。
            for n in range(1, 4):
                for m in range(1, 4):
                    self.JS_Z[n][m] = 0.0
                    self.JS_F[n][m] = 0.0
            self.ZJSBZ = False
            self.FJSBZ = False
            self.JSSJ = 0.0
            self.JSSJ2 = 0.0

        # ===== 主程序结束 =====
        return None

    def _shift_and_identify(
        self,
        arr: list[list[float]],
        PT: float,
        TI: float,
        PT1K: float,
        TI1K: float,
        dt_ms: int,
    ) -> None:
        """对一组积算历史（``JS_Z`` 或 ``JS_F``）执行：历史移位 → 收敛识别
        三档调整（9.6）→ 理论 TI 三档（9.7）。

        正负两条路径在 ST 中逐字相同，仅作用数组不同；识别成功的清零始终同时
        清 ``JS_Z[1,1/1,2]`` 与 ``JS_F[1,1/1,2]``（与 ST 一致）。
        """
        del dt_ms  # 本段无定时器；dt_ms 不参与。

        # 历史移位：[1] <- [2] <- [3] <- 当前。
        arr[1][1] = arr[1][2]
        arr[1][2] = arr[1][3]
        arr[1][3] = self.HSACCUM1.AV

        arr[2][1] = arr[2][2]
        arr[2][2] = arr[2][3]
        arr[2][3] = self.JSSJ

        arr[3][1] = arr[3][2]
        arr[3][2] = arr[3][3]
        arr[3][3] = self.ZDPC

        # 9.6 利用积算面积计算收敛率并按优先级减弱比例/积分作用。
        self.SLL11 = arr[1][2] / max(arr[1][1], 0.00001)
        self.SLL12 = arr[1][3] / max(arr[1][2], 0.00001)

        if (
            self.SLL11 < 10
            and self.SLL12 < 10
            and arr[3][1] > self.PVMAXDI
            and arr[3][2] > self.PVMAXDI
            and arr[3][3] > self.PVMAXDI
        ):
            self.SBCGBZ = False
            if self.SLL11 > 1.1 and self.SLL12 > 1.1:
                self.PT1 = (PT + self.PT1) * (1 + PT1K) - PT
                self.TI1 = (TI + self.TI1) * (1 + TI1K) - TI
                self.SBCGBZ = True
            if not self.SBCGBZ and self.SLL11 > 0.7 and self.SLL12 > 0.7:
                self.PT1 = (PT + self.PT1) * (1 + PT1K * 0.5) - PT
                self.TI1 = (TI + self.TI1) * (1 + TI1K * 0.5) - TI
                self.SBCGBZ = True
            if not self.SBCGBZ and self.SLL11 > 0.4 and self.SLL12 > 0.4:
                self.PT1 = (PT + self.PT1) * (1 + PT1K * 0.1) - PT
                self.TI1 = (TI + self.TI1) * (1 + TI1K * 0.1) - TI
                self.SBCGBZ = True

        if self.SBCGBZ:
            self.JS_Z[1][1] = 0.0
            self.JS_Z[1][2] = 0.0
            self.JS_F[1][1] = 0.0
            self.JS_F[1][2] = 0.0
            self.SBCGBZ = False

        # 9.7 利用最大偏差收敛率计算理论 TI 并向其靠拢。
        self.SLL21 = arr[3][2] / max(arr[3][1], 0.00001)
        self.SLL22 = arr[3][3] / max(arr[3][2], 0.00001)

        self.JSTI = -1.0
        if 0.9 < self.SLL21 < 1.1 and 0.9 < self.SLL22 < 1.1:
            self.JSTI = (arr[2][1] + arr[2][2]) * 0.5
        if 0.5 < self.SLL21 < 0.7 and 0.5 < self.SLL22 < 0.7:
            self.JSTI = (arr[2][1] + arr[2][2]) * 0.4
        if 0.1 < self.SLL21 < 0.3 and 0.1 < self.SLL22 < 0.3:
            self.JSTI = (arr[2][1] + arr[2][2]) * 0.3
        if self.JSTI > 0:
            self.TI1 = (
                (TI + self.TI1) * 0.8
                + min(max(self.JSTI, (TI + self.TI1) * 0.5), (TI + self.TI1) * 2) * 0.2
                - TI
            )
            self.JSTI = -1.0

    def _update_mean_z(self, dt_ms: int) -> None:
        """9.8 统计正积算时间均值。"""
        self.HLLIM1.step(
            dt_ms, IN=self.JSSJ, HL=self.JSSJZ * 1.5, LL=self.JSSJZ * 0.5
        )
        self.JSSJZ = self.JSSJZ * 0.95 + 0.05 * self.HLLIM1.AV

    def _update_mean_f(self, dt_ms: int) -> None:
        """9.8 统计负积算时间均值（源注释误写"正积算"，实际为负积算路径）。"""
        self.HLLIM1.step(
            dt_ms, IN=self.JSSJ, HL=self.JSSJF * 1.5, LL=self.JSSJF * 0.5
        )
        self.JSSJF = self.JSSJF * 0.95 + 0.05 * self.HLLIM1.AV
