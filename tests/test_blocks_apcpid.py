"""APCPID（变比例变积分 PID 调节器）业务块的契约测试。

按提示词 A~N 覆盖，唯一事实来源是 ST 实际执行顺序（``APCPID.txt``）：

* A 初始状态与默认值（含 PIDZZD1 独立真实实例）
* B 授权门控（BD_ERROR1 累加 / 回绕 / 失败保持状态 / 不调用 PIDZZD1 / 恢复 /
  外层+内层共 2 次授权调用）
* C CYCLE / 参数修正（CYCLE/MU 持久化；TIi/PTt 实例；KD/TD 本拍局部）
* D 旧 EK 顺序（TIi 用上一拍 EK）
* E ATE / TS / preRM 状态机
* F 误差与微分（0.9/0.1 滤波、AD 取反、EK_LAST、DEK、末尾历史更新）
* G 跟踪模式（RM=3/4，增量/位置，OutRH/OutT/OutB 限幅，TM）
* H 手动模式（CASE MM，OutM，LASTUKOUT，MM 归零，TM）
* I 自动核心 PID（RM=1/2/非法、死区、积分分离、B1..C4、DU_TEMP、±1e10 边界）
* J 自动增量式（OutRH-OC 限幅、DUOUT=DU+OC、SADD/SSUB）
* K 自动位置式（UK_1+DU、OutT-OC/OutB-OC、UKOUT vs LASTUKOUT、禁止方向保留 AV_TEMP）
* L OutRL 末尾行为
* M PIDZZD 调用顺序与 PT1/TI1 延迟生效
* N 注释与实际代码冲突

授权用真实许可链路（写入正确 BD_MM1~4），不 monkey patch 授权结果为成功。
"""

from __future__ import annotations

import unittest

from src.blocks import APCPID
from src.blocks.apcpidzzd import APCPIDZZD
from src.globals import LicenseContext
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import ManualDateTimeProvider, StaticSerialTextProvider

SERIAL = "PYPLC|TEST|MACHINE-0001"
TIME_MS = 5000  # totalSeconds%10=5（非 7 时段），授权通过后稳定放行


def make_ctx(authorized: bool = True, now_ms: int = TIME_MS) -> LicenseContext:
    ctx = LicenseContext(
        StaticSerialTextProvider(SERIAL),
        ManualDateTimeProvider(now_ms),
    )
    if authorized:
        zcm = BD_ZCM(StaticSerialTextProvider(SERIAL)).step(True)
        mm = derive_passwords_from_registration_codes(
            zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
        )
        ctx.set_passwords(*mm)
    return ctx


def make_block(authorized: bool = True) -> APCPID:
    return APCPID(make_ctx(authorized))


def plant_block(authorized: bool = True, **var) -> APCPID:
    """标准量程的 PID 实例：PVMU=100, PVMD=0, MU=100, MD=0。"""
    p = make_block(authorized)
    p.PVMU = 100.0
    p.PVMD = 0.0
    p.MU = 100.0
    p.MD = 0.0
    for k, v in var.items():
        setattr(p, k, v)
    return p


def step_pid(
    p: APCPID,
    *,
    SP: float = 50.0,
    PV: float = 50.0,
    IC: float = 0.0,
    OC: float = 0.0,
    TP: float = 0.0,
    TS: bool = False,
    RM: int = 1,
    OutT: float = 100.0,
    OutB: float = 0.0,
    SADD: bool = False,
    SSUB: bool = False,
    PT: float = 10.0,
    TI: float = 20.0,
    KD: float = 1.0,
    TD: float = 0.0,
    dt_ms: int = 500,
) -> None:
    p.step(
        dt_ms,
        SP=SP,
        PV=PV,
        IC=IC,
        OC=OC,
        TP=TP,
        TS=TS,
        RM=RM,
        OutT=OutT,
        OutB=OutB,
        SADD=SADD,
        SSUB=SSUB,
        PT=PT,
        TI=TI,
        KD=KD,
        TD=TD,
    )


def record_pidzzd_calls(p: APCPID) -> list:
    """包装 PIDZZD1.step，记录每次调用的关键字参数（转发真实调用）。"""
    calls: list = []
    orig = p.PIDZZD1.step

    def rec(dt_ms, **kw):
        calls.append(kw)
        return orig(dt_ms, **kw)

    p.PIDZZD1.step = rec  # type: ignore[assignment]
    return calls


# ============================ A. 初始状态 ============================


class TestInitialState(unittest.TestCase):
    def test_initial_literals(self):
        p = make_block()
        self.assertEqual(p.AV, 0.0)
        self.assertEqual(p.CYCLE, 0.5)
        self.assertEqual(p.OutRH, 5.0)
        self.assertEqual(p.OutRL, 0.0)
        self.assertEqual(p.DI, 0.0)
        self.assertEqual(p.SVH, 30.0)
        self.assertEqual(p.SVL, 0.5)
        self.assertEqual(p.KP, 0.0)
        self.assertEqual(p.KI, 0.0)
        self.assertEqual(p.PT1K, 0.0)
        self.assertEqual(p.TI1K, 0.0)

    def test_uninitialized_states_zero(self):
        p = make_block()
        for name in (
            "MU", "MD", "OutM", "AD", "MI", "MS", "MM", "PVMU", "PVMD",
            "preRM", "nowRM", "UK_1", "DU_1", "EK_1", "EK_2", "DEK", "DEK_1",
            "DEK_2", "PV_LAST", "deadenter", "TIi", "PX", "PT1", "TI1",
            "UK", "LASTUKOUT", "EK", "UKOUT", "DUOUT", "DU", "DU_TEMP", "SI",
            "B1", "B2", "C1", "C2", "C3", "C4", "PTt", "DI_SJ", "SVH_SJ",
            "SVL_SJ", "AV_TEMP", "EK_LAST",
        ):
            self.assertEqual(getattr(p, name), 0, f"{name} 应为 0")
        self.assertIs(p.TM, False)
        self.assertIs(p.ATE, False)

    def test_pidzzd1_is_real_independent_instance_sharing_ctx(self):
        p = make_block()
        self.assertIsInstance(p.PIDZZD1, APCPIDZZD)
        self.assertIs(p.PIDZZD1._ctx, p._ctx)
        # 不同 APCPID 拥有各自独立的 PIDZZD1。
        q = make_block()
        self.assertIsNot(p.PIDZZD1, q.PIDZZD1)


# ============================ B. 授权门控 ============================


class TestAuthGate(unittest.TestCase):
    def test_auth_fail_increments_bd_error1(self):
        p = plant_block(authorized=False)
        step_pid(p, PV=55.0)
        self.assertEqual(p._ctx.BD_ERROR1, 1.0)
        step_pid(p, PV=55.0)
        self.assertEqual(p._ctx.BD_ERROR1, 2.0)

    def test_bd_error1_wraparound(self):
        p = plant_block(authorized=False)
        p._ctx.BD_ERROR1 = 999999999.0
        step_pid(p, PV=55.0)
        self.assertEqual(p._ctx.BD_ERROR1, 100000000.0)

    def test_auth_fail_keeps_state_and_skips_pidzzd1(self):
        p = plant_block(authorized=False)
        p.AV = 7.0
        p.EK_1 = 3.0
        p.UK_1 = 4.0
        p.PT1 = 1.0
        p.TI1 = 2.0
        p.PIDZZD1.PT1 = 9.0
        calls = record_pidzzd_calls(p)
        step_pid(p, PV=55.0)
        self.assertEqual(p.AV, 7.0)
        self.assertEqual(p.EK_1, 3.0)
        self.assertEqual(p.UK_1, 4.0)
        self.assertEqual(p.PT1, 1.0)
        self.assertEqual(p.TI1, 2.0)
        self.assertEqual(p.PIDZZD1.PT1, 9.0)
        self.assertEqual(len(calls), 0)  # 授权失败不调用 PIDZZD1

    def test_auth_recovers_next_scan(self):
        ctx = make_ctx(authorized=False)
        p = APCPID(ctx)
        p.PVMU, p.PVMD, p.MU, p.MD = 100.0, 0.0, 100.0, 0.0
        step_pid(p, PV=55.0)  # 失败
        self.assertEqual(p.EK, 0.0)
        zcm = BD_ZCM(StaticSerialTextProvider(SERIAL)).step(True)
        ctx.set_passwords(
            *derive_passwords_from_registration_codes(
                zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
            )
        )
        step_pid(p, PV=55.0)  # 恢复：EK 被计算
        self.assertAlmostEqual(p.EK, 0.5)

    def test_two_auth_calls_on_success_one_on_fail(self):
        # 成功：外层 1 次 + 内层 APCPIDZZD 1 次 = 2 次。
        p = plant_block(authorized=True)
        calls: list = []
        orig = p._ctx.KZQBDYZMK.step

        def counting(dt_ms=0):
            calls.append(1)
            return orig(dt_ms)

        p._ctx.KZQBDYZMK.step = counting  # type: ignore[assignment]
        step_pid(p, PV=55.0)
        self.assertEqual(len(calls), 2)

        # 失败：仅外层 1 次。
        pf = plant_block(authorized=False)
        calls2: list = []
        orig2 = pf._ctx.KZQBDYZMK.step

        def counting2(dt_ms=0):
            calls2.append(1)
            return orig2(dt_ms)

        pf._ctx.KZQBDYZMK.step = counting2  # type: ignore[assignment]
        step_pid(pf, PV=55.0)
        self.assertEqual(len(calls2), 1)


# ============================ C. CYCLE / 参数修正 ============================


class TestCycleAndParamCorrection(unittest.TestCase):
    def test_cycle_le_zero_set_half_and_persists(self):
        p = plant_block(CYCLE=-1.0)
        step_pid(p, PV=55.0)
        self.assertEqual(p.CYCLE, 0.5)
        step_pid(p, PV=55.0)
        self.assertEqual(p.CYCLE, 0.5)  # 持久化

    def test_mu_minus_md_zero_corrected_and_persists(self):
        p = plant_block(MU=5.0, MD=5.0)
        step_pid(p, PV=55.0)
        self.assertEqual(p.MU, 5.00001)
        # 下一拍 MU-MD != 0，不再改写。
        step_pid(p, PV=55.0)
        self.assertEqual(p.MU, 5.00001)

    def test_tii_le_zero_set_to_0001(self):
        # ELSE 分支 TIi=TI+TI1=0 → 0.001（旧 EK=0 < SVH_SJ=30）。
        p = plant_block()
        step_pid(p, PV=55.0, TI=0.0)
        self.assertEqual(p.TIi, 0.001)

    def test_ptt_le_zero_set_to_0001(self):
        # PX=0 → PTt=0 → 0.001（PT=0, KP=0, PT1=0, SP=PV）。
        p = plant_block()
        step_pid(p, SP=50.0, PV=50.0, PT=0.0)
        self.assertEqual(p.PTt, 0.001)

    def test_kd_le_zero_local_only_affects_this_scan(self):
        # KD=0 → 本拍局部 kd=0.001；td=0.5,CYCLE=0.5 → B1=(1.0)/0.001=1000。
        p = plant_block()
        step_pid(p, PV=56.0, KD=0.0, TD=0.5)
        self.assertAlmostEqual(p.B1, 1000.0)
        self.assertFalse(hasattr(p, "KD"))  # KD 不持久化为实例属性

    def test_td_lt_zero_local_clamped_to_zero(self):
        # TD=-5 → 本拍局部 td=0 → B1=(0/CYCLE)/kd=0, C4=0。
        p = plant_block()
        step_pid(p, PV=56.0, TD=-5.0, KD=1.0)
        self.assertEqual(p.B1, 0.0)
        self.assertEqual(p.C4, 0.0)
        self.assertFalse(hasattr(p, "TD"))

    def test_pid_uses_cycle_not_dt_over_1000(self):
        # dt_ms=1000，但 CYCLE=0.5：B1=(td/CYCLE)/kd=(0.5/0.5)/1=1.0。
        # 若误用 dt/1000=1.0，则 B1=(0.5/1.0)/1=0.5。
        p = plant_block()
        step_pid(p, PV=56.0, TD=0.5, KD=1.0, dt_ms=1000)
        self.assertAlmostEqual(p.B1, 1.0)


# ============================ D. 旧 EK 顺序 ============================


class TestOldEKOrder(unittest.TestCase):
    def test_tii_uses_previous_ek_then_ek_recomputed(self):
        # 预置上一拍 EK=100 (>SVH_SJ=30)，KI=2，SP=50,PV=60。
        # TIi=TI + abs(SP-PV)*KI + TI1 = 20 + 10*2 + 0 = 40。
        p = plant_block(KI=2.0)
        p.EK = 100.0
        step_pid(p, SP=50.0, PV=60.0, TI=20.0)
        self.assertAlmostEqual(p.TIi, 40.0)
        # 本拍 EK 在 TIi 之后才重算：0.9*0 + 0.1*(60-50) = 1.0。
        self.assertAlmostEqual(p.EK, 1.0)


# ============================ E. ATE / TS / preRM ============================


class TestATEStateMachine(unittest.TestCase):
    def test_ate_false_does_not_touch_rm(self):
        # ATE=False, RM=1 → 自动路径，preRM 不变。
        p = plant_block(ATE=False)
        p.preRM = 0
        step_pid(p, PV=55.0, RM=1)
        self.assertEqual(p.preRM, 0)

    def test_ate_ts_rm_not_4_saves_prerm_and_tracks(self):
        # ATE=True, TS=True, RM=1 → preRM=1, rm=4 → 跟踪（增量式）。
        p = plant_block(ATE=True, OutM=1)
        step_pid(p, TS=True, RM=1, TP=3.0)
        self.assertEqual(p.preRM, 1)
        self.assertAlmostEqual(p.AV, 3.0)  # 跟踪增量输出=TP

    def test_ate_ts_false_prerm_not_4_restores(self):
        # ATE=True, TS=False, preRM=3 → rm=preRM=3（跟踪）, preRM=4。
        p = plant_block(ATE=True, OutM=1)
        p.preRM = 3
        step_pid(p, TS=False, RM=1, TP=2.0)
        self.assertEqual(p.preRM, 4)
        self.assertAlmostEqual(p.AV, 2.0)

    def test_prerm_persists_across_scans(self):
        p = plant_block(ATE=True, OutM=1)
        step_pid(p, TS=True, RM=1, TP=1.0)  # preRM=1
        self.assertEqual(p.preRM, 1)
        # 下一拍 TS=False, preRM=1(!=4) → rm=1（自动），preRM=4。
        step_pid(p, TS=False, RM=2, PV=55.0)
        self.assertEqual(p.preRM, 4)


# ============================ F. 误差与微分 ============================


class TestErrorAndDifferential(unittest.TestCase):
    def test_ek_filter_0_9_0_1(self):
        p = plant_block()
        p.EK_LAST = 10.0
        step_pid(p, SP=50.0, PV=60.0)  # EK=0.9*10+0.1*(60-50)=9+1=10
        self.assertAlmostEqual(p.EK, 10.0)

    def test_ad_negates_ek_but_ek_last_keeps_unnegated(self):
        p = plant_block(AD=1)
        step_pid(p, SP=50.0, PV=60.0)  # filtered = 0.1*10 = 1.0
        self.assertAlmostEqual(p.EK_LAST, 1.0)  # 未取反
        self.assertAlmostEqual(p.EK, -1.0)  # 取反

    def test_dek_pv_minus_pvlast_and_ad_negation(self):
        p = plant_block()
        p.PV_LAST = 40.0
        step_pid(p, PV=50.0)  # DEK = 50-40 = 10
        self.assertAlmostEqual(p.DEK, 10.0)
        q = plant_block(AD=1)
        q.PV_LAST = 40.0
        step_pid(q, PV=50.0)  # DEK = -(50-40) = -10
        self.assertAlmostEqual(q.DEK, -10.0)

    def test_history_updated_at_tail(self):
        p = plant_block()
        step_pid(p, SP=50.0, PV=56.0)
        ek1, dek1, pvl = p.EK, p.DEK, 56.0
        self.assertAlmostEqual(p.EK_1, ek1)
        self.assertAlmostEqual(p.DEK_1, dek1)
        self.assertAlmostEqual(p.PV_LAST, pvl)
        step_pid(p, SP=50.0, PV=57.0)
        self.assertAlmostEqual(p.EK_2, ek1)  # 上一拍 EK_1 移到 EK_2
        self.assertAlmostEqual(p.DEK_2, dek1)


# ============================ G. 跟踪模式 ============================


class TestTrackingMode(unittest.TestCase):
    def test_rm3_incremental_outrh_clamp(self):
        p = plant_block(OutM=1)
        step_pid(p, RM=3, TP=100.0)  # |100|>OutRH=5 → 5
        self.assertAlmostEqual(p.AV, 5.0)
        self.assertAlmostEqual(p.DU, 5.0)  # DU=DUOUT-OC

    def test_rm4_position_outt_outb_then_outrh(self):
        p = plant_block(OutM=0)
        step_pid(p, RM=4, TP=200.0, OutT=100.0, OutB=0.0)
        # UKOUT=min(100,200)=100,max(0,100)=100; |100-0|>5 → 0+5=5
        self.assertAlmostEqual(p.AV, 5.0)

    def test_tm_sets_local_sp_to_pv_into_pidzzd1(self):
        p = plant_block(OutM=1, TM=True)
        calls = record_pidzzd_calls(p)
        step_pid(p, RM=3, SP=50.0, PV=70.0, TP=1.0)
        self.assertAlmostEqual(calls[0]["SP"], 70.0)
        self.assertEqual(calls[0]["RM"], 3)

    def test_mm_not_reset_in_tracking(self):
        p = plant_block(OutM=1, MM=2)
        step_pid(p, RM=4, TP=1.0)
        self.assertEqual(p.MM, 2)


# ============================ H. 手动模式 ============================


class TestManualMode(unittest.TestCase):
    def test_increment_mm_cases(self):
        for mm, mi, ms, expect in [
            (1, 2.0, 9.0, 2.0),
            (2, 2.0, 9.0, -2.0),
            (3, 9.0, 3.0, 3.0),
            (4, 9.0, 3.0, -3.0),
            (9, 2.0, 3.0, 0.0),  # 非法 → 0
        ]:
            p = plant_block(OutM=1, MM=mm, MI=mi, MS=ms)
            step_pid(p, RM=0)
            self.assertAlmostEqual(p.AV, expect, msg=f"MM={mm}")

    def test_position_uses_lastukout(self):
        p = plant_block(OutM=0, MM=1, MI=2.0)
        p.AV = 10.0  # 顶部 LASTUKOUT=AV=10
        step_pid(p, RM=0, OutT=100.0)
        # UKOUT=MI=2; LASTUKOUT+UKOUT=12; clamp→12; |12-10|=2>5?no → AV=12
        self.assertAlmostEqual(p.AV, 12.0)

    def test_position_outrh_clamp(self):
        p = plant_block(OutM=0, MM=1, MI=100.0)
        step_pid(p, RM=0, OutT=1000.0)
        # UKOUT=100; LASTUKOUT(0)+100=100; |100-0|=100>5 → 0+5=5
        self.assertAlmostEqual(p.AV, 5.0)

    def test_mm_reset_to_zero_after_manual(self):
        p = plant_block(OutM=1, MM=1, MI=2.0)
        step_pid(p, RM=0)
        self.assertEqual(p.MM, 0)

    def test_tm_sets_local_sp_into_pidzzd1(self):
        p = plant_block(OutM=1, MM=1, MI=1.0, TM=True)
        calls = record_pidzzd_calls(p)
        step_pid(p, RM=0, SP=50.0, PV=70.0)
        self.assertAlmostEqual(calls[0]["SP"], 70.0)
        self.assertEqual(calls[0]["RM"], 0)


# ============================ I. 自动核心 PID ============================


class TestAutoCorePID(unittest.TestCase):
    def test_rm1_rm2_illegal_all_take_auto(self):
        for rm in (1, 2, 99):
            p = plant_block(OutM=0)
            step_pid(p, RM=rm, SP=50.0, PV=56.0, PT=10.0, TI=20.0)
            # 自动位置式：DU 限到 OutRH=5 → UK=5 → AV=5
            self.assertAlmostEqual(p.AV, 5.0, msg=f"RM={rm}")

    def test_deadzone_du_zero(self):
        p = plant_block(DI=50.0)  # DI_SJ=50
        step_pid(p, SP=50.0, PV=55.0)  # |EK|=0.5 <= 50 → 死区
        self.assertEqual(p.DU, 0.0)

    def test_integral_separation_si(self):
        p = plant_block()
        step_pid(p, SP=50.0, PV=55.0)  # EK=0.5 <= SVL_SJ=0.5 → SI=0
        self.assertEqual(p.SI, 0)
        q = plant_block()
        step_pid(q, SP=50.0, PV=56.0)  # EK=0.6 > 0.5 → SI=1
        self.assertEqual(q.SI, 1)

    def test_intermediate_coefficients(self):
        p = plant_block(OutM=0)
        step_pid(p, SP=50.0, PV=56.0, PT=10.0, TI=20.0, TD=0.5, KD=1.0)
        # PTt=0.1, TIi=20, SI=1, B1=1,B2=2,C1=0.5
        self.assertAlmostEqual(p.B1, 1.0)
        self.assertAlmostEqual(p.B2, 2.0)
        self.assertAlmostEqual(p.C1, 0.5)
        self.assertAlmostEqual(p.C2, 10.125)
        self.assertAlmostEqual(p.C3, -15.0)
        self.assertAlmostEqual(p.C4, 5.0)
        self.assertAlmostEqual(p.DU_TEMP, 283.075)

    def test_du_temp_exactly_1e10_gives_du_zero(self):
        # 构造 bracket=0（EK=0, DEK=0），DU_TEMP=C1*DU_1=0.5*2e10=1e10。
        p = plant_block(OutM=0, DI=-1.0)  # DI_SJ=-1 → 不进死区即便 EK=0
        p.DU_1 = 2e10
        p.PV_LAST = 50.0
        step_pid(p, SP=50.0, PV=50.0, TD=0.5, KD=1.0, PT=10.0, TI=20.0)
        self.assertEqual(p.DU_TEMP, 1e10)
        self.assertEqual(p.DU, 0.0)  # 1e10 < 1e10 为假 → DU=0

    def test_du_temp_just_below_1e10_passes(self):
        p = plant_block(OutM=0, DI=-1.0)
        p.DU_1 = 2e10 - 4
        p.PV_LAST = 50.0
        step_pid(p, SP=50.0, PV=50.0, TD=0.5, KD=1.0, PT=10.0, TI=20.0)
        self.assertEqual(p.DU_TEMP, 1e10 - 2)
        # DU=DU_TEMP（巨大）→ 位置式被 OutRH 限到 5
        self.assertAlmostEqual(p.DU, 5.0)


# ============================ J. 自动增量式 ============================


class TestAutoIncremental(unittest.TestCase):
    def test_du_clamped_to_outrh_minus_oc_and_duout(self):
        p = plant_block(OutM=1)
        step_pid(p, SP=50.0, PV=56.0, OC=2.0)
        # DU 巨大 → |DU|>(OutRH-OC=3) → DU=3; DUOUT=DU+OC=5
        self.assertAlmostEqual(p.DU, 3.0)
        self.assertAlmostEqual(p.DUOUT, 5.0)
        self.assertAlmostEqual(p.AV, 5.0)

    def test_sadd_limits_av_temp_to_min0(self):
        p = plant_block(OutM=1)
        step_pid(p, SP=50.0, PV=56.0, SADD=True)
        # AV_TEMP=5>0 → min(0,5)=0 → AV 未更新（|0-0|>0 假）
        self.assertEqual(p.AV_TEMP, 0.0)
        self.assertEqual(p.AV, 0.0)

    def test_ssub_limits_av_temp_to_max0(self):
        p = plant_block(OutM=1)
        step_pid(p, SP=50.0, PV=56.0, SSUB=True)
        # AV_TEMP=5 → max(0,5)=5 → 不变
        self.assertAlmostEqual(p.AV_TEMP, 5.0)

    def test_sadd_and_ssub_order(self):
        # 正输出：SADD 先 min(0,5)=0，再 SSUB max(0,0)=0 → 0
        p = plant_block(OutM=1)
        step_pid(p, SP=50.0, PV=56.0, SADD=True, SSUB=True)
        self.assertEqual(p.AV_TEMP, 0.0)


# ============================ K. 自动位置式 ============================


class TestAutoPosition(unittest.TestCase):
    def test_outt_oc_clamp(self):
        p = plant_block(OutM=0)
        step_pid(p, SP=50.0, PV=56.0, OC=1.0, OutT=3.0)
        # DU=5(限幅); UK=0+5=5; min(OutT-OC=2,5)=2; UKOUT=2+1=3; AV=3
        self.assertAlmostEqual(p.AV, 3.0)

    def test_ukout_vs_lastukout_outrh_clamp(self):
        p = plant_block(OutM=0)
        p.AV_TEMP = -10.0  # 自动起始 LASTUKOUT=AV_TEMP=-10
        step_pid(p, SP=50.0, PV=56.0, OutT=100.0, OutB=-100.0)
        # DU=5; UK=0+5=5; UKOUT=5; |5-(-10)|=15>5 → UKOUT=-10+5=-5; AV=-5
        self.assertAlmostEqual(p.AV, -5.0)

    def test_sadd_forbidden_direction_keeps_old_av_temp(self):
        p = plant_block(OutM=0)
        # 旧 AV_TEMP=0 → LASTUKOUT=0；上行 UKOUT>0 且 SADD → 禁止，保留旧 AV_TEMP
        step_pid(p, SP=50.0, PV=56.0, SADD=True, OC=2.0)
        self.assertEqual(p.AV_TEMP, 0.0)
        self.assertAlmostEqual(p.UK, -2.0)  # UK=AV_TEMP-OC=0-2
        self.assertEqual(p.AV, 0.0)

    def test_allowed_direction_updates_av_temp(self):
        p = plant_block(OutM=0)
        step_pid(p, SP=50.0, PV=56.0)  # 无 SADD/SSUB → AV_TEMP=UKOUT=5
        self.assertAlmostEqual(p.AV_TEMP, 5.0)
        self.assertAlmostEqual(p.AV, 5.0)


# ============================ L. OutRL 末尾行为 ============================


class TestOutRL(unittest.TestCase):
    def test_update_when_diff_gt_abs_outrl(self):
        p = plant_block(OutM=0, OutRL=0.0)
        step_pid(p, SP=50.0, PV=56.0)  # |0-5|=5 > 0 → AV=5
        self.assertAlmostEqual(p.AV, 5.0)

    def test_no_update_when_within_outrl(self):
        p = plant_block(OutM=0, OutRL=100.0)
        step_pid(p, SP=50.0, PV=56.0)  # |0-5|=5 > 100? no → AV 不更新
        self.assertEqual(p.AV, 0.0)
        self.assertAlmostEqual(p.AV_TEMP, 5.0)

    def test_negative_outrl_uses_abs(self):
        p = plant_block(OutM=0, OutRL=-100.0)
        step_pid(p, SP=50.0, PV=56.0)  # ABS(-100)=100 → 5>100? no → 不更新
        self.assertEqual(p.AV, 0.0)


# ============================ M. PIDZZD 顺序 / PT1 TI1 延迟 ============================


class TestPidzzdOrderAndDelay(unittest.TestCase):
    def test_pid_uses_old_pt1_ti1_this_scan(self):
        # 预置 PT1=2, TI1=3：PX=PT+...+PT1=12（PTt 用旧 PT1）；TIi=TI+TI1=23。
        p = plant_block()
        p.PT1 = 2.0
        p.TI1 = 3.0
        step_pid(p, SP=50.0, PV=56.0, PT=10.0, TI=20.0)
        self.assertAlmostEqual(p.PX, 12.0)
        self.assertAlmostEqual(p.TIi, 23.0)

    def test_pt1_ti1_copied_back_from_pidzzd1(self):
        p = plant_block()
        step_pid(p, SP=50.0, PV=56.0)
        self.assertEqual(p.PT1, p.PIDZZD1.PT1)
        self.assertEqual(p.TI1, p.PIDZZD1.TI1)

    def test_history_updated_before_pidzzd1_call(self):
        p = plant_block()
        observed = {}
        orig = p.PIDZZD1.step

        def rec(dt_ms, **kw):
            observed["DU_1_eq_DU"] = p.DU_1 == p.DU
            observed["EK_1_eq_EK"] = p.EK_1 == p.EK
            return orig(dt_ms, **kw)

        p.PIDZZD1.step = rec  # type: ignore[assignment]
        step_pid(p, SP=50.0, PV=56.0)
        self.assertTrue(observed["DU_1_eq_DU"])
        self.assertTrue(observed["EK_1_eq_EK"])


# ============================ N. 注释与实际代码冲突 ============================


class TestCommentConflicts(unittest.TestCase):
    def test_illegal_rm_enters_auto_not_hold_previous(self):
        # 注释称非法值保持前状态；实际 RM=2/非法走自动。
        p = plant_block(OutM=0)
        step_pid(p, RM=2, SP=50.0, PV=56.0)
        self.assertAlmostEqual(p.AV, 5.0)

    def test_outrl_is_av_commit_threshold_not_rate_limiter(self):
        # OutRL 仅在自动末尾决定是否提交 AV_TEMP，非常规限速器。
        p = plant_block(OutM=0, OutRL=100.0)
        step_pid(p, SP=50.0, PV=56.0)
        self.assertEqual(p.AV, 0.0)  # 被 OutRL 阈值挡下而非限速

    def test_kd_td_local_not_persisted_across_scans(self):
        # 第一拍 KD=0（局部修正 0.001），第二拍 KD=1 应正常使用 1（不被持久化）。
        p = plant_block()
        step_pid(p, PV=56.0, KD=0.0, TD=0.5)
        self.assertAlmostEqual(p.B1, 1000.0)
        step_pid(p, PV=56.0, KD=1.0, TD=0.5)
        self.assertAlmostEqual(p.B1, 1.0)


if __name__ == "__main__":
    unittest.main()
