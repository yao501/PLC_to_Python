"""APCPIDZZD（PID 自整定）业务块的契约测试。

按提示词 A~L 覆盖，唯一事实来源是 ST 实际执行顺序：

* A 初始状态
* B 授权门控（BD_ERROR5 累加 / 回绕 / 失败保持状态 / 恢复 / 每拍一次验证）
* C 500ms 扫描时间（TON PT=5000ms / +0.5 固定步进 / 非 dt/1000）
* D 严格阈值（0.5% 边界 / JSSJ==5 不写 / SQSJ 边界）
* E 离散积算与方向状态
* F 结束积算、移位与复位顺序
* G 发散/振荡识别三档
* H 理论 TI 三档
* I R_TRIG 长时间偏差路径
* J 非自动状态精确复位边界
* K 输出限幅
* L PT1K/TI1K 注释与实际代码冲突

授权用真实许可链路（写入正确 BD_MM1~4），不 monkey patch 授权结果。
"""

from __future__ import annotations

import unittest

from src.blocks import APCPIDZZD
from src.globals import LicenseContext
from src.licensing.bd_zcm import BD_ZCM
from src.licensing.issuer import derive_passwords_from_registration_codes
from src.licensing.providers import ManualDateTimeProvider, StaticSerialTextProvider

SERIAL = "PYPLC|TEST|MACHINE-0001"
TIME_MS = 5000  # 5s：totalSeconds%10=5（非 7 时段），授权通过后稳定放行


def make_ctx(authorized: bool = True, now_ms: int = TIME_MS) -> LicenseContext:
    ctx = LicenseContext(
        StaticSerialTextProvider(SERIAL),
        ManualDateTimeProvider(now_ms),
    )
    if authorized:
        # 按现有许可模块正常生成密码（BD_ZCM → derive），而非伪造/硬编码结果。
        zcm = BD_ZCM(StaticSerialTextProvider(SERIAL)).step(True)
        mm = derive_passwords_from_registration_codes(
            zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
        )
        ctx.set_passwords(*mm)
    return ctx


def make_block(authorized: bool = True) -> APCPIDZZD:
    return APCPIDZZD(make_ctx(authorized))


def step_pid(
    z: APCPIDZZD,
    *,
    PV: float,
    SP: float = 50.0,
    AV: float = 50.0,
    PT: float = 10.0,
    TI: float = 20.0,
    RM: int = 1,
    PVMU: float = 100.0,
    PVMD: float = 0.0,
    MU: float = 100.0,
    MD: float = 0.0,
    SADD: bool = False,
    SSUB: bool = False,
    PT1K: float = 1.0,
    TI1K: float = 1.0,
    dt_ms: int = 500,
) -> None:
    z.step(
        dt_ms,
        AV=AV,
        SP=SP,
        PV=PV,
        PT=PT,
        TI=TI,
        RM=RM,
        PVMU=PVMU,
        PVMD=PVMD,
        MU=MU,
        MD=MD,
        SADD=SADD,
        SSUB=SSUB,
        PT1K=PT1K,
        TI1K=TI1K,
    )


def accumulate_n(z: APCPIDZZD, n: int, dev: float = 1.0, dt_ms: int = 500) -> None:
    """把 z 推进到积算状态并执行 n 次有效积算（PV>SP，正积算）。

    dt=500、PT=5000：前 9 拍 TON1.Q=False（预热），其后每拍 Q=True 积算。
    """
    pv = 50.0 + dev
    for _ in range(9):
        step_pid(z, PV=pv, dt_ms=dt_ms)
    for _ in range(n):
        step_pid(z, PV=pv, dt_ms=dt_ms)


def end_cycle(z: APCPIDZZD) -> None:
    """偏差归零一拍：TON1.Q=False → 结束本次积算（写历史 + 复位）。"""
    step_pid(z, PV=50.0)


class TestInitialState(unittest.TestCase):
    """A. 初始状态。"""

    def test_initial_values(self):
        z = make_block()
        self.assertEqual(z.PT1, 0.0)
        self.assertEqual(z.TI1, 0.0)
        self.assertIs(z.ZJSBZ, True)
        self.assertIs(z.FJSBZ, True)
        self.assertEqual(z.JSSJ, 0.0)
        self.assertEqual(z.JSSJ2, 0.0)
        self.assertEqual(z.ZDPC, 0.0)
        self.assertEqual(z.JSSJZ, 20.0)
        self.assertEqual(z.JSSJF, 20.0)
        self.assertEqual(z.SQSJ, 0.0)
        self.assertIs(z.SBCGBZ, False)

    def test_arrays_zero(self):
        z = make_block()
        for n in range(1, 4):
            for m in range(1, 4):
                self.assertEqual(z.JS_Z[n][m], 0.0)
                self.assertEqual(z.JS_F[n][m], 0.0)

    def test_subblocks_present(self):
        z = make_block()
        self.assertEqual(z.TON1.ET_ms, 0)
        self.assertEqual(z.TON2.ET_ms, 0)
        self.assertEqual(z.HSACCUM1.AV, 0.0)


class TestAuthGate(unittest.TestCase):
    """B. 授权门控。"""

    def test_auth_fail_increments_bd_error5(self):
        z = make_block(authorized=False)
        ctx = z._ctx
        step_pid(z, PV=70.0)
        self.assertEqual(ctx.BD_ERROR5, 1.0)
        step_pid(z, PV=70.0)
        self.assertEqual(ctx.BD_ERROR5, 2.0)

    def test_bd_error5_wraparound(self):
        z = make_block(authorized=False)
        ctx = z._ctx
        ctx.BD_ERROR5 = 999999999.0
        step_pid(z, PV=70.0)
        self.assertEqual(ctx.BD_ERROR5, 100000000.0)

    def test_auth_fail_keeps_state_unchanged(self):
        z = make_block(authorized=False)
        z.PT1 = 5.0
        z.TI1 = 7.0
        z.JSSJ = 3.0
        z.ZJSBZ = False
        z.JS_Z[1][3] = 42.0
        step_pid(z, PV=70.0)
        self.assertEqual(z.PT1, 5.0)
        self.assertEqual(z.TI1, 7.0)
        self.assertEqual(z.JSSJ, 3.0)
        self.assertIs(z.ZJSBZ, False)
        self.assertEqual(z.JS_Z[1][3], 42.0)

    def test_auth_recovers_next_scan(self):
        ctx = make_ctx(authorized=False)
        z = APCPIDZZD(ctx)
        step_pid(z, PV=70.0)  # 失败：PVMAXDI 不应被计算
        self.assertEqual(z.PVMAXDI, 0.0)
        zcm = BD_ZCM(StaticSerialTextProvider(SERIAL)).step(True)
        ctx.set_passwords(
            *derive_passwords_from_registration_codes(
                zcm["ZCM1"], zcm["ZCM2"], zcm["ZCM3"]
            )
        )
        step_pid(z, PV=70.0)  # 恢复：主逻辑执行，PVMAXDI 被计算
        self.assertAlmostEqual(z.PVMAXDI, 2.0)

    def test_one_auth_call_per_step(self):
        z = make_block()
        calls = []
        orig = z._ctx.KZQBDYZMK.step

        def counting(dt_ms: int = 0):
            calls.append(1)
            return orig(dt_ms)

        z._ctx.KZQBDYZMK.step = counting  # type: ignore[assignment]
        step_pid(z, PV=70.0)
        self.assertEqual(len(calls), 1)


class TestScanTime(unittest.TestCase):
    """C. 500ms 扫描时间。"""

    def test_ton_pt_is_5000ms(self):
        z = make_block()
        # dt=500、PT=5000：第 9 拍仍 Q=False，第 10 拍 Q=True。
        for _ in range(9):
            step_pid(z, PV=70.0)
        self.assertIs(z.TON1.Q, False)
        step_pid(z, PV=70.0)
        self.assertIs(z.TON1.Q, True)

    def test_jssj_increments_half_second(self):
        z = make_block()
        accumulate_n(z, 4, dev=10.0)
        self.assertEqual(z.JSSJ, 2.0)

    def test_not_dt_over_1000(self):
        # dt=1000：第 5 拍 TON1.Q=True，仍每拍 +0.5（不是 +1.0）。
        z = make_block()
        for _ in range(4):
            step_pid(z, PV=70.0, dt_ms=1000)
        self.assertIs(z.TON1.Q, False)
        step_pid(z, PV=70.0, dt_ms=1000)  # 第 5 拍 Q=True，积算一次
        self.assertIs(z.TON1.Q, True)
        self.assertEqual(z.JSSJ, 0.5)

    def test_sqsj_increments_half_second(self):
        z = make_block()
        z.TON2.ET_ms = 5000  # 预置 TON2 已到时
        step_pid(z, PV=50.2)  # 偏差 0.2 < 0.5 → TON2.Q=True
        self.assertIs(z.TON2.Q, True)
        self.assertEqual(z.SQSJ, 0.5)
        step_pid(z, PV=50.2)
        self.assertEqual(z.SQSJ, 1.0)


class TestStrictThresholds(unittest.TestCase):
    """D. 严格阈值。"""

    def test_exactly_half_percent_neither_ton(self):
        z = make_block()
        for _ in range(12):
            step_pid(z, PV=50.5)  # 偏差恰为量程 0.5%（=0.5）
        self.assertEqual(z.TON1.ET_ms, 0)
        self.assertEqual(z.TON2.ET_ms, 0)
        self.assertIs(z.TON1.Q, False)
        self.assertIs(z.TON2.Q, False)

    def test_just_above_enters_ton1(self):
        z = make_block()
        for _ in range(10):
            step_pid(z, PV=50.6)  # 0.6 > 0.5
        self.assertIs(z.TON1.Q, True)

    def test_just_below_enters_ton2(self):
        z = make_block()
        for _ in range(10):
            step_pid(z, PV=50.4)  # 0.4 < 0.5
        self.assertIs(z.TON2.Q, True)

    def test_jssj_equal_5_not_written(self):
        z = make_block()
        accumulate_n(z, 10, dev=1.0)  # JSSJ = 5.0
        self.assertEqual(z.JSSJ, 5.0)
        end_cycle(z)
        self.assertEqual(z.JS_Z[1][3], 0.0)

    def test_jssj_above_5_written(self):
        z = make_block()
        accumulate_n(z, 11, dev=1.0)  # JSSJ = 5.5
        self.assertEqual(z.JSSJ, 5.5)
        end_cycle(z)
        self.assertAlmostEqual(z.JS_Z[1][3], 11.0)

    def test_sqsj_boundary_strict(self):
        z = make_block()
        z.SQSJ = 9.5
        z.TON2.ET_ms = 5000
        z.JS_Z[1][1] = 99.0
        step_pid(z, PV=50.4)  # TON2.Q=True → SQSJ=10.0；10>10 假，不清
        self.assertEqual(z.SQSJ, 10.0)
        self.assertEqual(z.JS_Z[1][1], 99.0)
        step_pid(z, PV=50.4)  # SQSJ=10.5 >10 → 清空并复位 SQSJ
        self.assertEqual(z.JS_Z[1][1], 0.0)
        self.assertEqual(z.SQSJ, 0.0)


class TestAccumAndDirection(unittest.TestCase):
    """E. 离散积算与方向状态。"""

    def test_accum_called_after_ton1(self):
        z = make_block()
        accumulate_n(z, 3, dev=2.0)
        self.assertAlmostEqual(z.HSACCUM1.AV, 6.0)  # 3 拍 × |PV-SP|=2

    def test_discrete_not_dt_integral(self):
        za = make_block()
        accumulate_n(za, 1, dev=3.0, dt_ms=500)
        self.assertAlmostEqual(za.HSACCUM1.AV, 3.0)
        zb = make_block()
        for _ in range(4):
            step_pid(zb, PV=53.0, dt_ms=1000)
        step_pid(zb, PV=53.0, dt_ms=1000)  # dt=1000 第 5 拍积算一次
        self.assertAlmostEqual(zb.HSACCUM1.AV, 3.0)  # 仍是 |PV-SP|，与 dt 无关

    def test_pv_gt_sp_sets_zjsbz(self):
        z = make_block()
        accumulate_n(z, 1, dev=5.0)  # PV=55 > SP=50
        self.assertIs(z.ZJSBZ, True)
        self.assertIs(z.FJSBZ, False)

    def test_pv_lt_sp_sets_fjsbz(self):
        z = make_block()
        pv = 45.0  # PV < SP
        for _ in range(10):
            step_pid(z, PV=pv)
        self.assertIs(z.FJSBZ, True)
        self.assertIs(z.ZJSBZ, False)

    def test_zdpc_tracks_max_deviation(self):
        z = make_block()
        # 预热到 Q=True 后给不同偏差，ZDPC 取最大。
        for _ in range(9):
            step_pid(z, PV=52.0)
        step_pid(z, PV=52.0)  # dev 2
        step_pid(z, PV=58.0)  # dev 8 → ZDPC=8
        step_pid(z, PV=54.0)  # dev 4 → ZDPC 仍 8
        self.assertAlmostEqual(z.ZDPC, 8.0)

    def test_reset_call_retains_last_i1(self):
        z = make_block()
        accumulate_n(z, 12, dev=1.0)  # AV=12, last_I1=1.0
        end_cycle(z)
        self.assertAlmostEqual(z._hsaccum_last_I1, 1.0)
        self.assertEqual(z.HSACCUM1.AV, 0.0)  # 复位到 IV
        # LR = 复位前 AV + 保留的 I1（若复位传 0 则 LR=12）→ 证明保留语义
        self.assertAlmostEqual(z.HSACCUM1.LR, z.JS_Z[1][3] + 1.0)


class TestEndShiftReset(unittest.TestCase):
    """F. 结束积算、移位与复位顺序。"""

    def test_positive_to_jsz_negative_to_jsf(self):
        z = make_block()
        accumulate_n(z, 12, dev=1.0)  # 正积算
        end_cycle(z)
        self.assertAlmostEqual(z.JS_Z[1][3], 12.0)
        self.assertEqual(z.JS_F[1][3], 0.0)
        # 负积算
        zf = make_block()
        for _ in range(9):
            step_pid(zf, PV=49.0)
        for _ in range(12):
            step_pid(zf, PV=49.0)
        end_cycle(zf)
        self.assertAlmostEqual(zf.JS_F[1][3], 12.0)
        self.assertEqual(zf.JS_Z[1][3], 0.0)

    def test_history_shift_order(self):
        z = make_block()
        for n in (12, 14, 16):
            accumulate_n(z, n, dev=1.0)
            end_cycle(z)
        # 时间行最干净地体现移位顺序 [1]<-[2]<-[3]<-当前：6/7/8 对应三次 JSSJ。
        self.assertEqual(z.JS_Z[2], [0.0, 6.0, 7.0, 8.0])
        self.assertEqual(z.JS_Z[3], [0.0, 1.0, 1.0, 1.0])
        # 面积行：因 TON1 延时窗口内（Q=False）每拍以 RS:=TRUE 调用 HSACCUM1，
        # 而 APCHSACCUM 每次调用都先 AV+=MC*I1 且 I1 保持上次值（=1.0），故预热
        # 9 拍各 +1 → 第二/三次起算面积含 +9 偏移（12 / 9+14=23 / 9+16=25）。
        # 这是基于 ST 调用路径 + APCHSACCUM 源逻辑的源级语义推导，尚未与真实
        # SoftPLC 实机轨迹做黄金对照（见 RISKS::APCPIDZZD-ACCUM-1）。
        self.assertEqual(z.JS_Z[1], [0.0, 12.0, 23.0, 25.0])
        self.assertTrue(z.JS_Z[1][1] < z.JS_Z[1][2] < z.JS_Z[1][3])

    def test_end_clears_counters_and_flags(self):
        z = make_block()
        accumulate_n(z, 12, dev=1.0)
        end_cycle(z)
        self.assertEqual(z.JSSJ, 0.0)
        self.assertEqual(z.JSSJ2, 0.0)
        self.assertEqual(z.ZDPC, 0.0)
        self.assertIs(z.ZJSBZ, False)
        self.assertIs(z.FJSBZ, False)

    def test_cold_start_both_flags_execute(self):
        z = make_block()
        # 构造冷启动两标志均 True 且 JSSJ>5 的结束拍。
        z.ZJSBZ = True
        z.FJSBZ = True
        z.JSSJ = 6.0
        z.ZDPC = 3.0
        z.HSACCUM1.AV = 10.0
        step_pid(z, PV=50.0)  # TON1.Q=False → 两个独立 IF 都执行
        self.assertAlmostEqual(z.JS_Z[1][3], 10.0)
        self.assertAlmostEqual(z.JS_F[1][3], 10.0)
        self.assertIs(z.ZJSBZ, False)
        self.assertIs(z.FJSBZ, False)


def _load_identify_state(z, arr, *, a, b, c, md1, md2, md3, t1, t2, jssj=6.0):
    """预置积算历史，使本次结束拍移位后得到指定的收敛率输入。

    移位规则 [1]<-[2]<-[3]<-当前，因此设置 pre[col2]、pre[col3] 与"当前值"
    （HSACCUM1.AV / ZDPC）即可控制移位后的三个槽位。
    """
    arr[1][2] = a
    arr[1][3] = b
    z.HSACCUM1.AV = c  # → arr[1][3]
    arr[2][2] = t1
    arr[2][3] = t2
    arr[3][2] = md1
    arr[3][3] = md2
    z.ZDPC = md3  # → arr[3][3]
    z.JSSJ = jssj


class TestIdentification(unittest.TestCase):
    """G. 发散/振荡识别三档。"""

    def _run_pos(self, **kw):
        z = make_block()
        z.ZJSBZ = True
        z.FJSBZ = False
        _load_identify_state(z, z.JS_Z, **kw)
        step_pid(z, PV=50.0, PT=10.0, TI=20.0)
        return z

    def test_divergence_big_adjust(self):
        # SLL11=2,SLL12=2 (>1.1) → coef=PT1K*1
        z = self._run_pos(a=1.0, b=2.0, c=4.0, md1=5, md2=5, md3=5, t1=10, t2=10)
        self.assertAlmostEqual(z.PT1, 10.0)  # (10+0)*(1+1)-10

    def test_medium_adjust(self):
        # SLL11=1,SLL12=1 (>0.7,<=1.1) → coef=PT1K*0.5
        z = self._run_pos(a=1.0, b=1.0, c=1.0, md1=5, md2=5, md3=5, t1=10, t2=10)
        self.assertAlmostEqual(z.PT1, 5.0)  # (10)*(1.5)-10

    def test_small_adjust(self):
        # SLL11=0.5,SLL12=0.5 (>0.4,<=0.7) → coef=PT1K*0.1
        z = self._run_pos(a=2.0, b=1.0, c=0.5, md1=5, md2=5, md3=5, t1=10, t2=10)
        self.assertAlmostEqual(z.PT1, 1.0)  # (10)*(1.1)-10

    def test_negative_path_adjusts(self):
        z = make_block()
        z.ZJSBZ = False
        z.FJSBZ = True
        _load_identify_state(z, z.JS_F, a=1.0, b=2.0, c=4.0, md1=5, md2=5, md3=5, t1=10, t2=10)
        step_pid(z, PV=50.0, PT=10.0, TI=20.0)
        self.assertAlmostEqual(z.PT1, 10.0)
        self.assertAlmostEqual(z.JS_F[1][3], 4.0)

    def test_pvmaxdi_not_satisfied_no_adjust(self):
        # 最大偏差 < PVMAXDI(=2) → 不识别
        z = self._run_pos(a=1.0, b=2.0, c=4.0, md1=1, md2=1, md3=1, t1=10, t2=10)
        self.assertEqual(z.PT1, 0.0)

    def test_sll_ge_10_no_adjust(self):
        # SLL12=20 >=10 → 条件假，不调整
        z = self._run_pos(a=1.0, b=2.0, c=40.0, md1=5, md2=5, md3=5, t1=10, t2=10)
        self.assertEqual(z.PT1, 0.0)

    def test_success_clears_only_front_area(self):
        z = self._run_pos(a=1.0, b=2.0, c=4.0, md1=5, md2=5, md3=5, t1=10, t2=10)
        self.assertEqual(z.JS_Z[1][1], 0.0)
        self.assertEqual(z.JS_Z[1][2], 0.0)
        self.assertEqual(z.JS_F[1][1], 0.0)
        self.assertEqual(z.JS_F[1][2], 0.0)
        # 当前面积、时间行、最大偏差行不被清除
        self.assertAlmostEqual(z.JS_Z[1][3], 4.0)
        self.assertNotEqual(z.JS_Z[2][3], 0.0)
        self.assertNotEqual(z.JS_Z[3][3], 0.0)


class TestTheoreticalTI(unittest.TestCase):
    """H. 理论 TI 三档（面积收敛率设小以隔离 PT1）。"""

    def _run_pos(self, *, md1, md2, md3):
        z = make_block()
        z.ZJSBZ = True
        z.FJSBZ = False
        # 面积 SLL 都很小（0.1）→ 不触发比例识别，PT1 保持 0。
        _load_identify_state(
            z, z.JS_Z, a=10.0, b=1.0, c=0.1, md1=md1, md2=md2, md3=md3, t1=30.0, t2=30.0
        )
        step_pid(z, PV=50.0, PT=10.0, TI=20.0)
        return z

    def test_ratio_about_1(self):
        # SLL21=SLL22=1 → JSTI=(30+30)*0.5=30
        z = self._run_pos(md1=5, md2=5, md3=5)
        self.assertAlmostEqual(z.PT1, 0.0)
        self.assertAlmostEqual(z.TI1, 2.0)  # 16 + min(max(30,10),40)*0.2 - 20

    def test_ratio_about_06(self):
        # SLL21=SLL22=0.6 → JSTI=60*0.4=24
        z = self._run_pos(md1=10, md2=6, md3=3.6)
        self.assertAlmostEqual(z.TI1, 0.8)  # 16 + 24*0.2 - 20

    def test_ratio_about_02(self):
        # SLL21=SLL22=0.2 → JSTI=60*0.3=18
        z = self._run_pos(md1=25, md2=5, md3=1)
        self.assertAlmostEqual(z.TI1, -0.4)  # 16 + 18*0.2 - 20

    def test_negative_path(self):
        z = make_block()
        z.ZJSBZ = False
        z.FJSBZ = True
        _load_identify_state(
            z, z.JS_F, a=10.0, b=1.0, c=0.1, md1=5, md2=5, md3=5, t1=30.0, t2=30.0
        )
        step_pid(z, PV=50.0, PT=10.0, TI=20.0)
        self.assertAlmostEqual(z.TI1, 2.0)


class TestRtrigLongDeviation(unittest.TestCase):
    """I. R_TRIG 长时间偏差路径。"""

    def _block_small_thresholds(self):
        z = make_block()
        # 让 (JSSJZ+JSSJF) 很小，阈值 2.5x / 4x 容易跨越。
        z.JSSJZ = 0.5
        z.JSSJF = 0.5  # S=1 → 阈值 2.5 与 4.0
        return z

    def test_first_cross_25x_once(self):
        z = self._block_small_thresholds()
        accumulate_n(z, 6, dev=10.0)  # JSSJ2 = 3.0 > 2.5 在第 6 拍触发
        self.assertAlmostEqual(z.TI1, -4.0)  # max((20+0)*0.8-20, -14)

    def test_no_repeat_while_held(self):
        z = self._block_small_thresholds()
        accumulate_n(z, 6, dev=10.0)
        ti_after_first = z.TI1
        # 继续保持（仍 <4.0 阈值），R_TRIG1 不应再次触发改变 TI1。
        step_pid(z, PV=60.0)  # JSSJ2=3.5，仍 >2.5 但非上升沿
        self.assertAlmostEqual(z.TI1, ti_after_first)

    def test_second_cross_4x(self):
        z = self._block_small_thresholds()
        accumulate_n(z, 9, dev=10.0)  # JSSJ2=4.5 > 4.0 在第 9 拍触发 R_TRIG2
        self.assertAlmostEqual(z.TI1, -7.2)  # max((20-4)*0.8-20, -14)

    def test_jssj2_resets_on_sadd(self):
        z = self._block_small_thresholds()
        accumulate_n(z, 4, dev=10.0)
        self.assertAlmostEqual(z.JSSJ2, 2.0)
        step_pid(z, PV=60.0, SADD=True)  # 条件不成立 → JSSJ2 归零
        self.assertEqual(z.JSSJ2, 0.0)


class TestNonAutoReset(unittest.TestCase):
    """J. 非自动状态精确复位边界。"""

    def test_non_auto_resets_and_preserves(self):
        z = make_block()
        # 预置一批状态
        z.JS_Z[1][3] = 5.0
        z.JS_F[2][2] = 6.0
        z.ZJSBZ = True
        z.FJSBZ = True
        z.JSSJ = 9.0
        z.JSSJ2 = 8.0
        z.PT1 = 3.3
        z.TI1 = 4.4
        z.SQSJ = 7.7
        z.ZDPC = 2.2
        z.JSSJZ = 30.0
        z.JSSJF = 40.0
        z.TON1.ET_ms = 1500
        z.TON2.ET_ms = 2500
        z.HSACCUM1.AV = 11.0
        z.R_TRIG1._CLK_prev = True

        step_pid(z, PV=70.0, RM=0)

        # 被复位
        for n in range(1, 4):
            for m in range(1, 4):
                self.assertEqual(z.JS_Z[n][m], 0.0)
                self.assertEqual(z.JS_F[n][m], 0.0)
        self.assertIs(z.ZJSBZ, False)
        self.assertIs(z.FJSBZ, False)
        self.assertEqual(z.JSSJ, 0.0)
        self.assertEqual(z.JSSJ2, 0.0)
        # 不被额外复位
        self.assertEqual(z.PT1, 3.3)
        self.assertEqual(z.TI1, 4.4)
        self.assertEqual(z.SQSJ, 7.7)
        self.assertEqual(z.ZDPC, 2.2)
        self.assertEqual(z.JSSJZ, 30.0)
        self.assertEqual(z.JSSJF, 40.0)
        self.assertEqual(z.TON1.ET_ms, 1500)
        self.assertEqual(z.TON2.ET_ms, 2500)
        self.assertEqual(z.HSACCUM1.AV, 11.0)
        self.assertIs(z.R_TRIG1._CLK_prev, True)


class TestOutputClamp(unittest.TestCase):
    """K. 输出限幅（发生在自动状态主逻辑末尾）。"""

    def test_clamp_upper(self):
        z = make_block()
        z.PT1 = 1000.0
        z.TI1 = 1000.0
        step_pid(z, PV=50.0, PT=10.0, TI=20.0)  # 末尾限幅
        self.assertAlmostEqual(z.PT1, 30.0)  # PT*3
        self.assertAlmostEqual(z.TI1, 60.0)  # TI*3

    def test_clamp_lower(self):
        z = make_block()
        z.PT1 = -1000.0
        z.TI1 = -1000.0
        step_pid(z, PV=50.0, PT=10.0, TI=20.0)
        self.assertAlmostEqual(z.PT1, -7.0)  # -0.7*PT
        self.assertAlmostEqual(z.TI1, -14.0)  # -0.7*TI


class TestCoefficientCommentConflict(unittest.TestCase):
    """L. PT1K/TI1K 注释与实际代码冲突（按代码实现）。"""

    def test_top_clear_when_le_zero(self):
        z = make_block()
        z.PT1 = 9.0
        z.TI1 = 9.0
        # PT1K/TI1K <=0：顶部清零（且本拍无识别分支改写）。
        step_pid(z, PV=50.0, PT1K=-0.5, TI1K=-0.5)
        self.assertEqual(z.PT1, 0.0)
        self.assertEqual(z.TI1, 0.0)

    def test_negative_coefficient_still_adjusts_in_identify(self):
        # 证明注释"<=0 整个周期不调整"与代码不符：顶部清零后，识别分支仍重新赋值。
        z = make_block()
        z.ZJSBZ = True
        z.FJSBZ = False
        _load_identify_state(z, z.JS_Z, a=1.0, b=2.0, c=4.0, md1=5, md2=5, md3=5, t1=10, t2=10)
        step_pid(z, PV=50.0, PT=10.0, TI=20.0, PT1K=-0.5, TI1K=1.0)
        # 顶部把 PT1 清零，随后发散识别重新赋值为 (10+0)*(1-0.5)-10 = -5（非零）
        self.assertAlmostEqual(z.PT1, -5.0)


if __name__ == "__main__":
    unittest.main()
