"""APCRSFNAUTOPARA（RSFN 自动参数推荐）业务块的契约测试。

按提示词 A~K 覆盖，唯一事实来源是 ST 实际执行顺序（``APCRSFNAUTOPARA.txt``）：

* A 初始状态、冷启动与 RESET
* B CYCLE/dt_ms 分离与量程
* C COLLECT_MODE 与样本判断
* D APCSPFINDER 集成（真实子实例，不伪造 SP）
* E 累计、面积、事件与边界
* F CALC_NOW 上升沿
* G 窗口有效性与 DATA_REASON 优先级
* H 单窗口推荐
* I 历史写入、H_N 变化与指针
* J 三阶段融合
* K 源 ST 边缘语义（RESET 残留累计 / EN=False 末尾更新 / 输出保持）

不使用授权、机器码、系统时间、网络或第三方依赖。关键路径均通过真实 ``step()``
扫描序列形成，不直接篡改私有状态。
"""

from __future__ import annotations

import unittest

from src.blocks import APCRSFNAUTOPARA
from src.blocks.apcspfinder import APCSPFINDER


def base_kwargs(**over):
    kw = dict(
        EN=True,
        RESET=False,
        CALC_NOW=False,
        CYCLE=1.0,
        COLLECT_MODE=1,
        SP=50.0,
        PV=50.0,
        AV=10.0,
        TP=0.0,
        TS=False,
        RSF_LEVEL=0.0,
        RSF_LOCK_LEVEL_IN=0.0,
        RSF_STEP=0.0,
    )
    kw.update(over)
    return kw


def step(r: APCRSFNAUTOPARA, dt_ms: int = 500, **over) -> None:
    r.step(dt_ms, **base_kwargs(**over))


def prime(r: APCRSFNAUTOPARA, pv: float = 45.0, av: float = 10.0, sp: float = 50.0) -> None:
    """EN=False 初始化拍：完成冷启动并把 PV_1/AV_1 设为当前输入，不累计窗口。"""
    step(r, EN=False, PV=pv, AV=av, SP=sp)


def slow_window(r: APCRSFNAUTOPARA, ticks: int = 5, pv: float = 45.0, sp: float = 50.0):
    """恒定 PV + 每拍 RSF_STEP 事件 → 有效"慢响应"窗口（W_SLOW），CALC_R 结算。"""
    for _ in range(ticks):
        step(r, COLLECT_MODE=0, SP=sp, PV=pv, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
    step(r, COLLECT_MODE=0, SP=sp, PV=pv, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)


# ============================ A. 初始 / 冷启动 / RESET ============================


class TestInitColdStartReset(unittest.TestCase):
    def test_initial_outputs_zero(self):
        r = APCRSFNAUTOPARA()
        for name in (
            "SP_USE", "SP_AUTO", "HISTORY_COUNT", "WINDOW_T", "TL_REC", "E4_REC",
            "AO4_REC", "RSF_HYS_REC", "FUSE_WEIGHT", "WINDOW_EVENT_N",
        ):
            self.assertEqual(getattr(r, name), 0.0, name)
        for name in (
            "RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG", "FINAL_WEAK",
            "WINDOW_VALID", "SP_VALID", "RSF_OK", "INIT_DONE",
        ):
            self.assertIs(getattr(r, name), False, name)
        self.assertEqual(r.MATCH_LEVEL, 0)
        self.assertEqual(r.DATA_REASON, 0)
        self.assertEqual(r.H_IDX, 1)

    def test_spf1_is_real_persistent_subinstance(self):
        r = APCRSFNAUTOPARA()
        self.assertIsInstance(r.SPF1, APCSPFINDER)
        before = r.SPF1
        step(r, PV=50.0)
        step(r, PV=51.0)
        self.assertIs(r.SPF1, before)  # 跨扫描同一实例，不每拍重建

    def test_cold_start_sets_init_done(self):
        r = APCRSFNAUTOPARA()
        self.assertIs(r.INIT_DONE, False)
        step(r, PV=50.0)
        self.assertIs(r.INIT_DONE, True)

    def test_cold_start_first_scan_can_collect(self):
        r = APCRSFNAUTOPARA()
        # 冷启动首拍 EN=True/RESET=False：先初始化，再进入采集分支
        step(r, EN=True, RESET=False, COLLECT_MODE=0, PV=45.0, CYCLE=1.0)
        self.assertEqual(r.WIN_N, 1)
        self.assertAlmostEqual(r.WIN_ELAPSED, 1.0)

    def test_en_true_reset_true_running_but_no_sample(self):
        r = APCRSFNAUTOPARA()
        step(r, EN=True, RESET=True, COLLECT_MODE=0, PV=45.0)
        self.assertIs(r.RUNNING, True)  # RUNNING:=EN
        self.assertEqual(r.WIN_N, 0)  # 采集分支 EN AND NOT RESET 不成立

    def test_reset_window_done_false(self):
        r = APCRSFNAUTOPARA()
        step(r, EN=True, RESET=True, PV=45.0)
        self.assertIs(r.WINDOW_DONE, False)

    def test_reset_clears_history_and_valid(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(3):
            slow_window(r)
        self.assertGreater(r.HISTORY_COUNT, 0)
        step(r, EN=True, RESET=True, PV=45.0)
        self.assertEqual(r.HISTORY_COUNT, 0.0)
        self.assertTrue(all(v is False for v in r.H_VALID[1:25]))

    def test_reset_sets_sp_use_to_pv(self):
        r = APCRSFNAUTOPARA()
        step(r, EN=True, RESET=True, PV=37.0)
        self.assertAlmostEqual(r.SP_USE, 37.0)
        self.assertIs(r.SP_VALID, False)


# ============================ B. CYCLE / dt_ms / 量程 ============================


class TestCycleDtRange(unittest.TestCase):
    def test_cycle_zero_or_negative(self):
        r = APCRSFNAUTOPARA()
        step(r, CYCLE=0.0, PV=50.0)
        self.assertEqual(r.CYCLE_S, 0.001)
        step(r, CYCLE=-2.0, PV=50.0)
        self.assertEqual(r.CYCLE_S, 0.001)

    def test_same_cycle_diff_dt_ms_identical(self):
        r1 = APCRSFNAUTOPARA()
        r2 = APCRSFNAUTOPARA()
        prime(r1)
        prime(r2)
        for _ in range(2):
            for _ in range(5):
                step(r1, dt_ms=500, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
                step(r2, dt_ms=999, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
            step(r1, dt_ms=500, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)
            step(r2, dt_ms=999, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertAlmostEqual(r1.WINDOW_T, r2.WINDOW_T)
        self.assertAlmostEqual(r1.HISTORY_COUNT, r2.HISTORY_COUNT)
        self.assertAlmostEqual(r1.E1_REC, r2.E1_REC)
        self.assertAlmostEqual(r1.AO4_REC, r2.AO4_REC)

    def test_phy_range_disabled_uses_mu_md(self):
        r = APCRSFNAUTOPARA()
        step(r, PHY_RANGE_EN=False, MU=80.0, MD=10.0, PHY_MU=999.0, PHY_MD=0.0, PV=50.0)
        self.assertAlmostEqual(r.OUT_RANGE_USE, 70.0)
        self.assertAlmostEqual(r.OUT_RANGE, 70.0)

    def test_phy_range_enabled_uses_phy(self):
        r = APCRSFNAUTOPARA()
        step(r, PHY_RANGE_EN=True, MU=80.0, MD=10.0, PHY_MU=200.0, PHY_MD=50.0, PV=50.0)
        self.assertAlmostEqual(r.OUT_RANGE_USE, 150.0)
        self.assertAlmostEqual(r.OUT_RANGE, 150.0)

    def test_invalid_range_temp_100_but_range_ok_false(self):
        r = APCRSFNAUTOPARA()
        step(r, MU=5.0, MD=5.0, PV=50.0)
        self.assertEqual(r.OUT_RANGE, 100)
        self.assertIs(r.RANGE_OK, False)

    def test_h_n_clamped(self):
        r = APCRSFNAUTOPARA()
        step(r, HISTORY_N=100, PV=50.0)
        self.assertEqual(r.H_N, 24)
        step(r, HISTORY_N=0, PV=50.0)
        self.assertEqual(r.H_N, 1)
        step(r, HISTORY_N=-5, PV=50.0)
        self.assertEqual(r.H_N, 1)

    def test_blend_clamped(self):
        r = APCRSFNAUTOPARA()
        step(r, REC_BLEND=5.0, PV=50.0)
        self.assertEqual(r.BLEND, 1)
        step(r, REC_BLEND=-1.0, PV=50.0)
        self.assertEqual(r.BLEND, 0)


# ============================ C. COLLECT_MODE 与样本 ============================


class TestCollectMode(unittest.TestCase):
    def _flags(self, mode, ts):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=mode, TS=ts, PV=45.0)
        return r.AUTO_SAMPLE, r.MAN_SAMPLE, r.SAMPLE_OK

    def test_mode0(self):
        self.assertEqual(self._flags(0, False), (True, False, True))
        self.assertEqual(self._flags(0, True), (False, False, False))

    def test_mode1(self):
        self.assertEqual(self._flags(1, False), (True, False, True))
        self.assertEqual(self._flags(1, True), (False, True, True))

    def test_mode2(self):
        self.assertEqual(self._flags(2, False), (False, False, False))
        self.assertEqual(self._flags(2, True), (False, True, True))

    def test_illegal_mode_no_sample(self):
        self.assertEqual(self._flags(5, False), (False, False, False))
        self.assertEqual(self._flags(5, True), (False, False, False))


# ============================ D. APCSPFINDER 集成 ============================


class TestSpfinderIntegration(unittest.TestCase):
    def test_manual_sp(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=50.0, SP_MAN=42.0, SP_MAN_EN=True, PV=45.0)
        self.assertEqual(r.SP_SOURCE, 1)
        self.assertAlmostEqual(r.SP_USE, 42.0)

    def test_tag_sp(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=48.0, SP_TAG_EN=True, PV=45.0)
        self.assertEqual(r.SP_SOURCE, 2)
        self.assertAlmostEqual(r.SP_USE, 48.0)

    def test_sp_invalid_sp_work_is_pv(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        # 无人工/现场 SP，自动短时间不合格 → SP_VALID False → SP_WORK=PV → ERR=0
        step(r, COLLECT_MODE=0, SP_TAG_EN=False, SP_AUTO_EN=True, PV=45.0)
        self.assertIs(r.SP_VALID, False)
        self.assertEqual(r.SP_SOURCE, 0)
        self.assertEqual(r.ERR, 0.0)
        self.assertEqual(r.ABS_ERR, 0.0)

    def test_spf_pvmu_is_max_e4x2_1(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, E4_IN=4.0, PV=45.0)
        self.assertAlmostEqual(r.SPF1.PV_RANGE, 8.0)  # |MAX(4*2,1)-0|
        step(r, COLLECT_MODE=0, E4_IN=0.2, PV=45.0)
        self.assertAlmostEqual(r.SPF1.PV_RANGE, 1.0)  # MAX(0.4,1)

    def test_spf_outt_is_out_range(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, MU=100.0, MD=0.0, PV=45.0)
        self.assertAlmostEqual(r.SPF1.OUT_RANGE, 100.0)


# ============================ E. 累计 / 面积 / 事件 / 边界 ============================


class TestAccumulationEvents(unittest.TestCase):
    def test_win_init_on_first_sample(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, AV=12.0)
        self.assertIs(r.WIN_INIT, True)
        self.assertEqual(r.WIN_PV_MAX, 45.0)
        self.assertEqual(r.WIN_PV_MIN, 45.0)
        self.assertEqual(r.WIN_AV_MAX, 12.0)

    def test_pos_neg_area(self):
        r = APCRSFNAUTOPARA()
        prime(r, pv=45.0)
        step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, CYCLE=1.0)  # ERR=+5
        self.assertAlmostEqual(r.WIN_ERR_AREA_POS, 5.0)
        self.assertEqual(r.WIN_ERR_AREA_NEG, 0.0)
        r2 = APCRSFNAUTOPARA()
        prime(r2, pv=55.0)
        step(r2, COLLECT_MODE=0, SP=50.0, PV=55.0, CYCLE=1.0)  # ERR=-5
        self.assertAlmostEqual(r2.WIN_ERR_AREA_NEG, 5.0)
        self.assertEqual(r2.WIN_ERR_AREA_POS, 0.0)

    def test_noise_accumulation(self):
        r = APCRSFNAUTOPARA()
        prime(r, pv=45.0)
        step(r, COLLECT_MODE=0, PV=47.0)  # D_PV=2
        self.assertAlmostEqual(r.WIN_NOISE_SUM, 2.0)
        self.assertEqual(r.WIN_NOISE_N, 1)

    def test_man_event_threshold(self):
        # MAN_TH = max(0.1, 100*0.0005)=0.1。D_AV>=0.1 → 事件；<0.1 且 TP 不变 → 无
        r = APCRSFNAUTOPARA()
        prime(r, av=10.0)
        step(r, COLLECT_MODE=2, TS=True, AV=10.5, PV=45.0)  # D_AV=0.5>=0.1
        self.assertEqual(r.WIN_MAN_EVENT_N, 1)
        r2 = APCRSFNAUTOPARA()
        prime(r2, av=10.0)
        step(r2, COLLECT_MODE=2, TS=True, AV=10.05, TP=0.0, PV=45.0)  # D_AV=0.05<0.1
        self.assertEqual(r2.WIN_MAN_EVENT_N, 0)

    def test_crossing(self):
        r = APCRSFNAUTOPARA()
        prime(r, pv=48.0)
        step(r, COLLECT_MODE=0, SP=50.0, PV=48.0)  # ERR=+2
        step(r, COLLECT_MODE=0, SP=50.0, PV=52.0)  # ERR=-2 → 变号穿越
        self.assertEqual(r.WIN_CROSS_N, 1)

    def test_rsf_step_event(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0)  # |1.0|>=0.1
        self.assertEqual(r.WIN_RSF_EVENT_N, 1)

    def test_rsf_level_enter_positive_event(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_LEVEL=0.0, RSF_STEP=0.0)  # 无事件
        n0 = r.WIN_RSF_EVENT_N
        step(r, COLLECT_MODE=0, PV=45.0, RSF_LEVEL=1.0, RSF_STEP=0.0)  # 0->1 且 >0
        self.assertEqual(r.WIN_RSF_EVENT_N, n0 + 1)

    def test_lock_enter_event(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_LOCK_LEVEL_IN=0.0)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_LOCK_LEVEL_IN=1.0)  # 0->正 → 闭锁事件
        self.assertEqual(r.WIN_LOCK_N, 1)

    def test_zone_boundaries(self):
        # E1_IN=1,E2_IN=2,E3_IN=3,E4_IN=4。ABS_ERR 落各区间。
        def zone_for(err_pv):
            r = APCRSFNAUTOPARA()
            prime(r, pv=err_pv)
            step(r, COLLECT_MODE=0, SP=50.0, PV=err_pv)
            return (r.WIN_ZONE1_T, r.WIN_ZONE2_T, r.WIN_ZONE3_T, r.WIN_ZONE4_T)
        self.assertEqual(zone_for(48.5), (1.0, 0.0, 0.0, 0.0))  # |1.5| in [1,2)
        self.assertEqual(zone_for(47.5), (0.0, 1.0, 0.0, 0.0))  # |2.5| in [2,3)
        self.assertEqual(zone_for(46.5), (0.0, 0.0, 1.0, 0.0))  # |3.5| in [3,4)
        self.assertEqual(zone_for(45.0), (0.0, 0.0, 0.0, 1.0))  # |5| >=4


# ============================ F. CALC_NOW 上升沿 ============================


class TestCalcEdge(unittest.TestCase):
    def test_rising_edge_settles(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(5):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        self.assertIs(r.WINDOW_DONE, False)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertIs(r.WINDOW_DONE, True)

    def test_continuous_true_no_repeat(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(5):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertIs(r.WINDOW_DONE, True)
        # 持续 True：CALC_R 不再为真 → 不重复结算
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertIs(r.WINDOW_DONE, False)

    def test_en_false_updates_calc_old_no_false_edge(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(5):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        # EN=False 但 CALC_NOW=True：不采集，但末尾更新 CALC_OLD=True
        step(r, EN=False, CALC_NOW=True, PV=45.0, MIN_WIN_T=3.0)
        self.assertIs(r.CALC_OLD, True)
        # 再启用 EN 且仍 True：CALC_R=False（非新上升沿）→ 不结算
        step(r, EN=True, COLLECT_MODE=0, CALC_NOW=True, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        self.assertIs(r.WINDOW_DONE, False)

    def test_calc_r_requires_min_win_t(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        # 仅 2 拍（<MIN_WIN_T=3）即 CALC_NOW 上升沿 → 不结算
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertIs(r.WINDOW_DONE, False)


# ============================ G. WINDOW_VALID 与 DATA_REASON 优先级 ============================


class TestDataReason(unittest.TestCase):
    def _settle(self, ticks=5, **over):
        over.setdefault("MIN_WIN_T", 3.0)
        r = APCRSFNAUTOPARA()
        prime(r, pv=over.get("PV", 45.0))
        for _ in range(ticks):
            step(r, COLLECT_MODE=0, RSF_STEP=1.0, **over)
        step(r, COLLECT_MODE=0, RSF_STEP=1.0, CALC_NOW=True, **over)
        return r

    def test_reason_3_range_invalid(self):
        r = self._settle(MU=0.0, MD=0.0, PV=45.0)
        self.assertEqual(r.DATA_REASON, 3)
        self.assertIs(r.WINDOW_VALID, False)

    def test_reason_6_sp_invalid(self):
        r = self._settle(SP_TAG_EN=False, SP_AUTO_EN=True, PV=45.0)
        self.assertEqual(r.DATA_REASON, 6)
        self.assertIs(r.WINDOW_VALID, False)

    def test_reason_4_event_insufficient(self):
        r = self._settle(MIN_STORE_EVENT=999.0, PV=45.0)
        self.assertEqual(r.DATA_REASON, 4)

    def test_reason_5_response_insufficient(self):
        # PV=SP → ERR=0 → peak=0 < max(noise*2,0.001)；事件足够（RSF）
        r = self._settle(SP=50.0, PV=50.0)
        self.assertEqual(r.DATA_REASON, 5)

    def test_reason_1_normal(self):
        r = self._settle(SP=50.0, PV=45.0)
        self.assertEqual(r.DATA_REASON, 1)
        self.assertIs(r.WINDOW_VALID, True)

    def test_reason_2_unreachable_at_settle(self):
        """DATA_REASON=2（时间不足）在结算点不可达（APCRSFNAUTOPARA-DATAREASON-1）。

        两个窗口结算条件都要求 ``WIN_ELAPSED>=MIN_WIN_T``，故进入 DATA_REASON
        计算时该条件恒成立，``ELSIF WIN_ELAPSED<MIN_WIN_T → 2`` 永远为假。
        即使 WIN_T<MIN_WIN_T 也如此。ChatGPT5.5 修复版的 Bug2 实时补丁会破坏
        "最近完成窗口快照"语义，已撤回不同步；此死分支按源码原样保留。
        """
        r = self._settle(WIN_T=1.0, MIN_WIN_T=3.0, SP=50.0, PV=45.0)
        self.assertIs(r.WINDOW_DONE, True)
        self.assertNotEqual(r.DATA_REASON, 2)
        self.assertGreaterEqual(r.WINDOW_T, 3.0)  # 结算时 WIN_ELAPSED 必 >=MIN_WIN_T

    def test_reason_2_not_written_during_accumulation(self):
        """积累阶段（窗口未结算）不应实时写入 DATA_REASON=2（已撤回 Bug2 补丁）。"""
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=3.0)
        self.assertIs(r.WINDOW_DONE, False)
        self.assertNotEqual(r.DATA_REASON, 2)  # 死分支未被实时触发


# ============================ H. 单窗口推荐 ============================


class TestSingleWindowRec(unittest.TestCase):
    def _strong_slow(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(3):
            slow_window(r)
        return r

    def test_e_monotonic(self):
        r = self._strong_slow()
        self.assertLessEqual(r.E1_REC, r.E2_REC)
        self.assertLessEqual(r.E2_REC, r.E3_REC)
        self.assertLessEqual(r.E3_REC, r.E4_REC)

    def test_ao_monotonic_and_limits(self):
        r = self._strong_slow()
        self.assertLessEqual(r.AO1_REC, r.AO2_REC)
        self.assertLessEqual(r.AO2_REC, r.AO3_REC)
        self.assertLessEqual(r.AO3_REC, r.AO4_REC)
        self.assertLessEqual(r.AO4_REC, r.OUT_RANGE * 0.50 + 1e-9)

    def test_slow_strong_reason_2_ao_up(self):
        r = self._strong_slow()
        self.assertIs(r.FINAL_STRONG, True)
        self.assertEqual(r.RSF_REASON, 2)
        self.assertAlmostEqual(r.AO1_REC, 1.10)  # AO1_IN=1 → *1.10

    def test_osc_strong_reason_3(self):
        r = APCRSFNAUTOPARA()
        prime(r, av=10.0)

        def osc_win():
            seq = [(0.0, 10.0), (1.0, 11.0), (0.0, 12.0), (1.0, 13.0), (0.0, 14.0)]
            for lock, av in seq:
                step(r, COLLECT_MODE=1, TS=True, SP=50.0, PV=45.0, AV=av,
                     RSF_LOCK_LEVEL_IN=lock, RSF_STEP=0.0, MIN_WIN_T=3.0, MIN_STORE_EVENT=1.0)
            step(r, COLLECT_MODE=1, TS=True, SP=50.0, PV=45.0, AV=15.0,
                 RSF_LOCK_LEVEL_IN=1.0, RSF_STEP=0.0, MIN_WIN_T=3.0, MIN_STORE_EVENT=1.0, CALC_NOW=True)

        for _ in range(3):
            osc_win()
        self.assertIs(r.FINAL_STRONG, True)
        self.assertEqual(r.RSF_REASON, 3)
        self.assertGreaterEqual(r.ZF_K_REC, 0.5)  # 振荡 → ZF_K 至少 0.5
        self.assertLess(r.RSF_HYS_REC, 0.8)  # 慢退回差降低

    def test_slow_and_osc_priority_reason_2(self):
        # 同时慢响应(RSF事件+PV不变)与振荡(locks>=2) → 优先 reason 2
        r = APCRSFNAUTOPARA()
        prime(r)

        def both_win():
            for k in range(5):
                lock = 1.0 if k % 2 == 1 else 0.0
                step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0, RSF_STEP=1.0,
                     RSF_LOCK_LEVEL_IN=lock, MIN_WIN_T=3.0)
            step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0, RSF_STEP=1.0,
                 RSF_LOCK_LEVEL_IN=1.0, MIN_WIN_T=3.0, CALC_NOW=True)

        for _ in range(3):
            both_win()
        self.assertIs(r.FINAL_STRONG, True)
        self.assertEqual(r.RSF_REASON, 2)

    def test_invalid_window_reason_5(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(5):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=0.0, MIN_STORE_EVENT=999.0, MIN_WIN_T=3.0)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=0.0, MIN_STORE_EVENT=999.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertIs(r.WINDOW_VALID, False)
        self.assertEqual(r.RSF_REASON, 5)


# ============ REAL exact-carrier family (Codex WP-141 Round 1 §2/§3) ============


class TestRealRecCarriers(unittest.TestCase):
    """Every current reachable REAL ``*_REC`` recommendation keeps ``float``.

    Same-source family as APCMAUTOPARA's 17: ``max(X_IN, 0)`` (and the
    ``max(min(X_IN, 1), 0)`` variants) select a Python ``int`` for a legitimate
    negative or out-of-band REAL input, which ``Store.check_value_type`` rejects
    for REAL.  Both the reset-init fallback and the invalid-window direct
    (``_REC = W_*``) settlement branch must keep an exact ``float`` carrier.
    """

    # Expected values are independently enumerated from the input contract,
    # never derived from the product implementation.  This includes the five
    # WP-142 clamps and WP-143's thirteen invalid-window neighbours.
    _REAL_RECOMMENDATIONS = (
        ("TL_REC", 0.0),
        ("TL1_REC", 1.0), ("TL2_REC", 1.0),
        ("TL3_REC", 1.0), ("TL4_REC", 1.0),
        ("E1_REC", 0.001), ("E2_REC", 0.001),
        ("E3_REC", 0.001), ("E4_REC", 0.001),
        ("AO1_REC", 0.0), ("AO2_REC", 0.0),
        ("AO3_REC", 0.0), ("AO4_REC", 0.0),
        ("RSF_LOCK_T_REC", 0.0),
        ("RSF_HYS_REC", 1.0), ("RSF_FAST_HYS_REC", 1.0),
        ("RSF_TLOUT_K_REC", 1.0), ("ZF_K_REC", 1.0),
    )
    _BOUNDARY_INPUTS = {
        "TL_IN": -1.0, "TL1_IN": -1.0, "TL2_IN": -1.0,
        "TL3_IN": -1.0, "TL4_IN": -1.0,
        "E1_IN": -1.0, "E2_IN": -1.0, "E3_IN": -1.0, "E4_IN": -1.0,
        "AO1_IN": -1.0, "AO2_IN": -1.0, "AO3_IN": -1.0, "AO4_IN": -1.0,
        "RSF_LOCK_T_IN": -1.0, "RSF_HYS_IN": 2.0,
        "RSF_FAST_HYS_IN": 2.0, "RSF_TLOUT_K_IN": 2.0, "ZF_K_IN": 2.0,
    }

    def test_reset_default_real_rec_carriers_keep_exact_float(self):
        self.assertEqual(len(self._REAL_RECOMMENDATIONS), 18)
        r = APCRSFNAUTOPARA()
        step(r, EN=True, RESET=True, **self._BOUNDARY_INPUTS)
        for output, expected in self._REAL_RECOMMENDATIONS:
            with self.subTest(path="reset", output=output):
                carrier = getattr(r, output)
                self.assertIs(type(carrier), float)
                self.assertEqual(carrier, expected)

    def test_invalid_window_direct_branch_keeps_real_rec_float(self):
        """An invalid window settles through the direct ``_REC = W_*`` branch
        (no history match, ``FUSE_SUM_W == 0``); the ``W_*`` scratch carriers
        clamp on the same integer floors, so the committed recommendations must
        still be exact ``float``.  ``RSF_TLOUT_K_IN`` / ``ZF_K_IN`` above 1 also
        make ``max(min(.,1),0)`` pick the integer literal ``1``."""
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(5):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=0.0, MIN_STORE_EVENT=999.0,
                 MIN_WIN_T=3.0, **self._BOUNDARY_INPUTS)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=0.0, MIN_STORE_EVENT=999.0,
             MIN_WIN_T=3.0, CALC_NOW=True, **self._BOUNDARY_INPUTS)
        # Invalid window => direct (scratch ``W_*``) settlement branch, not the
        # self-matching fusion branch whose division always yields float.
        self.assertIs(r.WINDOW_DONE, True)
        self.assertIs(r.WINDOW_VALID, False)
        for name, expected in self._REAL_RECOMMENDATIONS:
            with self.subTest(path="invalid-window", output=name):
                carrier = getattr(r, name)
                self.assertIs(type(carrier), float)
                self.assertEqual(carrier, expected)


# ============================ I. 历史写入 / H_N 变化 / 指针 ============================


class TestHistory(unittest.TestCase):
    def test_valid_window_written_and_self_fused(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        slow_window(r)  # 1 个有效窗口
        self.assertEqual(r.HISTORY_COUNT, 1.0)
        self.assertIs(r.H_VALID[1], True)
        # 当前窗口同拍参与融合 → 自匹配 → 弱推荐
        self.assertIs(r.FINAL_WEAK, True)
        self.assertGreater(r.FUSE_WEIGHT, 0)

    def test_h_idx_wraps_and_history_cap(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        # H_N=2：写 3 个有效窗口 → 指针回绕、HISTORY_COUNT 封顶 2
        for _ in range(3):
            slow_window2(r)
        self.assertEqual(r.HISTORY_COUNT, 2.0)
        self.assertIn(r.H_IDX, (1, 2))

    def test_history_shrink_clears_out_of_range_valid(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(3):
            slow_window(r)  # 默认 H_N=24，写 3 个，占 idx 1..3
        self.assertIs(r.H_VALID[3], True)
        # 缩小 H_N=2：清除超范围 H_VALID[3..24]，HISTORY_COUNT 限到 2，H_IDX 回 1
        step(r, COLLECT_MODE=0, PV=45.0, HISTORY_N=2)
        self.assertIs(r.H_VALID[3], False)
        self.assertLessEqual(r.HISTORY_COUNT, 2.0)
        self.assertLessEqual(r.H_IDX, 2)

    def test_history_grow_does_not_fabricate(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(2):
            slow_window(r)
        hc = r.HISTORY_COUNT
        step(r, COLLECT_MODE=0, PV=45.0, HISTORY_N=24)  # 增大不应伪造
        self.assertEqual(r.HISTORY_COUNT, hc)
        self.assertTrue(all(r.H_VALID[i] is False for i in range(3, 25)))


def slow_window2(r):
    """与 slow_window 相同，但 HISTORY_N=2，用于指针回绕测试。"""
    for _ in range(5):
        step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=3.0, HISTORY_N=2)
    step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=3.0, HISTORY_N=2, CALC_NOW=True)


# ============================ J. 三阶段融合 ============================


class TestFusion(unittest.TestCase):
    def test_strict_strong_match_level_1(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(3):
            slow_window(r)
        self.assertIs(r.FINAL_STRONG, True)
        self.assertIs(r.FINAL_VALID, True)
        self.assertEqual(r.MATCH_LEVEL, 1)

    def test_relaxed_strong_match_level_2(self):
        # 严格失配、放宽(x3)命中 → 第 2 阶段达强推荐
        k = dict(SIM_SP_K=0.025, SIM_PV_K=0.025, SIM_AV_K=0.025, SIM_ERR_K=0.025,
                 SIM_RELAX_K=3.0, FUSE_MIN_N=3.0, FUSE_MIN_WEIGHT=3.0)
        r = APCRSFNAUTOPARA()
        prime(r, pv=44.4)

        def win(pv):
            for _ in range(5):
                step(r, COLLECT_MODE=0, SP=50.0, PV=pv, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=3.0, **k)
            step(r, COLLECT_MODE=0, SP=50.0, PV=pv, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=3.0, CALC_NOW=True, **k)

        win(44.4)
        win(44.7)
        win(45.0)
        self.assertIs(r.FINAL_STRONG, True)
        self.assertEqual(r.MATCH_LEVEL, 2)

    def test_single_weak_window(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        slow_window(r)
        self.assertIs(r.FINAL_WEAK, True)
        self.assertIs(r.FINAL_VALID, False)
        self.assertEqual(r.RSF_REASON, 5)  # 弱推荐覆盖原因码

    def test_no_match_uses_w_fallback(self):
        # 无效窗口 + 空历史 → FUSE_SUM_W=0 → *_REC 使用 W_*（当前参数规范化）
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(5):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=0.0, MIN_STORE_EVENT=999.0, MIN_WIN_T=3.0)
        step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=0.0, MIN_STORE_EVENT=999.0, MIN_WIN_T=3.0, CALC_NOW=True)
        self.assertEqual(r.FUSE_WEIGHT, 0.0)
        self.assertEqual(r.MATCH_LEVEL, 0)
        self.assertIs(r.FINAL_WEAK, False)
        self.assertIs(r.FINAL_VALID, False)
        self.assertAlmostEqual(r.TL_REC, 10.0)  # MAX(TL_IN,0)
        self.assertAlmostEqual(r.E1_REC, 1.0)  # MAX(E1_IN,0.001)
        self.assertAlmostEqual(r.AO4_REC, 4.0)


# ============================ K. 源 ST 边缘语义 ============================


class TestEdgeSemantics(unittest.TestCase):
    def test_reset_clears_sp_pv_av_sum(self):
        """APCRSFNAUTOPARA-RESET-1（修复版基线）：RESET 现已清零 WIN_SP/PV/AV_SUM。"""
        r = APCRSFNAUTOPARA()
        prime(r, pv=45.0, av=10.0)
        for _ in range(3):
            step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0)  # 累计 3 个样本
        self.assertAlmostEqual(r.WIN_SP_SUM, 150.0)  # 50*3
        self.assertAlmostEqual(r.WIN_PV_SUM, 135.0)  # 45*3
        self.assertAlmostEqual(r.WIN_AV_SUM, 30.0)  # 10*3
        step(r, EN=True, RESET=True, PV=45.0, AV=10.0)
        # 修复后这三项随 RESET 一并清零
        self.assertEqual(r.WIN_SP_SUM, 0.0)
        self.assertEqual(r.WIN_PV_SUM, 0.0)
        self.assertEqual(r.WIN_AV_SUM, 0.0)
        self.assertEqual(r.WIN_ELAPSED, 0.0)
        self.assertEqual(r.WIN_N, 0.0)

    def test_reset_no_residual_in_next_window_avg(self):
        """修复版：中途 RESET 后下一窗口均值不再带入残留累计。"""
        r = APCRSFNAUTOPARA()
        prime(r, pv=45.0, av=10.0)
        for _ in range(3):
            step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0)  # 旧残留已被 RESET 清掉
        step(r, EN=True, RESET=True, PV=45.0, AV=10.0)  # 清三项累计
        # 新窗口 3 个样本后结算（CALC_R）
        for _ in range(3):
            step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=2.0)
        step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0, RSF_STEP=1.0, MIN_WIN_T=2.0, CALC_NOW=True)
        # 仅 4 个新样本（3+settle），均值干净 = 45（无残留污染）
        self.assertAlmostEqual(r.WIN_PV_AVG, 45.0)
        self.assertAlmostEqual(r.WIN_SP_AVG, 50.0)
        self.assertAlmostEqual(r.WIN_AV_AVG, 10.0)

    def test_en_false_updates_tail_state(self):
        """APCRSFNAUTOPARA-CALC-1 / EN=False 末尾仍更新边沿与变化量基准。"""
        r = APCRSFNAUTOPARA()
        prime(r)
        step(r, EN=False, CALC_NOW=True, PV=33.0, AV=22.0, TP=7.0,
             RSF_LEVEL=2.0, RSF_LOCK_LEVEL_IN=3.0)
        self.assertIs(r.CALC_OLD, True)
        self.assertEqual(r.PV_1, 33.0)
        self.assertEqual(r.AV_1, 22.0)
        self.assertEqual(r.TP_1, 7.0)
        self.assertEqual(r.RSF_LEVEL_1, 2.0)
        self.assertEqual(r.RSF_LOCK_LEVEL_1, 3.0)
        self.assertEqual(r.WIN_N, 0.0)  # 未采集

    def test_cold_start_first_sample_uses_zero_baseline(self):
        """APCRSFNAUTOPARA-START-1：冷启动首拍变化量相对零基准。

        源 ST 的初始化块（RESET OR NOT INIT_DONE）并未初始化 PV_1/AV_1/TP_1/
        ERR_1/RSF_LEVEL_1/RSF_LOCK_LEVEL_1，它们冷启动默认 0；同一拍又直接进入
        采集分支，故第一笔样本 D_PV=ABS(PV-0)、D_AV=ABS(AV-0)、ABS(TP-TP_1)=
        ABS(TP-0)。若 PV/AV/TP 初值非零，首拍会额外抬高噪声统计或产生手动/RSF
        事件。按源码原样保留（**不**在初始化块额外重置这些"上一拍"变量），锁定。
        """
        r = APCRSFNAUTOPARA()
        # 冷启动首拍即 EN=True/RESET=False，无任何预热
        step(r, EN=True, RESET=False, COLLECT_MODE=1, TS=True, SP=50.0,
             PV=45.0, AV=10.0, TP=7.0, CYCLE=1.0)
        self.assertEqual(r.D_PV, 45.0)  # ABS(45-0)
        self.assertEqual(r.D_AV, 10.0)  # ABS(10-0)
        self.assertEqual(r.WIN_NOISE_SUM, 45.0)  # 首拍噪声统计含零基准
        self.assertEqual(r.WIN_MAN_EVENT_N, 1.0)  # D_AV/ABS(TP-0) 均>=MAN_TH → 手动事件

    def test_init_block_does_not_reset_prev_baselines(self):
        """初始化/RESET 块不重置 PV_1/AV_1/TP_1/ERR_1 等（仅末尾无条件更新）。"""
        r = APCRSFNAUTOPARA()
        step(r, EN=True, RESET=True, SP=50.0, PV=99.0, AV=88.0, TP=77.0,
             RSF_LEVEL=4.0, RSF_LOCK_LEVEL_IN=3.0)
        # RESET 当拍不采集，但末尾仍把这些"上一拍"变量更新为当前输入
        self.assertEqual(r.PV_1, 99.0)
        self.assertEqual(r.AV_1, 88.0)
        self.assertEqual(r.TP_1, 77.0)
        self.assertEqual(r.RSF_LEVEL_1, 4.0)
        self.assertEqual(r.RSF_LOCK_LEVEL_1, 3.0)

    def test_outputs_hold_when_no_new_window(self):
        r = APCRSFNAUTOPARA()
        prime(r)
        for _ in range(3):
            slow_window(r)
        snap = (r.FINAL_STRONG, r.FINAL_VALID, r.WINDOW_VALID, r.MATCH_LEVEL,
                r.E1_REC, r.AO4_REC, r.TL_REC, r.RSF_REASON)
        # 再走几拍但不结算新窗口（CALC_NOW=False，WIN_ELAPSED 远小于 WIN_T）
        for _ in range(3):
            step(r, COLLECT_MODE=0, PV=45.0, RSF_STEP=1.0, MIN_WIN_T=300.0, WIN_T=7200.0)
        self.assertIs(r.WINDOW_DONE, False)
        snap2 = (r.FINAL_STRONG, r.FINAL_VALID, r.WINDOW_VALID, r.MATCH_LEVEL,
                 r.E1_REC, r.AO4_REC, r.TL_REC, r.RSF_REASON)
        self.assertEqual(snap, snap2)


if __name__ == "__main__":
    unittest.main()
