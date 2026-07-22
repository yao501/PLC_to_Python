"""APCM 综合控制功能块契约测试。

测试重点锁定本轮迁移约束：普通 FB 实例跨扫描保持、授权门控冻结、ZLOUT
上升沿累计、MM 单次消费、APARA 推荐应用写回 ``self.*``。
"""

from __future__ import annotations

import inspect
import unittest

from src.blocks import APCM, RealRef
from src.blocks.apcm import BLINK_TIMEHIGH_MS, FOP_DEFAULT_TB_SEC
from src.blocks.apcmautopara import APCMAUTOPARA
from src.blocks.apcpidzzd import APCPIDZZD
from src.globals import LicenseContext
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import ManualDateTimeProvider, StaticSerialTextProvider


SERIAL = "PYPLC|TEST|MACHINE-0001"
TIME_MS = 5000


def make_ctx(authorized: bool = True) -> LicenseContext:
    ctx = LicenseContext(
        StaticSerialTextProvider(SERIAL),
        ManualDateTimeProvider(TIME_MS),
    )
    if authorized:
        zcm = BD_ZCM(StaticSerialTextProvider(SERIAL)).step(True)
        ctx.set_passwords(
            *derive_passwords_from_registration_codes(
                zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
            )
        )
    return ctx


def make_block(authorized: bool = True) -> APCM:
    p = APCM(make_ctx(authorized))
    p.PVMU = 100.0
    p.PVMD = 0.0
    p.MU = 100.0
    p.MD = 0.0
    p.OUTT = 100.0
    p.OUTB = 0.0
    p.SP = 50.0
    p.PV = 50.0
    p.RM = 1
    p.ATE = False
    p.preRM = 4
    return p


def step_apcm(
    p: APCM,
    dt_ms: int = 500,
    *,
    ref: RealRef | None = None,
    **kwargs,
) -> RealRef:
    if ref is None:
        ref = RealRef(0.0)
    call = {
        "SP": p.SP,
        "PV": p.PV,
        "OC": p.OC,
        "TS": p.TS,
        "TP": p.TP,
        "zlout_ref": ref,
    }
    call.update(kwargs)
    p.step(dt_ms, **call)
    return ref


class TestInitialAndInstanceState(unittest.TestCase):
    def test_export_and_real_child_instances(self):
        p = make_block()
        self.assertIsInstance(p.PIDZZD1, APCPIDZZD)
        self.assertIs(p.PIDZZD1._ctx, p._ctx)
        self.assertIsInstance(p.APARA1, APCMAUTOPARA)
        self.assertEqual(BLINK_TIMEHIGH_MS, 500)
        self.assertEqual(FOP_DEFAULT_TB_SEC, 0.5)

    def test_step_interface_has_no_tunable_parameter_kwargs(self):
        params = inspect.signature(APCM.step).parameters
        self.assertIn("dt_ms", params)
        for name in ("SP", "PV", "OC", "TS", "TP", "zlout_ref"):
            self.assertIn(name, params)
            self.assertIs(params[name].default, inspect._empty)
        for name in ("RM", "OUTT", "OUTB", "SADD", "SSUB", "ZLEN", "ZSYK"):
            self.assertIn(name, params)
            self.assertIsNone(params[name].default)
        for name in ("PT", "TI", "TD", "DI", "TL", "TC", "CD_GD", "KP", "KI", "KD"):
            self.assertNotIn(name, params)

    def test_tunable_parameters_persist_across_scans(self):
        p = make_block()
        p.PT = 321.0
        p.KP = 1.25
        p.TI = 654.0
        p.KI = 2.5
        p.TD = 7.0
        p.KD = 3.5
        p.DI = 2.5
        p.TL = 8.0
        p.CD_GD = 1.25
        p.RSFEN = True
        p.RSF_LOCK_LEVEL = 3.0
        p.RSF_LOCK_SIG = 1.0

        step_apcm(p)
        step_apcm(p)

        self.assertEqual(p.PT, 321.0)
        self.assertEqual(p.KP, 1.25)
        self.assertEqual(p.TI, 654.0)
        self.assertEqual(p.KI, 2.5)
        self.assertEqual(p.TD, 7.0)
        self.assertEqual(p.KD, 3.5)
        self.assertEqual(p.DI, 2.5)
        self.assertEqual(p.TL, 8.0)
        self.assertEqual(p.CD_GD, 1.25)
        self.assertEqual(p.RSF_LOCK_LEVEL, 3.0)

    def test_hmi_can_overwrite_apara_applied_pt_on_next_scan(self):
        p = make_block()
        p.PT = 10.0
        p.APARA_APPLY_PID = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.PID_OK = True
            p.APARA1.PT_REC = 101.0
            p.APARA1.TI_REC = 102.0
            p.APARA1.TD_REC = 103.0
            p.APARA1.DI_REC = 104.0
            p.APARA1.SVH_REC = 105.0
            p.APARA1.SVL_REC = 106.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)
        self.assertEqual(p.PT, 101.0)

        p.PT = 200.0
        step_apcm(p)
        self.assertEqual(p.PT, 200.0)

    def test_fop_children_receive_source_default_tb(self):
        p = make_block()
        fop1_calls: list[dict] = []
        fop2_calls: list[dict] = []
        orig_fop1 = p.FOP1.step
        orig_fop2 = p.FOP2.step

        def fop1_rec(dt_ms: int, **kw):
            fop1_calls.append(kw)
            return orig_fop1(dt_ms, **kw)

        def fop2_rec(dt_ms: int, **kw):
            fop2_calls.append(kw)
            return orig_fop2(dt_ms, **kw)

        p.FOP1.step = fop1_rec  # type: ignore[assignment]
        p.FOP2.step = fop2_rec  # type: ignore[assignment]
        step_apcm(p)

        self.assertEqual(fop1_calls[-1]["TB"], 0.5)
        self.assertEqual(fop2_calls[-1]["TB"], 0.5)

    def test_fixed_half_second_counters_are_not_dt_scaled(self):
        a = make_block()
        b = make_block()
        a.PVMAX = 50.0
        b.PVMAX = 50.0

        step_apcm(a, 500)
        step_apcm(b, 1000)

        self.assertEqual(a.NUM, 0.5)
        self.assertEqual(b.NUM, 0.5)

    def test_no_internal_zlout_fallback(self):
        p = make_block()
        self.assertNotIn("_zlout_ref", p.__dict__)


class TestProcessInputs(unittest.TestCase):
    def test_process_inputs_and_overrides_are_written_before_auth(self):
        p = make_block(authorized=False)
        p.NUM = 9.0
        p.MM = 1
        p.APARA_RESET = True

        step_apcm(
            p,
            SP=11.0,
            PV=22.0,
            OC=3.0,
            TS=True,
            TP=44.0,
            RM=0,
            OUTT=88.0,
            OUTB=8.0,
            SADD=True,
            SSUB=True,
            ZLEN=True,
            ZSYK=2.0,
        )

        self.assertEqual((p.SP, p.PV, p.OC, p.TS, p.TP), (11.0, 22.0, 3.0, True, 44.0))
        self.assertEqual((p.RM, p.OUTT, p.OUTB, p.SADD, p.SSUB, p.ZLEN, p.ZSYK), (0, 88.0, 8.0, True, True, True, 2.0))
        self.assertEqual(p._ctx.BD_ERROR6, 1.0)
        self.assertEqual(p.NUM, 9.0)
        self.assertEqual(p.MM, 1)
        self.assertIs(p.APARA_RESET, True)

    def test_none_realtime_overrides_keep_existing_instance_fields(self):
        p = make_block()
        p.RM = 3
        p.OUTT = 90.0
        p.OUTB = 10.0
        p.SADD = True
        p.SSUB = False
        p.ZLEN = True
        p.ZSYK = 2.0

        step_apcm(
            p,
            RM=None,
            OUTT=None,
            OUTB=None,
            SADD=None,
            SSUB=None,
            ZLEN=None,
            ZSYK=None,
        )

        self.assertEqual((p.RM, p.OUTT, p.OUTB, p.SADD, p.SSUB, p.ZLEN, p.ZSYK), (3, 90.0, 10.0, True, False, True, 2.0))

    def test_source_assignments_to_cycle_kd_td_limits_write_back(self):
        p = make_block()
        p.CYCLE = 0.0
        p.KD = 0.0
        p.TD = -1.0
        p.ZSYK = 20.0
        p.OUTT = 200.0
        p.OUTB = -20.0

        step_apcm(p)

        self.assertEqual(p.CYCLE, 0.5)
        self.assertEqual(p.KD, 0.001)
        self.assertEqual(p.TD, 0.0)
        self.assertEqual(p.ZSYK, 10.0)
        self.assertEqual(p.OUTT, 100.0)
        self.assertEqual(p.OUTB, 0.0)


class TestAuthGate(unittest.TestCase):
    def test_success_has_outer_and_pidzzd_auth_calls(self):
        p = make_block(authorized=True)
        calls: list[int] = []
        orig = p._ctx.KZQBDYZMK.step

        def counting(dt_ms: int = 0):
            calls.append(1)
            return orig(dt_ms)

        p._ctx.KZQBDYZMK.step = counting  # type: ignore[assignment]
        step_apcm(p)
        self.assertEqual(len(calls), 2)

    def test_auth_failure_freezes_state_and_commands(self):
        p = make_block(authorized=False)
        p.PT = 222.0
        p.NUM = 9.0
        p.MM = 1
        p.APARA_RESET = True
        p.APARA_APPLY_PID = True
        p.AV = 12.0

        step_apcm(p)

        self.assertEqual(p._ctx.BD_ERROR6, 1.0)
        self.assertEqual(p._ctx.BD_ERROR5, 0.0)
        self.assertEqual(p.PT, 222.0)
        self.assertEqual(p.NUM, 9.0)
        self.assertEqual(p.MM, 1)
        self.assertIs(p.APARA_RESET, True)
        self.assertIs(p.APARA_APPLY_PID, True)
        self.assertEqual(p.AV, 12.0)

    def test_auth_failure_does_not_advance_child_fb_state(self):
        p = make_block(authorized=False)
        p.ZLEN = True
        p.AV = 100.0
        p.APARA_RESET = True
        p.APARA_CALC_NOW = True
        p.BLINK1._elapsed_ms = 123
        p.BLINK1.OUT = False
        p.CD_TON1.ET_ms = 456
        p.CD_TON1.Q = False
        p.R_TRIG02._CLK_prev = False
        p.R_TRIG02.Q = False
        p.PIDZZD1.PT1 = 7.0
        p.APARA1.RUNNING = False

        step_apcm(p, ref=RealRef(9.0))

        self.assertEqual(p.BLINK1._elapsed_ms, 123)
        self.assertIs(p.BLINK1.OUT, False)
        self.assertEqual(p.CD_TON1.ET_ms, 456)
        self.assertIs(p.CD_TON1.Q, False)
        self.assertIs(p.R_TRIG02._CLK_prev, False)
        self.assertIs(p.R_TRIG02.Q, False)
        self.assertEqual(p.PIDZZD1.PT1, 7.0)
        self.assertIs(p.APARA1.RUNNING, False)
        self.assertIs(p.APARA_RESET, True)
        self.assertIs(p.APARA_CALC_NOW, True)

    def test_auth_failure_uses_only_outer_auth_call(self):
        p = make_block(authorized=False)
        calls: list[int] = []
        orig = p._ctx.KZQBDYZMK.step

        def counting(dt_ms: int = 0):
            calls.append(1)
            return orig(dt_ms)

        p._ctx.KZQBDYZMK.step = counting  # type: ignore[assignment]
        step_apcm(p)
        self.assertEqual(len(calls), 1)

    def test_bd_error6_wraparound(self):
        p = make_block(authorized=False)
        p._ctx.BD_ERROR6 = 999999999.0
        step_apcm(p)
        self.assertEqual(p._ctx.BD_ERROR6, 100000000.0)


class TestZLOUT(unittest.TestCase):
    def test_rising_edge_accumulates_once_then_waits_for_next_edge(self):
        p = make_block()
        p.ZLEN = True
        ref = RealRef(10.0)

        p.AV = 100.0
        p.AV_R = 2.0
        p.AV_P_TEMP = 3.0
        step_apcm(p, ref=ref)
        self.assertEqual(ref.value, 15.0)

        p.AV = 100.0
        p.AV_R = 2.0
        p.AV_P_TEMP = 3.0
        step_apcm(p, ref=ref)
        self.assertEqual(ref.value, 15.0)

        p.AV = 50.0
        p.AV_R = 7.0
        p.AV_P_TEMP = 8.0
        step_apcm(p, ref=ref)
        self.assertEqual(ref.value, 15.0)

        p.AV = 100.0
        p.AV_R = 4.0
        p.AV_P_TEMP = 1.0
        step_apcm(p, ref=ref)
        self.assertEqual(ref.value, 20.0)

    def test_zlen_false_drives_rtrig02_false_and_does_not_change_zlout(self):
        p = make_block()
        p.ZLEN = True
        p.AV = 100.0
        p.AV_R = 2.0
        p.AV_P_TEMP = 3.0
        ref = RealRef(10.0)

        step_apcm(p, ref=ref)
        self.assertEqual(ref.value, 15.0)
        self.assertIs(p.R_TRIG02.Q, True)

        # 即使限位仍为 True，关闭 ZLEN 也必须推进 R_TRIG02(CLK=False)，
        # 不能把上一拍 Q=True 悬挂成持续整理/跟踪状态。
        p.AV = 100.0
        step_apcm(p, ref=ref, ZLEN=False, TP=-77.0)

        self.assertEqual(ref.value, 15.0)
        self.assertIs(p.ZL_EVENT, False)
        self.assertIs(p.R_TRIG02.Q, False)
        self.assertIs(p.R_TRIG02._CLK_prev, False)

        # 限位条件没有离开时重新打开 ZLEN，也必须形成一次确定的新事件。
        p.AV = 100.0
        p.AV_P_TEMP = 4.0
        p.AV_R = 1.0
        step_apcm(p, ref=ref, ZLEN=True, TP=-77.0)
        self.assertEqual(ref.value, 20.0)
        self.assertIs(p.ZL_EVENT, True)
        self.assertIs(p.R_TRIG02.Q, True)

    def test_zlout_uses_old_av_before_this_scan_total_output(self):
        p = make_block()
        p.ZLEN = True
        p.AV = 50.0
        p.AV_P_TEMP = 200.0
        ref = RealRef(0.0)

        step_apcm(p, ref=ref, RM=3, TP=200.0)
        self.assertEqual(ref.value, 0.0)

        p.AV_R = 2.0
        p.AV_P_TEMP = 3.0
        step_apcm(p, ref=ref, RM=3, TP=100.0)
        self.assertEqual(ref.value, 5.0)

    def test_cleanup_atomic_matrix_preserves_combination_and_blocks_all_rsf_bands(self):
        """上下限/四档/正反方向/两种 OutM 均不得在整理拍重触发 RSF。"""

        band_error = {1: 0.75, 2: 1.5, 3: 3.0, 4: 5.0}
        for out_m in (0, 1):
            for limit in (-100.0, 100.0):
                for band, magnitude in band_error.items():
                    for direction in (-1.0, 1.0):
                        with self.subTest(OutM=out_m, limit=limit, band=band, direction=direction):
                            p = make_block()
                            p.MD = -100.0
                            p.MU = 100.0
                            p.OUTB = -100.0
                            p.OUTT = 100.0
                            p.ZLEN = True
                            p.RSFEN = True
                            p.OutM = out_m
                            p.TL = 1.0
                            p.E1, p.E2, p.E3, p.E4 = 0.5, 1.0, 2.0, 4.0
                            p.AO1, p.AO2, p.AO3, p.AO4 = 3.0, 6.0, 9.0, 12.0

                            # 整理前 PID+RSF 的内部组合值恰为当前物理限位。
                            rsf_before = direction * (band + 2.0)
                            p.AV = limit
                            p.AV_R = rsf_before
                            p.AV_P_TEMP = limit - rsf_before
                            p.AV_P = p.AV_P_TEMP
                            setattr(p, f"CT_{band}", 1.0)  # 下一计数本可达到 2*TL
                            p.EK_R_1 = direction * magnitude
                            p.FLAG = 0.0
                            ref = RealRef(0.0)

                            step_apcm(
                                p,
                                ref=ref,
                                SP=50.0,
                                PV=50.0 + direction * magnitude,
                                TP=-limit,  # 故意与整理快照相反，证明事件拍不读取旧 TP
                            )

                            self.assertIs(p.ZL_EVENT, True)
                            self.assertAlmostEqual(p.ZL_PID_BASE, limit)
                            self.assertAlmostEqual(ref.value, limit)
                            self.assertAlmostEqual(p.AV_P_TEMP, limit)
                            self.assertAlmostEqual(p.AV_P, limit)
                            self.assertAlmostEqual(p.AV_R, 0.0)
                            self.assertAlmostEqual(p.AV, limit)
                            self.assertEqual(p.FLAG, 0.0)
                            self.assertEqual(getattr(p, f"CT_{band}"), 0.0)
                            for trig in (p.R_TRIG_R1, p.R_TRIG_R2, p.R_TRIG_R3, p.R_TRIG_R4, p.R_TRIG_R5):
                                self.assertIs(trig.Q, False)
                                self.assertIs(trig._CLK_prev, False)

    def test_cleanup_next_scan_requires_a_fresh_full_debounce(self):
        """整理后的最大档持续偏差也必须重新经过完整 2*TL 防抖。"""

        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.RSFEN = True
        p.TL = 1.0
        p.E1, p.E2, p.E3, p.E4 = 0.5, 1.0, 2.0, 4.0
        p.AO4 = 12.0
        p.AV = -100.0
        p.AV_P_TEMP = -90.0
        p.AV_R = -10.0
        p.CT_4 = 1.0
        p.EK_R_1 = -5.0
        ref = RealRef(0.0)

        step_apcm(p, ref=ref, SP=50.0, PV=45.0, TP=99.0)
        self.assertEqual((p.CT_4, p.AV_R, p.FLAG), (0.0, 0.0, 0.0))

        # 第一个后续拍仅建立 EK_R_1，不累计；第二拍累计 1；第三拍累计 2 后才触发。
        p.AV = -100.0
        step_apcm(p, ref=ref, SP=50.0, PV=45.0, TP=99.0)
        self.assertEqual((p.CT_4, p.AV_R, p.FLAG), (0.0, 0.0, 0.0))
        p.AV = -100.0
        step_apcm(p, ref=ref, SP=50.0, PV=45.0, TP=99.0)
        self.assertEqual((p.CT_4, p.AV_R, p.FLAG), (1.0, 0.0, 0.0))
        p.AV = -100.0
        step_apcm(p, ref=ref, SP=50.0, PV=45.0, TP=99.0)
        self.assertEqual(p.CT_4, 2.0)
        self.assertEqual(p.FLAG, 4.0)
        self.assertAlmostEqual(p.AV_R, 12.0)

    def test_cleanup_rebase_subtracts_oc_once_and_next_auto_scan_is_bumpless(self):
        """现场 OC 前馈不得在整理基准中丢失或于下一自动拍重复叠加。"""

        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.RSFEN = True
        p.OutM = 0
        p.AV = 100.0
        p.AV_P_TEMP = 90.0
        p.AV_R = 10.0
        ref = RealRef(0.0)

        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=20.0, TP=-75.0)

        self.assertEqual(p.ZL_PID_BASE, 100.0)
        self.assertEqual(p.AV_P_TEMP, 100.0)
        self.assertEqual(p.AV_R, 0.0)
        self.assertEqual(p.UK_1, 80.0)  # 组合基准100 - OC20
        self.assertEqual(p.AV, 100.0)

        # 下一拍退出限位条件，PID 自动分支 DU=0：UK80 + OC20 仍为100。
        p.AV = 99.0
        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=20.0, TP=-75.0)
        self.assertIs(p.ZL_EVENT, False)
        self.assertEqual(p.UK_1, 80.0)
        self.assertEqual(p.AV_P_TEMP, 100.0)
        self.assertEqual(p.AV, 100.0)

    def test_cleanup_event_freezes_cd_exit_to_preserve_total_output(self):
        """整理与 CD 退出同拍碰撞时，CD 必须冻结而不是丢失回补。"""

        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.OutM = 0
        p.AV = 100.0
        p.AV_P_TEMP = 80.0
        p.AV_R = 10.0
        p.AV_C_TEMP = 10.0
        p.AV_C = 10.0
        p.CDEN = False
        p.F_TRIG2._CLK_prev = True  # 若本拍运行 CD，本可形成切除下降沿
        ref = RealRef(0.0)

        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-75.0)

        self.assertIs(p.ZL_EVENT, True)
        self.assertEqual(p.ZL_PID_BASE, 90.0)
        self.assertEqual(p.AV_P_TEMP, 90.0)
        self.assertEqual(p.AV_R, 0.0)
        self.assertEqual(p.AV_C, 10.0)
        self.assertEqual(p.AV_C_TEMP, 10.0)
        self.assertIs(p.F_TRIG2._CLK_prev, True)  # 事件拍没有推进 CD 状态
        self.assertEqual(p.AV, 100.0)

    def test_cleanup_position_rebase_does_not_clamp_pid_before_cd_combination(self):
        """反向 CD 抵消 PID+RSF 时，整理基准不得先被 PID 限幅破坏总量。"""

        for limit, pid_rsf, cd in ((100.0, 110.0, -10.0), (-100.0, -110.0, 10.0)):
            with self.subTest(limit=limit, pid_rsf=pid_rsf, cd=cd):
                p = make_block()
                p.MD = -100.0
                p.MU = 100.0
                p.OUTB = -100.0
                p.OUTT = 100.0
                p.ZLEN = True
                p.OutM = 0
                p.AV = limit
                p.AV_P_TEMP = pid_rsf
                p.AV_R = 0.0
                p.AV_C_TEMP = cd
                p.AV_C = cd
                ref = RealRef(0.0)

                step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-limit)

                self.assertIs(p.ZL_EVENT, True)
                self.assertEqual(p.ZL_PID_BASE, pid_rsf)
                self.assertEqual(p.AV_P_TEMP, pid_rsf)
                self.assertEqual(p.AV_C, cd)
                self.assertEqual(p.AV_TEMP, limit)
                self.assertEqual(p.AV, limit)
                self.assertEqual(ref.value, pid_rsf)

                # 限位条件持续时不会重复整理。下一拍若 CD 已关闭，可以按原逻辑
                # 将 AV_C 回补进 PID，但组合总量和物理输出必须保持，ZLOUT 不重加。
                step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-limit)
                self.assertIs(p.ZL_EVENT, False)
                self.assertEqual(p.AV_P_TEMP + p.AV_R + p.AV_C, limit)
                self.assertEqual(p.AV_TEMP, limit)
                self.assertEqual(p.AV, limit)
                self.assertEqual(ref.value, pid_rsf)

    def test_cleanup_observer_modes_preserve_gc_offset_contract(self):
        """PCMMS=1/2 且 AV_GC 非零时，整理基准及内部 PID 状态各只扣一次补偿。"""

        for pcmms, expected_state in ((1, 80.0), (2, 90.0)):
            for out_m in (0, 1):
                with self.subTest(PCMMS=pcmms, OutM=out_m):
                    p = make_block()
                    p.AD = False
                    p.MD = -100.0
                    p.MU = 100.0
                    p.OUTB = -100.0
                    p.OUTT = 100.0
                    p.ZLEN = True
                    p.RSFEN = True
                    p.PCMMS = pcmms
                    p.GCEN = True
                    p.GC1 = 1.0
                    p.GC2 = 0.0
                    p.OutM = out_m
                    p.AV = 100.0
                    p.AV_1 = 100.0
                    p.AV_P_TEMP = 90.0
                    p.AV_R = 10.0
                    p.CT_4 = 1.0
                    p.EK_R_1 = -5.0
                    p.F_TRIG2.step(False)  # 排除冷启动 CD 下降沿干扰
                    ref = RealRef(0.0)

                    step_apcm(p, ref=ref, SP=50.0, PV=40.0, OC=20.0, TP=-77.0)

                    self.assertIs(p.ZL_EVENT, True)
                    self.assertEqual(p.AV_GC, -10.0)
                    self.assertEqual(p.ZL_PID_BASE, 100.0)
                    self.assertEqual(p.AV_P_TEMP, 100.0)
                    self.assertEqual(p.AV_R, 0.0)
                    self.assertEqual(p.AV, 100.0)
                    self.assertEqual(p.CT_4, 0.0)
                    self.assertEqual(ref.value, 100.0)
                    if out_m == 0:
                        self.assertEqual(p.UK_1, expected_state)
                    else:
                        self.assertEqual(p.DU_1, expected_state)

                    # 整理前 PID=90、整理后基准=100，因此次拍出现一次 R_TRIG03
                    # 上升沿；验证 AV_GC 非零时的位置保持/零增量路径仍不重复补偿。
                    step_apcm(p, ref=ref, SP=50.0, PV=40.0, OC=20.0, TP=-77.0)
                    self.assertIs(p.ZL_EVENT, False)
                    self.assertIs(p.R_TRIG03.Q, True)
                    self.assertEqual(ref.value, 100.0)
                    if out_m == 0:
                        self.assertEqual(p.AV_P_TEMP, 100.0)
                        self.assertEqual(p.UK_1, expected_state)
                    else:
                        self.assertEqual(p.AV_P_TEMP, 0.0)

    def test_cleanup_followup_rtrig03_contract_depends_on_output_mode(self):
        """整理后 R_TRIG03 次拍沿：位置式保持，增量式输出零增量命令。"""

        for out_m in (0, 1):
            with self.subTest(OutM=out_m):
                p = make_block()
                p.MD = -100.0
                p.MU = 100.0
                p.OUTB = -100.0
                p.OUTT = 100.0
                p.ZLEN = True
                p.RSFEN = True
                p.OutM = out_m
                p.AV = 100.0
                p.AV_1 = 100.0
                p.AV_P_TEMP = 90.0
                p.AV_R = 10.0
                p.F_TRIG2.step(False)  # 仅观察 R_TRIG03，不混入 CD 冷启动沿
                ref = RealRef(0.0)

                step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
                self.assertIs(p.ZL_EVENT, True)
                self.assertIs(p.R_TRIG03.Q, False)  # 本拍看到的是整理前 PID=90
                self.assertEqual(p.AV_P_TEMP, 100.0)
                self.assertEqual(ref.value, 100.0)

                step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
                self.assertIs(p.ZL_EVENT, False)
                self.assertIs(p.R_TRIG03.Q, True)
                self.assertEqual(p.AV_P_TEMP, 100.0 if out_m == 0 else 0.0)
                self.assertEqual(ref.value, 100.0)

                step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
                self.assertIs(p.R_TRIG03.Q, False)
                self.assertEqual(ref.value, 100.0)

    def test_cleanup_same_scan_rsf_disable_and_tracking_transitions(self):
        """RSFEN/TS/ATE/RM 与整理同拍时，事件优先级和旧模式收尾语义保持。"""

        # RSFEN 下降沿可以同拍出现，但事件重建优先，MM 不被手动分支消费。
        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.RSFEN = False
        p.F_TRIG1.step(True)
        p.AV = 100.0
        p.AV_P_TEMP = 90.0
        p.AV_R = 10.0
        p.MM = 3
        ref = RealRef(0.0)
        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
        self.assertIs(p.ZL_EVENT, True)
        self.assertIs(p.F_TRIG1.Q, True)
        self.assertEqual(p.AV_P_TEMP, 100.0)
        self.assertEqual(p.AV_R, 0.0)
        self.assertEqual(p.MM, 3)

        # TS+ATE 会先把 RM 切到 4，但整理分支当拍仍优先于普通 TP 跟踪。
        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.ATE = True
        p.TS = True
        p.AV = 100.0
        p.AV_P_TEMP = 90.0
        p.AV_R = 10.0
        ref = RealRef(0.0)
        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
        self.assertEqual(p.RM, 4)
        self.assertIs(p.ZL_EVENT, True)
        self.assertEqual(p.AV_P_TEMP, 100.0)
        self.assertEqual(ref.value, 100.0)

        # RM=3 的事件拍仍用冻结基准；下一普通跟踪拍才恢复使用 TP。
        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.RM = 3
        p.AV = 100.0
        p.AV_P_TEMP = 90.0
        p.AV_R = 10.0
        ref = RealRef(0.0)
        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
        self.assertEqual(p.AV_P_TEMP, 100.0)
        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
        self.assertEqual(p.AV_P_TEMP, -77.0)
        self.assertEqual(ref.value, 100.0)

    def test_cleanup_manual_and_output_inhibit_semantics_are_explicit(self):
        """RM=0 与 SADD/SSUB 的既有收尾不能被原子整理修复静默改变。"""

        p = make_block()
        p.MD = -100.0
        p.MU = 100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.ZLEN = True
        p.RM = 0
        p.AV = 100.0
        p.AV_P = 33.0
        p.AV_P_TEMP = 90.0
        p.AV_R = 10.0
        ref = RealRef(0.0)
        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-77.0)
        self.assertIs(p.ZL_EVENT, True)
        self.assertEqual(ref.value, 100.0)  # ZLOUT 仍按整理前 PID+RSF 累加
        self.assertEqual(p.AV_P_TEMP, 33.0)  # RM=0 的既有 AV_P 收尾仍最终生效

        for limit, inhibit_name in ((100.0, "SADD"), (-100.0, "SSUB")):
            with self.subTest(limit=limit, inhibit=inhibit_name):
                p = make_block()
                p.MD = -100.0
                p.MU = 100.0
                p.OUTB = -100.0
                p.OUTT = 100.0
                p.ZLEN = True
                setattr(p, inhibit_name, True)
                p.AV = limit
                p.AV_1 = limit
                p.AV_P_TEMP = limit * 0.9
                p.AV_R = limit * 0.1
                ref = RealRef(0.0)
                step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-limit)
                self.assertIs(p.ZL_EVENT, True)
                self.assertEqual(p.AV_P_TEMP, limit)
                self.assertEqual(p.AV_R, 0.0)
                self.assertEqual(p.AV, limit)
                self.assertEqual(ref.value, limit)

    def test_cleanup_rate_and_deadband_boundaries_keep_event_scan_continuous(self):
        """OutRL/RTH 工程边界不得在整理事件拍制造二次跳变。"""

        for limit in (-100.0, 100.0):
            for out_rl in (0.0, 0.5):
                for rth in (0.5, 100.0):
                    with self.subTest(limit=limit, OutRL=out_rl, RTH=rth):
                        p = make_block()
                        p.MD = -100.0
                        p.MU = 100.0
                        p.OUTB = -100.0
                        p.OUTT = 100.0
                        p.ZLEN = True
                        p.OutRL = out_rl
                        p.RTH = rth
                        p.AV = limit
                        p.AV_1 = limit
                        p.AV_P_TEMP = limit * 0.9
                        p.AV_R = limit * 0.1
                        ref = RealRef(0.0)

                        step_apcm(p, ref=ref, SP=50.0, PV=50.0, OC=0.0, TP=-limit)

                        self.assertIs(p.ZL_EVENT, True)
                        self.assertEqual(p.AV_TEMP, limit)
                        self.assertEqual(p.AV, limit)
                        self.assertEqual(ref.value, limit)

class TestMMOneShot(unittest.TestCase):
    def test_rm0_manual_command_is_consumed_once(self):
        p = make_block()
        p.RM = 0
        p.OutM = 1
        p.MM = 1
        p.MI = 5.0

        step_apcm(p)
        self.assertEqual(p.MM, 0)
        self.assertEqual(p.DUOUT, 5.0)

        step_apcm(p)
        self.assertEqual(p.MM, 0)
        self.assertEqual(p.DUOUT, 0.0)

    def test_forced_manual_path_from_rtrig03_also_clears_mm(self):
        p = make_block()
        p.RM = 1
        p.OutM = 1
        p.AV_P_TEMP = 100.0
        p.MM = 3
        p.MS = 6.0

        step_apcm(p)

        self.assertEqual(p.MM, 0)
        self.assertEqual(p.DUOUT, 6.0)

    def test_tracking_branch_does_not_clear_mm(self):
        p = make_block()
        p.RM = 3
        p.MM = 1

        step_apcm(p)

        self.assertEqual(p.MM, 1)


class TestObserverRSFCDPID(unittest.TestCase):
    def test_blink1_uses_500ms_high_and_unquantized_timelow(self):
        p = make_block()
        calls: list[dict] = []
        orig = p.BLINK1.step

        def blink_rec(dt_ms: int, ENABLE: bool, TIMELOW_ms: int, TIMEHIGH_ms: int):
            calls.append(
                {
                    "dt_ms": dt_ms,
                    "ENABLE": ENABLE,
                    "TIMELOW_ms": TIMELOW_ms,
                    "TIMEHIGH_ms": TIMEHIGH_ms,
                }
            )
            return orig(dt_ms, ENABLE, TIMELOW_ms, TIMEHIGH_ms)

        p.TC = 1.25
        p.BLINK1.step = blink_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual(calls[-1]["TIMELOW_ms"], 1250)
        self.assertEqual(calls[-1]["TIMEHIGH_ms"], 500)

    def test_blink2_uses_500ms_high_and_unquantized_timelow(self):
        p = make_block()
        calls: list[dict] = []
        orig = p.BLINK2.step

        def blink_rec(dt_ms: int, ENABLE: bool, TIMELOW_ms: int, TIMEHIGH_ms: int):
            calls.append(
                {
                    "dt_ms": dt_ms,
                    "ENABLE": ENABLE,
                    "TIMELOW_ms": TIMELOW_ms,
                    "TIMEHIGH_ms": TIMEHIGH_ms,
                }
            )
            return orig(dt_ms, ENABLE, TIMELOW_ms, TIMEHIGH_ms)

        p.TC_CD = 1.75
        p.BLINK2.step = blink_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual(calls[-1]["TIMELOW_ms"], 1750)
        self.assertEqual(calls[-1]["TIMEHIGH_ms"], 500)

    def test_av_gc_matches_source_pcmms_gate(self):
        cases = (
            (0, True, -10.0),
            (0, False, 0.0),
            (1, False, -10.0),
            (2, True, -10.0),
            (2, False, 0.0),
        )
        for pcmms, gcen, expected in cases:
            with self.subTest(PCMMS=pcmms, GCEN=gcen):
                p = make_block()
                p.PCMMS = pcmms
                p.GCEN = gcen
                p.AD = True
                p.GC1 = 1.0
                p.GC2 = 0.0
                p.OUTH = 100.0
                p.OUTL = -100.0
                p.AV = 50.0

                step_apcm(p, SP=50.0, PV=60.0)

                self.assertAlmostEqual(p.AV_J_GC, -10.0)
                self.assertAlmostEqual(p.AV_GC, expected)

    def test_ts_resets_rsf_state(self):
        p = make_block()
        p.AV_R = 1.0
        p.AV_R_TEMP = 2.0
        p.AV_R_TEMP1 = 3.0
        p.AV_R_TEMP2 = 4.0
        p.CT_TL = 5
        p.FLAG = 2.0
        p.CT_1 = 6.0
        p.CT_2 = 7.0
        p.CT_3 = 8.0
        p.CT_4 = 9.0
        p.CT_1_1 = 10.0
        p.CT_2_1 = 11.0
        p.CT_3_1 = 12.0
        p.CT_4_1 = 13.0
        p.CT_RSF_OUT = 14.0
        p.RSF_LOCK_LEVEL = 3.0
        p.RSF_LOCK_SIG = -1.0
        p.CT_RSF_LOCK = 15.0

        step_apcm(p, TS=True)

        for name in (
            "AV_R", "AV_R_TEMP", "AV_R_TEMP1", "AV_R_TEMP2", "FLAG",
            "CT_1", "CT_2", "CT_3", "CT_4", "CT_1_1", "CT_2_1",
            "CT_3_1", "CT_4_1", "CT_RSF_OUT", "RSF_LOCK_LEVEL",
            "RSF_LOCK_SIG", "CT_RSF_LOCK",
        ):
            self.assertEqual(getattr(p, name), 0, name)
        self.assertEqual(p.CT_TL, 0)

    def test_rsf_first_band_trigger_uses_fixed_count_and_output(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 1.0
        p.TL1 = 10.0
        p.E1 = 0.5
        p.E2 = 2.0
        p.E3 = 3.0
        p.E4 = 4.0
        p.AO1 = 3.0
        p.CT_1 = 1.0
        p.EK_R_1 = 1.0

        step_apcm(p, SP=50.0, PV=51.0)

        self.assertEqual(p.FLAG, 1.0)
        self.assertEqual(p.CT_1, 2.0)
        self.assertEqual(p.CT_TL, 1)
        self.assertAlmostEqual(p.AV_R_TEMP1, -3.0)
        self.assertAlmostEqual(p.AV_R, -3.0)

    def test_rsf_consolidation_clears_av_r_temp_before_readding(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.OUTT = 100.0
        p.RSFEN = False
        p.F_TRIG1._CLK_prev = False
        p.AV_R = 5.0
        p.AV_R_TEMP = 2.0
        p.AV_P_TEMP = 100.0

        step_apcm(p)

        self.assertIs(p.R_TRIG03.Q, True)
        self.assertEqual(p.AV_R_TEMP, 0.0)
        self.assertEqual(p.AV_R, 0.0)

    def test_rsf_reverse_exit_sets_same_direction_lock(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 10.0
        p.E1 = 1.0
        p.E2 = 5.0
        p.E3 = 10.0
        p.E4 = 20.0
        p.FLAG = 1.0
        p.AV_R_TEMP1 = 3.0
        p.CT_TL = 4
        p.CT_1_1 = 7.0
        p.CT_RSF_OUT = 9.0
        p.EK_R_1 = 2.0

        step_apcm(p, SP=50.0, PV=49.0)

        self.assertIs(p.RSF_EXIT, True)
        self.assertEqual(p.FLAG, 0.0)
        self.assertEqual(p.CT_TL, 0)
        self.assertEqual(p.CT_1_1, 0.0)
        self.assertEqual(p.CT_RSF_OUT, 0.0)
        self.assertEqual(p.RSF_LOCK_LEVEL, 1.0)
        self.assertEqual(p.RSF_LOCK_SIG, 1.0)
        self.assertEqual(p.CT_RSF_LOCK, 1.0)

    def test_rsf_lock_clears_when_error_direction_changes(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 100.0
        p.E1 = 1.0
        p.E2 = 5.0
        p.E3 = 10.0
        p.E4 = 20.0
        p.RSF_LOCK_LEVEL = 1.0
        p.RSF_LOCK_SIG = 1.0
        p.CT_RSF_LOCK = 3.0
        p.EK_R_1 = 2.0

        step_apcm(p, SP=50.0, PV=52.0)

        self.assertEqual(p.SIG, -1.0)
        self.assertEqual(p.RSF_LOCK_LEVEL, 0.0)
        self.assertEqual(p.RSF_LOCK_SIG, 0.0)
        self.assertEqual(p.CT_RSF_LOCK, 0.0)

    def test_rsf_lock_clears_after_timeout_count_reaches_twice_lock_t(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 100.0
        p.E1 = 1.0
        p.E2 = 5.0
        p.E3 = 10.0
        p.E4 = 20.0
        p.RSF_LOCK_T = 1.0
        p.RSF_LOCK_LEVEL = 1.0
        p.RSF_LOCK_SIG = 1.0
        p.CT_RSF_LOCK = 1.0
        p.EK_R_1 = -2.0

        step_apcm(p, SP=50.0, PV=49.0)

        self.assertEqual(p.SIG, 1.0)
        self.assertEqual(p.RSF_LOCK_LEVEL, 0.0)
        self.assertEqual(p.RSF_LOCK_SIG, 0.0)
        self.assertEqual(p.CT_RSF_LOCK, 0.0)

    def test_cd_ton1_does_not_enter_when_abs_cd_bh_strictly_below_gd(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.CDEN = True
        p.CD_GD = 1.0
        p.CD_K_FD = 2.0
        p.CD_K_J = 1.0
        p.TL = 0.0
        p.DI = 100.0

        step_apcm(p, SP=50.0, PV=50.0)

        self.assertLess(abs(p.CD_BH), p.CD_GD)
        self.assertIs(p.CD_TON1.Q, False)
        self.assertEqual(p.AV_C, 0.0)

    def test_cd_enter_boundary_and_recovery_consolidates_to_av_p_temp(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.CDEN = True
        p.CD_GD = 1.0
        p.CD_K = 0.5
        p.CD_K_FD = 2.0
        p.CD_K_J = 1.0
        p.CD_K_D = 0.0
        p.CDH = 10.0
        p.CDL = -10.0
        p.TL = 0.0
        p.DI = 100.0

        step_apcm(p, SP=50.0, PV=49.0)
        self.assertIs(p.CD_TON1.Q, True)
        self.assertEqual(abs(p.CD_BH), p.CD_GD)
        self.assertAlmostEqual(p.AV_C, 2.0)
        before_recovery_pid = p.AV_P_TEMP

        step_apcm(p, SP=50.0, PV=50.0)
        self.assertIs(p.CD_TON2.Q, True)
        self.assertIs(p.R_TRIG9.Q, True)
        self.assertEqual(p.AV_C_TEMP, 0.0)
        self.assertEqual(p.AV_C, 0.0)
        self.assertAlmostEqual(p.AV_P_TEMP, before_recovery_pid + 1.0)

    def test_cd_positive_tl_delays_enter_and_recovery_edges(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.CDEN = True
        p.CD_GD = 1.0
        p.CD_K = 0.5
        p.CD_K_FD = 2.0
        p.CD_K_J = 1.0
        p.CD_K_D = 0.0
        p.CDH = 10.0
        p.CDL = -10.0
        p.TL = 1.0
        p.DI = 100.0

        step_apcm(p, SP=50.0, PV=49.0)
        self.assertIs(p.CD_TON1.Q, False)
        self.assertEqual(p.CD_TON1.ET_ms, 500)
        self.assertEqual(p.AV_C, 0.0)

        step_apcm(p, SP=50.0, PV=49.0)
        self.assertIs(p.CD_TON1.Q, True)
        self.assertEqual(p.CD_TON1.ET_ms, 1000)
        self.assertAlmostEqual(p.AV_C, 2.0)
        before_recovery_pid = p.AV_P_TEMP

        step_apcm(p, SP=50.0, PV=50.0)
        self.assertIs(p.CD_TON2.Q, False)
        self.assertEqual(p.CD_TON2.ET_ms, 500)
        self.assertAlmostEqual(p.AV_C, 2.0)
        self.assertAlmostEqual(p.AV_P_TEMP, before_recovery_pid)

        step_apcm(p, SP=50.0, PV=50.0)
        self.assertIs(p.CD_TON2.Q, True)
        self.assertIs(p.R_TRIG9.Q, True)
        self.assertEqual(p.AV_C, 0.0)
        self.assertAlmostEqual(p.AV_P_TEMP, before_recovery_pid + 1.0)

    def test_tii_uses_old_ek_before_current_error_recalculation(self):
        p = make_block()
        p.TI = 100.0
        p.TI1 = 3.0
        p.KI = 2.0
        p.SVH = 10.0
        p.EK = 100.0
        p.EK_LAST = 0.0

        step_apcm(p, SP=50.0, PV=40.0)

        self.assertAlmostEqual(p.TIi, 123.0)
        self.assertAlmostEqual(p.EK, 1.0)

    def test_rsf_higher_bands_trigger_with_fixed_count_and_expected_output(self):
        cases = (
            (2, 1.0, 52.0, 0.4, 2.0, -0.4),
            (3, 1.0, 53.0, 0.5, 3.0, -0.5),
            (4, 1.0, 55.0, 0.6, 5.0, -0.6),
        )
        for band, ct_value, pv, ao, ek_r_1, expected_av_r in cases:
            with self.subTest(band=band):
                p = make_block()
                p.MD = -100.0
                p.OUTB = -100.0
                p.RSFEN = True
                p.TL = 1.0
                p.TL1 = 10.0
                p.TL2 = 10.0
                p.TL3 = 10.0
                p.TL4 = 10.0
                p.E1 = 0.5
                p.E2 = 2.0
                p.E3 = 3.0
                p.E4 = 4.0
                p.AO1 = 3.0
                p.AO2 = 0.4
                p.AO3 = 0.5
                p.AO4 = 0.6
                p.EK_R_1 = ek_r_1
                p.CT_1 = 0.0
                p.CT_2 = 0.0
                p.CT_3 = 0.0
                p.CT_4 = 0.0
                setattr(p, f"CT_{band}", ct_value)

                step_apcm(p, SP=50.0, PV=pv)

                self.assertEqual(p.FLAG, float(band))
                self.assertEqual(getattr(p, f"CT_{band}"), ct_value + 1.0)
                self.assertAlmostEqual(p.AV_R_TEMP1, expected_av_r)
                self.assertAlmostEqual(p.AV_R, expected_av_r)

    def test_rsf_fast_exit_when_error_below_fast_hys(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 10.0
        p.E1 = 1.0
        p.E2 = 5.0
        p.E3 = 10.0
        p.E4 = 20.0
        p.RSF_HYS = 0.8
        p.RSF_FAST_HYS = 0.5
        p.FLAG = 1.0
        p.AV_R_TEMP1 = 3.0
        p.EK_R_1 = -1.0

        step_apcm(p, SP=50.0, PV=49.6)

        self.assertIs(p.RSF_EXIT, True)
        self.assertEqual(p.FLAG, 0.0)
        self.assertEqual(p.RSF_LOCK_LEVEL, 1.0)
        self.assertEqual(p.RSF_LOCK_SIG, 1.0)

    def test_rsf_slow_exit_requires_ct_rsf_out_before_clearing_flag(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 1.0
        p.RSF_TLOUT_K = 1.0
        p.E1 = 1.0
        p.E2 = 5.0
        p.E3 = 10.0
        p.E4 = 20.0
        p.RSF_HYS = 0.8
        p.RSF_FAST_HYS = 0.5
        p.FLAG = 1.0
        p.AV_R_TEMP1 = 3.0
        p.CT_RSF_OUT = 0.0
        p.EK_R_1 = -1.0

        step_apcm(p, SP=50.0, PV=49.4)

        self.assertIs(p.RSF_EXIT, False)
        self.assertEqual(p.FLAG, 1.0)
        self.assertEqual(p.CT_RSF_OUT, 1.0)

        step_apcm(p, SP=50.0, PV=49.4)

        self.assertIs(p.RSF_EXIT, True)
        self.assertEqual(p.FLAG, 0.0)
        self.assertEqual(p.CT_RSF_OUT, 0.0)
        self.assertEqual(p.RSF_LOCK_LEVEL, 1.0)

    def test_rsf_lock_clears_when_error_reaches_next_band(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RSFEN = True
        p.TL = 100.0
        p.E1 = 1.0
        p.E2 = 2.0
        p.E3 = 10.0
        p.E4 = 20.0
        p.RSF_LOCK_LEVEL = 1.0
        p.RSF_LOCK_SIG = 1.0
        p.CT_RSF_LOCK = 5.0
        p.EK_R_1 = -2.0

        step_apcm(p, SP=50.0, PV=48.0)

        self.assertEqual(p.SIG, 1.0)
        self.assertEqual(p.RSF_LOCK_LEVEL, 0.0)
        self.assertEqual(p.RSF_LOCK_SIG, 0.0)
        self.assertEqual(p.CT_RSF_LOCK, 0.0)


class TestEmbeddedPIDBranches(unittest.TestCase):
    def test_auto_pid_branch_computes_nonzero_du_outside_deadband(self):
        p = make_block()
        p.MD = -100.0
        p.OUTB = -100.0
        p.RM = 1
        p.OutM = 1
        p.DI = 0.0
        p.PT = 100.0
        p.TI = 150.0
        p.TD = 0.0
        p.KD = 1.0
        p.OC = 0.0
        p.PV_LAST = 80.0
        p.F_TRIG1._CLK_prev = False
        p.F_TRIG2._CLK_prev = False

        step_apcm(p, SP=50.0, PV=80.0)

        self.assertNotEqual(p.DU, 0.0)
        self.assertNotEqual(p.AV_P_TEMP, 0.0)

    def test_tracking_incremental_branch_uses_tp_for_av_p_temp(self):
        p = make_block()
        p.RM = 3
        p.OutM = 1
        p.OC = 0.0

        step_apcm(p, SP=50.0, PV=50.0, TP=15.0)

        self.assertEqual(p.AV_P_TEMP, 15.0)
        self.assertEqual(p.DUOUT, 15.0)

    def test_tracking_position_branch_clamps_tp_to_output_limits(self):
        p = make_block()
        p.RM = 3
        p.OutM = 0
        p.OC = 0.0

        step_apcm(p, SP=50.0, PV=50.0, TP=150.0)

        self.assertEqual(p.AV_P_TEMP, 100.0)
        self.assertEqual(p.UKOUT, 100.0)


class TestPIDZZDAndAPARA(unittest.TestCase):
    def test_pidzzd_receives_sp_not_sp_v_after_total_output(self):
        p = make_block()
        calls: list[dict] = []
        orig = p.PIDZZD1.step
        p.RM = 3
        p.TM = True
        p.SP = 10.0
        p.PV = 42.0
        p.TP = 5.0

        def rec(dt_ms: int, **kw):
            calls.append(kw)
            return orig(dt_ms, **kw)

        p.PIDZZD1.step = rec  # type: ignore[assignment]
        step_apcm(p)

        self.assertEqual(calls[-1]["SP"], 10.0)
        self.assertEqual(p.SP_V, 42.0)

    def test_apara_apply_pid_writes_self_params_and_clears_button_through_step(self):
        p = make_block()
        p.APARA_APPLY_PID = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.PID_OK = True
            p.APARA1.PT_REC = 11.0
            p.APARA1.TI_REC = 12.0
            p.APARA1.TD_REC = 13.0
            p.APARA1.DI_REC = 14.0
            p.APARA1.SVH_REC = 15.0
            p.APARA1.SVL_REC = 16.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual((p.PT, p.TI, p.TD, p.DI, p.SVH, p.SVL), (11.0, 12.0, 13.0, 14.0, 15.0, 16.0))
        self.assertIs(p.APARA_APPLY_PID, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 1)
        self.assertIs(p.APARA_LAST_APPLY_OK, True)
        self.assertEqual(p.APARA_LAST_APPLY_REASON, 1)
        self.assertIs(p.APARA_FINAL_STRONG, True)
        self.assertIs(p.APARA_PID_OK, True)
        self.assertEqual(p.APARA_PT_REC, 11.0)

    def test_apara_final_weak_allows_gc_apply_without_group_ok_through_step(self):
        p = make_block()
        p.APARA_APPLY_GC = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_WEAK = True
            p.APARA1.GC_OK = False
            p.APARA1.TC_REC = 21.0
            p.APARA1.TZ_REC = 22.0
            p.APARA1.GC1_REC = 23.0
            p.APARA1.GC2_REC = 24.0
            p.APARA1.OUTH_REC = 25.0
            p.APARA1.OUTL_REC = -26.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual((p.TC, p.TZ, p.GC1, p.GC2, p.OUTH, p.OUTL), (21.0, 22.0, 23.0, 24.0, 25.0, -26.0))
        self.assertIs(p.APARA_APPLY_GC, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 3)
        self.assertIs(p.APARA_LAST_APPLY_OK, True)
        self.assertIs(p.APARA_FINAL_WEAK, True)
        self.assertIs(p.APARA_GC_OK, False)

    def test_apara_no_recommendation_does_not_write_cd_through_step(self):
        p = make_block()
        p.CD_GD = 1.0
        p.APARA_APPLY_CD = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = False
            p.APARA1.FINAL_WEAK = False
            p.APARA1.CD_OK = True
            p.APARA1.CD_GD_REC = 99.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual(p.CD_GD, 1.0)
        self.assertIs(p.APARA_APPLY_CD, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 4)
        self.assertIs(p.APARA_LAST_APPLY_OK, False)
        self.assertEqual(p.APARA_LAST_APPLY_REASON, 2)

    def test_apara_runs_after_pidzzd_and_pid_apply_affects_next_scan(self):
        p = make_block()
        p.PT = 10.0
        p.TI = 20.0
        p.TD = 1.0
        p.DI = 2.0
        p.SVH = 60.0
        p.SVL = 5.0
        p.APARA_APPLY_PID = True
        order: list[str] = []
        pidzzd_calls: list[dict] = []
        apara_calls: list[dict] = []
        orig_pidzzd = p.PIDZZD1.step

        def pidzzd_rec(dt_ms: int, **kw):
            order.append("pidzzd")
            pidzzd_calls.append(kw)
            return orig_pidzzd(dt_ms, **kw)

        def apara_rec(dt_ms: int, **kw):
            order.append("apara")
            apara_calls.append(kw)
            p.APARA1.FINAL_STRONG = True
            p.APARA1.FINAL_WEAK = False
            p.APARA1.PID_OK = True
            p.APARA1.PT_REC = 101.0
            p.APARA1.TI_REC = 102.0
            p.APARA1.TD_REC = 103.0
            p.APARA1.DI_REC = 104.0
            p.APARA1.SVH_REC = 105.0
            p.APARA1.SVL_REC = 106.0

        p.PIDZZD1.step = pidzzd_rec  # type: ignore[assignment]
        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p, SP=50.0, PV=50.0, RM=3, TP=12.0)

        self.assertEqual(order, ["pidzzd", "apara"])
        self.assertEqual(pidzzd_calls[-1]["PT"], 10.0)
        self.assertEqual(apara_calls[-1]["PT_IN"], 10.0)
        self.assertEqual((p.PT, p.TI, p.TD, p.DI, p.SVH, p.SVL), (101.0, 102.0, 103.0, 104.0, 105.0, 106.0))
        self.assertEqual(p.PT_1, 10.0)
        self.assertEqual(p.AV, 12.0)
        self.assertIs(p.APARA_APPLY_PID, False)

        order.clear()
        step_apcm(p, SP=50.0, PV=50.0)

        self.assertEqual(order, ["pidzzd", "apara"])
        self.assertEqual(p.PT_1, 101.0)

    def test_apara_reset_and_calc_are_rising_edge_pulses_through_step(self):
        p = make_block()
        p.APARA_RESET = True
        p.APARA_CALC_NOW = True
        calls: list[dict] = []

        def apara_rec(dt_ms: int, **kw):
            calls.append(kw)

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertIs(calls[-1]["RESET"], True)
        self.assertIs(calls[-1]["CALC_NOW"], True)
        self.assertIs(p.APARA_RESET, False)
        self.assertIs(p.APARA_CALC_NOW, False)

        step_apcm(p)

        self.assertIs(calls[-1]["RESET"], False)
        self.assertIs(calls[-1]["CALC_NOW"], False)

    def test_apara_apply_rsf_through_step_writes_group_and_clears_command(self):
        p = make_block()
        p.APARA_APPLY_RSF = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.FINAL_WEAK = False
            p.APARA1.RSF_OK = True
            p.APARA1.TL_REC = 31.0
            p.APARA1.TL1_REC = 32.0
            p.APARA1.TL2_REC = 33.0
            p.APARA1.TL3_REC = 34.0
            p.APARA1.TL4_REC = 35.0
            p.APARA1.E1_REC = 36.0
            p.APARA1.E2_REC = 37.0
            p.APARA1.E3_REC = 38.0
            p.APARA1.E4_REC = 39.0
            p.APARA1.AO1_REC = 40.0
            p.APARA1.AO2_REC = 41.0
            p.APARA1.AO3_REC = 42.0
            p.APARA1.AO4_REC = 43.0
            p.APARA1.RSF_LOCK_T_REC = 44.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual((p.TL, p.TL1, p.TL2, p.TL3, p.TL4), (31.0, 32.0, 33.0, 34.0, 35.0))
        self.assertEqual((p.E1, p.E2, p.E3, p.E4), (36.0, 37.0, 38.0, 39.0))
        self.assertEqual((p.AO1, p.AO2, p.AO3, p.AO4, p.RSF_LOCK_T), (40.0, 41.0, 42.0, 43.0, 44.0))
        self.assertIs(p.APARA_APPLY_RSF, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 2)
        self.assertIs(p.APARA_LAST_APPLY_OK, True)

    def test_apara_apply_cd_rejects_strong_result_without_group_ok_through_step(self):
        p = make_block()
        p.CD_GD = 1.0
        p.APARA_APPLY_CD = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.FINAL_WEAK = False
            p.APARA1.CD_OK = False
            p.APARA1.CD_GD_REC = 99.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual(p.CD_GD, 1.0)
        self.assertIs(p.APARA_APPLY_CD, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 4)
        self.assertIs(p.APARA_LAST_APPLY_OK, False)
        self.assertEqual(p.APARA_LAST_APPLY_REASON, 3)

    def test_apara_apply_gc_requires_new_rising_edge_to_repeat(self):
        p = make_block()
        p.APARA_APPLY_GC = True
        rec = {"TC": 21.0}

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.FINAL_WEAK = False
            p.APARA1.GC_OK = True
            p.APARA1.TC_REC = rec["TC"]
            p.APARA1.TZ_REC = 22.0
            p.APARA1.GC1_REC = 23.0
            p.APARA1.GC2_REC = 24.0
            p.APARA1.OUTH_REC = 25.0
            p.APARA1.OUTL_REC = -26.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)
        self.assertEqual(p.TC, 21.0)
        self.assertIs(p.APARA_APPLY_GC, False)

        rec["TC"] = 99.0
        p.APARA_APPLY_GC = True
        step_apcm(p)
        self.assertEqual(p.TC, 21.0)
        self.assertIs(p.APARA_APPLY_GC, True)

        p.APARA_APPLY_GC = False
        step_apcm(p)
        rec["TC"] = 33.0
        p.APARA_APPLY_GC = True
        step_apcm(p)
        self.assertEqual(p.TC, 33.0)

    def test_apara_apply_cd_success_writes_group_and_keeps_current_av(self):
        p = make_block()
        p.APARA_APPLY_CD = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.FINAL_WEAK = False
            p.APARA1.CD_OK = True
            p.APARA1.CD_GD_REC = 31.0
            p.APARA1.CD_K_REC = 0.2
            p.APARA1.CD_K_FD_REC = 3.0
            p.APARA1.CD_K_J_REC = 4.0
            p.APARA1.CD_K_D_REC = 5.0
            p.APARA1.CDH_REC = 6.0
            p.APARA1.CDL_REC = -7.0
            p.APARA1.TC_CD_REC = 8.0
            p.APARA1.TZ_CD_REC = 9.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p, RM=3, TP=12.0)

        self.assertEqual(p.AV, 12.0)
        self.assertEqual(
            (p.CD_GD, p.CD_K, p.CD_K_FD, p.CD_K_J, p.CD_K_D, p.CDH, p.CDL, p.TC_CD, p.TZ_CD),
            (31.0, 0.2, 3.0, 4.0, 5.0, 6.0, -7.0, 8.0, 9.0),
        )
        self.assertIs(p.APARA_APPLY_CD, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 4)
        self.assertIs(p.APARA_LAST_APPLY_OK, True)
        self.assertEqual(p.APARA_LAST_APPLY_REASON, 1)
        self.assertEqual(p.APARA_CD_GD_REC, 31.0)

    def test_apara_apply_pid_rejects_strong_result_without_pid_ok_through_step(self):
        p = make_block()
        p.PT = 10.0
        p.APARA_APPLY_PID = True

        def apara_rec(dt_ms: int, **kw):
            p.APARA1.FINAL_STRONG = True
            p.APARA1.FINAL_WEAK = False
            p.APARA1.PID_OK = False
            p.APARA1.PT_REC = 99.0

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        self.assertEqual(p.PT, 10.0)
        self.assertIs(p.APARA_APPLY_PID, False)
        self.assertEqual(p.APARA_LAST_APPLY_GROUP, 1)
        self.assertIs(p.APARA_LAST_APPLY_OK, False)
        self.assertEqual(p.APARA_LAST_APPLY_REASON, 3)

    def test_apara_mirrors_all_outputs_through_step(self):
        p = make_block()
        bool_names = {
            "RUNNING",
            "WINDOW_DONE",
            "FINAL_STRONG",
            "FINAL_WEAK",
            "SP_VALID",
            "SP_AUTO_OK",
            "SP_TAG_BAD",
            "PID_OK",
            "RSF_OK",
            "GC_OK",
            "CD_OK",
            "PID_FORMULA_VALID",
        }
        expected: dict[str, float | bool | int] = {}
        for index, name in enumerate(APCM._APARA_MIRRORS, start=1):
            if name in bool_names:
                expected[name] = index % 2 == 0
            elif name in {"MATCH_LEVEL", "DATA_REASON", "SP_SOURCE", "SP_REASON", "PID_REASON", "RSF_REASON", "GC_REASON", "CD_REASON"}:
                expected[name] = index
            else:
                expected[name] = float(index)

        def apara_rec(dt_ms: int, **kw):
            for name, value in expected.items():
                setattr(p.APARA1, name, value)

        p.APARA1.step = apara_rec  # type: ignore[assignment]

        step_apcm(p)

        for name, value in expected.items():
            self.assertEqual(getattr(p, f"APARA_{name}"), value, name)


if __name__ == "__main__":
    unittest.main()
