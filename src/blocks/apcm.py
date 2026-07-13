"""业务块 APCM：APC 智能综合控制模块。

本模块按 CODESYS 功能块实例语义迁移：一个长期存在的 ``APCM`` Python 对象
对应一个在普通 ``VAR`` 区声明的 APCM FB 实例。所有参数、输出、触发器、定时器、
子功能块和历史量均保存在 ``self.*``，跨 ``step()`` 扫描周期保持；``RETAIN`` /
``PERSISTENT RETAIN`` 的重启恢复不属于本轮范围。

``ZLOUT`` 是源 ST 的 ``VAR_IN_OUT``。Python 侧用可变引用 ``RealRef`` 适配，
调用方必须每拍传入外部实际引用。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.compat import real_to_time_ms
from src.globals import LicenseContext
from src.primitives import BLINK, F_TRIG, R_TRIG, TOF, TON

from .apchsfop import APCHSFOP
from .apchshllim import APCHSHLLIM
from .apcmautopara import APCMAUTOPARA
from .apcpidzzd import APCPIDZZD
from .apcstatistics import APCSTATISTICS


BLINK_TIMEHIGH_MS: int = 500
"""APCM 内部 BLINK1/BLINK2 的有效高电平宽度。

源 ST 写 ``T#300MS``；项目对同任务 500ms 采样场景按既有 GC/CD 约定量化为 500ms。
"""

FOP_DEFAULT_TB_SEC: float = 0.5
"""APCHSFOP 的源默认 ``TB``。APCM 源中 FOP1/FOP2 未显式传 TB。"""

FOP_KG: float = 1.0
TOF1_PT_MS: int = 5000


@dataclass
class RealRef:
    """``VAR_IN_OUT REAL`` 的最小可变引用适配。"""

    value: float


def _sel(g: bool, in0, in1):
    """CODESYS ``SEL(G, IN0, IN1)``：``G=False`` 取 ``IN0``，否则取 ``IN1``。"""

    return in1 if g else in0


class APCM:
    """APC 智能综合控制模块。

    构造::

        apcm = APCM(license_context)

    扫描周期推进::

        apcm.step(dt_ms, SP=sp, PV=pv, OC=oc, TS=ts, TP=tp, zlout_ref=zlout)

    过程量作为本拍输入管脚传入；上位/HMI 可直接读写长期配置字段
    （如 ``apcm.PT``、``apcm.TI``、``apcm.CD_GD``）。``step()`` 不接收
    PID/RSF/GC/CD/APARA 配置参数，避免默认实参每拍覆盖长期状态。
    """

    _APARA_MIRRORS = (
        "RUNNING",
        "WINDOW_DONE",
        "FINAL_STRONG",
        "FINAL_WEAK",
        "MATCH_LEVEL",
        "DATA_REASON",
        "SP_USE",
        "SP_AUTO",
        "SP_AUTO_CONF",
        "SP_STABLE_T_OUT",
        "SP_VALID",
        "SP_AUTO_OK",
        "SP_TAG_BAD",
        "SP_SOURCE",
        "SP_REASON",
        "PID_OK",
        "RSF_OK",
        "GC_OK",
        "CD_OK",
        "PID_REASON",
        "RSF_REASON",
        "GC_REASON",
        "CD_REASON",
        "HISTORY_COUNT",
        "SIMILAR_COUNT",
        "FUSE_WEIGHT",
        "WINDOW_EVENT_N",
        "WINDOW_T",
        "PT_REC",
        "TI_REC",
        "TD_REC",
        "DI_REC",
        "SVH_REC",
        "SVL_REC",
        "PID_FORMULA_VALID",
        "PT_FORMULA_REC",
        "TI_FORMULA_REC",
        "TL_REC",
        "TL1_REC",
        "TL2_REC",
        "TL3_REC",
        "TL4_REC",
        "E1_REC",
        "E2_REC",
        "E3_REC",
        "E4_REC",
        "AO1_REC",
        "AO2_REC",
        "AO3_REC",
        "AO4_REC",
        "RSF_LOCK_T_REC",
        "TC_REC",
        "TZ_REC",
        "GC1_REC",
        "GC2_REC",
        "OUTH_REC",
        "OUTL_REC",
        "CD_GD_REC",
        "CD_K_REC",
        "CD_K_FD_REC",
        "CD_K_J_REC",
        "CD_K_D_REC",
        "CDH_REC",
        "CDL_REC",
        "TC_CD_REC",
        "TZ_CD_REC",
    )

    def __init__(self, license_context: LicenseContext) -> None:
        self._ctx = license_context

        # ===== VAR_INPUT / 上位可调字段 =====
        self.SP: float = 0.0
        self.PV: float = 0.0
        self.OC: float = 0.0
        self.TS: bool = False
        self.TP: float = 0.0
        self.RM: int = 1

        self.PT: float = 100.0
        self.KP: float = 0.0
        self.TI: float = 150.0
        self.KI: float = 0.0
        self.TD: float = 0.0
        self.KD: float = 1.0

        self.OUTT: float = 0.0
        self.OUTB: float = 0.0
        self.SADD: bool = False
        self.SSUB: bool = False
        self.ZLEN: bool = False
        self.ZSYK: float = 1.0

        # ===== VAR_OUTPUT =====
        self.AV: float = 0.0
        self.AV_P: float = 0.0
        self.AV_R: float = 0.0
        self.AV_GC: float = 0.0
        self.AV_J: float = 0.0
        self.AV_D: float = 0.0
        self.AV_C: float = 0.0

        self.ZLOUT: float = 0.0

        # ===== 公共 / GC / 振荡判断 =====
        self.SP_RH: float = 500.0
        self.PCMMS: int = 0
        self.AD: bool = True
        self.LASTSP_V: float = 0.0
        self.SP_V: float = 0.0

        self.GCEN: bool = False
        self.TC: float = 10.0
        self.TZ: float = 10.0
        self.GC1: float = 1.0
        self.GC2: float = 6.0
        self.OUTH: float = 100.0
        self.OUTL: float = -100.0
        self.AV_TEMP1: float = 0.0
        self.JZ_ZUP1: float = 0.0
        self.JZ_ZUP: float = 0.0
        self.JZ_Z: float = 0.0
        self.AV_J_GC: float = 0.0
        self.AV_D_GC: float = 0.0

        self.GCAUTO: bool = False
        self.GCEN2: bool = False
        self.PVMAX: float = 0.0
        self.PVMIN: float = 0.0
        self.NUM: float = 0.0
        self.SUMMAX: float = 0.0
        self.SUMMIN: float = 0.0
        self.SBMAX: bool = False
        self.SBMIN: bool = False
        self.JSMJ: list[float] = [0.0] * 5
        self.PVZD: bool = False

        # ===== RSF =====
        self.RSFEN: bool = False
        self.TL: float = 5.0
        self.RSF_HYS: float = 0.8
        self.RSF_FAST_HYS: float = 0.5
        self.RSF_TLOUT_K: float = 0.5
        self.RSF_LOCK_T: float = 30.0
        self.EK_R: float = 0.0
        self.EK_R_1: float = 0.0
        self.E1_OUT: float = 0.0
        self.E1_FAST_OUT: float = 0.0
        self.TL_OUT: float = 0.0
        self.CT_RSF_OUT: float = 0.0
        self.RSF_EXIT: bool = False
        self.CT_TL: int = 0
        self.TL_TEMP: float = 0.0
        self.SIG: float = 0.0
        self.TL1: float = 10.0
        self.TL2: float = 10.0
        self.TL3: float = 10.0
        self.TL4: float = 10.0
        self.E1: float = 1.0
        self.E2: float = 2.0
        self.E3: float = 3.0
        self.E4: float = 4.0
        self.AO1: float = 0.3
        self.AO2: float = 0.4
        self.AO3: float = 0.5
        self.AO4: float = 0.6
        self.AV_R_TEMP: float = 0.0
        self.AV_R_TEMP1: float = 0.0
        self.AV_R_TEMP2: float = 0.0
        self.FLAG_1: float = 0.0
        self.FLAG: float = 0.0
        self.ZF_K: float = 0.0
        self.CT_1: float = 0.0
        self.CT_2: float = 0.0
        self.CT_3: float = 0.0
        self.CT_4: float = 0.0
        self.CT_1_1: float = 0.0
        self.CT_2_1: float = 0.0
        self.CT_3_1: float = 0.0
        self.CT_4_1: float = 0.0
        self.QJ1: bool = False
        self.QJ2: bool = False
        self.QJ3: bool = False
        self.QJ4: bool = False
        self.RSF_LOCK_LEVEL: float = 0.0
        self.RSF_LOCK_SIG: float = 0.0
        self.CT_RSF_LOCK: float = 0.0
        self.RSF_LOCK_R1: bool = False
        self.RSF_LOCK_R2: bool = False
        self.RSF_LOCK_R3: bool = False
        self.RSF_LOCK_R4: bool = False

        # ===== 重叠控制 =====
        self.CDEN: bool = False
        self.CD_GD: float = 0.0
        self.CD_K: float = 0.0
        self.CD_K_FD: float = 0.0
        self.CD_K_J: float = 0.0
        self.CD_K_D: float = 0.0
        self.CD_BH: float = 0.0
        self.CDH: float = 10.0
        self.CDL: float = -10.0
        self.TC_CD: float = 10.0
        self.TZ_CD: float = 10.0
        self.AV_C_TEMP: float = 0.0
        self.FLG: float = 0.0
        self.JZ_ZUP3: float = 0.0
        self.JZ_ZUP2: float = 0.0
        self.JZ_Z1: float = 0.0
        self.AV_D_TEMP: float = 0.0
        self.AV_D_TEMP_1: float = 0.0

        # ===== PID =====
        self.PVMU: float = 10.0
        self.PVMD: float = 0.0
        self.MU: float = 10.0
        self.MD: float = -10.0
        self.OutRL: float = 0.0
        self.PT1K: float = 0.0
        self.TI1K: float = 0.0
        self.CYCLE: float = 0.5
        self.OutM: int = 0
        self.TM: bool = False
        self.MI: float = 0.0
        self.MS: float = 0.0
        self.MM: int = 0
        self.ATE: bool = True
        self.DI: float = 0.0
        self.SVH: float = 50.0
        self.SVL: float = 0.0
        self.PT1: float = 0.0
        self.TI1: float = 0.0
        self.preRM: int = 0
        self.nowRM: int = 0
        self.UK_1: float = 0.0
        self.DU_1: float = 0.0
        self.EK_1: float = 0.0
        self.EK_2: float = 0.0
        self.DEK: float = 0.0
        self.DEK_1: float = 0.0
        self.DEK_2: float = 0.0
        self.PV_LAST: float = 0.0
        self.deadenter: int = 0
        self.TIi: float = 0.0
        self.PX: float = 0.0
        self.SADD_Z: bool = False
        self.SADD_TEMP: bool = False
        self.SSUB_TEMP: bool = False
        self.SSUB_Z: bool = False
        self.AV_P_TEMP: float = 0.0
        self.AV_TEMP: float = 0.0
        self.ZSYK_TEMP: float = 0.0
        self.XZ_K: float = 0.5
        self.XZEN: bool = False
        self.AV_1: float = 0.0
        self.RTH: float = 100.0
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
        self.SV_SJH: float = 0.0
        self.SV_SJL: float = 0.0
        self.AV_TEMP2: float = 0.0
        self.EK_LAST: float = 0.0
        self.AO1_1: float = 0.0
        self.AO2_1: float = 0.0
        self.AO3_1: float = 0.0
        self.AO4_1: float = 0.0
        self.PT_1: float = 0.0
        self.ZSYK_RSF: float = 1.0

        # ===== APCMAUTOPARA 参数 / 镜像 =====
        self.APARA_EN: bool = False
        self.APARA_RESET: bool = False
        self.APARA_CALC_NOW: bool = False
        self.APARA_COLLECT_MODE: int = 1
        self.APARA_SP_MAN: float = 0.0
        self.APARA_SP_MAN_EN: bool = False
        self.APARA_SP_TAG_EN: bool = True
        self.APARA_SP_AUTO_EN: bool = True
        self.APARA_SP_AUTO_REPLACE_BAD_TAG: bool = False
        self.APARA_SP_STABLE_T: float = 300.0
        self.APARA_SP_CONF_T: float = 900.0
        self.APARA_SP_PV_STABLE_ABS: float = 0.0
        self.APARA_SP_AV_STABLE_ABS: float = 0.0
        self.APARA_WIN_T: float = 7200.0
        self.APARA_MIN_WIN_T: float = 300.0
        self.APARA_MIN_STORE_EVENT: float = 1.0
        self.APARA_MIN_VALID_EVENT: float = 5.0
        self.APARA_HISTORY_N: int = 24
        self.APARA_FUSE_MIN_N: float = 3.0
        self.APARA_FUSE_MIN_WEIGHT: float = 3.0
        self.APARA_SIM_SP_K: float = 0.05
        self.APARA_SIM_PV_K: float = 0.05
        self.APARA_SIM_AV_K: float = 0.10
        self.APARA_SIM_ERR_K: float = 0.05
        self.APARA_SIM_SP_ABS: float = 0.0
        self.APARA_SIM_PV_ABS: float = 0.0
        self.APARA_SIM_AV_ABS: float = 0.0
        self.APARA_SIM_ERR_ABS: float = 0.0
        self.APARA_SIM_RELAX_K: float = 2.0
        self.APARA_MAN_MERGE_T: float = 10.0
        self.APARA_MAN_RESP_T: float = 60.0
        self.APARA_MAN_RESP_T_MAX: float = 7200.0
        self.APARA_MAN_AV_MIN: float = 0.1
        self.APARA_PID_FORMULA_EN: bool = True
        self.APARA_PID_LAMBDA_K: float = 1.5
        self.APARA_PID_MODEL_L_K: float = 0.2
        self.APARA_PID_FORMULA_BLEND: float = 0.8
        self.APARA_APPLY_PID: bool = False
        self.APARA_APPLY_RSF: bool = False
        self.APARA_APPLY_GC: bool = False
        self.APARA_APPLY_CD: bool = False
        self.APARA_LAST_APPLY_GROUP: int = 0
        self.APARA_LAST_APPLY_OK: bool = False
        self.APARA_LAST_APPLY_REASON: int = 0
        self.APARA_CAN_APPLY: bool = False
        self.APARA_RESET_PLS: bool = False
        self.APARA_CALC_NOW_PLS: bool = False
        for name in self._APARA_MIRRORS:
            setattr(self, f"APARA_{name}", 0.0)
        for name in (
            "APARA_RUNNING",
            "APARA_WINDOW_DONE",
            "APARA_FINAL_STRONG",
            "APARA_FINAL_WEAK",
            "APARA_SP_VALID",
            "APARA_SP_AUTO_OK",
            "APARA_SP_TAG_BAD",
            "APARA_PID_OK",
            "APARA_RSF_OK",
            "APARA_GC_OK",
            "APARA_CD_OK",
            "APARA_PID_FORMULA_VALID",
        ):
            setattr(self, name, False)
        self.APARA_MATCH_LEVEL = 0
        self.APARA_DATA_REASON = 0
        self.APARA_SP_SOURCE = 0
        self.APARA_SP_REASON = 0
        self.APARA_PID_REASON = 0
        self.APARA_RSF_REASON = 0
        self.APARA_GC_REASON = 0
        self.APARA_CD_REASON = 0

        # ===== 嵌套 FB 实例 =====
        self.R_TRIG02 = R_TRIG()
        self.R_TRIG03 = R_TRIG()
        self.F_TRIG1 = F_TRIG()
        self.F_TRIG2 = F_TRIG()
        self.R_TRIG01 = R_TRIG()
        self.BLINK1 = BLINK()
        self.LIM1 = APCHSHLLIM()
        self.FOP1 = APCHSFOP()
        self.STAT1 = APCSTATISTICS()
        self.R_TRIG_E1 = R_TRIG()
        self.R_TRIG_E2 = R_TRIG()
        self.F_TRIG_E1 = F_TRIG()
        self.F_TRIG_E2 = F_TRIG()
        self.R_TRIG_R1 = R_TRIG()
        self.R_TRIG_R2 = R_TRIG()
        self.R_TRIG_R3 = R_TRIG()
        self.R_TRIG_R4 = R_TRIG()
        self.R_TRIG_R5 = R_TRIG()
        self.TOF1 = TOF()
        self.CD_TON1 = TON()
        self.CD_TON2 = TON()
        self.R_TRIG9 = R_TRIG()
        self.FOP2 = APCHSFOP()
        self.STAT2 = APCSTATISTICS()
        self.R_TRIG04 = R_TRIG()
        self.BLINK2 = BLINK()
        self.R_TRIG05 = R_TRIG()
        self.R_TRIG06 = R_TRIG()
        self.PIDZZD1 = APCPIDZZD(license_context)
        self.R_TRIG_APARA_RESET = R_TRIG()
        self.R_TRIG_APARA_CALC = R_TRIG()
        self.R_TRIG_APARA_PID = R_TRIG()
        self.R_TRIG_APARA_RSF = R_TRIG()
        self.R_TRIG_APARA_GC = R_TRIG()
        self.R_TRIG_APARA_CD = R_TRIG()
        self.APARA1 = APCMAUTOPARA()

    def step(
        self,
        dt_ms: int,
        *,
        SP: float,
        PV: float,
        OC: float,
        TS: bool,
        TP: float,
        zlout_ref: RealRef,
        RM: int | None = None,
        OUTT: float | None = None,
        OUTB: float | None = None,
        SADD: bool | None = None,
        SSUB: bool | None = None,
        ZLEN: bool | None = None,
        ZSYK: float | None = None,
    ) -> None:
        """推进一个扫描周期。

        ``SP/PV/OC/TS/TP`` 是每拍输入管脚，先写入实例字段再进入授权门控；
        可选实时覆盖项只在非 ``None`` 时写入。可调配置参数仍由上位直接写
        ``self.*`` 或由源逻辑/APARA 应用写回。
        """

        self._write_step_inputs(
            SP=SP,
            PV=PV,
            OC=OC,
            TS=TS,
            TP=TP,
            RM=RM,
            OUTT=OUTT,
            OUTB=OUTB,
            SADD=SADD,
            SSUB=SSUB,
            ZLEN=ZLEN,
            ZSYK=ZSYK,
        )

        self._ctx.KZQBDYZMK.step(dt_ms)
        if self._ctx.KZQBDYZMK.OK % 10000 != 0:
            self._ctx.BD_ERROR6 = self._ctx.BD_ERROR6 + 1
            if self._ctx.BD_ERROR6 > 999999999:
                self._ctx.BD_ERROR6 = 100000000.0
            self.ZLOUT = zlout_ref.value
            return

        self._clamp_top_parameters()
        self._update_sp_v()
        self._update_limit_flags()
        self._run_oscillation_detection()
        self._run_observer(dt_ms)
        self._run_zlout(zlout_ref)
        self._run_rsf(dt_ms)
        self._run_pre_limit_cleanup()
        self._run_cd(dt_ms)
        self._run_pid()
        self._run_total_output()
        self._run_pidzzd(dt_ms)
        self._run_apara(dt_ms)
        self.ZLOUT = zlout_ref.value

    def _write_step_inputs(
        self,
        *,
        SP: float,
        PV: float,
        OC: float,
        TS: bool,
        TP: float,
        RM: int | None,
        OUTT: float | None,
        OUTB: float | None,
        SADD: bool | None,
        SSUB: bool | None,
        ZLEN: bool | None,
        ZSYK: float | None,
    ) -> None:
        self.SP = SP
        self.PV = PV
        self.OC = OC
        self.TS = TS
        self.TP = TP

        if RM is not None:
            self.RM = RM
        if OUTT is not None:
            self.OUTT = OUTT
        if OUTB is not None:
            self.OUTB = OUTB
        if SADD is not None:
            self.SADD = SADD
        if SSUB is not None:
            self.SSUB = SSUB
        if ZLEN is not None:
            self.ZLEN = ZLEN
        if ZSYK is not None:
            self.ZSYK = ZSYK

    def _clamp_top_parameters(self) -> None:
        if self.ZSYK > 10.0:
            self.ZSYK = 10.0
        if self.ZSYK < 0.1:
            self.ZSYK = 0.1

        if self.OUTT > self.MU:
            self.OUTT = self.MU
        if self.OUTT < self.MD:
            self.OUTT = self.MD
        if self.OUTB > self.MU:
            self.OUTB = self.MU
        if self.OUTB < self.MD:
            self.OUTB = self.MD

    def _update_sp_v(self) -> None:
        if self.TS:
            self.SP_V = self.SP

        limit = (self.PVMU - self.PVMD) * self.SP_RH * 0.01
        if abs(self.SP - self.SP_V) >= limit:
            if self.SP > self.SP_V:
                self.SP_V = self.LASTSP_V + limit
            else:
                self.SP_V = self.LASTSP_V - limit
        else:
            self.SP_V = self.SP
        self.LASTSP_V = self.SP_V

    def _update_limit_flags(self) -> None:
        self.SADD_TEMP = _sel(self.AV >= self.OUTT, False, True)
        self.SADD_Z = (self.SADD or self.SADD_TEMP) and (not self.TS) and self.RM == 1
        self.SSUB_TEMP = _sel(self.AV <= self.OUTB, False, True)
        self.SSUB_Z = (self.SSUB or self.SSUB_TEMP) and (not self.TS) and self.RM == 1

    def _run_oscillation_detection(self) -> None:
        pv_range = self.PVMU - self.PVMD
        if abs(self.PV - self.PVMAX) < pv_range * 0.02 or abs(self.PV - self.PVMIN) < pv_range * 0.02:
            self.NUM = min(self.NUM + 0.5, 3600.0)
        else:
            self.NUM = 0.0

        if self.NUM > 300.0:
            self.PVMAX = self.PV
            self.PVMIN = self.PV
            self.SUMMAX = 0.0
            self.SUMMIN = 0.0
            self.SBMAX = False
            self.SBMIN = False
            for i in range(1, 5):
                self.JSMJ[i] = 0.0
            self.PVZD = False

        if self.SBMAX:
            self.SUMMAX += abs(self.PV) * 0.5

        self.R_TRIG_E1.step((self.PVMAX - self.PV) > pv_range * 0.01)
        if self.R_TRIG_E1.Q:
            self.PVMIN = self.PV
            self.SBMAX = True
            self.SBMIN = False
        if (self.PV - self.PVMAX) > pv_range * 0.01:
            self.PVMAX = _sel((self.PV - self.PVMAX) > pv_range * 0.01, self.PVMAX, self.PV)
            self.SUMMAX = 0.0

        if self.SBMIN:
            self.SUMMIN += abs(self.PV) * 0.5

        self.R_TRIG_E2.step((self.PV - self.PVMIN) > pv_range * 0.01)
        if self.R_TRIG_E2.Q:
            self.PVMAX = self.PV
            self.SBMIN = True
            self.SBMAX = False
        if (self.PV - self.PVMIN) < pv_range * -0.01:
            self.PVMIN = _sel((self.PV - self.PVMIN) < pv_range * -0.01, self.PVMIN, self.PV)
            self.SUMMIN = 0.0

        self.F_TRIG_E1.step(self.SBMAX)
        self.F_TRIG_E2.step(self.SBMIN)
        if self.F_TRIG_E1.Q or self.F_TRIG_E2.Q:
            self.JSMJ[4] = self.JSMJ[3]
            self.JSMJ[3] = self.JSMJ[2]
            self.JSMJ[2] = self.JSMJ[1]
            if self.SUMMAX != 0.0:
                self.JSMJ[1] = self.SUMMAX
            if self.SUMMIN != 0.0:
                self.JSMJ[1] = self.SUMMIN

        if all(self.JSMJ[i] != 0.0 for i in range(1, 5)):
            self.PVZD = (
                self.JSMJ[3] / self.JSMJ[4] >= 0.9
                and self.JSMJ[2] / self.JSMJ[3] >= 0.9
                and self.JSMJ[1] / self.JSMJ[2] >= 0.9
            )

        self.ZSYK_TEMP = self.ZSYK * _sel(self.PVZD and self.XZEN, 1.0, self.XZ_K)

    def _run_observer(self, dt_ms: int) -> None:
        blink_out = self.BLINK1.step(
            dt_ms,
            ENABLE=True,
            TIMELOW_ms=real_to_time_ms(self.TC * 1000.0),
            TIMEHIGH_ms=BLINK_TIMEHIGH_MS,
        )
        self.R_TRIG01.step(blink_out)
        if self.R_TRIG01.Q:
            self.JZ_ZUP1 = self.JZ_ZUP
            self.JZ_ZUP = self.JZ_Z

        self.STAT1.step(dt_ms, IN=self.PV, RESET=self.R_TRIG01.Q)
        self.JZ_Z = self.STAT1.AVG
        self.FOP1.step(
            dt_ms,
            IN=self.JZ_ZUP - self.JZ_ZUP1,
            TC=self.TZ,
            KG=FOP_KG,
            TB=FOP_DEFAULT_TB_SEC,
        )
        self.AV_TEMP1 = self.FOP1.AV
        sign = _sel(self.AD, 1.0, -1.0)
        self.AV_J_GC = (self.PV - self.SP_V) * self.GC1 * sign
        self.AV_D_GC = self.AV_TEMP1 * self.GC2 * sign
        self.AV_J = _sel(
            self.SADD_Z,
            _sel(self.SSUB_Z, self.AV_J_GC, max(self.AV_J_GC, self.AV_J)),
            min(self.AV_J_GC, self.AV_J),
        )
        self.AV_D = _sel(
            self.SADD_Z,
            _sel(self.SSUB_Z, self.AV_D_GC, max(self.AV_D_GC, self.AV_D)),
            min(self.AV_D_GC, self.AV_D),
        )
        self.LIM1.step(dt_ms, IN=(self.AV_J + self.AV_D) * self.ZSYK_TEMP, HL=self.OUTH, LL=self.OUTL)
        self.AV_GC = _sel(
            self.PCMMS == 1,
            _sel(self.GCEN and (self.PCMMS == 0 or self.PCMMS == 2) and (not self.TS) and self.RM == 1, 0.0, self.LIM1.AV),
            self.AV_J_GC + self.AV_D_GC,
        )

    def _run_zlout(self, zlout_ref: RealRef) -> None:
        if self.ZLEN:
            self.R_TRIG02.step(self.AV >= self.OUTT or self.AV <= self.OUTB)
            if self.R_TRIG02.Q:
                zlout_ref.value += self.AV_R + self.AV_P_TEMP

    def _run_rsf(self, dt_ms: int) -> None:
        self.TL = max(self.TL, 0.0)
        self.TL1 = max(self.TL1, 0.0)
        self.TL2 = max(self.TL2, 0.0)
        self.TL3 = max(self.TL3, 0.0)
        self.TL4 = max(self.TL4, 0.0)
        self.E1 = max(0.0, self.E1)
        self.E2 = max(0.0, self.E2)
        self.E3 = max(0.0, self.E3)
        self.E4 = max(0.0, self.E4)
        self.AO1 = max(0.0, self.AO1)
        self.AO2 = max(0.0, self.AO2)
        self.AO3 = max(0.0, self.AO3)
        self.AO4 = max(0.0, self.AO4)
        self.ZF_K = max(min(1.0, self.ZF_K), 0.0)
        self.RSF_HYS = max(min(1.0, self.RSF_HYS), 0.1)
        self.RSF_FAST_HYS = max(min(self.RSF_FAST_HYS, self.RSF_HYS), 0.01)
        self.RSF_TLOUT_K = max(min(1.0, self.RSF_TLOUT_K), 0.0)
        self.RSF_LOCK_T = max(self.RSF_LOCK_T, 0.0)
        self.E1_OUT = self.E1 * self.RSF_HYS
        self.E1_FAST_OUT = self.E1 * self.RSF_FAST_HYS
        self.TL_OUT = self.TL * self.RSF_TLOUT_K

        self.ZSYK_RSF = max(min(self.ZSYK_TEMP, 1.5), 0.5)
        self.AO1_1 = self.AO1 * self.ZSYK_RSF
        self.AO2_1 = self.AO2 * self.ZSYK_RSF
        self.AO3_1 = self.AO3 * self.ZSYK_RSF
        self.AO4_1 = self.AO4 * self.ZSYK_RSF

        if self.R_TRIG02.Q:
            self.AV_R = 0.0
            self.AV_R_TEMP = 0.0
            self.AV_R_TEMP1 = 0.0
            self.AV_R_TEMP2 = 0.0

        if self.TS:
            self._reset_rsf_state()
        elif self.RM == 1 and self.RSFEN:
            self._run_rsf_active(dt_ms)

        self.FLAG_1 = self.FLAG
        self.EK_R_1 = self.EK_R

        self.R_TRIG03.step(self.AV_P_TEMP >= self.OUTT or self.AV_P_TEMP <= self.OUTB)
        self.F_TRIG1.step(self.RSFEN)
        if self.F_TRIG1.Q:
            self.FLAG = 0.0
            self.CT_1 = 0.0
            self.CT_2 = 0.0
            self.CT_3 = 0.0
            self.CT_4 = 0.0
            self.CT_1_1 = 0.0
            self.CT_2_1 = 0.0
            self.CT_3_1 = 0.0
            self.CT_4_1 = 0.0
            self.CT_RSF_OUT = 0.0
            self.RSF_LOCK_LEVEL = 0.0
            self.RSF_LOCK_SIG = 0.0
            self.CT_RSF_LOCK = 0.0
        if self.R_TRIG03.Q or self.F_TRIG1.Q:
            self.AV_P_TEMP += self.AV_R
            self.AV_R = 0.0
            self.AV_R_TEMP = 0.0

        self.AV_R += self.AV_R_TEMP

    def _reset_rsf_state(self) -> None:
        self.AV_R = 0.0
        self.AV_R_TEMP = 0.0
        self.AV_R_TEMP1 = 0.0
        self.AV_R_TEMP2 = 0.0
        self.CT_TL = 0
        self.FLAG = 0.0
        self.CT_1 = 0.0
        self.CT_2 = 0.0
        self.CT_3 = 0.0
        self.CT_4 = 0.0
        self.CT_1_1 = 0.0
        self.CT_2_1 = 0.0
        self.CT_3_1 = 0.0
        self.CT_4_1 = 0.0
        self.CT_RSF_OUT = 0.0
        self.RSF_LOCK_LEVEL = 0.0
        self.RSF_LOCK_SIG = 0.0
        self.CT_RSF_LOCK = 0.0

    def _run_rsf_active(self, dt_ms: int) -> None:
        if self.PCMMS == 0:
            if self.AD:
                self.EK_R = self.PV - self.SP_V
            else:
                self.EK_R = self.SP_V - self.PV
        if self.PCMMS == 1 or self.PCMMS == 2:
            self.EK_R = 0.0 - (self.AV_J_GC + self.AV_D_GC)

        if self.EK_R <= 0.0:
            self.SIG = 1.0
        if self.EK_R > 0.0:
            self.SIG = -1.0

        self._update_rsf_counters()
        self._update_rsf_qj()
        self._update_rsf_exit()
        self._update_rsf_lock()

        self.RSF_LOCK_R1 = self.RSF_LOCK_LEVEL == 1 and self.RSF_LOCK_SIG == self.SIG
        self.RSF_LOCK_R2 = self.RSF_LOCK_LEVEL == 2 and self.RSF_LOCK_SIG == self.SIG
        self.RSF_LOCK_R3 = self.RSF_LOCK_LEVEL == 3 and self.RSF_LOCK_SIG == self.SIG
        self.RSF_LOCK_R4 = self.RSF_LOCK_LEVEL == 4 and self.RSF_LOCK_SIG == self.SIG

        self.R_TRIG_R1.step(((self.CT_1 >= 2 * self.TL) or self.QJ1) and (not self.RSF_LOCK_R1))
        self.R_TRIG_R2.step(((self.CT_2 >= 2 * self.TL) or self.QJ2) and (not self.RSF_LOCK_R2))
        self.R_TRIG_R3.step(((self.CT_3 >= 2 * self.TL) or self.QJ3) and (not self.RSF_LOCK_R3))
        self.R_TRIG_R4.step(((self.CT_4 >= 2 * self.TL) or self.QJ4) and (not self.RSF_LOCK_R4))

        if self.R_TRIG_R1.Q:
            self.FLAG_1 = self.FLAG
            self.FLAG = 1.0
            self.CT_TL = 0
            self.AV_R_TEMP1 = self.AO1_1 * self.SIG
        if self.R_TRIG_R2.Q:
            self.FLAG_1 = self.FLAG
            self.FLAG = 2.0
            self.CT_TL = 0
            self.AV_R_TEMP1 = self.AO2_1 * self.SIG
        if self.R_TRIG_R3.Q:
            self.FLAG_1 = self.FLAG
            self.FLAG = 3.0
            self.CT_TL = 0
            self.AV_R_TEMP1 = self.AO3_1 * self.SIG
        if self.R_TRIG_R4.Q:
            self.FLAG_1 = self.FLAG
            self.FLAG = 4.0
            self.CT_TL = 0
            self.AV_R_TEMP1 = self.AO4_1 * self.SIG

        self._update_rsf_flag_cycle()

        if self.CT_TL >= self.TL_TEMP:
            self.AV_R_TEMP2 = 0.0
            self.CT_TL = 0

        self.R_TRIG_R5.step(self.FLAG - self.FLAG_1 < 0.0)
        self.TOF1.step(dt_ms, self.SADD_Z or self.SSUB_Z, TOF1_PT_MS)
        if self.R_TRIG_R5.Q:
            self.AV_R_TEMP = (self.AV_R_TEMP1 - self.AV_R_TEMP2) * (1.0 - _sel(self.TOF1.Q, self.ZF_K, 1.0))
        else:
            self.AV_R_TEMP = self.AV_R_TEMP1 - self.AV_R_TEMP2

        if self.AV_R_TEMP > 0.0 and self.SADD_Z:
            self.AV_R_TEMP = 0.0
        if self.AV_R_TEMP < 0.0 and self.SSUB_Z:
            self.AV_R_TEMP = 0.0
        self.AV_R_TEMP2 = self.AV_R_TEMP1

    def _update_rsf_counters(self) -> None:
        if abs(self.EK_R) >= self.E1 and abs(self.EK_R) < self.E2 and self.EK_R * self.EK_R_1 > 0.0:
            self.CT_1 = min(self.CT_1 + 1.0, 3600.0)
            self.CT_1_1 = min(self.CT_1_1 + 1.0, 3600.0)
        else:
            self.CT_1 = 0.0

        if abs(self.EK_R) >= self.E2 and abs(self.EK_R) < self.E3 and self.EK_R * self.EK_R_1 > 0.0:
            self.CT_2 = min(self.CT_2 + 1.0, 3600.0)
            self.CT_2_1 = min(self.CT_2_1 + 1.0, 3600.0)
        else:
            self.CT_2 = 0.0

        if abs(self.EK_R) >= self.E3 and abs(self.EK_R) < self.E4 and self.EK_R * self.EK_R_1 > 0.0:
            self.CT_3 = min(self.CT_3 + 1.0, 3600.0)
            self.CT_3_1 = min(self.CT_3_1 + 1.0, 3600.0)
        else:
            self.CT_3 = 0.0

        if abs(self.EK_R) >= self.E4 and self.EK_R * self.EK_R_1 > 0.0:
            self.CT_4 = min(self.CT_4 + 1.0, 3600.0)
            self.CT_4_1 = min(self.CT_4_1 + 1.0, 3600.0)
        else:
            self.CT_4 = 0.0

    def _update_rsf_qj(self) -> None:
        active_count = (
            _sel(self.CT_1_1 == 0.0, 1.0, 0.0)
            + _sel(self.CT_2_1 == 0.0, 1.0, 0.0)
            + _sel(self.CT_3_1 == 0.0, 1.0, 0.0)
            + _sel(self.CT_4_1 == 0.0, 1.0, 0.0)
        )
        total = self.CT_1_1 + self.CT_2_1 + self.CT_3_1 + self.CT_4_1
        threshold = active_count * 2.0 * self.TL
        self.QJ1 = total > threshold and self.CT_1_1 > max(max(self.CT_2_1, self.CT_3_1), self.CT_4_1)
        self.QJ2 = total > threshold and self.CT_2_1 > max(max(self.CT_3_1, self.CT_1_1), self.CT_4_1)
        self.QJ3 = total > threshold and self.CT_3_1 > max(max(self.CT_2_1, self.CT_1_1), self.CT_4_1)
        self.QJ4 = total > threshold and self.CT_4_1 > max(max(self.CT_2_1, self.CT_3_1), self.CT_1_1)

    def _update_rsf_exit(self) -> None:
        self.RSF_EXIT = False
        if self.EK_R_1 * self.EK_R < 0.0:
            self.RSF_EXIT = True
            self.CT_RSF_OUT = 0.0
        elif self.FLAG == 0.0:
            self.CT_RSF_OUT = 0.0
            if abs(self.EK_R) < self.E1:
                self.RSF_EXIT = True
        else:
            if abs(self.EK_R) < self.E1_FAST_OUT:
                self.RSF_EXIT = True
                self.CT_RSF_OUT = 0.0
            elif abs(self.EK_R) < self.E1_OUT:
                self.CT_RSF_OUT = min(self.CT_RSF_OUT + 1.0, 3600.0)
                if self.CT_RSF_OUT >= 2.0 * self.TL_OUT:
                    self.RSF_EXIT = True
                    self.CT_RSF_OUT = 0.0
            else:
                self.CT_RSF_OUT = 0.0

        if self.RSF_EXIT:
            if self.FLAG > 0.0:
                self.RSF_LOCK_LEVEL = self.FLAG
                if self.AV_R_TEMP1 >= 0.0:
                    self.RSF_LOCK_SIG = 1.0
                else:
                    self.RSF_LOCK_SIG = -1.0
                self.CT_RSF_LOCK = 0.0
            self.FLAG_1 = self.FLAG
            self.FLAG = 0.0
            self.CT_TL = 0
            self.CT_1_1 = 0.0
            self.CT_2_1 = 0.0
            self.CT_3_1 = 0.0
            self.CT_4_1 = 0.0
            self.CT_RSF_OUT = 0.0

    def _update_rsf_lock(self) -> None:
        if self.RSF_LOCK_LEVEL > 0.0:
            self.CT_RSF_LOCK = min(self.CT_RSF_LOCK + 1.0, 100000000.0)
            if self.CT_RSF_LOCK >= 2.0 * self.RSF_LOCK_T:
                self.RSF_LOCK_LEVEL = 0.0
            if self.SIG != self.RSF_LOCK_SIG:
                self.RSF_LOCK_LEVEL = 0.0
            if (
                (self.RSF_LOCK_LEVEL == 1 and abs(self.EK_R) >= self.E2)
                or (self.RSF_LOCK_LEVEL == 2 and abs(self.EK_R) >= self.E3)
                or (self.RSF_LOCK_LEVEL == 3 and abs(self.EK_R) >= self.E4)
            ):
                self.RSF_LOCK_LEVEL = 0.0

        if self.RSF_LOCK_LEVEL == 0.0:
            self.RSF_LOCK_SIG = 0.0
            self.CT_RSF_LOCK = 0.0

    def _update_rsf_flag_cycle(self) -> None:
        if self.FLAG == 0.0:
            self.AV_R_TEMP1 = 0.0
        if self.FLAG == 1.0:
            self.TL_TEMP = 2.0 * self.TL1
            self.CT_TL += 1
            self.CT_1_1 = 0.0
            self.CT_2_1 = 0.0
            self.CT_3_1 = 0.0
            self.CT_4_1 = 0.0
        if self.FLAG == 2.0:
            self.TL_TEMP = 2.0 * self.TL2
            self.CT_TL += 1
            self.CT_1_1 = 0.0
            self.CT_2_1 = 0.0
            self.CT_3_1 = 0.0
            self.CT_4_1 = 0.0
        if self.FLAG == 3.0:
            self.TL_TEMP = 2.0 * self.TL3
            self.CT_TL += 1
            self.CT_1_1 = 0.0
            self.CT_2_1 = 0.0
            self.CT_3_1 = 0.0
            self.CT_4_1 = 0.0
        if self.FLAG == 4.0:
            self.TL_TEMP = 2.0 * self.TL4
            self.CT_TL += 1
            self.CT_1_1 = 0.0
            self.CT_2_1 = 0.0
            self.CT_3_1 = 0.0
            self.CT_4_1 = 0.0

    def _run_pre_limit_cleanup(self) -> None:
        margin = (self.OUTT - self.OUTB) * 0.01 * max(self.OutRL, 0.5)
        self.R_TRIG05.step(self.AV_TEMP > self.OUTT + margin)
        self.R_TRIG06.step(self.AV_TEMP < self.OUTB - margin)
        if self.R_TRIG05.Q:
            self.AV_P_TEMP = self.AV_P_TEMP - (self.AV_TEMP - self.OUTT) + margin
        if self.R_TRIG06.Q:
            self.AV_P_TEMP = self.AV_P_TEMP + (self.AV_TEMP - self.OUTB) - margin

    def _run_cd(self, dt_ms: int) -> None:
        blink_out = self.BLINK2.step(
            dt_ms,
            ENABLE=True,
            TIMELOW_ms=real_to_time_ms(self.TC_CD * 1000.0),
            TIMEHIGH_ms=BLINK_TIMEHIGH_MS,
        )
        self.R_TRIG04.step(blink_out)
        if self.R_TRIG04.Q:
            self.JZ_ZUP3 = self.JZ_ZUP2
            self.JZ_ZUP2 = self.JZ_Z1
        self.STAT2.step(dt_ms, IN=self.PV, RESET=self.R_TRIG04.Q)
        self.JZ_Z1 = self.STAT2.AVG
        self.FOP2.step(
            dt_ms,
            IN=self.JZ_ZUP2 - self.JZ_ZUP3,
            TC=self.TZ_CD,
            KG=FOP_KG,
            TB=FOP_DEFAULT_TB_SEC,
        )
        self.AV_D_TEMP = self.FOP2.AV * self.CD_K_D
        sign = _sel(self.AD, 1.0, -1.0)
        self.CD_BH = ((self.PV - self.SP_V) * self.CD_K_J + self.FOP2.AV * self.CD_K_D) * sign

        self.CD_TON1.step(dt_ms, abs(self.CD_BH) >= self.CD_GD, real_to_time_ms(self.TL * 1000.0))
        if self.CD_TON1.Q and self.CDEN and self.RM == 1 and not self.TS:
            cd_out = self.CD_BH * self.CD_K_FD * self.ZSYK_TEMP
            self.AV_C_TEMP = _sel(
                self.SADD_Z,
                _sel(self.SSUB_Z, cd_out, max(cd_out, self.AV_C_TEMP)),
                min(cd_out, self.AV_C_TEMP),
            )
            self.FLG = _sel(self.CD_BH > 0.0, _sel(self.CD_BH < 0.0, self.FLG, -1.0), 1.0)

        self.CD_TON2.step(dt_ms, abs(self.CD_BH) < self.CD_GD, real_to_time_ms(self.TL * 1000.0))
        self.R_TRIG9.step((self.CD_TON2.Q or self.TS == 1 or self.RM != 1) and self.AV_C_TEMP != 0.0)
        self.F_TRIG2.step(self.CDEN)
        if self.R_TRIG9.Q or self.F_TRIG2.Q:
            cd_back = self.CD_GD * self.CD_K_FD * self.ZSYK_TEMP * self.CD_K * self.FLG
            self.AV_C_TEMP = min(
                max(
                    _sel(
                        self.SADD_Z,
                        _sel(self.SSUB_Z, cd_back, max(cd_back, self.AV_C_TEMP)),
                        min(cd_back, self.AV_C_TEMP),
                    ),
                    self.CDL,
                ),
                self.CDH,
            )
            self.AV_P_TEMP += self.AV_C_TEMP
            self.AV_C_TEMP = 0.0

        self.AV_C_TEMP = max(min(self.AV_C_TEMP, self.CDH), self.CDL)
        self.AV_C = self.AV_C_TEMP
        self.AV_D_TEMP_1 = self.AV_D_TEMP

    def _gc_offset(self) -> float:
        return _sel(self.PCMMS == 1, self.AV_GC, 0.0)

    def _run_pid(self) -> None:
        self.PT_1 = self.PT / self.ZSYK_TEMP

        if self.CYCLE <= 0.0:
            self.CYCLE = 0.5

        self.DI_SJ = self.DI * 0.01 * abs(self.PVMU - self.PVMD)
        self.SV_SJH = self.SVH * 0.01 * abs(self.PVMU - self.PVMD)
        self.SV_SJL = self.SVL * 0.01 * abs(self.PVMU - self.PVMD)

        self.TIi = self.TI + _sel(abs(self.EK) >= self.SV_SJH, 0.0, abs(self.SP_V - self.PV) * self.KI) + self.TI1
        if self.TIi <= 0.0:
            self.TIi = 0.001
        if self.KD <= 0.0:
            self.KD = 0.001
        if self.TD < 0.0:
            self.TD = 0.0
        if self.MU - self.MD == 0.0:
            self.MU = self.MD + 0.00001

        self.LASTUKOUT = self.AV_P_TEMP
        self.UK = self.UK_1
        self.PX = self.PT_1 + abs(self.SP_V - self.PV) * self.KP + self.PT1
        self.PTt = 0.01 * self.PX * (self.PVMU - self.PVMD) / (self.MU - self.MD)
        if self.PTt <= 0.0:
            self.PTt = 0.001

        if self.ATE:
            if self.TS:
                if self.RM != 4:
                    self.preRM = self.RM
                    self.RM = 4
            elif self.preRM != 4:
                self.RM = self.preRM
                self.preRM = 4

        self.EK = 0.9 * self.EK_LAST + 0.1 * (self.PV - self.SP_V)
        self.EK_LAST = self.EK
        if self.AD:
            self.EK = -self.EK

        self.DEK = self.PV - self.PV_LAST
        if self.AD:
            self.DEK = -self.DEK

        if self.RM == 3 or self.RM == 4 or self.R_TRIG02.Q:
            self._run_pid_tracking()
        elif self.RM == 0 or self.R_TRIG03.Q or self.R_TRIG9.Q or self.F_TRIG1.Q or self.F_TRIG2.Q or self.R_TRIG05.Q or self.R_TRIG06.Q:
            self._run_pid_manual()
        else:
            self._run_pid_auto()

        if self.RM == 0:
            self.AV_P_TEMP = self.AV_P
        else:
            self.AV_P = self.AV_P_TEMP - self._gc_offset()

        self.DU_1 = self.DU
        self.UK_1 = self.UK
        self.EK_2 = self.EK_1
        self.EK_1 = self.EK
        self.PV_LAST = self.PV
        self.DEK_2 = self.DEK_1
        self.DEK_1 = self.DEK

    def _run_pid_tracking(self) -> None:
        offset = self._gc_offset()
        if self.OutM == 1:
            self.DUOUT = self.TP + offset
            self.AV_P_TEMP = self.DUOUT
            self.AV_TEMP2 = self.AV_P_TEMP
            self.DU = self.DUOUT - self.OC - offset
        else:
            self.UKOUT = self.TP + offset
            self.UKOUT = max(min(self.UKOUT, self.OUTT), self.OUTB)
            self.AV_P_TEMP = self.UKOUT
            self.AV_TEMP2 = self.AV_P_TEMP
            self.UK = self.UKOUT - self.OC - offset
        if self.TM:
            self.SP_V = self.PV

    def _run_pid_manual(self) -> None:
        offset = self._gc_offset()
        if self.OutM == 1:
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
            self.AV_P_TEMP = self.DUOUT
            self.AV_TEMP2 = self.AV_P_TEMP
            self.DU = self.DUOUT - self.OC - offset
        else:
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
            self.UKOUT = max(min(self.UKOUT, self.OUTT), self.OUTB)
            self.AV_P_TEMP = self.UKOUT
            self.AV_TEMP2 = self.AV_P_TEMP
            self.UK = self.UKOUT - self.OC - offset

        self.MM = 0
        if self.TM:
            self.SP_V = self.PV

    def _run_pid_auto(self) -> None:
        offset = self._gc_offset()
        self.LASTUKOUT = self.AV_TEMP2

        if abs(self.EK) <= self.DI_SJ:
            self.DU = 0.0
            self.UK = self.UK_1
        else:
            if abs(self.EK) <= self.SV_SJL:
                self.SI = 0
            else:
                self.SI = 1

            self.B1 = (self.TD / self.CYCLE) / self.KD
            self.B2 = 1.0 + self.B1
            self.C1 = self.B1 / self.B2
            self.C2 = (1.0 + self.SI * self.CYCLE / self.TIi + self.TD / self.CYCLE) / (self.B2 * self.PTt)
            self.C3 = -(1.0 + 2.0 * self.TD / self.CYCLE) / (self.B2 * self.PTt)
            self.C4 = (self.TD / self.CYCLE) / (self.B2 * self.PTt)
            self.DU_TEMP = self.C1 * self.DU_1 + (1.0 - self.C1) * (1.0 / self.PTt) * (
                (self.EK - self.EK_1)
                + (self.SI * self.CYCLE / self.TIi) * self.EK
                + (self.TD / self.CYCLE) * (self.DEK - 2.0 * self.DEK_1 + self.DEK_2)
            )
            if self.DU_TEMP < 10000000000.0 and self.DU_TEMP > -10000000000.0:
                self.DU = self.DU_TEMP
            else:
                self.DU = 0.0

        if self.OutM == 1:
            self.DUOUT = self.DU + self.OC + offset
            self.AV_TEMP2 = self.DUOUT
            if self.SADD_Z:
                self.AV_TEMP2 = min(0.0, self.AV_TEMP2)
            if self.SSUB_Z:
                self.AV_TEMP2 = max(0.0, self.AV_TEMP2)
        else:
            self.UK = self.UK_1 + self.DU
            self.UKOUT = self.UK + self.OC + offset
            if not ((self.SADD_Z and self.UKOUT > self.LASTUKOUT) or (self.SSUB_Z and self.UKOUT < self.LASTUKOUT)):
                self.AV_TEMP2 = self.UKOUT
            else:
                self.UK = self.AV_TEMP2 - self.OC - offset

        self.AV_P_TEMP = self.AV_TEMP2

    def _run_total_output(self) -> None:
        self.AV_TEMP = self.AV_P_TEMP + self.AV_R + self.AV_C
        if abs(self.AV_TEMP - self.AV) > abs((self.MU - self.MD) * self.OutRL * 0.01):
            self.AV = self.AV_TEMP

        rate = (self.MU - self.MD) * self.RTH * 0.01
        if self.AV - self.AV_1 > rate:
            self.AV = self.AV_1 + rate
        elif self.AV - self.AV_1 < -rate:
            self.AV = self.AV_1 - rate

        self.AV = min(max(self.AV, self.OUTB), self.OUTT)
        self.AV_1 = self.AV

    def _run_pidzzd(self, dt_ms: int) -> None:
        self.PIDZZD1.step(
            dt_ms,
            AV=self.AV,
            SP=self.SP,
            PV=self.PV,
            PT=self.PT,
            TI=self.TI,
            RM=self.RM,
            PVMU=self.PVMU,
            PVMD=self.PVMD,
            MU=self.MU,
            MD=self.MD,
            SADD=self.SADD,
            SSUB=self.SSUB,
            PT1K=self.PT1K,
            TI1K=self.TI1K,
        )
        self.PT1 = self.PIDZZD1.PT1
        self.TI1 = self.PIDZZD1.TI1

    def _run_apara(self, dt_ms: int) -> None:
        self.R_TRIG_APARA_RESET.step(self.APARA_RESET)
        self.R_TRIG_APARA_CALC.step(self.APARA_CALC_NOW)
        self.APARA_RESET_PLS = self.R_TRIG_APARA_RESET.Q
        self.APARA_CALC_NOW_PLS = self.R_TRIG_APARA_CALC.Q

        self.APARA1.step(
            dt_ms,
            EN=self.APARA_EN,
            RESET=self.APARA_RESET_PLS,
            CALC_NOW=self.APARA_CALC_NOW_PLS,
            CYCLE=self.CYCLE,
            COLLECT_MODE=self.APARA_COLLECT_MODE,
            SP=self.SP_V,
            SP_MAN=self.APARA_SP_MAN,
            SP_MAN_EN=self.APARA_SP_MAN_EN,
            SP_TAG_EN=self.APARA_SP_TAG_EN,
            SP_AUTO_EN=self.APARA_SP_AUTO_EN,
            SP_AUTO_REPLACE_BAD_TAG=self.APARA_SP_AUTO_REPLACE_BAD_TAG,
            SP_STABLE_T=self.APARA_SP_STABLE_T,
            SP_CONF_T=self.APARA_SP_CONF_T,
            SP_PV_STABLE_ABS=self.APARA_SP_PV_STABLE_ABS,
            SP_AV_STABLE_ABS=self.APARA_SP_AV_STABLE_ABS,
            PV=self.PV,
            AV=self.AV,
            RM=self.RM,
            TS=self.TS,
            PVMU=self.PVMU,
            PVMD=self.PVMD,
            MU=self.MU,
            MD=self.MD,
            OUTT=self.OUTT,
            OUTB=self.OUTB,
            WIN_T=self.APARA_WIN_T,
            MIN_WIN_T=self.APARA_MIN_WIN_T,
            MIN_STORE_EVENT=self.APARA_MIN_STORE_EVENT,
            MIN_VALID_EVENT=self.APARA_MIN_VALID_EVENT,
            HISTORY_N=self.APARA_HISTORY_N,
            FUSE_MIN_N=self.APARA_FUSE_MIN_N,
            FUSE_MIN_WEIGHT=self.APARA_FUSE_MIN_WEIGHT,
            SIM_SP_K=self.APARA_SIM_SP_K,
            SIM_PV_K=self.APARA_SIM_PV_K,
            SIM_AV_K=self.APARA_SIM_AV_K,
            SIM_ERR_K=self.APARA_SIM_ERR_K,
            SIM_SP_ABS=self.APARA_SIM_SP_ABS,
            SIM_PV_ABS=self.APARA_SIM_PV_ABS,
            SIM_AV_ABS=self.APARA_SIM_AV_ABS,
            SIM_ERR_ABS=self.APARA_SIM_ERR_ABS,
            SIM_RELAX_K=self.APARA_SIM_RELAX_K,
            MAN_MERGE_T=self.APARA_MAN_MERGE_T,
            MAN_RESP_T=self.APARA_MAN_RESP_T,
            MAN_RESP_T_MAX=self.APARA_MAN_RESP_T_MAX,
            MAN_AV_MIN=self.APARA_MAN_AV_MIN,
            PT_IN=self.PT,
            TI_IN=self.TI,
            TD_IN=self.TD,
            DI_IN=self.DI,
            SVH_IN=self.SVH,
            SVL_IN=self.SVL,
            PID_FORMULA_EN=self.APARA_PID_FORMULA_EN,
            PID_LAMBDA_K=self.APARA_PID_LAMBDA_K,
            PID_MODEL_L_K=self.APARA_PID_MODEL_L_K,
            PID_FORMULA_BLEND=self.APARA_PID_FORMULA_BLEND,
            TL_IN=self.TL,
            TL1_IN=self.TL1,
            TL2_IN=self.TL2,
            TL3_IN=self.TL3,
            TL4_IN=self.TL4,
            E1_IN=self.E1,
            E2_IN=self.E2,
            E3_IN=self.E3,
            E4_IN=self.E4,
            AO1_IN=self.AO1,
            AO2_IN=self.AO2,
            AO3_IN=self.AO3,
            AO4_IN=self.AO4,
            RSF_LOCK_T_IN=self.RSF_LOCK_T,
            TC_IN=self.TC,
            TZ_IN=self.TZ,
            GC1_IN=self.GC1,
            GC2_IN=self.GC2,
            OUTH_IN=self.OUTH,
            OUTL_IN=self.OUTL,
            CD_GD_IN=self.CD_GD,
            CD_K_IN=self.CD_K,
            CD_K_FD_IN=self.CD_K_FD,
            CD_K_J_IN=self.CD_K_J,
            CD_K_D_IN=self.CD_K_D,
            CDH_IN=self.CDH,
            CDL_IN=self.CDL,
            TC_CD_IN=self.TC_CD,
            TZ_CD_IN=self.TZ_CD,
        )

        self._mirror_apara_outputs()
        self._apply_apara_recommendations()
        self._clear_apara_commands()

    def _mirror_apara_outputs(self) -> None:
        for name in self._APARA_MIRRORS:
            setattr(self, f"APARA_{name}", getattr(self.APARA1, name))

    def _apply_apara_recommendations(self) -> None:
        self.APARA_CAN_APPLY = self.APARA1.FINAL_STRONG or self.APARA1.FINAL_WEAK
        self.R_TRIG_APARA_PID.step(self.APARA_APPLY_PID)
        self.R_TRIG_APARA_RSF.step(self.APARA_APPLY_RSF)
        self.R_TRIG_APARA_GC.step(self.APARA_APPLY_GC)
        self.R_TRIG_APARA_CD.step(self.APARA_APPLY_CD)

        if self.R_TRIG_APARA_PID.Q:
            self.APARA_LAST_APPLY_GROUP = 1
            self.APARA_LAST_APPLY_OK = False
            if not self.APARA_CAN_APPLY:
                self.APARA_LAST_APPLY_REASON = 2
            elif not (self.APARA1.PID_OK or self.APARA1.FINAL_WEAK):
                self.APARA_LAST_APPLY_REASON = 3
            else:
                self.PT = self.APARA1.PT_REC
                self.TI = self.APARA1.TI_REC
                self.TD = self.APARA1.TD_REC
                self.DI = self.APARA1.DI_REC
                self.SVH = self.APARA1.SVH_REC
                self.SVL = self.APARA1.SVL_REC
                self.APARA_LAST_APPLY_OK = True
                self.APARA_LAST_APPLY_REASON = 1

        if self.R_TRIG_APARA_RSF.Q:
            self.APARA_LAST_APPLY_GROUP = 2
            self.APARA_LAST_APPLY_OK = False
            if not self.APARA_CAN_APPLY:
                self.APARA_LAST_APPLY_REASON = 2
            elif not (self.APARA1.RSF_OK or self.APARA1.FINAL_WEAK):
                self.APARA_LAST_APPLY_REASON = 3
            else:
                self.TL = self.APARA1.TL_REC
                self.TL1 = self.APARA1.TL1_REC
                self.TL2 = self.APARA1.TL2_REC
                self.TL3 = self.APARA1.TL3_REC
                self.TL4 = self.APARA1.TL4_REC
                self.E1 = self.APARA1.E1_REC
                self.E2 = self.APARA1.E2_REC
                self.E3 = self.APARA1.E3_REC
                self.E4 = self.APARA1.E4_REC
                self.AO1 = self.APARA1.AO1_REC
                self.AO2 = self.APARA1.AO2_REC
                self.AO3 = self.APARA1.AO3_REC
                self.AO4 = self.APARA1.AO4_REC
                self.RSF_LOCK_T = self.APARA1.RSF_LOCK_T_REC
                self.APARA_LAST_APPLY_OK = True
                self.APARA_LAST_APPLY_REASON = 1

        if self.R_TRIG_APARA_GC.Q:
            self.APARA_LAST_APPLY_GROUP = 3
            self.APARA_LAST_APPLY_OK = False
            if not self.APARA_CAN_APPLY:
                self.APARA_LAST_APPLY_REASON = 2
            elif not (self.APARA1.GC_OK or self.APARA1.FINAL_WEAK):
                self.APARA_LAST_APPLY_REASON = 3
            else:
                self.TC = self.APARA1.TC_REC
                self.TZ = self.APARA1.TZ_REC
                self.GC1 = self.APARA1.GC1_REC
                self.GC2 = self.APARA1.GC2_REC
                self.OUTH = self.APARA1.OUTH_REC
                self.OUTL = self.APARA1.OUTL_REC
                self.APARA_LAST_APPLY_OK = True
                self.APARA_LAST_APPLY_REASON = 1

        if self.R_TRIG_APARA_CD.Q:
            self.APARA_LAST_APPLY_GROUP = 4
            self.APARA_LAST_APPLY_OK = False
            if not self.APARA_CAN_APPLY:
                self.APARA_LAST_APPLY_REASON = 2
            elif not (self.APARA1.CD_OK or self.APARA1.FINAL_WEAK):
                self.APARA_LAST_APPLY_REASON = 3
            else:
                self.CD_GD = self.APARA1.CD_GD_REC
                self.CD_K = self.APARA1.CD_K_REC
                self.CD_K_FD = self.APARA1.CD_K_FD_REC
                self.CD_K_J = self.APARA1.CD_K_J_REC
                self.CD_K_D = self.APARA1.CD_K_D_REC
                self.CDH = self.APARA1.CDH_REC
                self.CDL = self.APARA1.CDL_REC
                self.TC_CD = self.APARA1.TC_CD_REC
                self.TZ_CD = self.APARA1.TZ_CD_REC
                self.APARA_LAST_APPLY_OK = True
                self.APARA_LAST_APPLY_REASON = 1

    def _clear_apara_commands(self) -> None:
        if self.APARA_RESET_PLS:
            self.APARA_RESET = False
        if self.APARA_CALC_NOW_PLS:
            self.APARA_CALC_NOW = False
        if self.R_TRIG_APARA_PID.Q:
            self.APARA_APPLY_PID = False
        if self.R_TRIG_APARA_RSF.Q:
            self.APARA_APPLY_RSF = False
        if self.R_TRIG_APARA_GC.Q:
            self.APARA_APPLY_GC = False
        if self.R_TRIG_APARA_CD.Q:
            self.APARA_APPLY_CD = False


__all__ = ["APCM", "RealRef", "BLINK_TIMEHIGH_MS", "FOP_DEFAULT_TB_SEC"]
