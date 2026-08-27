"""业务块 APCRSFNAUTOPARA：RSFN 自动参数推荐功能块。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCRSFNAUTOPARA.txt``。

**唯一事实来源是 ST 的实际执行顺序**（不是注释、不是提示词）。

**模块定位**：面向未来的 ``APCRSFN`` 软伺服模块，通过窗口统计 + 历史窗口融合
推荐 RSF 相关参数（``TL/TL1~TL4/E1~E4/AO1~AO4/RSF_LOCK_T/RSF_HYS/
RSF_FAST_HYS/RSF_TLOUT_K/ZF_K``）。推荐值仅输出为 ``*_REC``，**不**写入
``APCRSFN`` 实际参数；**不**接 :class:`LicenseContext`、**不**新增授权门控、
**不**读系统时钟。复用既有 :class:`APCSPFINDER` 实例获取分析 SP，**不**重写
SP 自动寻找逻辑。

关键契约（详见 ``docs/RISKS.md`` 五-L）：

* **APCRSFNAUTOPARA-CYCLE-1**：窗口/面积/统计时间严格由 ``CYCLE_S=MAX(CYCLE,
  0.001)`` 推进；``dt_ms`` 仅为统一接口保留并转发给 ``SPF1``（其内部 ``del
  dt_ms`` 忽略），**不**参与任何累计、**不**用 ``dt_ms/1000`` 替换。
* **APCRSFNAUTOPARA-RANGE-1**：输出量程无效时内部 ``OUT_RANGE`` 临时取 100 以
  继续计算，但 ``RANGE_OK`` 仍为 False 并阻止窗口成为有效数据。
* **APCRSFNAUTOPARA-RESET-1**（已随修复版基线更新）：RESET 块现已清零
  ``WIN_SP_SUM/WIN_PV_SUM/WIN_AV_SUM``（修复版新增三行）；中途 RESET 后下一
  窗口均值不再带入残留累计。原始基线曾**未**清这三项（注释与实现冲突的源缺陷），
  现按 ChatGPT5.5 修复版基线同步为已修复。
* **APCRSFNAUTOPARA-RUNNING-1**：``RUNNING:=EN`` 直接镜像 EN；``EN=True`` 且
  ``RESET=True`` 时 ``RUNNING=True`` 但数据采集分支不执行。
* **APCRSFNAUTOPARA-SPFINDER-1**：仅复用 ``APCSPFINDER`` 获取分析 ``SP_USE``，
  不写入现场控制 SP 或 ``APCRSFN`` 实际设定值；内部偏差用 ``SP_WORK``（无效
  时为 PV）。
* **APCRSFNAUTOPARA-FUSION-1**：有效窗口先入历史、后参与同拍三阶段相似融合；
  弱推荐不等于 ``FINAL_VALID``，弱推荐时 ``RSF_REASON`` 最终置为 5。
* **APCRSFNAUTOPARA-CALC-1**：``CALC_NOW`` 仅上升沿可提前结算；``CALC_OLD`` 在
  ``EN=False/RESET=True`` 时仍于每拍末尾更新。
* **APCRSFNAUTOPARA-DATAREASON-1**：``DATA_REASON=2``（时间不足）在结算点不可达——
  两个窗口结算条件都要求 ``WIN_ELAPSED>=MIN_WIN_T``，故进入结算 DATA_REASON 判断时
  ``ELSIF WIN_ELAPSED<MIN_WIN_T → 2`` 永远为假（死分支）。这是源代码遗留的诊断/可观测
  性缺陷，**不**影响推荐参数计算，且 ``DATA_REASON/WINDOW_T/ERR_*`` 等均为"最近完成
  窗口快照"语义。按源码原样保留分支、**不**删除、**不**改为实时写入（详见 RISKS：
  ChatGPT5.5 修复版的 Bug2 实时补丁会破坏快照一致性，已撤回不同步；若将来需要画面显示
  "当前窗口还差多少时间"，应单独新增 ``CURRENT_WINDOW_T/CURRENT_DATA_REASON`` 实时输出，
  不复用本快照组）。

执行顺序严格保留：基础限幅(273-288) → 历史数量变化(290-307) → 推荐默认回退
(309-329) → RESET/冷启动初始化(331-399) → ``RUNNING:=EN``(401) →
``IF EN AND NOT RESET`` 采集/窗口结算(404-906) → 末尾状态更新(909-915)。
``ERR/ABS_ERR/D_PV/D_AV`` 仅在采集块内重算，EN=False 时保留旧值，末尾
``ERR_1:=ERR`` 用旧 ``ERR``——均按源码保留。
"""

from __future__ import annotations

from .apcspfinder import APCSPFINDER


class APCRSFNAUTOPARA:
    """RSFN 自动参数推荐功能块。携带跨扫描状态，内含真实 ``APCSPFINDER`` 子实例。"""

    def __init__(self) -> None:
        # ===== VAR_OUTPUT =====
        self.RUNNING: bool = False
        self.WINDOW_DONE: bool = False
        self.FINAL_VALID: bool = False
        self.FINAL_STRONG: bool = False
        self.FINAL_WEAK: bool = False
        self.MATCH_LEVEL: int = 0
        self.WINDOW_VALID: bool = False
        self.DATA_REASON: int = 0
        self.SP_USE: float = 0.0
        self.SP_AUTO: float = 0.0
        self.SP_VALID: bool = False
        self.SP_AUTO_OK: bool = False
        self.SP_TAG_BAD: bool = False
        self.SP_SOURCE: int = 0
        self.SP_REASON: int = 0
        self.SP_AUTO_CONF: float = 0.0
        self.SP_STABLE_T_OUT: float = 0.0
        self.RSF_OK: bool = False
        self.RSF_REASON: int = 0

        self.HISTORY_COUNT: float = 0.0
        self.SIMILAR_COUNT: float = 0.0
        self.FUSE_WEIGHT: float = 0.0
        self.WINDOW_EVENT_N: float = 0.0
        self.WINDOW_T: float = 0.0
        self.AUTO_SAMPLE_T: float = 0.0
        self.MAN_EVENT_N: float = 0.0
        self.CROSS_COUNT: float = 0.0
        self.RSF_TRIGGER_N: float = 0.0
        self.RSF_LOCK_N: float = 0.0
        self.ERR_ABS_AVG: float = 0.0
        self.ERR_AREA_POS: float = 0.0
        self.ERR_AREA_NEG: float = 0.0
        self.ERR_PEAK_ABS: float = 0.0
        self.AVG_CROSS_T: float = 0.0
        self.PV_DELTA: float = 0.0
        self.AV_DELTA: float = 0.0
        self.NOISE_EST: float = 0.0
        self.PROCESS_GAIN: float = 0.0

        self.TL_REC: float = 0.0
        self.TL1_REC: float = 0.0
        self.TL2_REC: float = 0.0
        self.TL3_REC: float = 0.0
        self.TL4_REC: float = 0.0
        self.E1_REC: float = 0.0
        self.E2_REC: float = 0.0
        self.E3_REC: float = 0.0
        self.E4_REC: float = 0.0
        self.AO1_REC: float = 0.0
        self.AO2_REC: float = 0.0
        self.AO3_REC: float = 0.0
        self.AO4_REC: float = 0.0
        self.RSF_LOCK_T_REC: float = 0.0
        self.RSF_HYS_REC: float = 0.0
        self.RSF_FAST_HYS_REC: float = 0.0
        self.RSF_TLOUT_K_REC: float = 0.0
        self.ZF_K_REC: float = 0.0

        # ===== VAR（内部状态，跨扫描保留）=====
        self.INIT_DONE: bool = False
        self.CALC_OLD: bool = False
        self.CALC_R: bool = False
        self.CYCLE_S: float = 0.0
        self.OUT_RANGE: float = 0.0
        self.OUT_RANGE_USE: float = 0.0
        self.RANGE_OK: bool = False
        self.H_N: int = 0
        self.H_N_OLD: int = 0
        self.MATCH_STAGE: int = 0
        self.BLEND: float = 0.0
        self.MAN_TH: float = 0.0
        self.SPF1: APCSPFINDER = APCSPFINDER()
        self.SP_WORK: float = 0.0

        self.ERR: float = 0.0
        self.ERR_1: float = 0.0
        self.ABS_ERR: float = 0.0
        self.D_PV: float = 0.0
        self.D_AV: float = 0.0
        self.PV_1: float = 0.0
        self.AV_1: float = 0.0
        self.TP_1: float = 0.0
        self.RSF_LEVEL_1: float = 0.0
        self.RSF_LOCK_LEVEL_1: float = 0.0
        self.AUTO_SAMPLE: bool = False
        self.MAN_SAMPLE: bool = False
        self.SAMPLE_OK: bool = False

        self.WIN_ELAPSED: float = 0.0
        self.WIN_AUTO_T: float = 0.0
        self.WIN_MAN_T: float = 0.0
        self.WIN_N: float = 0.0
        self.WIN_SP_SUM: float = 0.0
        self.WIN_PV_SUM: float = 0.0
        self.WIN_AV_SUM: float = 0.0
        self.WIN_ERR_ABS_SUM: float = 0.0
        self.WIN_ERR_AREA_POS: float = 0.0
        self.WIN_ERR_AREA_NEG: float = 0.0
        self.WIN_ERR_PEAK_ABS: float = 0.0
        self.WIN_PV_MAX: float = 0.0
        self.WIN_PV_MIN: float = 0.0
        self.WIN_AV_MAX: float = 0.0
        self.WIN_AV_MIN: float = 0.0
        self.WIN_INIT: bool = False
        self.WIN_NOISE_SUM: float = 0.0
        self.WIN_NOISE_N: float = 0.0
        self.WIN_CROSS_N: float = 0.0
        self.WIN_SEG_T: float = 0.0
        self.WIN_SEG_T_SUM: float = 0.0
        self.WIN_RSF_EVENT_N: float = 0.0
        self.WIN_MAN_EVENT_N: float = 0.0
        self.WIN_LOCK_N: float = 0.0
        self.WIN_ZONE1_T: float = 0.0
        self.WIN_ZONE2_T: float = 0.0
        self.WIN_ZONE3_T: float = 0.0
        self.WIN_ZONE4_T: float = 0.0
        self.WIN_SP_AVG: float = 0.0
        self.WIN_PV_AVG: float = 0.0
        self.WIN_AV_AVG: float = 0.0
        self.WIN_ERR_AVG: float = 0.0
        self.WIN_PV_DELTA: float = 0.0
        self.WIN_AV_DELTA: float = 0.0
        self.WIN_WEIGHT: float = 0.0
        self.WIN_SCALE: float = 0.0

        self.W_TL: float = 0.0
        self.W_TL1: float = 0.0
        self.W_TL2: float = 0.0
        self.W_TL3: float = 0.0
        self.W_TL4: float = 0.0
        self.W_E1: float = 0.0
        self.W_E2: float = 0.0
        self.W_E3: float = 0.0
        self.W_E4: float = 0.0
        self.W_AO1: float = 0.0
        self.W_AO2: float = 0.0
        self.W_AO3: float = 0.0
        self.W_AO4: float = 0.0
        self.W_RSF_LOCK_T: float = 0.0
        self.W_RSF_HYS: float = 0.0
        self.W_RSF_FAST_HYS: float = 0.0
        self.W_RSF_TLOUT_K: float = 0.0
        self.W_ZF_K: float = 0.0
        self.W_RESP_T: float = 0.0
        self.W_GAIN: float = 0.0
        self.W_BASE_E: float = 0.0
        self.W_OSC: bool = False
        self.W_SLOW: bool = False
        self.W_NOISE_HIGH: bool = False

        self.SIM_SP_TH: float = 0.0
        self.SIM_PV_TH: float = 0.0
        self.SIM_AV_TH: float = 0.0
        self.SIM_ERR_TH: float = 0.0
        self.SIM_RELAX_USE: float = 0.0
        self.FUSE_SUM_W: float = 0.0
        self.FUSE_W: float = 0.0
        self.FUSE_STRONG: bool = False

        # 历史数组：1-based，index 0 不使用（对应 ST ARRAY[1..24]）。
        self.H_IDX: int = 1
        self.H_VALID = [False] * 25
        self.H_WEIGHT = [0.0] * 25
        self.H_SP = [0.0] * 25
        self.H_PV = [0.0] * 25
        self.H_AV = [0.0] * 25
        self.H_ERR = [0.0] * 25
        self.H_TL = [0.0] * 25
        self.H_TL1 = [0.0] * 25
        self.H_TL2 = [0.0] * 25
        self.H_TL3 = [0.0] * 25
        self.H_TL4 = [0.0] * 25
        self.H_E1 = [0.0] * 25
        self.H_E2 = [0.0] * 25
        self.H_E3 = [0.0] * 25
        self.H_E4 = [0.0] * 25
        self.H_AO1 = [0.0] * 25
        self.H_AO2 = [0.0] * 25
        self.H_AO3 = [0.0] * 25
        self.H_AO4 = [0.0] * 25
        self.H_RSF_LOCK_T = [0.0] * 25
        self.H_RSF_HYS = [0.0] * 25
        self.H_RSF_FAST_HYS = [0.0] * 25
        self.H_RSF_TLOUT_K = [0.0] * 25
        self.H_ZF_K = [0.0] * 25

    def step(
        self,
        dt_ms: int,
        *,
        EN: bool,
        RESET: bool,
        CALC_NOW: bool,
        CYCLE: float = 0.5,
        COLLECT_MODE: int = 1,
        SP: float,
        SP_MAN: float = 0.0,
        SP_MAN_EN: bool = False,
        SP_TAG_EN: bool = True,
        SP_AUTO_EN: bool = True,
        SP_AUTO_REPLACE_BAD_TAG: bool = False,
        SP_STABLE_T: float = 300.0,
        SP_CONF_T: float = 900.0,
        SP_PV_STABLE_ABS: float = 0.0,
        SP_AV_STABLE_ABS: float = 0.0,
        PV: float,
        AV: float,
        TP: float,
        TS: bool,
        MU: float = 100.0,
        MD: float = 0.0,
        PHY_RANGE_EN: bool = False,
        PHY_MU: float = 100.0,
        PHY_MD: float = 0.0,
        RSF_LEVEL: float,
        RSF_LOCK_LEVEL_IN: float,
        RSF_STEP: float,
        WIN_T: float = 7200.0,
        MIN_WIN_T: float = 300.0,
        MIN_STORE_EVENT: float = 1.0,
        MIN_VALID_EVENT: float = 5.0,
        HISTORY_N: int = 24,
        FUSE_MIN_N: float = 3.0,
        FUSE_MIN_WEIGHT: float = 3.0,
        SIM_SP_K: float = 0.05,
        SIM_PV_K: float = 0.05,
        SIM_AV_K: float = 0.10,
        SIM_ERR_K: float = 0.05,
        SIM_SP_ABS: float = 0.0,
        SIM_PV_ABS: float = 0.0,
        SIM_AV_ABS: float = 0.0,
        SIM_ERR_ABS: float = 0.0,
        SIM_RELAX_K: float = 2.0,
        MAN_AV_MIN: float = 0.1,
        AO_GAIN_K: float = 0.5,
        REC_BLEND: float = 0.7,
        TL_IN: float = 10.0,
        TL1_IN: float = 60.0,
        TL2_IN: float = 60.0,
        TL3_IN: float = 60.0,
        TL4_IN: float = 60.0,
        E1_IN: float = 1.0,
        E2_IN: float = 2.0,
        E3_IN: float = 3.0,
        E4_IN: float = 4.0,
        AO1_IN: float = 1.0,
        AO2_IN: float = 2.0,
        AO3_IN: float = 3.0,
        AO4_IN: float = 4.0,
        RSF_LOCK_T_IN: float = 30.0,
        RSF_HYS_IN: float = 0.8,
        RSF_FAST_HYS_IN: float = 0.5,
        RSF_TLOUT_K_IN: float = 0.5,
        ZF_K_IN: float = 0.0,
    ) -> None:
        # ---- 基础状态和安全限幅（273-288，每拍无条件）----
        self.CALC_R = CALC_NOW and (not self.CALC_OLD)
        self.WINDOW_DONE = False
        self.CYCLE_S = max(CYCLE, 0.001)
        if PHY_RANGE_EN:
            self.OUT_RANGE_USE = abs(PHY_MU - PHY_MD)
        else:
            self.OUT_RANGE_USE = abs(MU - MD)
        self.OUT_RANGE = self.OUT_RANGE_USE
        self.RANGE_OK = self.OUT_RANGE > 0
        if self.OUT_RANGE <= 0:
            self.OUT_RANGE = 100
        self.H_N = min(max(HISTORY_N, 1), 24)
        self.BLEND = min(max(REC_BLEND, 0), 1)
        self.MAN_TH = max(MAN_AV_MIN, self.OUT_RANGE * 0.0005)

        # ---- 历史窗口数量变化处理（290-307）----
        if not self.INIT_DONE:
            self.H_N_OLD = self.H_N
        if self.INIT_DONE and (self.H_N != self.H_N_OLD):
            if self.H_N < self.H_N_OLD:
                for i in range(self.H_N + 1, 25):
                    self.H_VALID[i] = False
                if self.HISTORY_COUNT > float(self.H_N):
                    self.HISTORY_COUNT = float(self.H_N)
                if self.H_IDX > self.H_N:
                    self.H_IDX = 1
            self.H_N_OLD = self.H_N

        # ---- 推荐值默认回退到当前参数（309-329）----
        if (self.HISTORY_COUNT == 0) or RESET or (not self.INIT_DONE):
            self.TL_REC = float(max(TL_IN, 0))
            self.TL1_REC = float(max(TL1_IN, self.TL_REC + 1))
            self.TL2_REC = float(max(TL2_IN, self.TL_REC + 1))
            self.TL3_REC = float(max(TL3_IN, self.TL_REC + 1))
            self.TL4_REC = float(max(TL4_IN, self.TL_REC + 1))
            self.E1_REC = float(max(E1_IN, 0.001))
            self.E2_REC = float(max(E2_IN, self.E1_REC))
            self.E3_REC = float(max(E3_IN, self.E2_REC))
            self.E4_REC = float(max(E4_IN, self.E3_REC))
            self.AO1_REC = float(max(AO1_IN, 0))
            self.AO2_REC = float(max(AO2_IN, self.AO1_REC))
            self.AO3_REC = float(max(AO3_IN, self.AO2_REC))
            self.AO4_REC = float(max(AO4_IN, self.AO3_REC))
            self.RSF_LOCK_T_REC = float(max(RSF_LOCK_T_IN, 0))
            self.RSF_HYS_REC = float(max(min(RSF_HYS_IN, 1), 0.1))
            self.RSF_FAST_HYS_REC = float(
                max(min(RSF_FAST_HYS_IN, self.RSF_HYS_REC), 0.01))
            self.RSF_TLOUT_K_REC = float(max(min(RSF_TLOUT_K_IN, 1), 0))
            self.ZF_K_REC = float(max(min(ZF_K_IN, 1), 0))

        # ---- 复位处理 / 冷启动初始化（331-399）----
        if RESET or (not self.INIT_DONE):
            self.RUNNING = False
            self.WINDOW_DONE = False
            self.FINAL_VALID = False
            self.FINAL_STRONG = False
            self.FINAL_WEAK = False
            self.MATCH_LEVEL = 0
            self.WINDOW_VALID = False
            self.DATA_REASON = 0
            self.SP_USE = PV
            self.SP_AUTO = 0.0
            self.SP_VALID = False
            self.SP_AUTO_OK = False
            self.SP_TAG_BAD = False
            self.SP_SOURCE = 0
            self.SP_REASON = 0
            self.SP_AUTO_CONF = 0.0
            self.SP_STABLE_T_OUT = 0.0
            self.SPF1.step(
                dt_ms,
                EN=False,
                RESET=True,
                CYCLE=self.CYCLE_S,
                SAMPLE_OK=False,
                SP_TAG=SP,
                SP_TAG_EN=SP_TAG_EN,
                PV=PV,
                AV=AV,
                PVMU=max(E4_IN * 2, 1),
                PVMD=0.0,
                OUTT=self.OUT_RANGE,
                OUTB=0.0,
            )
            self.RSF_OK = False
            self.RSF_REASON = 0
            self.HISTORY_COUNT = 0.0
            self.SIMILAR_COUNT = 0.0
            self.FUSE_WEIGHT = 0.0
            self.WINDOW_EVENT_N = 0.0
            self.WINDOW_T = 0.0
            self.AUTO_SAMPLE_T = 0.0
            self.MAN_EVENT_N = 0.0
            self.CROSS_COUNT = 0.0
            self.RSF_TRIGGER_N = 0.0
            self.RSF_LOCK_N = 0.0
            self.ERR_ABS_AVG = 0.0
            self.ERR_AREA_POS = 0.0
            self.ERR_AREA_NEG = 0.0
            self.ERR_PEAK_ABS = 0.0
            self.AVG_CROSS_T = 0.0
            self.PV_DELTA = 0.0
            self.AV_DELTA = 0.0
            self.NOISE_EST = 0.0
            self.PROCESS_GAIN = 0.0
            self.WIN_ELAPSED = 0.0
            self.WIN_AUTO_T = 0.0
            self.WIN_MAN_T = 0.0
            self.WIN_N = 0.0
            self.WIN_SP_SUM = 0.0  # 修复：RESET 清窗口 SP 累计，避免污染下一窗口均值
            self.WIN_PV_SUM = 0.0  # 修复：RESET 清窗口 PV 累计，避免污染下一窗口均值
            self.WIN_AV_SUM = 0.0  # 修复：RESET 清窗口 AV 累计，避免污染下一窗口均值
            self.WIN_INIT = False
            self.WIN_ERR_ABS_SUM = 0.0
            self.WIN_ERR_AREA_POS = 0.0
            self.WIN_ERR_AREA_NEG = 0.0
            self.WIN_ERR_PEAK_ABS = 0.0
            self.WIN_NOISE_SUM = 0.0
            self.WIN_NOISE_N = 0.0
            self.WIN_CROSS_N = 0.0
            self.WIN_SEG_T = 0.0
            self.WIN_SEG_T_SUM = 0.0
            self.WIN_RSF_EVENT_N = 0.0
            self.WIN_MAN_EVENT_N = 0.0
            self.WIN_LOCK_N = 0.0
            self.WIN_ZONE1_T = 0.0
            self.WIN_ZONE2_T = 0.0
            self.WIN_ZONE3_T = 0.0
            self.WIN_ZONE4_T = 0.0
            self.H_IDX = 1
            for i in range(1, 25):
                self.H_VALID[i] = False
                self.H_WEIGHT[i] = 0.0
            self.INIT_DONE = True

        self.RUNNING = EN  # （401）镜像 EN（APCRSFNAUTOPARA-RUNNING-1）

        # ---- 数据采集（404-906）----
        if EN and (not RESET):
            self.AUTO_SAMPLE = ((COLLECT_MODE == 0) or (COLLECT_MODE == 1)) and (not TS)
            self.MAN_SAMPLE = ((COLLECT_MODE == 1) or (COLLECT_MODE == 2)) and TS
            self.SAMPLE_OK = self.AUTO_SAMPLE or self.MAN_SAMPLE

            self.SPF1.step(
                dt_ms,
                EN=EN,
                RESET=RESET,
                CYCLE=self.CYCLE_S,
                SAMPLE_OK=self.SAMPLE_OK,
                SP_MAN=SP_MAN,
                SP_MAN_EN=SP_MAN_EN,
                SP_TAG=SP,
                SP_TAG_EN=SP_TAG_EN,
                SP_AUTO_EN=SP_AUTO_EN,
                SP_AUTO_REPLACE_BAD_TAG=SP_AUTO_REPLACE_BAD_TAG,
                PV=PV,
                AV=AV,
                PVMU=max(E4_IN * 2, 1),
                PVMD=0.0,
                OUTT=self.OUT_RANGE,
                OUTB=0.0,
                SP_STABLE_T=SP_STABLE_T,
                SP_CONF_T=SP_CONF_T,
                PV_STABLE_ABS=SP_PV_STABLE_ABS,
                AV_STABLE_ABS=SP_AV_STABLE_ABS,
            )
            self.SP_USE = self.SPF1.SP_USE
            self.SP_AUTO = self.SPF1.SP_AUTO
            self.SP_VALID = self.SPF1.SP_VALID
            self.SP_AUTO_OK = self.SPF1.SP_AUTO_OK
            self.SP_TAG_BAD = self.SPF1.SP_TAG_BAD
            self.SP_SOURCE = self.SPF1.SP_SOURCE
            self.SP_REASON = self.SPF1.SP_REASON
            self.SP_AUTO_CONF = self.SPF1.SP_AUTO_CONF
            self.SP_STABLE_T_OUT = self.SPF1.SP_STABLE_T_OUT
            self.SP_WORK = self.SP_USE
            if not self.SP_VALID:
                self.SP_WORK = PV

            self.ERR = self.SP_WORK - PV
            self.ABS_ERR = abs(self.ERR)
            self.D_PV = abs(PV - self.PV_1)
            self.D_AV = abs(AV - self.AV_1)

            if self.SAMPLE_OK:
                if not self.WIN_INIT:
                    self.WIN_PV_MAX = PV
                    self.WIN_PV_MIN = PV
                    self.WIN_AV_MAX = AV
                    self.WIN_AV_MIN = AV
                    self.WIN_INIT = True

                self.WIN_ELAPSED = self.WIN_ELAPSED + self.CYCLE_S
                self.WIN_N = self.WIN_N + 1
                self.WIN_SP_SUM = self.WIN_SP_SUM + self.SP_WORK
                self.WIN_PV_SUM = self.WIN_PV_SUM + PV
                self.WIN_AV_SUM = self.WIN_AV_SUM + AV
                self.WIN_ERR_ABS_SUM = self.WIN_ERR_ABS_SUM + self.ABS_ERR
                self.WIN_ERR_PEAK_ABS = max(self.WIN_ERR_PEAK_ABS, self.ABS_ERR)
                self.WIN_PV_MAX = max(self.WIN_PV_MAX, PV)
                self.WIN_PV_MIN = min(self.WIN_PV_MIN, PV)
                self.WIN_AV_MAX = max(self.WIN_AV_MAX, AV)
                self.WIN_AV_MIN = min(self.WIN_AV_MIN, AV)
                self.WIN_NOISE_SUM = self.WIN_NOISE_SUM + self.D_PV
                self.WIN_NOISE_N = self.WIN_NOISE_N + 1

                if self.ERR >= 0:
                    self.WIN_ERR_AREA_POS = self.WIN_ERR_AREA_POS + self.ERR * self.CYCLE_S
                else:
                    self.WIN_ERR_AREA_NEG = self.WIN_ERR_AREA_NEG + abs(self.ERR) * self.CYCLE_S

                if self.AUTO_SAMPLE:
                    self.WIN_AUTO_T = self.WIN_AUTO_T + self.CYCLE_S
                if self.MAN_SAMPLE:
                    self.WIN_MAN_T = self.WIN_MAN_T + self.CYCLE_S
                    if self.D_AV >= self.MAN_TH or abs(TP - self.TP_1) >= self.MAN_TH:
                        self.WIN_MAN_EVENT_N = self.WIN_MAN_EVENT_N + 1

                if (self.WIN_N > 1) and (self.ERR * self.ERR_1 < 0):
                    self.WIN_CROSS_N = self.WIN_CROSS_N + 1
                    self.WIN_SEG_T_SUM = self.WIN_SEG_T_SUM + self.WIN_SEG_T
                    self.WIN_SEG_T = 0.0
                else:
                    self.WIN_SEG_T = self.WIN_SEG_T + self.CYCLE_S

                if (abs(RSF_STEP) >= self.MAN_TH) or (
                    (RSF_LEVEL != self.RSF_LEVEL_1) and (RSF_LEVEL > 0)
                ):
                    self.WIN_RSF_EVENT_N = self.WIN_RSF_EVENT_N + 1
                if (RSF_LOCK_LEVEL_IN > 0) and (self.RSF_LOCK_LEVEL_1 == 0):
                    self.WIN_LOCK_N = self.WIN_LOCK_N + 1

                if (self.ABS_ERR >= E1_IN) and (self.ABS_ERR < E2_IN):
                    self.WIN_ZONE1_T = self.WIN_ZONE1_T + self.CYCLE_S
                elif (self.ABS_ERR >= E2_IN) and (self.ABS_ERR < E3_IN):
                    self.WIN_ZONE2_T = self.WIN_ZONE2_T + self.CYCLE_S
                elif (self.ABS_ERR >= E3_IN) and (self.ABS_ERR < E4_IN):
                    self.WIN_ZONE3_T = self.WIN_ZONE3_T + self.CYCLE_S
                elif self.ABS_ERR >= E4_IN:
                    self.WIN_ZONE4_T = self.WIN_ZONE4_T + self.CYCLE_S

            # ---- 窗口结束：单窗口推荐 + 写历史 + 三阶段融合（517-905）----
            if (self.WIN_ELAPSED >= max(WIN_T, MIN_WIN_T)) or (
                self.CALC_R and (self.WIN_ELAPSED >= MIN_WIN_T)
            ):
                self.WINDOW_DONE = True
                self.WINDOW_T = self.WIN_ELAPSED
                self.AUTO_SAMPLE_T = self.WIN_AUTO_T
                self.MAN_EVENT_N = self.WIN_MAN_EVENT_N
                self.CROSS_COUNT = self.WIN_CROSS_N
                self.RSF_TRIGGER_N = self.WIN_RSF_EVENT_N
                self.RSF_LOCK_N = self.WIN_LOCK_N
                self.WINDOW_EVENT_N = (
                    self.WIN_RSF_EVENT_N + self.WIN_MAN_EVENT_N + self.WIN_CROSS_N
                )

                if self.WIN_N > 0:
                    self.WIN_SP_AVG = self.WIN_SP_SUM / self.WIN_N
                    self.WIN_PV_AVG = self.WIN_PV_SUM / self.WIN_N
                    self.WIN_AV_AVG = self.WIN_AV_SUM / self.WIN_N
                    self.WIN_ERR_AVG = self.WIN_ERR_ABS_SUM / self.WIN_N
                else:
                    self.WIN_SP_AVG = self.SP_WORK
                    self.WIN_PV_AVG = PV
                    self.WIN_AV_AVG = AV
                    self.WIN_ERR_AVG = self.ABS_ERR
                self.ERR_ABS_AVG = self.WIN_ERR_AVG
                self.ERR_AREA_POS = self.WIN_ERR_AREA_POS
                self.ERR_AREA_NEG = self.WIN_ERR_AREA_NEG
                self.ERR_PEAK_ABS = self.WIN_ERR_PEAK_ABS
                self.WIN_PV_DELTA = abs(self.WIN_PV_MAX - self.WIN_PV_MIN)
                self.WIN_AV_DELTA = abs(self.WIN_AV_MAX - self.WIN_AV_MIN)
                self.PV_DELTA = self.WIN_PV_DELTA
                self.AV_DELTA = self.WIN_AV_DELTA
                if self.WIN_NOISE_N > 0:
                    self.NOISE_EST = self.WIN_NOISE_SUM / self.WIN_NOISE_N
                else:
                    self.NOISE_EST = 0.0
                if self.WIN_CROSS_N > 0:
                    self.AVG_CROSS_T = self.WIN_SEG_T_SUM / self.WIN_CROSS_N
                else:
                    self.AVG_CROSS_T = 0.0
                if self.WIN_AV_DELTA > 0.001:
                    self.PROCESS_GAIN = self.WIN_PV_DELTA / max(self.WIN_AV_DELTA, 0.001)
                else:
                    self.PROCESS_GAIN = 0.0

                self.WIN_SCALE = max(max(E4_IN * 2, self.WIN_ERR_PEAK_ABS * 2), 1)
                self.WINDOW_VALID = (
                    (self.WIN_ELAPSED >= MIN_WIN_T)
                    and self.RANGE_OK
                    and self.SP_VALID
                    and (self.WINDOW_EVENT_N >= MIN_STORE_EVENT)
                    and (self.WIN_ERR_PEAK_ABS >= max(self.NOISE_EST * 2, 0.001))
                )
                if not self.RANGE_OK:
                    self.DATA_REASON = 3
                elif not self.SP_VALID:
                    self.DATA_REASON = 6
                elif self.WIN_ELAPSED < MIN_WIN_T:
                    self.DATA_REASON = 2
                elif self.WINDOW_EVENT_N < MIN_STORE_EVENT:
                    self.DATA_REASON = 4
                elif self.WIN_ERR_PEAK_ABS < max(self.NOISE_EST * 2, 0.001):
                    self.DATA_REASON = 5
                else:
                    self.DATA_REASON = 1

                # 单窗口推荐先从当前参数开始（579-596）
                self.W_TL = max(TL_IN, 0)
                self.W_TL1 = max(TL1_IN, self.W_TL + 1)
                self.W_TL2 = max(TL2_IN, self.W_TL + 1)
                self.W_TL3 = max(TL3_IN, self.W_TL + 1)
                self.W_TL4 = max(TL4_IN, self.W_TL + 1)
                self.W_E1 = max(E1_IN, 0.001)
                self.W_E2 = max(E2_IN, self.W_E1)
                self.W_E3 = max(E3_IN, self.W_E2)
                self.W_E4 = max(E4_IN, self.W_E3)
                self.W_AO1 = max(AO1_IN, 0)
                self.W_AO2 = max(AO2_IN, self.W_AO1)
                self.W_AO3 = max(AO3_IN, self.W_AO2)
                self.W_AO4 = max(AO4_IN, self.W_AO3)
                self.W_RSF_LOCK_T = max(RSF_LOCK_T_IN, 0)
                self.W_RSF_HYS = max(min(RSF_HYS_IN, 1), 0.1)
                self.W_RSF_FAST_HYS = max(min(RSF_FAST_HYS_IN, self.W_RSF_HYS), 0.01)
                self.W_RSF_TLOUT_K = max(min(RSF_TLOUT_K_IN, 1), 0)
                self.W_ZF_K = max(min(ZF_K_IN, 1), 0)

                if self.WINDOW_VALID:
                    self.W_OSC = (self.WIN_CROSS_N >= 3) or (self.WIN_LOCK_N >= 2)
                    self.W_SLOW = (self.WIN_RSF_EVENT_N > 0) and (
                        self.WIN_PV_DELTA
                        < max(self.WIN_ERR_PEAK_ABS * 0.2, self.NOISE_EST * 3)
                    )
                    self.W_NOISE_HIGH = self.NOISE_EST > max(
                        self.WIN_ERR_AVG * 0.3, self.WIN_SCALE * 0.002
                    )

                    self.W_BASE_E = max(
                        max(self.NOISE_EST * 3, self.WIN_ERR_AVG * 0.5), 0.001
                    )
                    self.W_E1 = (1 - self.BLEND) * self.W_E1 + self.BLEND * self.W_BASE_E
                    self.W_E2 = (1 - self.BLEND) * self.W_E2 + self.BLEND * max(
                        self.W_E1 * 1.8, self.WIN_ERR_AVG
                    )
                    self.W_E3 = (1 - self.BLEND) * self.W_E3 + self.BLEND * max(
                        self.W_E2 * 1.5, self.WIN_ERR_PEAK_ABS * 0.6
                    )
                    self.W_E4 = (1 - self.BLEND) * self.W_E4 + self.BLEND * max(
                        self.W_E3 * 1.3, self.WIN_ERR_PEAK_ABS * 0.9
                    )
                    self.W_E1 = max(self.W_E1, 0.001)
                    self.W_E2 = max(self.W_E2, self.W_E1)
                    self.W_E3 = max(self.W_E3, self.W_E2)
                    self.W_E4 = max(self.W_E4, self.W_E3)

                    self.W_GAIN = self.PROCESS_GAIN
                    if self.W_GAIN > 0.001:
                        self.W_AO1 = (1 - self.BLEND) * self.W_AO1 + self.BLEND * min(
                            max(self.W_E1 / max(self.W_GAIN, 0.001) * AO_GAIN_K, 0),
                            self.OUT_RANGE * 0.20,
                        )
                        self.W_AO2 = (1 - self.BLEND) * self.W_AO2 + self.BLEND * min(
                            max(self.W_E2 / max(self.W_GAIN, 0.001) * AO_GAIN_K, 0),
                            self.OUT_RANGE * 0.25,
                        )
                        self.W_AO3 = (1 - self.BLEND) * self.W_AO3 + self.BLEND * min(
                            max(self.W_E3 / max(self.W_GAIN, 0.001) * AO_GAIN_K, 0),
                            self.OUT_RANGE * 0.30,
                        )
                        self.W_AO4 = (1 - self.BLEND) * self.W_AO4 + self.BLEND * min(
                            max(self.W_E4 / max(self.W_GAIN, 0.001) * AO_GAIN_K, 0),
                            self.OUT_RANGE * 0.35,
                        )
                    if self.W_SLOW:
                        self.W_AO1 = self.W_AO1 * 1.10
                        self.W_AO2 = self.W_AO2 * 1.10
                        self.W_AO3 = self.W_AO3 * 1.10
                        self.W_AO4 = self.W_AO4 * 1.10
                        self.RSF_REASON = 2
                    elif self.W_OSC:
                        self.W_AO1 = self.W_AO1 * 0.90
                        self.W_AO2 = self.W_AO2 * 0.90
                        self.W_AO3 = self.W_AO3 * 0.90
                        self.W_AO4 = self.W_AO4 * 0.90
                        self.RSF_REASON = 3
                    elif self.W_NOISE_HIGH:
                        self.RSF_REASON = 4
                    else:
                        self.RSF_REASON = 1
                    self.W_AO1 = min(max(self.W_AO1, 0), self.OUT_RANGE * 0.35)
                    self.W_AO2 = min(max(self.W_AO2, self.W_AO1), self.OUT_RANGE * 0.40)
                    self.W_AO3 = min(max(self.W_AO3, self.W_AO2), self.OUT_RANGE * 0.45)
                    self.W_AO4 = min(max(self.W_AO4, self.W_AO3), self.OUT_RANGE * 0.50)

                    if self.AVG_CROSS_T > 0:
                        self.W_RESP_T = self.AVG_CROSS_T
                    else:
                        self.W_RESP_T = max(
                            max(self.W_TL1, self.W_TL2), max(self.W_TL3, self.W_TL4)
                        )
                    if self.W_NOISE_HIGH:
                        self.W_TL = min(max(self.W_TL * 1.2, self.W_TL + 1), 120)
                    self.W_TL = max(self.W_TL, 1)
                    self.W_TL1 = max(self.W_TL + 1, max(self.W_TL1, self.W_RESP_T * 0.5))
                    self.W_TL2 = max(self.W_TL1, max(self.W_TL2, self.W_RESP_T * 0.6))
                    self.W_TL3 = max(self.W_TL2, max(self.W_TL3, self.W_RESP_T * 0.8))
                    self.W_TL4 = max(self.W_TL3, max(self.W_TL4, self.W_RESP_T))
                    if self.W_SLOW:
                        self.W_TL1 = self.W_TL1 * 1.10
                        self.W_TL2 = self.W_TL2 * 1.10
                        self.W_TL3 = self.W_TL3 * 1.10
                        self.W_TL4 = self.W_TL4 * 1.10
                    self.W_TL1 = min(max(self.W_TL1, self.W_TL + 1), 7200)
                    self.W_TL2 = min(max(self.W_TL2, self.W_TL1), 7200)
                    self.W_TL3 = min(max(self.W_TL3, self.W_TL2), 7200)
                    self.W_TL4 = min(max(self.W_TL4, self.W_TL3), 7200)

                    self.W_RSF_LOCK_T = max(self.W_RSF_LOCK_T, self.W_TL * 3)
                    if self.W_OSC:
                        self.W_RSF_LOCK_T = self.W_RSF_LOCK_T * 1.30
                        self.W_RSF_HYS = max(self.W_RSF_HYS - 0.10, 0.5)
                        self.W_ZF_K = max(self.W_ZF_K, 0.5)
                    self.W_RSF_LOCK_T = min(max(self.W_RSF_LOCK_T, 10), 180)
                    self.W_RSF_HYS = max(min(self.W_RSF_HYS, 1), 0.1)
                    self.W_RSF_FAST_HYS = max(min(self.W_RSF_FAST_HYS, self.W_RSF_HYS), 0.01)
                    self.W_RSF_TLOUT_K = max(min(self.W_RSF_TLOUT_K, 1), 0)
                    self.W_ZF_K = max(min(self.W_ZF_K, 1), 0)
                else:
                    self.RSF_REASON = 5

                # 有效窗口写入历史缓存（682-713）
                if self.WINDOW_VALID:
                    self.WIN_WEIGHT = min(
                        max(self.WINDOW_EVENT_N / max(MIN_VALID_EVENT, 1), 0.2), 1
                    )
                    idx = self.H_IDX
                    self.H_VALID[idx] = True
                    self.H_WEIGHT[idx] = self.WIN_WEIGHT
                    self.H_SP[idx] = self.WIN_SP_AVG
                    self.H_PV[idx] = self.WIN_PV_AVG
                    self.H_AV[idx] = self.WIN_AV_AVG
                    self.H_ERR[idx] = self.WIN_ERR_AVG
                    self.H_TL[idx] = self.W_TL
                    self.H_TL1[idx] = self.W_TL1
                    self.H_TL2[idx] = self.W_TL2
                    self.H_TL3[idx] = self.W_TL3
                    self.H_TL4[idx] = self.W_TL4
                    self.H_E1[idx] = self.W_E1
                    self.H_E2[idx] = self.W_E2
                    self.H_E3[idx] = self.W_E3
                    self.H_E4[idx] = self.W_E4
                    self.H_AO1[idx] = self.W_AO1
                    self.H_AO2[idx] = self.W_AO2
                    self.H_AO3[idx] = self.W_AO3
                    self.H_AO4[idx] = self.W_AO4
                    self.H_RSF_LOCK_T[idx] = self.W_RSF_LOCK_T
                    self.H_RSF_HYS[idx] = self.W_RSF_HYS
                    self.H_RSF_FAST_HYS[idx] = self.W_RSF_FAST_HYS
                    self.H_RSF_TLOUT_K[idx] = self.W_RSF_TLOUT_K
                    self.H_ZF_K[idx] = self.W_ZF_K
                    self.HISTORY_COUNT = min(self.HISTORY_COUNT + 1, float(self.H_N))
                    self.H_IDX = self.H_IDX + 1
                    if self.H_IDX > self.H_N:
                        self.H_IDX = 1

                # 从历史窗口筛选相似工况并融合推荐（715-829）
                self.FINAL_STRONG = False
                self.FINAL_WEAK = False
                self.MATCH_LEVEL = 0
                self.FUSE_WEIGHT = 0.0
                self.SIM_RELAX_USE = max(SIM_RELAX_K, 1)
                for match_stage in range(1, 4):
                    self.MATCH_STAGE = match_stage
                    if not self.FINAL_STRONG:
                        self.SIMILAR_COUNT = 0.0
                        self.FUSE_SUM_W = 0.0
                        self.TL_REC = 0.0
                        self.TL1_REC = 0.0
                        self.TL2_REC = 0.0
                        self.TL3_REC = 0.0
                        self.TL4_REC = 0.0
                        self.E1_REC = 0.0
                        self.E2_REC = 0.0
                        self.E3_REC = 0.0
                        self.E4_REC = 0.0
                        self.AO1_REC = 0.0
                        self.AO2_REC = 0.0
                        self.AO3_REC = 0.0
                        self.AO4_REC = 0.0
                        self.RSF_LOCK_T_REC = 0.0
                        self.RSF_HYS_REC = 0.0
                        self.RSF_FAST_HYS_REC = 0.0
                        self.RSF_TLOUT_K_REC = 0.0
                        self.ZF_K_REC = 0.0

                        if match_stage == 1:
                            if SIM_SP_ABS > 0:
                                self.SIM_SP_TH = SIM_SP_ABS
                            else:
                                self.SIM_SP_TH = SIM_SP_K * self.WIN_SCALE
                            if SIM_PV_ABS > 0:
                                self.SIM_PV_TH = SIM_PV_ABS
                            else:
                                self.SIM_PV_TH = SIM_PV_K * self.WIN_SCALE
                            if SIM_AV_ABS > 0:
                                self.SIM_AV_TH = SIM_AV_ABS
                            else:
                                self.SIM_AV_TH = SIM_AV_K * self.OUT_RANGE
                            if SIM_ERR_ABS > 0:
                                self.SIM_ERR_TH = SIM_ERR_ABS
                            else:
                                self.SIM_ERR_TH = SIM_ERR_K * self.WIN_SCALE
                        elif match_stage == 2:
                            if SIM_SP_ABS > 0:
                                self.SIM_SP_TH = (
                                    max(SIM_SP_ABS, SIM_SP_K * self.WIN_SCALE)
                                    * self.SIM_RELAX_USE
                                )
                            else:
                                self.SIM_SP_TH = (
                                    SIM_SP_K * self.WIN_SCALE * self.SIM_RELAX_USE
                                )
                            if SIM_PV_ABS > 0:
                                self.SIM_PV_TH = (
                                    max(SIM_PV_ABS, SIM_PV_K * self.WIN_SCALE)
                                    * self.SIM_RELAX_USE
                                )
                            else:
                                self.SIM_PV_TH = (
                                    SIM_PV_K * self.WIN_SCALE * self.SIM_RELAX_USE
                                )
                            if SIM_AV_ABS > 0:
                                self.SIM_AV_TH = (
                                    max(SIM_AV_ABS, SIM_AV_K * self.OUT_RANGE)
                                    * self.SIM_RELAX_USE
                                )
                            else:
                                self.SIM_AV_TH = (
                                    SIM_AV_K * self.OUT_RANGE * self.SIM_RELAX_USE
                                )
                            if SIM_ERR_ABS > 0:
                                self.SIM_ERR_TH = (
                                    max(SIM_ERR_ABS, SIM_ERR_K * self.WIN_SCALE)
                                    * self.SIM_RELAX_USE
                                )
                            else:
                                self.SIM_ERR_TH = (
                                    SIM_ERR_K * self.WIN_SCALE * self.SIM_RELAX_USE
                                )
                        else:
                            self.SIM_SP_TH = (
                                max(SIM_SP_K * self.WIN_SCALE, SIM_SP_ABS)
                                * self.SIM_RELAX_USE
                            )
                            self.SIM_PV_TH = (
                                max(SIM_PV_K * self.WIN_SCALE, SIM_PV_ABS)
                                * self.SIM_RELAX_USE
                            )
                            self.SIM_AV_TH = (
                                max(SIM_AV_K * self.OUT_RANGE, SIM_AV_ABS)
                                * self.SIM_RELAX_USE
                            )
                            self.SIM_ERR_TH = (
                                max(SIM_ERR_K * self.WIN_SCALE, SIM_ERR_ABS)
                                * self.SIM_RELAX_USE
                            )

                        for i in range(1, self.H_N + 1):
                            if self.H_VALID[i]:
                                if (
                                    (abs(self.H_SP[i] - self.WIN_SP_AVG) <= self.SIM_SP_TH)
                                    and (abs(self.H_PV[i] - self.WIN_PV_AVG) <= self.SIM_PV_TH)
                                    and (abs(self.H_AV[i] - self.WIN_AV_AVG) <= self.SIM_AV_TH)
                                    and (abs(self.H_ERR[i] - self.WIN_ERR_AVG) <= self.SIM_ERR_TH)
                                ):
                                    self.FUSE_W = max(self.H_WEIGHT[i], 0.1)
                                    self.SIMILAR_COUNT = self.SIMILAR_COUNT + 1
                                    self.FUSE_SUM_W = self.FUSE_SUM_W + self.FUSE_W
                                    self.TL_REC = self.TL_REC + self.H_TL[i] * self.FUSE_W
                                    self.TL1_REC = self.TL1_REC + self.H_TL1[i] * self.FUSE_W
                                    self.TL2_REC = self.TL2_REC + self.H_TL2[i] * self.FUSE_W
                                    self.TL3_REC = self.TL3_REC + self.H_TL3[i] * self.FUSE_W
                                    self.TL4_REC = self.TL4_REC + self.H_TL4[i] * self.FUSE_W
                                    self.E1_REC = self.E1_REC + self.H_E1[i] * self.FUSE_W
                                    self.E2_REC = self.E2_REC + self.H_E2[i] * self.FUSE_W
                                    self.E3_REC = self.E3_REC + self.H_E3[i] * self.FUSE_W
                                    self.E4_REC = self.E4_REC + self.H_E4[i] * self.FUSE_W
                                    self.AO1_REC = self.AO1_REC + self.H_AO1[i] * self.FUSE_W
                                    self.AO2_REC = self.AO2_REC + self.H_AO2[i] * self.FUSE_W
                                    self.AO3_REC = self.AO3_REC + self.H_AO3[i] * self.FUSE_W
                                    self.AO4_REC = self.AO4_REC + self.H_AO4[i] * self.FUSE_W
                                    self.RSF_LOCK_T_REC = (
                                        self.RSF_LOCK_T_REC
                                        + self.H_RSF_LOCK_T[i] * self.FUSE_W
                                    )
                                    self.RSF_HYS_REC = (
                                        self.RSF_HYS_REC + self.H_RSF_HYS[i] * self.FUSE_W
                                    )
                                    self.RSF_FAST_HYS_REC = (
                                        self.RSF_FAST_HYS_REC
                                        + self.H_RSF_FAST_HYS[i] * self.FUSE_W
                                    )
                                    self.RSF_TLOUT_K_REC = (
                                        self.RSF_TLOUT_K_REC
                                        + self.H_RSF_TLOUT_K[i] * self.FUSE_W
                                    )
                                    self.ZF_K_REC = (
                                        self.ZF_K_REC + self.H_ZF_K[i] * self.FUSE_W
                                    )

                        self.FUSE_STRONG = (self.SIMILAR_COUNT >= FUSE_MIN_N) and (
                            self.FUSE_SUM_W >= FUSE_MIN_WEIGHT
                        )
                        if self.FUSE_STRONG:
                            self.FINAL_STRONG = True
                        if self.FUSE_SUM_W > 0:
                            self.MATCH_LEVEL = match_stage

                if self.FUSE_SUM_W > 0:
                    self.TL_REC = self.TL_REC / self.FUSE_SUM_W
                    self.TL1_REC = self.TL1_REC / self.FUSE_SUM_W
                    self.TL2_REC = self.TL2_REC / self.FUSE_SUM_W
                    self.TL3_REC = self.TL3_REC / self.FUSE_SUM_W
                    self.TL4_REC = self.TL4_REC / self.FUSE_SUM_W
                    self.E1_REC = self.E1_REC / self.FUSE_SUM_W
                    self.E2_REC = self.E2_REC / self.FUSE_SUM_W
                    self.E3_REC = self.E3_REC / self.FUSE_SUM_W
                    self.E4_REC = self.E4_REC / self.FUSE_SUM_W
                    self.AO1_REC = self.AO1_REC / self.FUSE_SUM_W
                    self.AO2_REC = self.AO2_REC / self.FUSE_SUM_W
                    self.AO3_REC = self.AO3_REC / self.FUSE_SUM_W
                    self.AO4_REC = self.AO4_REC / self.FUSE_SUM_W
                    self.RSF_LOCK_T_REC = self.RSF_LOCK_T_REC / self.FUSE_SUM_W
                    self.RSF_HYS_REC = self.RSF_HYS_REC / self.FUSE_SUM_W
                    self.RSF_FAST_HYS_REC = self.RSF_FAST_HYS_REC / self.FUSE_SUM_W
                    self.RSF_TLOUT_K_REC = self.RSF_TLOUT_K_REC / self.FUSE_SUM_W
                    self.ZF_K_REC = self.ZF_K_REC / self.FUSE_SUM_W
                else:
                    self.TL_REC = float(self.W_TL)
                    self.TL1_REC = float(self.W_TL1)
                    self.TL2_REC = float(self.W_TL2)
                    self.TL3_REC = float(self.W_TL3)
                    self.TL4_REC = float(self.W_TL4)
                    self.E1_REC = float(self.W_E1)
                    self.E2_REC = float(self.W_E2)
                    self.E3_REC = float(self.W_E3)
                    self.E4_REC = float(self.W_E4)
                    self.AO1_REC = float(self.W_AO1)
                    self.AO2_REC = float(self.W_AO2)
                    self.AO3_REC = float(self.W_AO3)
                    self.AO4_REC = float(self.W_AO4)
                    self.RSF_LOCK_T_REC = float(self.W_RSF_LOCK_T)
                    self.RSF_HYS_REC = float(self.W_RSF_HYS)
                    self.RSF_FAST_HYS_REC = float(self.W_RSF_FAST_HYS)
                    self.RSF_TLOUT_K_REC = float(self.W_RSF_TLOUT_K)
                    self.ZF_K_REC = float(self.W_ZF_K)

                self.FINAL_STRONG = (self.SIMILAR_COUNT >= FUSE_MIN_N) and (
                    self.FUSE_SUM_W >= FUSE_MIN_WEIGHT
                )
                self.FINAL_WEAK = (self.FUSE_SUM_W > 0) and (not self.FINAL_STRONG)
                self.FINAL_VALID = self.FINAL_STRONG
                self.FUSE_WEIGHT = self.FUSE_SUM_W
                self.RSF_OK = self.FINAL_VALID and self.WINDOW_VALID
                if not self.FINAL_VALID:
                    self.RSF_REASON = 5

                # 清空窗口累计量，准备下一窗口（881-904）
                self.WIN_ELAPSED = 0.0
                self.WIN_AUTO_T = 0.0
                self.WIN_MAN_T = 0.0
                self.WIN_N = 0.0
                self.WIN_SP_SUM = 0.0
                self.WIN_PV_SUM = 0.0
                self.WIN_AV_SUM = 0.0
                self.WIN_ERR_ABS_SUM = 0.0
                self.WIN_ERR_AREA_POS = 0.0
                self.WIN_ERR_AREA_NEG = 0.0
                self.WIN_ERR_PEAK_ABS = 0.0
                self.WIN_INIT = False
                self.WIN_NOISE_SUM = 0.0
                self.WIN_NOISE_N = 0.0
                self.WIN_CROSS_N = 0.0
                self.WIN_SEG_T = 0.0
                self.WIN_SEG_T_SUM = 0.0
                self.WIN_RSF_EVENT_N = 0.0
                self.WIN_MAN_EVENT_N = 0.0
                self.WIN_LOCK_N = 0.0
                self.WIN_ZONE1_T = 0.0
                self.WIN_ZONE2_T = 0.0
                self.WIN_ZONE3_T = 0.0
                self.WIN_ZONE4_T = 0.0

        # ---- 保存本周期状态（909-915，无条件）----
        self.CALC_OLD = CALC_NOW
        self.ERR_1 = self.ERR
        self.PV_1 = PV
        self.AV_1 = AV
        self.TP_1 = TP
        self.RSF_LEVEL_1 = RSF_LEVEL
        self.RSF_LOCK_LEVEL_1 = RSF_LOCK_LEVEL_IN

        return None
