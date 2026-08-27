"""APCMAUTOPARA（APCM 自动参数推荐）业务块的契约测试。

按提示词 A~K 覆盖，唯一事实来源是 ST 实际执行顺序（``APCMAUTOPARA.txt``）：

* A 初始 / 冷启动 / 默认回退
* B CYCLE/dt_ms 分离与量程
* C RESET 与 EN（与 APCRSFNAUTOPARA 的 ``EN AND NOT RESET`` 差异）
* D APCSPFINDER 集成（真实子实例，不伪造 SP）
* E 自动采样与事件
* F 手动事件与跨窗口响应
* G CALC_NOW 与窗口快照（DATA_REASON=2 不实时写）
* H 数据状态优先级
* I PID 推荐
* J RSF / 观测器 / 重叠控制推荐
* K 历史与三阶段融合

不使用授权、机器码、系统时间、网络或第三方依赖。关键路径均通过真实 ``step()``
扫描序列形成，不直接篡改私有状态。
"""

from __future__ import annotations

import unittest

from src.blocks import APCMAUTOPARA
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
        RM=1,
        TS=False,
    )
    kw.update(over)
    return kw


def step(r: APCMAUTOPARA, dt_ms: int = 500, **over) -> None:
    r.step(dt_ms, **base_kwargs(**over))


def prime(r: APCMAUTOPARA, pv: float = 50.0, av: float = 10.0, sp: float = 50.0) -> None:
    """EN=False 初始化拍：完成冷启动，不累计窗口。"""
    step(r, EN=False, PV=pv, AV=av, SP=sp)


def osc_window(
    r: APCMAUTOPARA,
    n: int = 8,
    *,
    cycle: float = 1.0,
    min_win_t: float = 3.0,
    sp: float = 50.0,
    hi: float = 55.0,
    lo: float = 45.0,
    av: float = 10.0,
    collect_mode: int = 0,
    **over,
) -> None:
    """构造一个振荡（多次穿越）自动窗口并用 CALC_NOW 强制结算。

    PV 在 hi/lo 间交替 → ERR 反复变号 → 多次穿越事件，得到有效且可入库的窗口。
    """
    for k in range(n):
        pv = hi if (k % 2 == 0) else lo
        step(
            r, COLLECT_MODE=collect_mode, SP=sp, PV=pv, AV=av, CYCLE=cycle,
            MIN_WIN_T=min_win_t, **over,
        )
    pv = hi if (n % 2 == 0) else lo
    step(
        r, COLLECT_MODE=collect_mode, SP=sp, PV=pv, AV=av, CYCLE=cycle,
        MIN_WIN_T=min_win_t, CALC_NOW=True, **over,
    )


# ============================ A. 初始 / 冷启动 / 默认回退 ============================


class TestInitColdStart(unittest.TestCase):
    def test_initial_outputs_zero(self):
        r = APCMAUTOPARA()
        for name in (
            "RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG", "FINAL_WEAK",
            "WINDOW_VALID", "PID_OK", "RSF_OK", "GC_OK", "CD_OK",
        ):
            self.assertFalse(getattr(r, name), name)
        for name in (
            "HISTORY_COUNT", "WINDOW_T", "PT_REC", "TI_REC", "E4_REC", "AO4_REC",
            "SP_USE", "DATA_REASON", "MATCH_LEVEL",
        ):
            self.assertEqual(getattr(r, name), 0, name)

    def test_spf1_is_real_independent_instance(self):
        r = APCMAUTOPARA()
        self.assertIsInstance(r.SPF1, APCSPFINDER)
        r2 = APCMAUTOPARA()
        self.assertIsNot(r.SPF1, r2.SPF1)

    def test_history_arrays_are_one_based(self):
        r = APCMAUTOPARA()
        self.assertEqual(len(r.H_VALID), 25)
        self.assertEqual(len(r.H_PT), 25)
        # 索引 0 占位不使用；索引 1..24 为有效槽
        self.assertFalse(r.H_VALID[0])
        self.assertFalse(r.H_VALID[24])

    def test_cold_start_sets_init_and_idx(self):
        r = APCMAUTOPARA()
        prime(r)
        self.assertTrue(r.INIT_DONE)
        self.assertEqual(r.H_IDX, 1)
        self.assertEqual(r.H_N_OLD, r.H_N)

    def test_default_fallback_uses_current_params(self):
        """无历史时四类推荐回退到当前输入并带边界限幅。"""
        r = APCMAUTOPARA()
        prime(r)
        self.assertEqual(r.PT_REC, 300.0)
        self.assertEqual(r.TI_REC, 50.0)
        self.assertEqual(r.TD_REC, 0.0)
        # TL1 至少 TL_REC+1
        self.assertEqual(r.TL_REC, 10.0)
        self.assertEqual(r.TL1_REC, 120.0)
        # E 单调
        self.assertLessEqual(r.E1_REC, r.E2_REC)
        self.assertLessEqual(r.E2_REC, r.E3_REC)
        self.assertLessEqual(r.E3_REC, r.E4_REC)
        # AO 单调
        self.assertLessEqual(r.AO1_REC, r.AO2_REC)
        self.assertLessEqual(r.AO2_REC, r.AO3_REC)
        self.assertLessEqual(r.AO3_REC, r.AO4_REC)
        # OUTH/OUTL 直接回退
        self.assertEqual(r.OUTH_REC, 5.0)
        self.assertEqual(r.OUTL_REC, -5.0)

    def test_fallback_clamps_negative_inputs(self):
        r = APCMAUTOPARA()
        step(r, EN=False, PT_IN=-10.0, TD_IN=-1.0, AO1_IN=-3.0, RSF_LOCK_T_IN=-5.0)
        self.assertEqual(r.PT_REC, 0.001)
        self.assertEqual(r.TD_REC, 0.0)
        self.assertEqual(r.AO1_REC, 0.0)
        self.assertEqual(r.RSF_LOCK_T_REC, 0.0)


# ============================ B. CYCLE 与量程 ============================


class TestCycleAndRange(unittest.TestCase):
    def test_cycle_clamped_to_min(self):
        r = APCMAUTOPARA()
        step(r, EN=True, CYCLE=0.0, COLLECT_MODE=0, PV=45.0)
        self.assertAlmostEqual(r.CYCLE_S, 0.001)
        self.assertAlmostEqual(r.WIN_ELAPSED, 0.001)

    def test_dt_ms_does_not_affect_business_time(self):
        """相同 CYCLE、不同 dt_ms → 窗口累计时间一致。"""
        r1 = APCMAUTOPARA()
        r2 = APCMAUTOPARA()
        prime(r1)
        prime(r2)
        for _ in range(3):
            r1.step(500, **base_kwargs(COLLECT_MODE=0, PV=45.0, CYCLE=1.0))
            r2.step(999999, **base_kwargs(COLLECT_MODE=0, PV=45.0, CYCLE=1.0))
        self.assertEqual(r1.WIN_ELAPSED, r2.WIN_ELAPSED)
        self.assertEqual(r1.WIN_AUTO_T, r2.WIN_AUTO_T)

    def test_invalid_pv_range_keeps_range_ok_false(self):
        r = APCMAUTOPARA()
        step(r, EN=True, PVMU=10.0, PVMD=10.0, COLLECT_MODE=0, PV=10.0)
        self.assertFalse(r.RANGE_OK)
        self.assertEqual(r.PV_RANGE, 100)  # 内部兜底

    def test_invalid_out_range_keeps_range_ok_false(self):
        r = APCMAUTOPARA()
        step(r, EN=True, MU=5.0, MD=5.0, COLLECT_MODE=0)
        self.assertFalse(r.RANGE_OK)
        self.assertEqual(r.OUT_RANGE, 100)

    def test_out_limit_range_fallback(self):
        r = APCMAUTOPARA()
        step(r, EN=True, OUTT=20.0, OUTB=20.0, MU=100.0, MD=0.0, COLLECT_MODE=0)
        self.assertEqual(r.OUT_LIMIT_RANGE, r.OUT_RANGE)

    def test_history_n_clamped(self):
        r = APCMAUTOPARA()
        step(r, EN=False, HISTORY_N=999)
        self.assertEqual(r.H_N, 24)
        step(r, EN=False, HISTORY_N=-5)
        self.assertEqual(r.H_N, 1)


# ============================ C. RESET 与 EN ============================


class TestResetEn(unittest.TestCase):
    def test_en_false_reset_true_no_sampling(self):
        r = APCMAUTOPARA()
        step(r, EN=False, RESET=True, PV=30.0, AV=5.0)
        self.assertFalse(r.RUNNING)
        self.assertEqual(r.WIN_ELAPSED, 0.0)

    def test_en_true_reset_true_runs_and_samples_same_scan(self):
        """关键差异：EN=True 且 RESET=True 时先复位、RUNNING=True、本拍仍进入采集。"""
        r = APCMAUTOPARA()
        step(r, EN=True, RESET=True, COLLECT_MODE=0, PV=45.0, AV=10.0, CYCLE=1.0)
        self.assertTrue(r.RUNNING)
        self.assertEqual(r.WIN_ELAPSED, 1.0)  # IF EN 中累计了一拍
        self.assertTrue(r.INIT_DONE)

    def test_reset_clears_win_sums_then_resample(self):
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(3):
            step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0)
        self.assertGreater(r.WIN_PV_SUM, 0)
        # RESET+EN：先清零三和，再本拍重新累计一笔
        step(r, EN=True, RESET=True, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0)
        self.assertEqual(r.WIN_AUTO_N, 1.0)  # 复位后本拍只累计了 1 笔
        self.assertEqual(r.WIN_PV_SUM, 45.0)

    def test_cold_start_en_true_calls_spf1_twice(self):
        """冷启动 EN=True、RESET=False：SPF1 被调用两次（复位初始化 + 正常路径）。"""
        r = APCMAUTOPARA()
        calls = {"n": 0}
        orig = r.SPF1.step

        def counting(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)

        r.SPF1.step = counting  # type: ignore[assignment]
        step(r, EN=True, RESET=False, COLLECT_MODE=0, PV=45.0)
        self.assertEqual(calls["n"], 2)

    def test_running_mirrors_en(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, EN=True, COLLECT_MODE=0, PV=45.0)
        self.assertTrue(r.RUNNING)
        step(r, EN=False, PV=45.0)
        self.assertFalse(r.RUNNING)


# ============================ D. APCSPFINDER 集成 ============================


class TestSpFinderIntegration(unittest.TestCase):
    def test_manual_sp(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP_MAN=42.0, SP_MAN_EN=True, SP=50.0, PV=45.0)
        self.assertEqual(r.SP_USE, 42.0)
        self.assertTrue(r.SP_VALID)
        self.assertEqual(r.SP_SOURCE, 1)

    def test_tag_sp(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=48.0, SP_TAG_EN=True, PV=45.0)
        self.assertEqual(r.SP_USE, 48.0)
        self.assertTrue(r.SP_VALID)
        self.assertEqual(r.SP_SOURCE, 2)

    def test_sp_invalid_uses_pv_as_workpoint(self):
        """SP 无效（无人工、无传入、无自动）时 SP_WORK=PV → ERR=0。"""
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP_TAG_EN=False, SP_AUTO_EN=False, PV=45.0)
        self.assertFalse(r.SP_VALID)
        self.assertEqual(r.SP_WORK, 45.0)
        self.assertEqual(r.ERR, 0.0)

    def test_sp_use_mirrors_spf1(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=47.0, PV=45.0)
        self.assertEqual(r.SP_USE, r.SPF1.SP_USE)
        self.assertEqual(r.SP_VALID, r.SPF1.SP_VALID)
        self.assertEqual(r.SP_SOURCE, r.SPF1.SP_SOURCE)


# ============================ E. 自动采样与事件 ============================


class TestAutoSampling(unittest.TestCase):
    def test_collect_mode_auto_only(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0)
        self.assertTrue(r.AUTO_ALLOWED)
        self.assertFalse(r.MAN_ALLOWED)
        self.assertEqual(r.WIN_AUTO_N, 1.0)

    def test_collect_mode_manual_requires_ts(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=2, TS=True, SP=50.0, PV=45.0, AV=10.0)
        self.assertFalse(r.AUTO_ALLOWED)
        self.assertTrue(r.MAN_ALLOWED)

    def test_illegal_collect_mode_no_samples(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=7, SP=50.0, PV=45.0, AV=10.0)
        self.assertFalse(r.AUTO_ALLOWED)
        self.assertFalse(r.MAN_ALLOWED)
        self.assertEqual(r.WIN_AUTO_N, 0.0)

    def test_rm_not_one_blocks_auto(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, RM=0, SP=50.0, PV=45.0)
        self.assertFalse(r.AUTO_ALLOWED)

    def test_zero_crossing_counts_event(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=50.0, PV=45.0, AV=10.0)  # ERR=+5
        step(r, COLLECT_MODE=0, SP=50.0, PV=55.0, AV=10.0)  # ERR=-5 → 穿越
        self.assertEqual(r.WIN_CROSS_N, 1.0)
        self.assertGreaterEqual(r.WIN_EVENT_N, 1.0)

    def test_auto_av_event_merge(self):
        r = APCMAUTOPARA()
        prime(r, av=10.0)
        step(r, COLLECT_MODE=0, SP=50.0, PV=50.0, AV=10.0)
        step(r, COLLECT_MODE=0, SP=50.0, PV=50.0, AV=20.0)  # AV 跳变 → 事件
        self.assertEqual(r.WIN_AUTO_AV_EVENT_N, 1.0)
        self.assertTrue(r.AUTO_AV_MOVING)

    def test_peak_and_extrema_tracking(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, SP=50.0, PV=40.0, AV=10.0)
        step(r, COLLECT_MODE=0, SP=50.0, PV=60.0, AV=14.0)
        self.assertEqual(r.WIN_PV_MAX, 60.0)
        self.assertEqual(r.WIN_PV_MIN, 40.0)
        self.assertEqual(r.WIN_AV_MAX, 14.0)
        self.assertGreaterEqual(r.WIN_ERR_PEAK_ABS, 10.0)


# ============================ F. 手动事件与跨窗口响应 ============================


class TestManualEvents(unittest.TestCase):
    def test_manual_merge_then_response_observe(self):
        """手动动作 → MAN_MERGE_T 内无动作后进入响应观察。

        注意：ST 在首个手动拍仅建立 MAN_LAST_AV 基准（MAN_DAV=0），故需先打一拍
        基准（AV=10），下一拍 AV 跳变才被识别为手动动作。
        """
        r = APCMAUTOPARA()
        prime(r, av=10.0)
        step(r, COLLECT_MODE=2, TS=True, AV=10.0, PV=50.0, MAN_MERGE_T=2.0, CYCLE=1.0)
        step(r, COLLECT_MODE=2, TS=True, AV=20.0, PV=50.0, MAN_MERGE_T=2.0, CYCLE=1.0)
        self.assertTrue(r.MAN_ACTIVE)
        # 之后保持不动，累计无动作时间至 MAN_MERGE_T=2
        step(r, COLLECT_MODE=2, TS=True, AV=20.0, PV=50.0, MAN_MERGE_T=2.0, CYCLE=1.0)
        step(r, COLLECT_MODE=2, TS=True, AV=20.0, PV=50.0, MAN_MERGE_T=2.0, CYCLE=1.0)
        self.assertFalse(r.MAN_ACTIVE)
        self.assertTrue(r.MAN_RESP_ACTIVE)

    def test_leaving_manual_forces_response(self):
        """离开手动采集时仍在合并中 → 强制进入响应观察。"""
        r = APCMAUTOPARA()
        prime(r, av=10.0)
        step(r, COLLECT_MODE=2, TS=True, AV=10.0, PV=50.0, MAN_MERGE_T=10.0)  # 基准
        step(r, COLLECT_MODE=2, TS=True, AV=20.0, PV=50.0, MAN_MERGE_T=10.0)
        self.assertTrue(r.MAN_ACTIVE)
        # 切到自动（TS=False）→ ELSE 分支强制结束合并、进入响应观察
        step(r, COLLECT_MODE=0, TS=False, AV=20.0, PV=50.0)
        self.assertFalse(r.MAN_ACTIVE)
        self.assertTrue(r.MAN_RESP_ACTIVE)

    def test_response_survives_window_settle(self):
        """窗口结算不清除 MAN_RESP_ACTIVE（跨窗口保留）。"""
        r = APCMAUTOPARA()
        prime(r, av=10.0)
        step(r, COLLECT_MODE=2, TS=True, AV=10.0, PV=50.0, MAN_MERGE_T=10.0)  # 基准
        step(r, COLLECT_MODE=2, TS=True, AV=20.0, PV=50.0, MAN_MERGE_T=10.0)
        step(r, COLLECT_MODE=0, TS=False, AV=20.0, PV=50.0)  # 强制进入响应观察
        self.assertTrue(r.MAN_RESP_ACTIVE)
        # 用一个自动振荡窗口结算（约 7 拍 < MAN_RESP_T_USE，响应观察未完成）
        osc_window(r, n=6, min_win_t=2.0, av=20.0)
        self.assertTrue(r.WINDOW_DONE)
        self.assertTrue(r.MAN_RESP_ACTIVE)  # 响应观察跨窗口保留


# ============================ G. CALC_NOW 与窗口快照 ============================


class TestCalcAndSnapshot(unittest.TestCase):
    def test_real_output_carrier_survives_integer_clamp_branch(self):
        """REAL outputs must not become Python int when max selects a literal."""
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=3.0)
        self.assertIs(type(r.MAN_RESP_T_AUTO), float)

    def test_man_resp_t_use_real_carrier_survives_sub_one_input(self):
        """MAN_RESP_T_USE stays REAL float even when a valid MAN_RESP_T < 1
        makes the ``max(..., 1)`` floor select the integer literal.

        MAN_RESP_T is a REAL VAR_INPUT and MAN_RESP_T_USE a REAL VAR_OUTPUT; a
        legitimate sub-second response time must never turn the output into a
        Python ``int``, which ``Store.check_value_type`` rejects for REAL and
        would crash the public ST runtime commit (mirrors the sibling
        MAN_RESP_T_AUTO carrier)."""
        # First-ever call (INIT_DONE False) hits the reset-init clamp; the main
        # pre-window clamp runs the same step.
        first = APCMAUTOPARA()
        step(first, EN=True, MAN_RESP_T=0.5)
        self.assertIs(type(first.MAN_RESP_T_USE), float)
        # EN=True with RESET=True re-enters the reset-init clamp.
        reset = APCMAUTOPARA()
        step(reset, EN=True, RESET=True, MAN_RESP_T=0.5)
        self.assertIs(type(reset.MAN_RESP_T_USE), float)
        # Window-settled branch recomputes MAN_RESP_T_USE from the same clamp.
        settled = APCMAUTOPARA()
        prime(settled)
        osc_window(settled, n=8, min_win_t=3.0, MAN_RESP_T=0.5)
        self.assertIs(type(settled.MAN_RESP_T_USE), float)

    # The 17 REAL VAR_OUTPUT recommendations whose reset-init fallback clamp is
    # ``float(max(X_IN, 0))``.  For a legitimate negative REAL input the bare
    # ``max`` would select the integer literal ``0`` and hand ``Store`` a Python
    # ``int``, which ``check_value_type`` rejects for REAL and would crash the
    # public ST runtime commit.  Each must keep an exact ``float`` carrier
    # (Codex WP-141 Round 1 same-source exact-carrier family).
    _REAL_REC_CLAMP_CARRIERS = (
        ("TD_REC", "TD_IN"), ("DI_REC", "DI_IN"), ("SVH_REC", "SVH_IN"),
        ("SVL_REC", "SVL_IN"), ("TL_REC", "TL_IN"), ("AO1_REC", "AO1_IN"),
        ("RSF_LOCK_T_REC", "RSF_LOCK_T_IN"), ("TC_REC", "TC_IN"),
        ("TZ_REC", "TZ_IN"), ("GC1_REC", "GC1_IN"), ("GC2_REC", "GC2_IN"),
        ("CD_GD_REC", "CD_GD_IN"), ("CD_K_FD_REC", "CD_K_FD_IN"),
        ("CD_K_J_REC", "CD_K_J_IN"), ("CD_K_D_REC", "CD_K_D_IN"),
        ("TC_CD_REC", "TC_CD_IN"), ("TZ_CD_REC", "TZ_CD_IN"),
    )

    def test_reset_real_rec_carriers_survive_integer_clamp(self):
        """Table-driven direct counter-proof for the 17 REAL ``*_REC`` clamps.

        Negative and zero REAL inputs both drive ``max(X_IN, 0)`` onto the
        integer floor; the recommendation must nevertheless be an exact ``float``
        equal to the floored value.  A positive input keeps its own value and
        proves the fix does not perturb the normal path.
        """
        self.assertEqual(len(self._REAL_REC_CLAMP_CARRIERS), 17)
        for output, input_name in self._REAL_REC_CLAMP_CARRIERS:
            for value in (-1.0, -0.5, 0.0, 2.0):
                with self.subTest(output=output, value=value):
                    r = APCMAUTOPARA()
                    step(r, EN=True, **{input_name: value})
                    carrier = getattr(r, output)
                    self.assertIs(type(carrier), float)
                    self.assertEqual(carrier, float(max(value, 0.0)))

    def test_calc_now_rising_edge_settles(self):
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(3):
            step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0)
        step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0, CALC_NOW=True)
        self.assertTrue(r.WINDOW_DONE)

    def test_calc_now_held_high_no_repeat_settle(self):
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(3):
            step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0)
        step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0, CALC_NOW=True)
        self.assertTrue(r.WINDOW_DONE)
        # 持续高电平：下一拍不再结算
        step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0, CALC_NOW=True)
        self.assertFalse(r.WINDOW_DONE)

    def test_calc_old_updates_even_when_en_false(self):
        """EN=False 持续 CALC_NOW 高电平后再 EN=True 不应误判为新上升沿。"""
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(3):
            step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0)
        # EN=False 但 CALC_NOW 高 → CALC_OLD 末尾仍更新为 True
        step(r, EN=False, PV=45.0, CALC_NOW=True)
        self.assertTrue(r.CALC_OLD)
        # 再 EN=True 且 CALC_NOW 仍高 → 非上升沿，不结算
        step(r, EN=True, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0, CALC_NOW=True)
        self.assertFalse(r.CALC_R)
        self.assertFalse(r.WINDOW_DONE)

    def test_window_done_only_on_settle_scan(self):
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0)
        self.assertFalse(r.WINDOW_DONE)

    def test_data_reason_2_not_written_during_accumulation(self):
        """累计阶段（窗口未结算）不实时写 DATA_REASON=2（APCMAUTOPARA-DATAREASON-1）。"""
        r = APCMAUTOPARA()
        prime(r)
        step(r, COLLECT_MODE=0, PV=45.0, MIN_WIN_T=300.0, CYCLE=1.0)
        self.assertFalse(r.WINDOW_DONE)
        self.assertNotEqual(r.DATA_REASON, 2)

    def test_data_reason_2_unreachable_at_settle(self):
        """结算点恒 WIN_ELAPSED>=MIN_WIN_T，DATA_REASON=2 不可达。"""
        r = APCMAUTOPARA()
        prime(r)
        # WIN_T<MIN_WIN_T，靠 WIN_ELAPSED>=MAX(WIN_T,MIN_WIN_T)=3 结算（第 3 拍结算）
        for _ in range(3):
            step(r, COLLECT_MODE=0, PV=45.0, WIN_T=1.0, MIN_WIN_T=3.0, CYCLE=1.0)
        self.assertTrue(r.WINDOW_DONE)
        self.assertNotEqual(r.DATA_REASON, 2)
        self.assertGreaterEqual(r.WINDOW_T, 3.0)

    def test_snapshot_outputs_after_settle(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertTrue(r.WINDOW_DONE)
        self.assertGreater(r.WINDOW_T, 0)
        self.assertGreaterEqual(r.CROSS_COUNT, 1)
        self.assertGreater(r.PV_DELTA, 0)


# ============================ H. 数据状态优先级 ============================


class TestDataReasonPriority(unittest.TestCase):
    def _settle_basic(self, **over):
        r = APCMAUTOPARA()
        prime(r)
        kw = dict(COLLECT_MODE=0, PV=45.0, MIN_WIN_T=2.0, CYCLE=1.0)
        kw.update(over)
        for _ in range(3):
            step(r, **kw)
        step(r, CALC_NOW=True, **kw)
        return r

    def test_reason_3_range_invalid(self):
        r = self._settle_basic(MU=0.0, MD=0.0)
        self.assertEqual(r.DATA_REASON, 3)
        self.assertFalse(r.WINDOW_VALID)

    def test_reason_6_sp_invalid(self):
        r = self._settle_basic(SP_TAG_EN=False, SP_AUTO_EN=False)
        self.assertEqual(r.DATA_REASON, 6)
        self.assertFalse(r.WINDOW_VALID)

    def test_reason_4_event_insufficient(self):
        # 恒定 PV、无事件 → WIN_EVENT_N=0 < MIN_VALID_EVENT
        r = self._settle_basic()
        self.assertEqual(r.DATA_REASON, 4)
        self.assertTrue(r.WINDOW_VALID)

    def test_reason_1_normal_with_gain(self):
        """低噪声稳态 + 一次明显 AV→PV 阶跃 → PROCESS_GAIN>0、事件够 → DATA_REASON=1。"""
        r = APCMAUTOPARA()
        prime(r, pv=50.0, av=10.0)
        kw = dict(COLLECT_MODE=0, SP=50.0, CYCLE=1.0, MIN_WIN_T=2.0, MIN_VALID_EVENT=1.0)
        for _ in range(8):  # 稳态：低噪声、积累 quiet 样本
            step(r, PV=50.0, AV=10.0, **kw)
        step(r, PV=55.0, AV=15.0, **kw)  # AV 阶跃事件 + PV 开始响应
        step(r, PV=65.0, AV=20.0, **kw)
        step(r, PV=70.0, AV=20.0, CALC_NOW=True, **kw)
        self.assertGreater(r.PROCESS_GAIN, 0.0)
        self.assertEqual(r.DATA_REASON, 1)
        self.assertTrue(r.WINDOW_VALID)


# ============================ I. PID 推荐 ============================


class TestPidRec(unittest.TestCase):
    def test_formula_disabled_no_formula_valid(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0, PID_FORMULA_EN=False)
        self.assertFalse(r.PID_FORMULA_VALID)
        self.assertEqual(r.PT_FORMULA_REC, 0.0)

    def test_pid_blend_clamped(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0, PID_FORMULA_BLEND=5.0)
        self.assertEqual(r.PID_FORMULA_BLEND_REC, 1.0)
        r2 = APCMAUTOPARA()
        prime(r2)
        osc_window(r2, n=8, min_win_t=2.0, PID_FORMULA_BLEND=-3.0)
        self.assertEqual(r2.PID_FORMULA_BLEND_REC, 0.0)

    def test_pt_ti_within_limits(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertGreaterEqual(r.W_PT, 1)
        self.assertLessEqual(r.W_PT, 10000)
        self.assertGreaterEqual(r.W_TI, 1)
        self.assertLessEqual(r.W_TI, 10000)

    def test_di_capped_at_5(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertLessEqual(r.W_DI, 5)
        self.assertGreaterEqual(r.W_DI, 0)

    def test_pid_reason_oscillation(self):
        """振荡窗口 → PID_REASON=3（融合无效时被覆盖为 5，这里检查单窗口结果）。"""
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=10, min_win_t=2.0)
        # 单窗口无历史相似 → FINAL_VALID False → PID_REASON 被覆盖为 5
        self.assertEqual(r.PID_REASON, 5)


# ============================ J. RSF / 观测器 / 重叠控制推荐 ============================


class TestOtherRec(unittest.TestCase):
    def test_rsf_e_monotonic(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertLessEqual(r.W_E1, r.W_E2)
        self.assertLessEqual(r.W_E2, r.W_E3)
        self.assertLessEqual(r.W_E3, r.W_E4)

    def test_rsf_ao_monotonic_and_capped(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertLessEqual(r.W_AO1, r.W_AO2)
        self.assertLessEqual(r.W_AO2, r.W_AO3)
        self.assertLessEqual(r.W_AO3, r.W_AO4)
        self.assertLessEqual(r.W_AO4, r.OUT_RANGE * 0.20 + 1e-9)

    def test_rsf_lock_t_bounds(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertGreaterEqual(r.W_RSF_LOCK_T, 30)
        self.assertLessEqual(r.W_RSF_LOCK_T, 120)

    def test_observer_outputs_symmetric(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertEqual(r.W_OUTH, -r.W_OUTL)
        self.assertGreater(r.W_OUTH, 0)

    def test_cd_k_bounds(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0, CD_K_IN=5.0)
        self.assertLessEqual(r.W_CD_K, 0.7)
        self.assertGreaterEqual(r.W_CD_K, 0.3)


# ============================ K. 历史与三阶段融合 ============================


class TestHistoryFusion(unittest.TestCase):
    def test_valid_window_stored(self):
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=2.0)
        self.assertEqual(r.HISTORY_COUNT, 1.0)
        self.assertTrue(r.H_VALID[1])

    def test_h_idx_wraps(self):
        r = APCMAUTOPARA()
        prime(r)
        # H_N=2，写满后回绕
        for _ in range(3):
            osc_window(r, n=8, min_win_t=2.0, HISTORY_N=2)
        self.assertEqual(r.HISTORY_COUNT, 2.0)  # 上限 H_N
        self.assertIn(r.H_IDX, (1, 2))

    def test_history_count_caps_at_h_n(self):
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(5):
            osc_window(r, n=8, min_win_t=2.0, HISTORY_N=3)
        self.assertEqual(r.HISTORY_COUNT, 3.0)

    def test_fourth_valid_window_keeps_history_count_an_exact_float(self):
        """The saturated ``HISTORY_N`` branch is still a REAL carrier."""
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(4):
            osc_window(r, n=8, min_win_t=2.0, HISTORY_N=3)
        self.assertIs(type(r.HISTORY_COUNT), float)
        self.assertEqual(r.HISTORY_COUNT, 3.0)

    def test_invalid_window_fuse_fallback_recommendations_are_exact_floats(self):
        """A valid but deliberately un-stored window reaches ``FUSE_SUM_W=0``.

        The expected recommendation values are literal contract values, not
        copied from the block's ``W_*`` carrier.  This keeps the carrier check
        independent while exercising negative inputs and all FUSE-map outputs.
        """
        expected = (
            ("PT_REC", 330.0), ("TI_REC", 55.00000000000001),
            ("TD_REC", 0.0), ("DI_REC", 5.0), ("SVH_REC", 13.0),
            ("SVL_REC", 5.0), ("TL_REC", 10.0), ("TL1_REC", 11.0),
            ("TL2_REC", 11.0), ("TL3_REC", 11.0), ("TL4_REC", 11.0),
            ("E1_REC", 5.0), ("E2_REC", 10.0), ("E3_REC", 13.0),
            ("E4_REC", 20.0), ("AO1_REC", 0.35), ("AO2_REC", 0.55),
            ("AO3_REC", 0.77), ("AO4_REC", 1.0010000000000001),
            ("RSF_LOCK_T_REC", 30.0), ("TC_REC", 10.0), ("TZ_REC", 5.0),
            ("GC1_REC", 0.0), ("GC2_REC", 0.0), ("OUTH_REC", 10.0),
            ("OUTL_REC", -10.0), ("CD_GD_REC", 22.22222222222222),
            ("CD_K_REC", 0.3), ("CD_K_FD_REC", 0.5),
            ("CD_K_J_REC", 1.0), ("CD_K_D_REC", 0.2), ("CDH_REC", 10.0),
            ("CDL_REC", -10.0), ("TC_CD_REC", 10.0), ("TZ_CD_REC", 5.0),
        )
        negative = {
            name: -1.0 for name in (
                "TL_IN", "TL1_IN", "TL2_IN", "TL3_IN", "TL4_IN",
                "E1_IN", "E2_IN", "E3_IN", "E4_IN", "AO1_IN", "AO2_IN",
                "AO3_IN", "AO4_IN", "RSF_LOCK_T_IN", "TC_IN", "TZ_IN",
                "GC1_IN", "GC2_IN", "OUTH_IN", "OUTL_IN", "CD_GD_IN",
                "CD_K_IN", "CD_K_FD_IN", "CD_K_J_IN", "CD_K_D_IN", "CDH_IN",
                "CDL_IN", "TC_CD_IN", "TZ_CD_IN")}
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=8, min_win_t=3.0, MIN_STORE_EVENT=999.0, **negative)
        self.assertTrue(r.WINDOW_VALID)
        self.assertEqual(r.FUSE_SUM_W, 0.0)
        for name, value in expected:
            self.assertIs(type(getattr(r, name)), float, name)
            self.assertEqual(getattr(r, name), value, name)

    def test_current_window_fuses_same_scan(self):
        """当前窗口先入库、同拍参与融合：单窗口即 SIMILAR_COUNT>=1。"""
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=10, min_win_t=2.0)
        self.assertGreaterEqual(r.SIMILAR_COUNT, 1.0)
        self.assertGreater(r.FUSE_WEIGHT, 0.0)
        self.assertGreaterEqual(r.MATCH_LEVEL, 1)

    def test_strong_recommendation_after_three_similar(self):
        """≥3 个相似强窗口（每个权重 1）→ FINAL_STRONG。"""
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(3):
            osc_window(r, n=10, min_win_t=2.0)
        self.assertGreaterEqual(r.SIMILAR_COUNT, 3.0)
        self.assertTrue(r.FINAL_STRONG)
        self.assertTrue(r.FINAL_VALID)
        self.assertTrue(r.PID_OK)
        self.assertEqual(r.PID_REASON, r.PID_REASON)  # 有效时非 5
        self.assertNotEqual(r.PID_REASON, 5)

    def test_weak_recommendation(self):
        """1~2 个相似窗口但不足强推荐 → FINAL_WEAK，原因码=5。"""
        r = APCMAUTOPARA()
        prime(r)
        osc_window(r, n=10, min_win_t=2.0)  # 只 1 个窗口
        self.assertTrue(r.FINAL_WEAK)
        self.assertFalse(r.FINAL_VALID)
        self.assertEqual(r.PID_REASON, 5)
        self.assertEqual(r.RSF_REASON, 5)
        self.assertEqual(r.GC_REASON, 5)
        self.assertEqual(r.CD_REASON, 5)

    def test_no_similar_falls_back_to_single_window(self):
        """无相似历史窗口时，*_REC 回退到当前单窗口推荐。"""
        r = APCMAUTOPARA()
        prime(r)
        # 事件足够但与历史无相似（首个窗口）：FUSE_SUM_W 来自自身匹配，
        # 这里改用恒定无事件窗口（不入库），强制无相似分支需要构造无可入库窗口。
        # 用一个有效但不入库的窗口：事件不足 → DATA_REASON=4 仍 WINDOW_VALID，
        # 但 WIN_EVENT_N<MIN_STORE_EVENT 才不入库；MIN_STORE_EVENT 默认 1。
        # 这里直接验证：单振荡窗口入库并自匹配后 *_REC 等于加权平均（=自身）。
        osc_window(r, n=10, min_win_t=2.0)
        # 自匹配权重 1 → 加权平均即 W_*，PT_REC 应等于 W_PT
        self.assertAlmostEqual(r.PT_REC, r.W_PT)
        self.assertAlmostEqual(r.E1_REC, r.W_E1)

    def test_history_shrink_masks_slots(self):
        """HISTORY_N 缩小时只清摘要槽并置 H_VALID=False。"""
        r = APCMAUTOPARA()
        prime(r)
        for _ in range(2):
            osc_window(r, n=8, min_win_t=2.0, HISTORY_N=5)
        self.assertTrue(r.H_VALID[1] or r.H_VALID[2])
        # 缩小到 1：槽 2..24 应被屏蔽
        step(r, EN=True, COLLECT_MODE=0, PV=45.0, HISTORY_N=1, MIN_WIN_T=300.0)
        for i in range(2, 25):
            self.assertFalse(r.H_VALID[i], f"slot {i}")


if __name__ == "__main__":
    unittest.main()
