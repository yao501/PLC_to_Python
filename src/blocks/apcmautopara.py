"""业务块 APCMAUTOPARA：APCM 自动参数推荐功能块。

对应 CODESYS ST 源：``/Users/guangyaosun/Desktop/APCMAUTOPARA.txt``（唯一业务基线）。

**唯一事实来源是 ST 的实际执行顺序**（不是注释、不是提示词）。

模块定位：采集自动运行数据与手动调整事件 → 窗口统计 → PID/RSF/观测器/重叠控制四组
单窗口推荐 → 历史相似工况匹配 → 三阶段融合输出。**只输出推荐值与状态**，不写入 APCM
实际控制参数，也不写现场 SP。复用真实 :class:`APCSPFINDER` 子实例提供分析 ``SP_USE``。

关键契约（详见 ``docs/RISKS.md`` 的 ``APCMAUTOPARA-*``）：

* **APCMAUTOPARA-CYCLE-1**：业务累计时间一律由 ``CYCLE_S=MAX(CYCLE,0.001)`` 驱动；
  ``dt_ms`` 仅为统一 ``step`` 接口保留、仅转发给 ``SPF1.step()``，**不**参与本模块任何
  累计/缩放。
* **APCMAUTOPARA-RESET-1**：源 ST 顶层为 ``IF RESET OR NOT INIT_DONE`` 初始化、随后
  ``RUNNING:=EN``、再 ``IF EN`` 采集。故 ``EN=True 且 RESET=True`` 时：先完整复位，
  ``RUNNING=True``，本拍仍进入 ``IF EN`` 采集路径。**不得**改写为 ``IF EN AND NOT
  RESET``（与 APCRSFNAUTOPARA 不同）。默认回退块在复位块之前执行且不被复位块覆盖
  ``*_REC`` 主推荐。
* **APCMAUTOPARA-SPFINDER-1**：仅复用 ``APCSPFINDER`` 提供分析 SP；``SP_USE`` 只用于
  推荐分析，不写入现场控制 SP。冷启动/复位且 ``EN=True`` 时 ``SPF1`` 当拍被调用两次
  （复位初始化调用 + 正常路径调用）。
* **APCMAUTOPARA-DATAREASON-1**：``DATA_REASON`` 与 ``WINDOW_T/ERR_*/PV_DELTA/AV_DELTA``
  同属"最近完成窗口快照"。结算块内 ``ELSIF WIN_ELAPSED<MIN_WIN_T → 2`` 在当前结算条件
  （恒 ``WIN_ELAPSED>=MIN_WIN_T``）下不可达，为源遗留分支，原样保留，**不**在累计阶段
  实时写 ``DATA_REASON=2``。
* **APCMAUTOPARA-MANRESP-1**：``MAN_RESP_ACTIVE`` 可跨窗口保留；手动动作与其最终响应
  统计可能落在不同窗口，按源 ST 保留，不擅自"修复"。
* **APCMAUTOPARA-HISTORY-1**：历史数量缩小时仅清理源代码指定摘要项并置 ``H_VALID=False``；
  未清零的 ``H_PT~H_TZ_CD`` 推荐缓存由 ``H_VALID=False`` 屏蔽。
"""

from __future__ import annotations

from .apcspfinder import APCSPFINDER


class APCMAUTOPARA:
    """APCM 自动参数推荐功能块。携带跨扫描状态，内部复用真实 ``APCSPFINDER`` 子实例。

    构造::

        APCMAUTOPARA()

    单周期推进::

        step(dt_ms, *, EN, RESET, CALC_NOW, CYCLE, COLLECT_MODE, SP, ...)

    全部 ``VAR_OUTPUT`` / ``VAR`` 均为可读取实例属性，历史数组为 1-based（索引 1..24）。
    """

    # 融合三元组：(REC 输出名, H 缓存名, W 单窗口暂存名)。
    # 同一组在"每阶段清零 / 加权累计 / 除权平均 / 无相似回退 / 写历史"中保持一致顺序。
    _FUSE_MAP = (
        ("PT_REC", "H_PT", "W_PT"),
        ("TI_REC", "H_TI", "W_TI"),
        ("TD_REC", "H_TD", "W_TD"),
        ("DI_REC", "H_DI", "W_DI"),
        ("SVH_REC", "H_SVH", "W_SVH"),
        ("SVL_REC", "H_SVL", "W_SVL"),
        ("TL_REC", "H_TL", "W_TL"),
        ("TL1_REC", "H_TL1", "W_TL1"),
        ("TL2_REC", "H_TL2", "W_TL2"),
        ("TL3_REC", "H_TL3", "W_TL3"),
        ("TL4_REC", "H_TL4", "W_TL4"),
        ("E1_REC", "H_E1", "W_E1"),
        ("E2_REC", "H_E2", "W_E2"),
        ("E3_REC", "H_E3", "W_E3"),
        ("E4_REC", "H_E4", "W_E4"),
        ("AO1_REC", "H_AO1", "W_AO1"),
        ("AO2_REC", "H_AO2", "W_AO2"),
        ("AO3_REC", "H_AO3", "W_AO3"),
        ("AO4_REC", "H_AO4", "W_AO4"),
        ("RSF_LOCK_T_REC", "H_RSF_LOCK_T", "W_RSF_LOCK_T"),
        ("TC_REC", "H_TC", "W_TC"),
        ("TZ_REC", "H_TZ", "W_TZ"),
        ("GC1_REC", "H_GC1", "W_GC1"),
        ("GC2_REC", "H_GC2", "W_GC2"),
        ("OUTH_REC", "H_OUTH", "W_OUTH"),
        ("OUTL_REC", "H_OUTL", "W_OUTL"),
        ("CD_GD_REC", "H_CD_GD", "W_CD_GD"),
        ("CD_K_REC", "H_CD_K", "W_CD_K"),
        ("CD_K_FD_REC", "H_CD_K_FD", "W_CD_K_FD"),
        ("CD_K_J_REC", "H_CD_K_J", "W_CD_K_J"),
        ("CD_K_D_REC", "H_CD_K_D", "W_CD_K_D"),
        ("CDH_REC", "H_CDH", "W_CDH"),
        ("CDL_REC", "H_CDL", "W_CDL"),
        ("TC_CD_REC", "H_TC_CD", "W_TC_CD"),
        ("TZ_CD_REC", "H_TZ_CD", "W_TZ_CD"),
    )

    def __init__(self) -> None:
        # ===== VAR_OUTPUT（初值 0 / False）=====
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

        self.PID_OK: bool = False
        self.RSF_OK: bool = False
        self.GC_OK: bool = False
        self.CD_OK: bool = False
        self.PID_REASON: int = 0
        self.RSF_REASON: int = 0
        self.GC_REASON: int = 0
        self.CD_REASON: int = 0

        self.HISTORY_COUNT: float = 0.0
        self.SIMILAR_COUNT: float = 0.0
        self.FUSE_WEIGHT: float = 0.0
        self.WINDOW_EVENT_N: float = 0.0
        self.WINDOW_T: float = 0.0
        self.AUTO_SAMPLE_T: float = 0.0
        self.MAN_EVENT_N: float = 0.0
        self.MAN_RESP_T_AUTO: float = 0.0
        self.MAN_RESP_T_USE: float = 0.0
        self.CROSS_COUNT: float = 0.0
        self.ERR_ABS_AVG: float = 0.0
        self.ERR_AREA_POS: float = 0.0
        self.ERR_AREA_NEG: float = 0.0
        self.ERR_PEAK_ABS: float = 0.0
        self.AVG_CROSS_T: float = 0.0
        self.PV_DELTA: float = 0.0
        self.AV_DELTA: float = 0.0
        self.NOISE_EST: float = 0.0
        self.PROCESS_GAIN: float = 0.0

        # PID 推荐值
        self.PT_REC: float = 0.0
        self.TI_REC: float = 0.0
        self.TD_REC: float = 0.0
        self.DI_REC: float = 0.0
        self.SVH_REC: float = 0.0
        self.SVL_REC: float = 0.0
        self.PID_FORMULA_VALID: bool = False
        self.PT_FORMULA_REC: float = 0.0
        self.TI_FORMULA_REC: float = 0.0
        self.PID_MODEL_GAIN_REC: float = 0.0
        self.PID_MODEL_T_REC: float = 0.0
        self.PID_MODEL_L_REC: float = 0.0
        self.PID_MODEL_LAMBDA_REC: float = 0.0
        self.PID_FORMULA_BLEND_REC: float = 0.0

        # RSF 推荐值
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

        # 观测器推荐值
        self.TC_REC: float = 0.0
        self.TZ_REC: float = 0.0
        self.GC1_REC: float = 0.0
        self.GC2_REC: float = 0.0
        self.OUTH_REC: float = 0.0
        self.OUTL_REC: float = 0.0

        # 重叠控制推荐值
        self.CD_GD_REC: float = 0.0
        self.CD_K_REC: float = 0.0
        self.CD_K_FD_REC: float = 0.0
        self.CD_K_J_REC: float = 0.0
        self.CD_K_D_REC: float = 0.0
        self.CDH_REC: float = 0.0
        self.CDL_REC: float = 0.0
        self.TC_CD_REC: float = 0.0
        self.TZ_CD_REC: float = 0.0

        # ===== VAR（内部状态）=====
        self.INIT_DONE: bool = False
        self.CALC_OLD: bool = False
        self.CALC_R: bool = False
        self.I: int = 0
        self.H_IDX: int = 0
        self.H_N: int = 0
        self.H_N_OLD: int = 0

        self.CYCLE_S: float = 0.0
        self.PV_RANGE: float = 0.0
        self.OUT_RANGE: float = 0.0
        self.OUT_LIMIT_RANGE: float = 0.0
        self.OUT_LIMIT_MARGIN: float = 0.0
        self.RANGE_OK: bool = False
        self.MAN_TH: float = 0.0
        self.AUTO_ALLOWED: bool = False
        self.MAN_ALLOWED: bool = False
        self.SPF1: APCSPFINDER = APCSPFINDER()
        self.SP_WORK: float = 0.0

        # 当前窗口实时统计
        self.WIN_ELAPSED: float = 0.0
        self.WIN_AUTO_T: float = 0.0
        self.WIN_AUTO_N: float = 0.0
        self.WIN_SP_SUM: float = 0.0
        self.WIN_PV_SUM: float = 0.0
        self.WIN_AV_SUM: float = 0.0
        self.WIN_ERR_AREA_TOTAL: float = 0.0
        self.WIN_ERR_AREA_POS: float = 0.0
        self.WIN_ERR_AREA_NEG: float = 0.0
        self.WIN_ERR_PEAK_POS: float = 0.0
        self.WIN_ERR_PEAK_NEG: float = 0.0
        self.WIN_ERR_PEAK_ABS: float = 0.0
        self.WIN_CROSS_N: float = 0.0
        self.WIN_SEG_T: float = 0.0
        self.WIN_SEG_AREA: float = 0.0
        self.WIN_LAST_AREA1: float = 0.0
        self.WIN_LAST_AREA2: float = 0.0
        self.WIN_LAST_AREA3: float = 0.0
        self.WIN_LAST_T1: float = 0.0
        self.WIN_LAST_T2: float = 0.0
        self.WIN_LAST_T3: float = 0.0
        self.WIN_SEG_PEAK: float = 0.0
        self.WIN_LAST_PEAK1: float = 0.0
        self.WIN_LAST_PEAK2: float = 0.0
        self.WIN_LAST_PEAK3: float = 0.0
        self.WIN_PV_MAX: float = 0.0
        self.WIN_PV_MIN: float = 0.0
        self.WIN_AV_MAX: float = 0.0
        self.WIN_AV_MIN: float = 0.0
        self.WIN_PV_STEP_SUM: float = 0.0
        self.WIN_PV_STEP_MAX: float = 0.0
        self.WIN_QUIET_STEP_SUM: float = 0.0
        self.WIN_QUIET_N: float = 0.0
        self.WIN_AUTO_AV_EVENT_N: float = 0.0
        self.WIN_EVENT_N: float = 0.0
        self.WIN_FIRST: bool = False
        self.ERR: float = 0.0
        self.ERR_1: float = 0.0
        self.ABS_ERR: float = 0.0
        self.PV_1: float = 0.0
        self.AV_1: float = 0.0
        self.PV_STEP: float = 0.0
        self.AV_STEP: float = 0.0
        self.AUTO_AV_MOVING: bool = False
        self.AUTO_AV_QUIET_T: float = 0.0

        # 手动事件合并和响应观察
        self.MAN_INIT: bool = False
        self.MAN_LAST_AV: float = 0.0
        self.MAN_DAV: float = 0.0
        self.MAN_ACTIVE: bool = False
        self.MAN_NO_CHANGE_T: float = 0.0
        self.MAN_START_AV: float = 0.0
        self.MAN_END_AV: float = 0.0
        self.MAN_SUM_ABS_AV: float = 0.0
        self.MAN_START_PV: float = 0.0
        self.MAN_RESP_ACTIVE: bool = False
        self.MAN_RESP_CT: float = 0.0
        self.MAN_RESP_START_PV: float = 0.0
        self.MAN_RESP_PV_MAX: float = 0.0
        self.MAN_RESP_PV_MIN: float = 0.0
        self.MAN_RESP_NET_AV: float = 0.0
        self.MAN_RESP_SUM_ABS_AV: float = 0.0
        self.MAN_EVENT_CNT: float = 0.0
        self.MAN_GAIN_SUM: float = 0.0
        self.MAN_GAIN_N: float = 0.0
        self.MAN_BAD_N: float = 0.0
        self.MAN_RESP_DELTA: float = 0.0
        self.MAN_RESP_VALID_T_SUM: float = 0.0
        self.MAN_RESP_VALID_N: float = 0.0

        # 最近窗口摘要
        self.W_SP_AVG: float = 0.0
        self.W_PV_AVG: float = 0.0
        self.W_AV_AVG: float = 0.0
        self.W_EVENT_N: float = 0.0
        self.W_GAIN: float = 0.0
        self.W_NOISE_HIGH: bool = False
        self.W_OSC: bool = False
        self.W_SLOW: bool = False
        self.W_AREA_RATIO12: float = 0.0
        self.W_AREA_RATIO23: float = 0.0
        self.W_AREA_BALANCE: float = 0.0
        self.W_PEAK_RATIO12: float = 0.0
        self.W_PEAK_RATIO23: float = 0.0
        self.W_PID_AREA_VALID: bool = False
        self.W_PID_AREA_DIVERGE: bool = False
        self.W_PID_AREA_EQUAL: bool = False
        self.W_PID_AREA_OSC: bool = False
        self.W_PID_PEAK_VALID: bool = False

        # 单窗口推荐暂存
        self.W_PT: float = 0.0
        self.W_TI: float = 0.0
        self.W_TD: float = 0.0
        self.W_DI: float = 0.0
        self.W_SVH: float = 0.0
        self.W_SVL: float = 0.0
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
        self.W_TC: float = 0.0
        self.W_TZ: float = 0.0
        self.W_GC1: float = 0.0
        self.W_GC2: float = 0.0
        self.W_OUTH: float = 0.0
        self.W_OUTL: float = 0.0
        self.W_CD_GD: float = 0.0
        self.W_CD_K: float = 0.0
        self.W_CD_K_FD: float = 0.0
        self.W_CD_K_J: float = 0.0
        self.W_CD_K_D: float = 0.0
        self.W_CDH: float = 0.0
        self.W_CDL: float = 0.0
        self.W_TC_CD: float = 0.0
        self.W_TZ_CD: float = 0.0

        self.BASE_T: float = 0.0
        self.BASE_AO: float = 0.0
        self.E4_BASE: float = 0.0
        self.PID_PEAK_MIN: float = 0.0
        self.W_PT_ABS: float = 0.0
        self.W_PID_FORMULA_VALID: bool = False
        self.W_MODEL_GAIN_N: float = 0.0
        self.W_MODEL_T: float = 0.0
        self.W_MODEL_L: float = 0.0
        self.W_MODEL_LAMBDA: float = 0.0
        self.W_PT_FORMULA: float = 0.0
        self.W_TI_FORMULA: float = 0.0
        self.PID_BLEND_USE: float = 0.0
        self.W_TI_THEORY: float = 0.0
        self.W_TI_TARGET: float = 0.0
        self.LIMIT_TEMP: float = 0.0
        self.FUSE_W: float = 0.0
        self.FUSE_SUM_W: float = 0.0
        self.FUSE_STRONG: bool = False
        self.MATCH_STAGE: int = 0
        self.MATCH_OK: bool = False
        self.SIM_RELAX_USE: float = 0.0
        self.SIM_SP_PROP: float = 0.0
        self.SIM_PV_PROP: float = 0.0
        self.SIM_AV_PROP: float = 0.0
        self.SIM_ERR_PROP: float = 0.0
        self.SIM_SP_LIMIT: float = 0.0
        self.SIM_PV_LIMIT: float = 0.0
        self.SIM_AV_LIMIT: float = 0.0
        self.SIM_ERR_LIMIT: float = 0.0

        # 历史窗口缓存（1-based，索引 1..24；索引 0 占位不使用）
        self.H_VALID = [False] * 25
        for name in (
            "H_SP_AVG", "H_PV_AVG", "H_AV_AVG", "H_ERR_ABS_AVG", "H_EVENT_N",
            "H_NOISE_EST", "H_GAIN", "H_PT", "H_TI", "H_TD", "H_DI", "H_SVH",
            "H_SVL", "H_TL", "H_TL1", "H_TL2", "H_TL3", "H_TL4", "H_E1", "H_E2",
            "H_E3", "H_E4", "H_AO1", "H_AO2", "H_AO3", "H_AO4", "H_RSF_LOCK_T",
            "H_TC", "H_TZ", "H_GC1", "H_GC2", "H_OUTH", "H_OUTL", "H_CD_GD",
            "H_CD_K", "H_CD_K_FD", "H_CD_K_J", "H_CD_K_D", "H_CDH", "H_CDL",
            "H_TC_CD", "H_TZ_CD",
        ):
            setattr(self, name, [0.0] * 25)

    def step(
        self,
        dt_ms: int,
        *,
        EN: bool = False,
        RESET: bool = False,
        CALC_NOW: bool = False,
        CYCLE: float = 0.5,
        COLLECT_MODE: int = 1,
        SP: float = 0.0,
        SP_MAN: float = 0.0,
        SP_MAN_EN: bool = False,
        SP_TAG_EN: bool = True,
        SP_AUTO_EN: bool = True,
        SP_AUTO_REPLACE_BAD_TAG: bool = False,
        SP_STABLE_T: float = 300.0,
        SP_CONF_T: float = 900.0,
        SP_PV_STABLE_ABS: float = 0.0,
        SP_AV_STABLE_ABS: float = 0.0,
        PV: float = 0.0,
        AV: float = 0.0,
        RM: int = 1,
        TS: bool = False,
        PVMU: float = 100.0,
        PVMD: float = 0.0,
        MU: float = 100.0,
        MD: float = 0.0,
        OUTT: float = 100.0,
        OUTB: float = 0.0,
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
        MAN_MERGE_T: float = 10.0,
        MAN_RESP_T: float = 60.0,
        MAN_RESP_T_MAX: float = 7200.0,
        MAN_AV_MIN: float = 0.1,
        PT_IN: float = 300.0,
        TI_IN: float = 50.0,
        TD_IN: float = 0.0,
        DI_IN: float = 0.0,
        SVH_IN: float = 30.0,
        SVL_IN: float = 0.0,
        PID_FORMULA_EN: bool = True,
        PID_LAMBDA_K: float = 1.5,
        PID_MODEL_L_K: float = 0.2,
        PID_FORMULA_BLEND: float = 0.8,
        TL_IN: float = 10.0,
        TL1_IN: float = 120.0,
        TL2_IN: float = 120.0,
        TL3_IN: float = 120.0,
        TL4_IN: float = 120.0,
        E1_IN: float = 1.0,
        E2_IN: float = 2.0,
        E3_IN: float = 3.0,
        E4_IN: float = 4.0,
        AO1_IN: float = 0.3,
        AO2_IN: float = 0.4,
        AO3_IN: float = 0.5,
        AO4_IN: float = 0.6,
        RSF_LOCK_T_IN: float = 30.0,
        TC_IN: float = 10.0,
        TZ_IN: float = 20.0,
        GC1_IN: float = 1.0,
        GC2_IN: float = 6.0,
        OUTH_IN: float = 5.0,
        OUTL_IN: float = -5.0,
        CD_GD_IN: float = 0.0,
        CD_K_IN: float = 0.5,
        CD_K_FD_IN: float = 1.0,
        CD_K_J_IN: float = 1.0,
        CD_K_D_IN: float = 1.0,
        CDH_IN: float = 5.0,
        CDL_IN: float = -5.0,
        TC_CD_IN: float = 10.0,
        TZ_CD_IN: float = 20.0,
    ) -> None:
        # ---- 基础状态（437-453，每拍无条件）----
        self.CALC_R = CALC_NOW and (not self.CALC_OLD)
        self.WINDOW_DONE = False
        self.CYCLE_S = max(CYCLE, 0.001)
        self.PV_RANGE = abs(PVMU - PVMD)
        self.OUT_RANGE = abs(MU - MD)
        self.OUT_LIMIT_RANGE = abs(OUTT - OUTB)
        self.RANGE_OK = (self.PV_RANGE > 0) and (self.OUT_RANGE > 0)
        if self.PV_RANGE <= 0:
            self.PV_RANGE = 100
        if self.OUT_RANGE <= 0:
            self.OUT_RANGE = 100
        if self.OUT_LIMIT_RANGE <= 0:
            self.OUT_LIMIT_RANGE = self.OUT_RANGE
        self.OUT_LIMIT_MARGIN = max(self.OUT_LIMIT_RANGE * 0.01, self.OUT_RANGE * 0.002)
        self.H_N = min(max(HISTORY_N, 1), 24)

        # ---- HISTORY_N 变化整理历史缓存（455-471）----
        if self.INIT_DONE and (self.H_N != self.H_N_OLD):
            if self.H_N < self.H_N_OLD:
                for i in range(1, 25):
                    if i > self.H_N:
                        self.H_VALID[i] = False
                        self.H_EVENT_N[i] = 0.0
                        self.H_SP_AVG[i] = 0.0
                        self.H_PV_AVG[i] = 0.0
                        self.H_AV_AVG[i] = 0.0
                        self.H_ERR_ABS_AVG[i] = 0.0
                        self.H_NOISE_EST[i] = 0.0
                        self.H_GAIN[i] = 0.0
            self.H_N_OLD = self.H_N

        if self.HISTORY_COUNT > self.H_N:
            self.HISTORY_COUNT = float(self.H_N)
        if self.H_IDX > self.H_N:
            self.H_IDX = 1
        self.MAN_TH = max(MAN_AV_MIN, self.OUT_RANGE * 0.0005)
        self.MAN_RESP_T_USE = max(max(MAN_RESP_T, self.MAN_RESP_T_AUTO), 1)
        self.MAN_RESP_T_USE = float(min(
            self.MAN_RESP_T_USE, max(max(MAN_RESP_T_MAX, MAN_RESP_T), 1)
        ))

        # ---- 推荐值默认回退到当前参数（483-519）----
        if (self.HISTORY_COUNT == 0) or RESET or (not self.INIT_DONE):
            self.PT_REC = max(PT_IN, 0.001)
            self.TI_REC = max(TI_IN, 0.001)
            self.TD_REC = float(max(TD_IN, 0))
            self.DI_REC = float(max(DI_IN, 0))
            self.SVH_REC = float(max(SVH_IN, 0))
            self.SVL_REC = float(max(SVL_IN, 0))
            self.TL_REC = float(max(TL_IN, 0))
            self.TL1_REC = max(TL1_IN, self.TL_REC + 1)
            self.TL2_REC = max(TL2_IN, self.TL_REC + 1)
            self.TL3_REC = max(TL3_IN, self.TL_REC + 1)
            self.TL4_REC = max(TL4_IN, self.TL_REC + 1)
            self.E1_REC = max(E1_IN, 0.001)
            self.E2_REC = max(E2_IN, self.E1_REC)
            self.E3_REC = max(E3_IN, self.E2_REC)
            self.E4_REC = max(E4_IN, self.E3_REC)
            self.AO1_REC = float(max(AO1_IN, 0))
            self.AO2_REC = max(AO2_IN, self.AO1_REC)
            self.AO3_REC = max(AO3_IN, self.AO2_REC)
            self.AO4_REC = max(AO4_IN, self.AO3_REC)
            self.RSF_LOCK_T_REC = float(max(RSF_LOCK_T_IN, 0))
            self.TC_REC = float(max(TC_IN, 0))
            self.TZ_REC = float(max(TZ_IN, 0))
            self.GC1_REC = float(max(GC1_IN, 0))
            self.GC2_REC = float(max(GC2_IN, 0))
            self.OUTH_REC = OUTH_IN
            self.OUTL_REC = OUTL_IN
            self.CD_GD_REC = float(max(CD_GD_IN, 0))
            self.CD_K_REC = CD_K_IN
            self.CD_K_FD_REC = float(max(CD_K_FD_IN, 0))
            self.CD_K_J_REC = float(max(CD_K_J_IN, 0))
            self.CD_K_D_REC = float(max(CD_K_D_IN, 0))
            self.CDH_REC = CDH_IN
            self.CDL_REC = CDL_IN
            self.TC_CD_REC = float(max(TC_CD_IN, 0))
            self.TZ_CD_REC = float(max(TZ_CD_IN, 0))

        # ---- 复位全部统计和历史缓存（522-647）----
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
                PVMU=PVMU,
                PVMD=PVMD,
                OUTT=MU,
                OUTB=MD,
            )
            self.PID_OK = False
            self.RSF_OK = False
            self.GC_OK = False
            self.CD_OK = False
            self.PID_FORMULA_VALID = False
            self.PID_REASON = 0
            self.RSF_REASON = 0
            self.GC_REASON = 0
            self.CD_REASON = 0

            self.HISTORY_COUNT = 0.0
            self.SIMILAR_COUNT = 0.0
            self.FUSE_WEIGHT = 0.0
            self.WINDOW_EVENT_N = 0.0
            self.WINDOW_T = 0.0
            self.AUTO_SAMPLE_T = 0.0
            self.MAN_EVENT_N = 0.0
            self.CROSS_COUNT = 0.0
            self.ERR_ABS_AVG = 0.0
            self.ERR_AREA_POS = 0.0
            self.ERR_AREA_NEG = 0.0
            self.ERR_PEAK_ABS = 0.0
            self.AVG_CROSS_T = 0.0
            self.PV_DELTA = 0.0
            self.AV_DELTA = 0.0
            self.NOISE_EST = 0.0
            self.PROCESS_GAIN = 0.0
            self.PT_FORMULA_REC = 0.0
            self.TI_FORMULA_REC = 0.0
            self.PID_MODEL_GAIN_REC = 0.0
            self.PID_MODEL_T_REC = 0.0
            self.PID_MODEL_L_REC = 0.0
            self.PID_MODEL_LAMBDA_REC = 0.0
            self.PID_FORMULA_BLEND_REC = 0.0

            self.H_IDX = 1
            self.H_N_OLD = self.H_N

            self.WIN_ELAPSED = 0.0
            self.WIN_AUTO_T = 0.0
            self.WIN_AUTO_N = 0.0
            self.WIN_SP_SUM = 0.0
            self.WIN_PV_SUM = 0.0
            self.WIN_AV_SUM = 0.0
            self.WIN_ERR_AREA_TOTAL = 0.0
            self.WIN_ERR_AREA_POS = 0.0
            self.WIN_ERR_AREA_NEG = 0.0
            self.WIN_ERR_PEAK_POS = 0.0
            self.WIN_ERR_PEAK_NEG = 0.0
            self.WIN_ERR_PEAK_ABS = 0.0
            self.WIN_CROSS_N = 0.0
            self.WIN_SEG_T = 0.0
            self.WIN_SEG_AREA = 0.0
            self.WIN_LAST_AREA1 = 0.0
            self.WIN_LAST_AREA2 = 0.0
            self.WIN_LAST_AREA3 = 0.0
            self.WIN_LAST_T1 = 0.0
            self.WIN_LAST_T2 = 0.0
            self.WIN_LAST_T3 = 0.0
            self.WIN_SEG_PEAK = 0.0
            self.WIN_LAST_PEAK1 = 0.0
            self.WIN_LAST_PEAK2 = 0.0
            self.WIN_LAST_PEAK3 = 0.0
            self.WIN_PV_MAX = PV
            self.WIN_PV_MIN = PV
            self.WIN_AV_MAX = AV
            self.WIN_AV_MIN = AV
            self.WIN_PV_STEP_SUM = 0.0
            self.WIN_PV_STEP_MAX = 0.0
            self.WIN_QUIET_STEP_SUM = 0.0
            self.WIN_QUIET_N = 0.0
            self.WIN_AUTO_AV_EVENT_N = 0.0
            self.WIN_EVENT_N = 0.0
            self.WIN_FIRST = True

            self.MAN_INIT = False
            self.MAN_ACTIVE = False
            self.MAN_RESP_ACTIVE = False
            self.MAN_RESP_CT = 0.0
            self.MAN_EVENT_CNT = 0.0
            self.MAN_EVENT_N = 0.0
            self.MAN_RESP_T_AUTO = 0.0
            self.MAN_RESP_T_USE = float(max(MAN_RESP_T, 1))
            self.MAN_GAIN_SUM = 0.0
            self.MAN_GAIN_N = 0.0
            self.MAN_BAD_N = 0.0
            self.MAN_RESP_VALID_T_SUM = 0.0
            self.MAN_RESP_VALID_N = 0.0

            for i in range(1, 25):
                self.H_VALID[i] = False
                self.H_EVENT_N[i] = 0.0
                self.H_SP_AVG[i] = 0.0
                self.H_PV_AVG[i] = 0.0
                self.H_AV_AVG[i] = 0.0
                self.H_ERR_ABS_AVG[i] = 0.0
                self.H_NOISE_EST[i] = 0.0
                self.H_GAIN[i] = 0.0

            self.INIT_DONE = True

        # ---- RUNNING:=EN（649）----
        self.RUNNING = EN

        if EN:
            self.WIN_ELAPSED = self.WIN_ELAPSED + self.CYCLE_S
            self.AUTO_ALLOWED = (
                ((COLLECT_MODE == 0) or (COLLECT_MODE == 1))
                and (RM == 1)
                and (not TS)
            )
            self.MAN_ALLOWED = ((COLLECT_MODE == 1) or (COLLECT_MODE == 2)) and TS

            # ---- 分析 SP 选择和自动寻找（656-691）----
            self.SPF1.step(
                dt_ms,
                EN=EN,
                RESET=RESET,
                CYCLE=self.CYCLE_S,
                SAMPLE_OK=self.AUTO_ALLOWED or self.MAN_ALLOWED,
                SP_MAN=SP_MAN,
                SP_MAN_EN=SP_MAN_EN,
                SP_TAG=SP,
                SP_TAG_EN=SP_TAG_EN,
                SP_AUTO_EN=SP_AUTO_EN,
                SP_AUTO_REPLACE_BAD_TAG=SP_AUTO_REPLACE_BAD_TAG,
                PV=PV,
                AV=AV,
                PVMU=PVMU,
                PVMD=PVMD,
                OUTT=MU,
                OUTB=MD,
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

            # ---- 当前窗口首次采样初始化（693-704）----
            if self.WIN_FIRST:
                self.ERR = self.SP_WORK - PV
                self.ERR_1 = self.ERR
                self.PV_1 = PV
                self.AV_1 = AV
                self.WIN_PV_MAX = PV
                self.WIN_PV_MIN = PV
                self.WIN_AV_MAX = AV
                self.WIN_AV_MIN = AV
                self.WIN_FIRST = False

            # ---- 自动状态流式统计（706-783）----
            if self.AUTO_ALLOWED:
                self.ERR = self.SP_WORK - PV
                self.ABS_ERR = abs(self.ERR)
                self.PV_STEP = abs(PV - self.PV_1)
                self.AV_STEP = abs(AV - self.AV_1)

                self.WIN_AUTO_T = self.WIN_AUTO_T + self.CYCLE_S
                self.WIN_AUTO_N = self.WIN_AUTO_N + 1
                self.WIN_SP_SUM = self.WIN_SP_SUM + self.SP_WORK
                self.WIN_PV_SUM = self.WIN_PV_SUM + PV
                self.WIN_AV_SUM = self.WIN_AV_SUM + AV
                self.WIN_ERR_AREA_TOTAL = (
                    self.WIN_ERR_AREA_TOTAL + self.ABS_ERR * self.CYCLE_S
                )

                if self.ERR >= 0:
                    self.WIN_ERR_AREA_POS = (
                        self.WIN_ERR_AREA_POS + self.ERR * self.CYCLE_S
                    )
                else:
                    self.WIN_ERR_AREA_NEG = (
                        self.WIN_ERR_AREA_NEG + abs(self.ERR) * self.CYCLE_S
                    )

                self.WIN_ERR_PEAK_POS = max(self.WIN_ERR_PEAK_POS, self.ERR)
                self.WIN_ERR_PEAK_NEG = min(self.WIN_ERR_PEAK_NEG, self.ERR)
                self.WIN_ERR_PEAK_ABS = max(self.WIN_ERR_PEAK_ABS, self.ABS_ERR)

                self.WIN_PV_MAX = max(self.WIN_PV_MAX, PV)
                self.WIN_PV_MIN = min(self.WIN_PV_MIN, PV)
                self.WIN_AV_MAX = max(self.WIN_AV_MAX, AV)
                self.WIN_AV_MIN = min(self.WIN_AV_MIN, AV)
                self.WIN_PV_STEP_SUM = self.WIN_PV_STEP_SUM + self.PV_STEP
                self.WIN_PV_STEP_MAX = max(self.WIN_PV_STEP_MAX, self.PV_STEP)

                if (self.AV_STEP < self.OUT_RANGE * 0.001) and (
                    self.ABS_ERR < self.PV_RANGE * 0.05
                ):
                    self.WIN_QUIET_STEP_SUM = self.WIN_QUIET_STEP_SUM + self.PV_STEP
                    self.WIN_QUIET_N = self.WIN_QUIET_N + 1

                self.WIN_SEG_AREA = self.WIN_SEG_AREA + self.ABS_ERR * self.CYCLE_S
                self.WIN_SEG_T = self.WIN_SEG_T + self.CYCLE_S
                self.WIN_SEG_PEAK = max(self.WIN_SEG_PEAK, self.ABS_ERR)
                if (self.WIN_AUTO_N > 1) and (self.ERR * self.ERR_1 < 0):
                    self.WIN_CROSS_N = self.WIN_CROSS_N + 1
                    self.WIN_EVENT_N = self.WIN_EVENT_N + 1
                    self.WIN_LAST_AREA3 = self.WIN_LAST_AREA2
                    self.WIN_LAST_AREA2 = self.WIN_LAST_AREA1
                    self.WIN_LAST_AREA1 = self.WIN_SEG_AREA
                    self.WIN_LAST_T3 = self.WIN_LAST_T2
                    self.WIN_LAST_T2 = self.WIN_LAST_T1
                    self.WIN_LAST_T1 = self.WIN_SEG_T
                    self.WIN_LAST_PEAK3 = self.WIN_LAST_PEAK2
                    self.WIN_LAST_PEAK2 = self.WIN_LAST_PEAK1
                    self.WIN_LAST_PEAK1 = self.WIN_SEG_PEAK
                    self.WIN_SEG_AREA = 0.0
                    self.WIN_SEG_T = 0.0
                    self.WIN_SEG_PEAK = 0.0

                if (self.AV_STEP > max(self.MAN_TH, self.OUT_RANGE * 0.001)) and (
                    not self.AUTO_AV_MOVING
                ):
                    self.WIN_AUTO_AV_EVENT_N = self.WIN_AUTO_AV_EVENT_N + 1
                    self.WIN_EVENT_N = self.WIN_EVENT_N + 1
                    self.AUTO_AV_MOVING = True
                    self.AUTO_AV_QUIET_T = 0.0
                if self.AUTO_AV_MOVING:
                    if self.AV_STEP < self.OUT_RANGE * 0.0005:
                        self.AUTO_AV_QUIET_T = self.AUTO_AV_QUIET_T + self.CYCLE_S
                        if self.AUTO_AV_QUIET_T >= max(MAN_MERGE_T, 1):
                            self.AUTO_AV_MOVING = False
                            self.AUTO_AV_QUIET_T = 0.0
                    else:
                        self.AUTO_AV_QUIET_T = 0.0

                self.ERR_1 = self.ERR
                self.PV_1 = PV
                self.AV_1 = AV

            # ---- 手动调整事件合并（785-853）----
            if self.MAN_ALLOWED:
                if not self.MAN_INIT:
                    self.MAN_LAST_AV = AV
                    self.MAN_INIT = True

                self.MAN_DAV = AV - self.MAN_LAST_AV
                if abs(self.MAN_DAV) >= self.MAN_TH:
                    if self.MAN_RESP_ACTIVE:
                        self.MAN_RESP_DELTA = max(
                            abs(self.MAN_RESP_PV_MAX - self.MAN_RESP_START_PV),
                            abs(self.MAN_RESP_PV_MIN - self.MAN_RESP_START_PV),
                        )
                        if (abs(self.MAN_RESP_NET_AV) >= self.MAN_TH) and (
                            self.MAN_RESP_DELTA
                            >= max(self.NOISE_EST * 2, self.PV_RANGE * 0.001)
                        ):
                            self.MAN_EVENT_CNT = self.MAN_EVENT_CNT + 1
                            self.WIN_EVENT_N = self.WIN_EVENT_N + 1
                            self.MAN_GAIN_SUM = self.MAN_GAIN_SUM + self.MAN_RESP_DELTA / max(
                                abs(self.MAN_RESP_NET_AV), 0.001
                            )
                            self.MAN_GAIN_N = self.MAN_GAIN_N + 1
                            self.MAN_RESP_VALID_T_SUM = (
                                self.MAN_RESP_VALID_T_SUM + self.MAN_RESP_CT
                            )
                            self.MAN_RESP_VALID_N = self.MAN_RESP_VALID_N + 1
                            if self.MAN_RESP_SUM_ABS_AV > abs(self.MAN_RESP_NET_AV) * 3:
                                self.MAN_BAD_N = self.MAN_BAD_N + 1
                        self.MAN_RESP_ACTIVE = False

                    if not self.MAN_ACTIVE:
                        self.MAN_ACTIVE = True
                        self.MAN_START_AV = self.MAN_LAST_AV
                        self.MAN_END_AV = AV
                        self.MAN_SUM_ABS_AV = abs(self.MAN_DAV)
                        self.MAN_START_PV = PV
                        self.MAN_NO_CHANGE_T = 0.0
                    else:
                        self.MAN_END_AV = AV
                        self.MAN_SUM_ABS_AV = self.MAN_SUM_ABS_AV + abs(self.MAN_DAV)
                        self.MAN_NO_CHANGE_T = 0.0
                    self.MAN_LAST_AV = AV
                else:
                    if self.MAN_ACTIVE:
                        self.MAN_NO_CHANGE_T = self.MAN_NO_CHANGE_T + self.CYCLE_S
                        if self.MAN_NO_CHANGE_T >= max(MAN_MERGE_T, 1):
                            self.MAN_ACTIVE = False
                            self.MAN_RESP_ACTIVE = True
                            self.MAN_RESP_CT = 0.0
                            self.MAN_RESP_START_PV = PV
                            self.MAN_RESP_PV_MAX = PV
                            self.MAN_RESP_PV_MIN = PV
                            self.MAN_RESP_NET_AV = self.MAN_END_AV - self.MAN_START_AV
                            self.MAN_RESP_SUM_ABS_AV = self.MAN_SUM_ABS_AV
            else:
                if self.MAN_ACTIVE:
                    self.MAN_ACTIVE = False
                    self.MAN_RESP_ACTIVE = True
                    self.MAN_RESP_CT = 0.0
                    self.MAN_RESP_START_PV = PV
                    self.MAN_RESP_PV_MAX = PV
                    self.MAN_RESP_PV_MIN = PV
                    self.MAN_RESP_NET_AV = self.MAN_END_AV - self.MAN_START_AV
                    self.MAN_RESP_SUM_ABS_AV = self.MAN_SUM_ABS_AV
                if not TS:
                    self.MAN_INIT = False

            # ---- 手动动作后的 PV 响应观察（855-875）----
            if self.MAN_RESP_ACTIVE:
                self.MAN_RESP_CT = self.MAN_RESP_CT + self.CYCLE_S
                self.MAN_RESP_PV_MAX = max(self.MAN_RESP_PV_MAX, PV)
                self.MAN_RESP_PV_MIN = min(self.MAN_RESP_PV_MIN, PV)
                if self.MAN_RESP_CT >= max(self.MAN_RESP_T_USE, 1):
                    self.MAN_RESP_DELTA = max(
                        abs(self.MAN_RESP_PV_MAX - self.MAN_RESP_START_PV),
                        abs(self.MAN_RESP_PV_MIN - self.MAN_RESP_START_PV),
                    )
                    if (abs(self.MAN_RESP_NET_AV) >= self.MAN_TH) and (
                        self.MAN_RESP_DELTA
                        >= max(self.NOISE_EST * 2, self.PV_RANGE * 0.001)
                    ):
                        self.MAN_EVENT_CNT = self.MAN_EVENT_CNT + 1
                        self.WIN_EVENT_N = self.WIN_EVENT_N + 1
                        self.MAN_GAIN_SUM = self.MAN_GAIN_SUM + self.MAN_RESP_DELTA / max(
                            abs(self.MAN_RESP_NET_AV), 0.001
                        )
                        self.MAN_GAIN_N = self.MAN_GAIN_N + 1
                        self.MAN_RESP_VALID_T_SUM = (
                            self.MAN_RESP_VALID_T_SUM + self.MAN_RESP_CT
                        )
                        self.MAN_RESP_VALID_N = self.MAN_RESP_VALID_N + 1
                        if self.MAN_RESP_SUM_ABS_AV > abs(self.MAN_RESP_NET_AV) * 3:
                            self.MAN_BAD_N = self.MAN_BAD_N + 1
                    self.MAN_RESP_ACTIVE = False

            # ---- 窗口结束：单窗口推荐并写入历史缓存（877-1586）----
            if (self.WIN_ELAPSED >= max(WIN_T, MIN_WIN_T)) or (
                self.CALC_R and (self.WIN_ELAPSED >= MIN_WIN_T)
            ):
                self._settle_window(
                    PV=PV,
                    AV=AV,
                    OUTT=OUTT,
                    OUTB=OUTB,
                    MIN_WIN_T=MIN_WIN_T,
                    MIN_STORE_EVENT=MIN_STORE_EVENT,
                    MIN_VALID_EVENT=MIN_VALID_EVENT,
                    FUSE_MIN_N=FUSE_MIN_N,
                    FUSE_MIN_WEIGHT=FUSE_MIN_WEIGHT,
                    SIM_SP_K=SIM_SP_K,
                    SIM_PV_K=SIM_PV_K,
                    SIM_AV_K=SIM_AV_K,
                    SIM_ERR_K=SIM_ERR_K,
                    SIM_SP_ABS=SIM_SP_ABS,
                    SIM_PV_ABS=SIM_PV_ABS,
                    SIM_AV_ABS=SIM_AV_ABS,
                    SIM_ERR_ABS=SIM_ERR_ABS,
                    SIM_RELAX_K=SIM_RELAX_K,
                    MAN_RESP_T=MAN_RESP_T,
                    MAN_RESP_T_MAX=MAN_RESP_T_MAX,
                    PT_IN=PT_IN,
                    TI_IN=TI_IN,
                    TD_IN=TD_IN,
                    PID_FORMULA_EN=PID_FORMULA_EN,
                    PID_LAMBDA_K=PID_LAMBDA_K,
                    PID_MODEL_L_K=PID_MODEL_L_K,
                    PID_FORMULA_BLEND=PID_FORMULA_BLEND,
                    TL_IN=TL_IN,
                    TZ_IN=TZ_IN,
                    GC1_IN=GC1_IN,
                    GC2_IN=GC2_IN,
                    CD_K_IN=CD_K_IN,
                    CD_K_FD_IN=CD_K_FD_IN,
                    CD_K_J_IN=CD_K_J_IN,
                    CD_K_D_IN=CD_K_D_IN,
                    AO1_IN=AO1_IN,
                    E1_IN=E1_IN,
                )

        # ---- CALC_OLD:=CALC_NOW（1589）----
        self.CALC_OLD = CALC_NOW
        return None

    def _settle_window(
        self,
        *,
        PV: float,
        AV: float,
        OUTT: float,
        OUTB: float,
        MIN_WIN_T: float,
        MIN_STORE_EVENT: float,
        MIN_VALID_EVENT: float,
        FUSE_MIN_N: float,
        FUSE_MIN_WEIGHT: float,
        SIM_SP_K: float,
        SIM_PV_K: float,
        SIM_AV_K: float,
        SIM_ERR_K: float,
        SIM_SP_ABS: float,
        SIM_PV_ABS: float,
        SIM_AV_ABS: float,
        SIM_ERR_ABS: float,
        SIM_RELAX_K: float,
        MAN_RESP_T: float,
        MAN_RESP_T_MAX: float,
        PT_IN: float,
        TI_IN: float,
        TD_IN: float,
        PID_FORMULA_EN: bool,
        PID_LAMBDA_K: float,
        PID_MODEL_L_K: float,
        PID_FORMULA_BLEND: float,
        TL_IN: float,
        TZ_IN: float,
        GC1_IN: float,
        GC2_IN: float,
        CD_K_IN: float,
        CD_K_FD_IN: float,
        CD_K_J_IN: float,
        CD_K_D_IN: float,
        AO1_IN: float,
        E1_IN: float,
    ) -> None:
        """窗口结算：摘要 → 有效性 → 四类单窗口推荐 → 写历史 → 三阶段融合 → 清窗口。

        对应 ST 877-1586（仅在窗口结束条件成立时执行）。
        """
        self.WINDOW_DONE = True
        self.WINDOW_T = self.WIN_ELAPSED
        self.AUTO_SAMPLE_T = self.WIN_AUTO_T
        self.CROSS_COUNT = self.WIN_CROSS_N
        self.MAN_EVENT_N = self.MAN_EVENT_CNT
        self.WINDOW_EVENT_N = self.WIN_EVENT_N
        self.PV_DELTA = self.WIN_PV_MAX - self.WIN_PV_MIN
        self.AV_DELTA = self.WIN_AV_MAX - self.WIN_AV_MIN

        if self.WIN_AUTO_N > 0:
            self.W_SP_AVG = self.WIN_SP_SUM / self.WIN_AUTO_N
            self.W_PV_AVG = self.WIN_PV_SUM / self.WIN_AUTO_N
            self.W_AV_AVG = self.WIN_AV_SUM / self.WIN_AUTO_N
            self.ERR_ABS_AVG = self.WIN_ERR_AREA_TOTAL / max(self.WIN_AUTO_T, 0.001)
        else:
            self.W_SP_AVG = self.SP_WORK
            self.W_PV_AVG = PV
            self.W_AV_AVG = AV
            self.ERR_ABS_AVG = 0.0

        self.ERR_AREA_POS = self.WIN_ERR_AREA_POS
        self.ERR_AREA_NEG = self.WIN_ERR_AREA_NEG
        self.ERR_PEAK_ABS = self.WIN_ERR_PEAK_ABS
        if self.WIN_CROSS_N > 0:
            self.AVG_CROSS_T = self.WIN_AUTO_T / max(self.WIN_CROSS_N, 1)
        else:
            self.AVG_CROSS_T = 0.0

        if self.WIN_QUIET_N > 5:
            self.NOISE_EST = max(
                (self.WIN_QUIET_STEP_SUM / max(self.WIN_QUIET_N, 1)) * 2,
                self.PV_RANGE * 0.001,
            )
        else:
            self.NOISE_EST = max(
                (self.WIN_PV_STEP_SUM / max(self.WIN_AUTO_N, 1)) * 0.5,
                self.PV_RANGE * 0.001,
            )
        self.NOISE_EST = min(
            max(self.NOISE_EST, self.PV_RANGE * 0.0005), self.PV_RANGE * 0.05
        )

        if self.MAN_GAIN_N > 0:
            self.PROCESS_GAIN = self.MAN_GAIN_SUM / self.MAN_GAIN_N
        elif (self.AV_DELTA >= self.OUT_RANGE * 0.005) and (
            self.PV_DELTA >= max(self.NOISE_EST * 3, self.PV_RANGE * 0.002)
        ):
            self.PROCESS_GAIN = self.PV_DELTA / max(self.AV_DELTA, 0.001)
        else:
            self.PROCESS_GAIN = 0.0
        self.W_GAIN = self.PROCESS_GAIN

        if self.WIN_LAST_AREA2 > 0:
            self.W_AREA_RATIO12 = self.WIN_LAST_AREA1 / self.WIN_LAST_AREA2
        else:
            self.W_AREA_RATIO12 = 0.0
        if self.WIN_LAST_AREA3 > 0:
            self.W_AREA_RATIO23 = self.WIN_LAST_AREA2 / self.WIN_LAST_AREA3
        else:
            self.W_AREA_RATIO23 = 0.0
        if (self.ERR_AREA_POS + self.ERR_AREA_NEG) > 0:
            self.W_AREA_BALANCE = abs(self.ERR_AREA_POS - self.ERR_AREA_NEG) / (
                self.ERR_AREA_POS + self.ERR_AREA_NEG
            )
        else:
            self.W_AREA_BALANCE = 1.0
        if self.WIN_LAST_PEAK2 > 0:
            self.W_PEAK_RATIO12 = self.WIN_LAST_PEAK1 / self.WIN_LAST_PEAK2
        else:
            self.W_PEAK_RATIO12 = 0.0
        if self.WIN_LAST_PEAK3 > 0:
            self.W_PEAK_RATIO23 = self.WIN_LAST_PEAK2 / self.WIN_LAST_PEAK3
        else:
            self.W_PEAK_RATIO23 = 0.0

        self.PID_PEAK_MIN = max(self.PV_RANGE * 0.02, self.NOISE_EST * 3)
        self.W_PID_AREA_VALID = (
            (self.WIN_LAST_AREA1 > 0)
            and (self.WIN_LAST_AREA2 > 0)
            and (self.WIN_LAST_AREA3 > 0)
            and (self.WIN_LAST_PEAK1 > self.PID_PEAK_MIN)
            and (self.WIN_LAST_PEAK2 > self.PID_PEAK_MIN)
            and (self.WIN_LAST_PEAK3 > self.PID_PEAK_MIN)
        )
        self.W_PID_AREA_DIVERGE = (
            self.W_PID_AREA_VALID
            and (self.W_AREA_RATIO12 > 1.1)
            and (self.W_AREA_RATIO23 > 1.1)
        )
        self.W_PID_AREA_EQUAL = (
            self.W_PID_AREA_VALID
            and (self.W_AREA_RATIO12 > 0.7)
            and (self.W_AREA_RATIO23 > 0.7)
            and (not self.W_PID_AREA_DIVERGE)
        )
        self.W_PID_AREA_OSC = (
            self.W_PID_AREA_VALID
            and (self.W_AREA_RATIO12 > 0.4)
            and (self.W_AREA_RATIO23 > 0.4)
            and (not self.W_PID_AREA_EQUAL)
            and (not self.W_PID_AREA_DIVERGE)
        )
        self.W_PID_PEAK_VALID = (
            (self.WIN_LAST_PEAK1 > self.PID_PEAK_MIN)
            and (self.WIN_LAST_PEAK2 > self.PID_PEAK_MIN)
            and (self.WIN_LAST_PEAK3 > self.PID_PEAK_MIN)
            and (self.WIN_LAST_T1 > 0)
            and (self.WIN_LAST_T2 > 0)
        )

        self.W_NOISE_HIGH = self.NOISE_EST > self.PV_RANGE * 0.01
        self.W_OSC = (self.WIN_CROSS_N >= 2) and (
            ((self.W_AREA_RATIO12 > 0.85) and (self.W_AREA_RATIO23 > 0.85))
            or (self.W_AREA_BALANCE < 0.35)
            or self.W_PID_AREA_DIVERGE
            or self.W_PID_AREA_EQUAL
        )
        # 注意：ST 用的是输入 E1_IN（不是 E1_REC）。
        self.W_SLOW = (self.WIN_CROSS_N == 0) and (
            self.ERR_ABS_AVG > max(E1_IN, self.NOISE_EST * 3)
        )

        if not self.RANGE_OK:
            self.WINDOW_VALID = False
            self.DATA_REASON = 3
        elif not self.SP_VALID:
            self.WINDOW_VALID = False
            self.DATA_REASON = 6
        elif self.WIN_ELAPSED < MIN_WIN_T:
            # 源遗留不可达分支（结算条件恒 WIN_ELAPSED>=MIN_WIN_T），原样保留。
            self.WINDOW_VALID = False
            self.DATA_REASON = 2
        elif self.WIN_EVENT_N < MIN_VALID_EVENT:
            self.WINDOW_VALID = True
            self.DATA_REASON = 4
        elif (self.PROCESS_GAIN <= 0) and (
            self.PV_DELTA < max(self.NOISE_EST * 3, self.PV_RANGE * 0.002)
        ):
            self.WINDOW_VALID = True
            self.DATA_REASON = 5
        else:
            self.WINDOW_VALID = True
            self.DATA_REASON = 1

        # ---- 单窗口 PID 推荐（986-1085）----
        self.W_PT = max(PT_IN, 0.001)
        self.W_TI = max(TI_IN, 0.001)
        self.W_TD = max(TD_IN, 0)
        self.W_PT_ABS = 0.0
        self.W_PID_FORMULA_VALID = False
        self.W_MODEL_GAIN_N = 0.0
        self.W_MODEL_T = 0.0
        self.W_MODEL_L = 0.0
        self.W_MODEL_LAMBDA = 0.0
        self.W_PT_FORMULA = 0.0
        self.W_TI_FORMULA = 0.0
        self.PID_BLEND_USE = min(max(PID_FORMULA_BLEND, 0), 1)
        if (
            PID_FORMULA_EN
            and (self.PROCESS_GAIN > 0.0001)
            and (not self.W_NOISE_HIGH)
            and self.RANGE_OK
        ):
            self.W_MODEL_GAIN_N = (
                self.PROCESS_GAIN * self.OUT_RANGE / max(self.PV_RANGE, 0.001)
            )
            self.W_PT_ABS = 100 * self.W_MODEL_GAIN_N
            if self.AVG_CROSS_T > 0:
                self.W_MODEL_T = max(self.AVG_CROSS_T, self.CYCLE_S)
            elif self.MAN_RESP_VALID_N > 0:
                self.W_MODEL_T = max(
                    (self.MAN_RESP_VALID_T_SUM / self.MAN_RESP_VALID_N) * 0.5,
                    self.CYCLE_S,
                )
            elif self.MAN_RESP_T_AUTO > 0:
                self.W_MODEL_T = max(self.MAN_RESP_T_AUTO * 0.5, self.CYCLE_S)
            else:
                self.W_MODEL_T = 0.0
            if self.W_MODEL_T > 0:
                self.W_MODEL_L = min(
                    max(
                        self.W_MODEL_T * min(max(PID_MODEL_L_K, 0.05), 0.8),
                        self.CYCLE_S,
                    ),
                    self.W_MODEL_T,
                )
                self.W_MODEL_LAMBDA = max(
                    self.W_MODEL_T, max(PID_LAMBDA_K, 0.5) * self.W_MODEL_L
                )
                self.W_PT_FORMULA = (
                    100
                    * self.W_MODEL_GAIN_N
                    * (self.W_MODEL_LAMBDA + self.W_MODEL_L)
                    / max(self.W_MODEL_T, 0.001)
                )
                self.W_TI_FORMULA = self.W_MODEL_T + self.W_MODEL_L * 0.5
                self.W_PT_FORMULA = min(max(self.W_PT_FORMULA, 1), 10000)
                self.W_TI_FORMULA = min(max(self.W_TI_FORMULA, 1), 10000)
                self.W_PT = (
                    self.W_PT * (1 - self.PID_BLEND_USE)
                    + self.W_PT_FORMULA * self.PID_BLEND_USE
                )
                self.W_TI = (
                    self.W_TI * (1 - self.PID_BLEND_USE)
                    + self.W_TI_FORMULA * self.PID_BLEND_USE
                )
                self.W_PID_FORMULA_VALID = True
        self.PID_FORMULA_VALID = self.W_PID_FORMULA_VALID
        self.PID_FORMULA_BLEND_REC = self.PID_BLEND_USE
        if self.W_PID_FORMULA_VALID:
            self.PT_FORMULA_REC = self.W_PT_FORMULA
            self.TI_FORMULA_REC = self.W_TI_FORMULA
            self.PID_MODEL_GAIN_REC = self.W_MODEL_GAIN_N
            self.PID_MODEL_T_REC = self.W_MODEL_T
            self.PID_MODEL_L_REC = self.W_MODEL_L
            self.PID_MODEL_LAMBDA_REC = self.W_MODEL_LAMBDA
        else:
            self.PT_FORMULA_REC = 0.0
            self.TI_FORMULA_REC = 0.0
            self.PID_MODEL_GAIN_REC = 0.0
            self.PID_MODEL_T_REC = 0.0
            self.PID_MODEL_L_REC = 0.0
            self.PID_MODEL_LAMBDA_REC = 0.0

        self.W_TI_THEORY = 0.0
        if self.W_PID_PEAK_VALID:
            if (
                (self.W_PEAK_RATIO12 < 1.1)
                and (self.W_PEAK_RATIO12 > 0.9)
                and (self.W_PEAK_RATIO23 < 1.1)
                and (self.W_PEAK_RATIO23 > 0.9)
            ):
                self.W_TI_THEORY = (self.WIN_LAST_T1 + self.WIN_LAST_T2) * 0.5
            elif (
                (self.W_PEAK_RATIO12 < 0.7)
                and (self.W_PEAK_RATIO12 > 0.5)
                and (self.W_PEAK_RATIO23 < 0.7)
                and (self.W_PEAK_RATIO23 > 0.5)
            ):
                self.W_TI_THEORY = (self.WIN_LAST_T1 + self.WIN_LAST_T2) * 0.4
            elif (
                (self.W_PEAK_RATIO12 < 0.3)
                and (self.W_PEAK_RATIO12 > 0.1)
                and (self.W_PEAK_RATIO23 < 0.3)
                and (self.W_PEAK_RATIO23 > 0.1)
            ):
                self.W_TI_THEORY = (self.WIN_LAST_T1 + self.WIN_LAST_T2) * 0.3
        if self.W_TI_THEORY > 0:
            self.W_TI_TARGET = min(
                max(self.W_TI_THEORY, self.W_TI * 0.5), self.W_TI * 2
            )
            self.W_TI = self.W_TI * 0.8 + self.W_TI_TARGET * 0.2

        if self.W_NOISE_HIGH:
            self.W_PT = self.W_PT * 1.10
            self.W_TI = self.W_TI * 1.10
            self.PID_REASON = 4
        elif self.W_PID_AREA_DIVERGE:
            self.W_PT = self.W_PT * 1.50
            self.W_TI = self.W_TI * 1.50
            self.PID_REASON = 3
        elif self.W_PID_AREA_EQUAL:
            self.W_PT = self.W_PT * 1.25
            self.W_TI = self.W_TI * 1.25
            self.PID_REASON = 3
        elif self.W_PID_AREA_OSC or self.W_OSC:
            self.W_PT = self.W_PT * 1.10
            self.W_TI = self.W_TI * 1.10
            self.PID_REASON = 3
        elif self.W_SLOW:
            self.W_PT = self.W_PT * 0.85
            if (AV < (OUTT - self.OUT_LIMIT_MARGIN)) and (
                AV > (OUTB + self.OUT_LIMIT_MARGIN)
            ):
                self.W_TI = self.W_TI * 0.80
            else:
                self.W_TI = self.W_TI * 0.90
            self.PID_REASON = 2
        else:
            self.PID_REASON = 1
        self.W_PT = min(max(self.W_PT, 1), 10000)
        self.W_TI = min(max(self.W_TI, 1), 10000)
        self.W_DI = min(
            max(
                100 * max(self.NOISE_EST * 1.5, self.PV_RANGE * 0.001) / self.PV_RANGE,
                0,
            ),
            5,
        )

        # ---- 单窗口 RSF 推荐（1087-1151）----
        self.W_E1 = max(self.NOISE_EST * 3, self.PV_RANGE * 0.002)
        self.W_E1 = min(max(self.W_E1, self.PV_RANGE * 0.001), self.PV_RANGE * 0.05)
        self.E4_BASE = max(self.W_E1 * 4, self.ERR_PEAK_ABS * 0.8)
        self.E4_BASE = min(max(self.E4_BASE, self.W_E1 * 4), self.PV_RANGE * 0.30)
        self.W_E2 = max(self.W_E1 * 2, self.E4_BASE * 0.35)
        self.W_E3 = max(self.W_E2 * 1.2, self.E4_BASE * 0.65)
        self.W_E4 = max(self.W_E3 * 1.2, self.E4_BASE)
        if self.AVG_CROSS_T > 0:
            self.W_TL = min(max(self.AVG_CROSS_T * 0.25, 3), 30)
        else:
            self.W_TL = min(max(TL_IN, 3), 30)
        if self.W_NOISE_HIGH:
            self.W_TL = min(max(self.W_TL, 10), 60)
        if self.AVG_CROSS_T > 0:
            self.BASE_T = self.AVG_CROSS_T
        else:
            self.BASE_T = max(self.W_TL * 3, 30)
        self.W_TL1 = max(self.W_TL + 1, self.BASE_T * 0.5)
        self.W_TL2 = max(self.W_TL1, self.BASE_T * 0.75)
        self.W_TL3 = max(self.W_TL2, self.BASE_T)
        self.W_TL4 = max(self.W_TL3, self.BASE_T * 1.25)
        # 更新手动响应观察自动估算时间（1112-1125）
        if self.WINDOW_VALID and (self.WIN_EVENT_N >= MIN_STORE_EVENT):
            self.MAN_RESP_T_AUTO = 0.0
            if self.AVG_CROSS_T > 0:
                self.MAN_RESP_T_AUTO = max(
                    self.MAN_RESP_T_AUTO, self.AVG_CROSS_T * 2
                )
            if self.MAN_RESP_VALID_N > 0:
                self.MAN_RESP_T_AUTO = max(
                    self.MAN_RESP_T_AUTO,
                    (self.MAN_RESP_VALID_T_SUM / self.MAN_RESP_VALID_N) * 1.5,
                )
            self.MAN_RESP_T_AUTO = max(self.MAN_RESP_T_AUTO, self.W_TL4)
            self.MAN_RESP_T_AUTO = float(
                min(
                    max(self.MAN_RESP_T_AUTO, 0),
                    max(max(MAN_RESP_T_MAX, MAN_RESP_T), 1),
                )
            )
            self.MAN_RESP_T_USE = max(max(MAN_RESP_T, self.MAN_RESP_T_AUTO), 1)
            self.MAN_RESP_T_USE = float(min(
                self.MAN_RESP_T_USE, max(max(MAN_RESP_T_MAX, MAN_RESP_T), 1)
            ))
        if self.PROCESS_GAIN > 0.0001:
            self.BASE_AO = max(
                self.W_E1 / self.PROCESS_GAIN * 0.35, self.OUT_RANGE * 0.003
            )
        else:
            self.BASE_AO = max(AO1_IN, self.OUT_RANGE * 0.005)
        self.BASE_AO = min(
            max(self.BASE_AO, self.OUT_RANGE * 0.001), self.OUT_RANGE * 0.05
        )
        if self.W_OSC:
            self.BASE_AO = self.BASE_AO * 0.7
        elif self.W_SLOW:
            self.BASE_AO = self.BASE_AO * 1.2
        self.W_AO1 = self.BASE_AO
        self.W_AO2 = max(self.W_AO1 * 1.5, self.W_AO1 + self.OUT_RANGE * 0.002)
        self.W_AO3 = max(self.W_AO2 * 1.4, self.W_AO2 + self.OUT_RANGE * 0.002)
        self.W_AO4 = max(self.W_AO3 * 1.3, self.W_AO3 + self.OUT_RANGE * 0.002)
        self.W_AO4 = min(self.W_AO4, self.OUT_RANGE * 0.20)
        self.W_AO3 = min(self.W_AO3, self.W_AO4)
        self.W_AO2 = min(self.W_AO2, self.W_AO3)
        self.W_AO1 = min(self.W_AO1, self.W_AO2)
        self.W_RSF_LOCK_T = max(30, self.W_TL * 3)
        if self.AVG_CROSS_T > 0:
            self.W_RSF_LOCK_T = max(self.W_RSF_LOCK_T, self.AVG_CROSS_T)
        self.W_RSF_LOCK_T = min(self.W_RSF_LOCK_T, 120)
        self.W_SVL = min(max(100 * self.W_E1 / self.PV_RANGE, 0), 20)
        self.W_SVH = min(max(100 * self.W_E3 / self.PV_RANGE, self.W_SVL), 80)

        # ---- 单窗口观测器推荐（1153-1176）----
        self.W_TC = max(self.W_TL, 5)
        if self.AVG_CROSS_T > 0:
            self.W_TZ = min(max(self.AVG_CROSS_T * 0.5, 5), 120)
        else:
            self.W_TZ = min(max(TZ_IN, 5), 120)
        self.W_GC1 = max(GC1_IN, 0)
        self.W_GC2 = max(GC2_IN, 0)
        if self.W_SLOW:
            self.W_GC1 = max(self.W_GC1, 1)
            self.GC_REASON = 2
        elif (self.WIN_PV_STEP_MAX > self.NOISE_EST * 5) and (not self.W_NOISE_HIGH):
            self.W_GC2 = max(self.W_GC2, 1)
            self.GC_REASON = 3
        elif self.W_NOISE_HIGH:
            self.W_GC2 = min(self.W_GC2, max(GC2_IN * 0.7, 0.5))
            self.GC_REASON = 4
        else:
            self.GC_REASON = 1
        self.LIMIT_TEMP = min(
            max(self.OUT_RANGE * 0.10, self.OUT_RANGE * 0.05), self.OUT_RANGE * 0.15
        )
        self.W_OUTH = self.LIMIT_TEMP
        self.W_OUTL = -self.LIMIT_TEMP

        # ---- 单窗口重叠控制推荐（1178-1200）----
        self.W_CD_GD = max(self.W_E3, self.NOISE_EST * 5)
        self.W_CD_K = min(max(CD_K_IN, 0.3), 0.7)
        self.W_CD_K_FD = min(max(CD_K_FD_IN, 0.5), 2)
        self.W_CD_K_J = max(CD_K_J_IN, 1)
        if self.W_NOISE_HIGH:
            self.W_CD_K_D = min(max(CD_K_D_IN * 0.7, 0.2), 1)
            self.CD_REASON = 4
        elif self.WIN_PV_STEP_MAX > self.NOISE_EST * 6:
            self.W_CD_K_D = max(CD_K_D_IN, 1)
            self.CD_REASON = 3
        elif self.ERR_PEAK_ABS > self.W_E4:
            self.W_CD_K_D = max(CD_K_D_IN, 1)
            self.CD_REASON = 2
        else:
            self.W_CD_K_D = max(CD_K_D_IN, 0)
            self.CD_REASON = 1
        self.LIMIT_TEMP = min(
            max(self.OUT_RANGE * 0.10, self.OUT_RANGE * 0.05), self.OUT_RANGE * 0.20
        )
        self.W_CDH = self.LIMIT_TEMP
        self.W_CDL = -self.LIMIT_TEMP
        self.W_TC_CD = max(self.W_TL, 5)
        self.W_TZ_CD = self.W_TZ

        # ---- 写入历史缓存（1202-1260）----
        if self.WINDOW_VALID and (self.WIN_EVENT_N >= MIN_STORE_EVENT):
            self.H_VALID[self.H_IDX] = True
            self.H_SP_AVG[self.H_IDX] = self.W_SP_AVG
            self.H_PV_AVG[self.H_IDX] = self.W_PV_AVG
            self.H_AV_AVG[self.H_IDX] = self.W_AV_AVG
            self.H_ERR_ABS_AVG[self.H_IDX] = self.ERR_ABS_AVG
            self.H_EVENT_N[self.H_IDX] = self.WIN_EVENT_N
            self.H_NOISE_EST[self.H_IDX] = self.NOISE_EST
            self.H_GAIN[self.H_IDX] = self.PROCESS_GAIN
            for _rec, h_name, w_name in self._FUSE_MAP:
                getattr(self, h_name)[self.H_IDX] = getattr(self, w_name)

            self.HISTORY_COUNT = float(min(self.HISTORY_COUNT + 1, self.H_N))
            self.H_IDX = self.H_IDX + 1
            if self.H_IDX > self.H_N:
                self.H_IDX = 1

        # ---- 三阶段相似工况匹配并融合（1262-1422）----
        self.FINAL_STRONG = False
        self.FINAL_WEAK = False
        self.MATCH_LEVEL = 0
        self.FUSE_WEIGHT = 0.0
        self.SIM_RELAX_USE = max(SIM_RELAX_K, 1)
        self.SIM_SP_PROP = self.PV_RANGE * max(SIM_SP_K, 0)
        self.SIM_PV_PROP = self.PV_RANGE * max(SIM_PV_K, 0)
        self.SIM_AV_PROP = self.OUT_RANGE * max(SIM_AV_K, 0)
        self.SIM_ERR_PROP = self.PV_RANGE * max(SIM_ERR_K, 0)

        for stage in range(1, 4):
            self.MATCH_STAGE = stage
            if not self.FINAL_STRONG:
                self.SIMILAR_COUNT = 0.0
                self.FUSE_SUM_W = 0.0
                for rec_name, _h, _w in self._FUSE_MAP:
                    setattr(self, rec_name, 0.0)

                if SIM_SP_ABS > 0:
                    self.SIM_SP_LIMIT = SIM_SP_ABS
                else:
                    self.SIM_SP_LIMIT = self.SIM_SP_PROP
                if SIM_PV_ABS > 0:
                    self.SIM_PV_LIMIT = SIM_PV_ABS
                else:
                    self.SIM_PV_LIMIT = self.SIM_PV_PROP
                if SIM_AV_ABS > 0:
                    self.SIM_AV_LIMIT = SIM_AV_ABS
                else:
                    self.SIM_AV_LIMIT = self.SIM_AV_PROP
                if SIM_ERR_ABS > 0:
                    self.SIM_ERR_LIMIT = SIM_ERR_ABS
                else:
                    self.SIM_ERR_LIMIT = self.SIM_ERR_PROP

                if self.MATCH_STAGE == 2:
                    self.SIM_SP_LIMIT = self.SIM_SP_LIMIT * self.SIM_RELAX_USE
                    self.SIM_PV_LIMIT = self.SIM_PV_LIMIT * self.SIM_RELAX_USE
                    self.SIM_AV_LIMIT = self.SIM_AV_LIMIT * self.SIM_RELAX_USE
                    self.SIM_ERR_LIMIT = self.SIM_ERR_LIMIT * self.SIM_RELAX_USE
                elif self.MATCH_STAGE == 3:
                    self.SIM_SP_LIMIT = max(
                        self.SIM_SP_LIMIT * self.SIM_RELAX_USE, self.SIM_SP_PROP
                    )
                    self.SIM_PV_LIMIT = max(
                        self.SIM_PV_LIMIT * self.SIM_RELAX_USE, self.SIM_PV_PROP
                    )
                    self.SIM_AV_LIMIT = max(
                        self.SIM_AV_LIMIT * self.SIM_RELAX_USE, self.SIM_AV_PROP
                    )
                    self.SIM_ERR_LIMIT = max(
                        self.SIM_ERR_LIMIT * self.SIM_RELAX_USE, self.SIM_ERR_PROP
                    )

                for i in range(1, self.H_N + 1):
                    self.MATCH_OK = (
                        self.H_VALID[i]
                        and (self.H_EVENT_N[i] >= MIN_STORE_EVENT)
                        and (abs(self.H_SP_AVG[i] - self.W_SP_AVG) <= self.SIM_SP_LIMIT)
                        and (abs(self.H_PV_AVG[i] - self.W_PV_AVG) <= self.SIM_PV_LIMIT)
                        and (abs(self.H_AV_AVG[i] - self.W_AV_AVG) <= self.SIM_AV_LIMIT)
                        and (
                            abs(self.H_ERR_ABS_AVG[i] - self.ERR_ABS_AVG)
                            <= self.SIM_ERR_LIMIT
                        )
                    )

                    if self.MATCH_OK:
                        self.FUSE_W = min(
                            max(self.H_EVENT_N[i] / max(MIN_VALID_EVENT, 1), 0.1), 1
                        )
                        self.SIMILAR_COUNT = self.SIMILAR_COUNT + 1
                        self.FUSE_SUM_W = self.FUSE_SUM_W + self.FUSE_W
                        for rec_name, h_name, _w in self._FUSE_MAP:
                            setattr(
                                self,
                                rec_name,
                                getattr(self, rec_name)
                                + getattr(self, h_name)[i] * self.FUSE_W,
                            )

                self.FUSE_STRONG = (self.SIMILAR_COUNT >= FUSE_MIN_N) and (
                    self.FUSE_SUM_W >= FUSE_MIN_WEIGHT
                )
                if self.FUSE_SUM_W > 0:
                    self.MATCH_LEVEL = self.MATCH_STAGE
                if self.FUSE_STRONG:
                    self.FINAL_STRONG = True

        if self.FUSE_SUM_W > 0:
            for rec_name, _h, _w in self._FUSE_MAP:
                setattr(self, rec_name, getattr(self, rec_name) / self.FUSE_SUM_W)
        else:
            for rec_name, _h, w_name in self._FUSE_MAP:
                setattr(self, rec_name, float(getattr(self, w_name)))

        self.FINAL_STRONG = (self.SIMILAR_COUNT >= FUSE_MIN_N) and (
            self.FUSE_SUM_W >= FUSE_MIN_WEIGHT
        )
        self.FINAL_WEAK = (self.FUSE_SUM_W > 0) and (not self.FINAL_STRONG)
        self.FINAL_VALID = self.FINAL_STRONG
        if self.FUSE_SUM_W <= 0:
            self.MATCH_LEVEL = 0
        self.FUSE_WEIGHT = self.FUSE_SUM_W
        self.PID_OK = self.FINAL_VALID and self.WINDOW_VALID
        self.RSF_OK = self.FINAL_VALID and self.WINDOW_VALID
        self.GC_OK = self.FINAL_VALID and self.WINDOW_VALID and (not self.W_NOISE_HIGH)
        self.CD_OK = (
            self.FINAL_VALID
            and self.WINDOW_VALID
            and (self.ERR_PEAK_ABS > max(self.E3_REC, self.NOISE_EST * 5))
        )
        if not self.FINAL_VALID:
            self.PID_REASON = 5
            self.RSF_REASON = 5
            self.GC_REASON = 5
            self.CD_REASON = 5
        elif self.W_NOISE_HIGH:
            self.RSF_REASON = 4
        elif self.W_OSC:
            self.RSF_REASON = 3
        elif self.W_SLOW:
            self.RSF_REASON = 2
        else:
            self.RSF_REASON = 1

        # ---- 清空当前窗口，保留历史缓存和最终推荐（1538-1585）----
        self.WIN_ELAPSED = 0.0
        self.WIN_AUTO_T = 0.0
        self.WIN_AUTO_N = 0.0
        self.WIN_SP_SUM = 0.0
        self.WIN_PV_SUM = 0.0
        self.WIN_AV_SUM = 0.0
        self.WIN_ERR_AREA_TOTAL = 0.0
        self.WIN_ERR_AREA_POS = 0.0
        self.WIN_ERR_AREA_NEG = 0.0
        self.WIN_ERR_PEAK_POS = 0.0
        self.WIN_ERR_PEAK_NEG = 0.0
        self.WIN_ERR_PEAK_ABS = 0.0
        self.WIN_CROSS_N = 0.0
        self.WIN_SEG_T = 0.0
        self.WIN_SEG_AREA = 0.0
        self.WIN_LAST_AREA1 = 0.0
        self.WIN_LAST_AREA2 = 0.0
        self.WIN_LAST_AREA3 = 0.0
        self.WIN_LAST_T1 = 0.0
        self.WIN_LAST_T2 = 0.0
        self.WIN_LAST_T3 = 0.0
        self.WIN_SEG_PEAK = 0.0
        self.WIN_LAST_PEAK1 = 0.0
        self.WIN_LAST_PEAK2 = 0.0
        self.WIN_LAST_PEAK3 = 0.0

        self.WIN_PV_MAX = PV
        self.WIN_PV_MIN = PV
        self.WIN_AV_MAX = AV
        self.WIN_AV_MIN = AV
        self.WIN_PV_STEP_SUM = 0.0
        self.WIN_PV_STEP_MAX = 0.0
        self.WIN_QUIET_STEP_SUM = 0.0
        self.WIN_QUIET_N = 0.0
        self.WIN_AUTO_AV_EVENT_N = 0.0
        self.WIN_EVENT_N = 0.0
        self.WIN_FIRST = True

        self.MAN_EVENT_CNT = 0.0
        self.MAN_GAIN_SUM = 0.0
        self.MAN_GAIN_N = 0.0
        self.MAN_BAD_N = 0.0
        self.MAN_RESP_VALID_T_SUM = 0.0
        self.MAN_RESP_VALID_N = 0.0
