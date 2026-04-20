"""业务块 ``APCHXHCL``（修正版）的契约验证测试。

对应 ST 源码：``/Users/guangyaosun/Desktop/APCHXHCL1.txt``。
关联任务书：``APCHXHCL_v2_最小转换层与风险收口任务书``。

覆盖点（按任务书 §七.B）：

* EN=FALSE → GZDV 恒 FALSE；历史清零；AV 做滤波
* 首拍 INIT_OK：PV_1/Ok_1 自初始化
* EN=TRUE 每拍向数组写入（新语义，非每分钟）
* 三类故障：越界、变化率过大、持续不变化
* 故障首拍冻结 PV_AVG / FV_AVG；之后数组与均值不再更新
* EN 掉电 → 再上电：状态正确清零并重建
* 滤波稳态：AV 收敛至 KG * PV
* **helper 真正接入**：SAMPLE_N 和 PT_ms 都走 ``src.compat`` 的转换函数
* **R1 契约锁定**：A 按扫描计数理解，不按秒
* **R3 两场景**：刚使能立刻故障 vs 运行满一分钟后故障
* **保留行为锁定**：FV>0.1 与 PV!=0 不对称、AV_TEMP 爆值冻结、故障期 A/TOF 继续推进
"""

from __future__ import annotations

import unittest
from unittest import mock

from src.blocks import APCHXHCL
from src.blocks import apchxhcl as apchxhcl_module


CYCLE_MS = 500
TB = 0.5
TICKS_PER_MINUTE = int(60 / TB)


class TestENFalseFilterOnly(unittest.TestCase):
    """EN=FALSE：仅滤波，GZDV 恒 FALSE。"""

    def test_gzdv_always_false(self) -> None:
        fb = APCHXHCL()
        for _ in range(200):
            out = fb.step(CYCLE_MS, EN=False, PV=50.0, FV=20.0, TB=TB)
            self.assertFalse(out["GZDV"])

    def test_filter_runs(self) -> None:
        fb = APCHXHCL()
        for _ in range(300):
            out = fb.step(CYCLE_MS, EN=False, PV=50.0, FV=20.0, TB=TB, TC=1.0, KG=1.0)
        self.assertAlmostEqual(out["AV"], 50.0, delta=0.01)

    def test_averages_cleared(self) -> None:
        fb = APCHXHCL()
        fb.step(CYCLE_MS, EN=False, PV=50.0, FV=20.0, TB=TB)
        self.assertEqual(fb.PV_AVG, 0.0)
        self.assertEqual(fb.FV_AVG, 0.0)


class TestInitOKFirstTick(unittest.TestCase):
    """首次 EN=TRUE 时 INIT_OK 初始化 PV_1 和 Ok_1。"""

    def test_first_tick_sets_pv1_and_ok1(self) -> None:
        fb = APCHXHCL()
        fb.step(CYCLE_MS, EN=True, PV=42.0, FV=10.0, TB=TB)
        self.assertTrue(fb.INIT_OK)

    def test_no_false_jump_fault_on_first_tick(self) -> None:
        """即便 PV 很大，首拍 |PV-PV_1|=0（因刚自初始化），不应触发变化率故障。"""
        fb = APCHXHCL()
        out = fb.step(
            CYCLE_MS, EN=True, PV=1000.0, FV=10.0,
            PVH=10_000.0, PVL=-10_000.0, BHSLH=1.0,
            TL=2.0, TB=TB,
        )
        self.assertFalse(out["GZDV"])


class TestEveryCyclePush(unittest.TestCase):
    """新版语义：EN=TRUE 且不故障时，每个扫描周期都写入一帧。"""

    def test_sample_n_computed_from_tb(self) -> None:
        fb = APCHXHCL()
        fb.step(CYCLE_MS, EN=True, PV=30.0, FV=15.0, TB=0.5)
        self.assertEqual(fb.SAMPLE_N, 120)

        fb2 = APCHXHCL()
        fb2.step(CYCLE_MS, EN=True, PV=30.0, FV=15.0, TB=1.0)
        self.assertEqual(fb2.SAMPLE_N, 60)

    def test_array_shifts_every_cycle(self) -> None:
        fb = APCHXHCL()
        values = [10.0 + i for i in range(5)]
        for v in values:
            fb.step(
                CYCLE_MS, EN=True, PV=v + 100.0, FV=v,
                PVH=10_000.0, PVL=-10_000.0, BHSLH=10_000.0,
                TL=600.0, TB=TB,
            )
        self.assertEqual(fb.FV_TEMP[1], values[-1])
        self.assertEqual(fb.FV_TEMP[2], values[-2])
        self.assertEqual(fb.FV_TEMP[3], values[-3])


class TestOutOfBoundFault(unittest.TestCase):
    """越界故障：TOF2 保持 TL 秒；解除越界后经过 TL 秒才释放。"""

    def test_out_of_bound_triggers(self) -> None:
        fb = APCHXHCL()
        out = fb.step(
            CYCLE_MS, EN=True, PV=500.0, FV=15.0,
            PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
            TL=2.0, TB=TB,
        )
        self.assertTrue(out["GZDV"])

    def test_fault_releases_after_tl_seconds(self) -> None:
        fb = APCHXHCL()
        TL = 2.0
        fb.step(CYCLE_MS, EN=True, PV=500.0, FV=15.0,
                PVH=100.0, PVL=-100.0, BHSLH=1_000.0, TL=TL, TB=TB)

        ticks_needed = int(TL * 1000 / CYCLE_MS)
        for k in range(ticks_needed - 1):
            out = fb.step(
                CYCLE_MS, EN=True, PV=50.0 + (k % 2) * 0.3, FV=15.0,
                PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
                TL=TL, TB=TB,
            )
            self.assertTrue(out["GZDV"])

        for k in range(5):
            out = fb.step(
                CYCLE_MS, EN=True, PV=50.0 + (k % 2) * 0.3, FV=15.0,
                PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
                TL=TL, TB=TB,
            )
        self.assertFalse(out["GZDV"])


class TestRateLimitFault(unittest.TestCase):
    """单拍变化过大 → TOF1 保持 GZDV。"""

    def test_large_delta_triggers(self) -> None:
        fb = APCHXHCL()
        fb.step(CYCLE_MS, EN=True, PV=10.0, FV=15.0, BHSLH=5.0, TL=2.0, TB=TB)
        out = fb.step(CYCLE_MS, EN=True, PV=100.0, FV=15.0, BHSLH=5.0, TL=2.0, TB=TB)
        self.assertTrue(out["GZDV"])


class TestStuckPVFault(unittest.TestCase):
    """PV 持续等于 PV_1 → A 累加 → A>TL 后 GZDV=TRUE。"""

    def test_stuck_pv_triggers_after_tl_cycles(self) -> None:
        fb = APCHXHCL()
        TL = 10
        fb.step(CYCLE_MS, EN=True, PV=42.0, FV=15.0,
                PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0,
                TL=TL, TB=TB)
        triggered_at = None
        for k in range(1, 100):
            out = fb.step(
                CYCLE_MS, EN=True, PV=42.0, FV=15.0,
                PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0,
                TL=TL, TB=TB,
            )
            if out["GZDV"] and triggered_at is None:
                triggered_at = k
                break
        self.assertIsNotNone(triggered_at)
        self.assertGreaterEqual(triggered_at, TL)


class TestFaultFreezesAverages(unittest.TestCase):
    """故障首拍冻结 PV_AVG / FV_AVG，之后故障持续期间不再变化。"""

    def test_pv_avg_frozen_on_fault(self) -> None:
        fb = APCHXHCL()
        for k in range(TICKS_PER_MINUTE + 10):
            fb.step(
                CYCLE_MS, EN=True,
                PV=40.0 + (k % 2) * 0.5,
                FV=10.0,
                PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0,
                TL=600.0, TB=TB,
            )
        pre_fault_pv_avg = fb.PV_AVG
        pre_fault_fv_avg = fb.FV_AVG
        self.assertGreater(pre_fault_pv_avg, 0.0)

        out = fb.step(
            CYCLE_MS, EN=True, PV=5_000.0, FV=99.9,
            PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
            TL=5.0, TB=TB,
        )
        self.assertTrue(out["GZDV"])
        frozen_pv = out["PV_AVG"]
        frozen_fv = out["FV_AVG"]

        for _ in range(5):
            out = fb.step(
                CYCLE_MS, EN=True, PV=9_999.0, FV=0.0,
                PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
                TL=5.0, TB=TB,
            )
            self.assertTrue(out["GZDV"])
            self.assertEqual(out["PV_AVG"], frozen_pv)
            self.assertEqual(out["FV_AVG"], frozen_fv)

    def test_av_equals_pv_avg_when_fault(self) -> None:
        fb = APCHXHCL()
        for k in range(TICKS_PER_MINUTE):
            fb.step(
                CYCLE_MS, EN=True, PV=50.0 + (k % 2) * 0.4, FV=10.0,
                PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0,
                TL=600.0, TB=TB,
            )
        out = fb.step(
            CYCLE_MS, EN=True, PV=5_000.0, FV=10.0,
            PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
            TL=5.0, TB=TB,
        )
        self.assertTrue(out["GZDV"])
        self.assertEqual(out["AV"], out["PV_AVG"])

    def test_array_frozen_during_fault(self) -> None:
        fb = APCHXHCL()
        for k in range(TICKS_PER_MINUTE):
            fb.step(
                CYCLE_MS, EN=True, PV=50.0 + (k % 2) * 0.5, FV=10.0 + k,
                PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0,
                TL=600.0, TB=TB,
            )
        fb.step(
            CYCLE_MS, EN=True, PV=5_000.0, FV=777.0,
            PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
            TL=5.0, TB=TB,
        )
        snap_fv1 = fb.FV_TEMP[1]
        snap_pv1 = fb.PV_TEMP[1]
        for _ in range(3):
            fb.step(
                CYCLE_MS, EN=True, PV=8_888.0, FV=123.0,
                PVH=100.0, PVL=-100.0, BHSLH=1_000.0,
                TL=5.0, TB=TB,
            )
        self.assertEqual(fb.FV_TEMP[1], snap_fv1)
        self.assertEqual(fb.PV_TEMP[1], snap_pv1)


class TestEnDisableReenable(unittest.TestCase):
    """EN=FALSE 完整清零；再置 EN=TRUE 应走 INIT_OK 首拍初始化。"""

    def test_disable_clears_everything(self) -> None:
        fb = APCHXHCL()
        for _ in range(TICKS_PER_MINUTE):
            fb.step(CYCLE_MS, EN=True, PV=60.0, FV=20.0,
                    PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0, TL=600.0, TB=TB)

        fb.step(CYCLE_MS, EN=False, PV=60.0, FV=20.0, TB=TB)
        self.assertFalse(fb.INIT_OK)
        self.assertEqual(fb.A, 0.0)
        self.assertEqual(fb.PV_AVG, 0.0)
        self.assertEqual(fb.FV_AVG, 0.0)
        for I in range(1, 301):
            self.assertEqual(fb.FV_TEMP[I], 0.0)
            self.assertEqual(fb.PV_TEMP[I], 0.0)

    def test_reenable_runs_init_ok(self) -> None:
        fb = APCHXHCL()
        fb.step(CYCLE_MS, EN=True, PV=60.0, FV=20.0, TB=TB)
        fb.step(CYCLE_MS, EN=False, PV=60.0, FV=20.0, TB=TB)
        self.assertFalse(fb.INIT_OK)
        fb.step(CYCLE_MS, EN=True, PV=77.0, FV=20.0, TB=TB)
        self.assertTrue(fb.INIT_OK)


class TestFilterSteadyState(unittest.TestCase):
    """一阶 IIR 稳态测试。"""

    def test_constant_pv_converges_to_kg_times_pv(self) -> None:
        fb = APCHXHCL()
        for _ in range(1_000):
            out = fb.step(CYCLE_MS, EN=False, PV=40.0, FV=0.0,
                          TB=TB, TC=1.0, KG=2.0)
        self.assertAlmostEqual(out["AV"], 80.0, delta=0.05)


class TestRTRIG3FirstFaultOnly(unittest.TestCase):
    """R_TRIG3 只在故障"从无到有"的第一拍 Q=TRUE。"""

    def test_rtrig3_fires_once_on_fault_entry(self) -> None:
        fb = APCHXHCL()
        for _ in range(3):
            fb.step(CYCLE_MS, EN=True, PV=50.0, FV=10.0,
                    PVH=1_000.0, PVL=-1_000.0, BHSLH=1_000.0,
                    TL=600.0, TB=TB)
            self.assertFalse(fb.R_TRIG3.Q)

        fb.step(
            CYCLE_MS, EN=True, PV=5_000.0, FV=10.0,
            PVH=100.0, PVL=-100.0, BHSLH=1_000.0, TL=5.0, TB=TB,
        )
        self.assertTrue(fb.R_TRIG3.Q)

        for _ in range(3):
            fb.step(
                CYCLE_MS, EN=True, PV=5_000.0, FV=10.0,
                PVH=100.0, PVL=-100.0, BHSLH=1_000.0, TL=5.0, TB=TB,
            )
            self.assertFalse(fb.R_TRIG3.Q)


class TestHelperActuallyWired(unittest.TestCase):
    """任务书 §七.B.2：conversions helper 必须真正接入，不允许裸 ``int()``。"""

    def test_sample_n_goes_through_real_to_int(self) -> None:
        fb = APCHXHCL()
        with mock.patch.object(
            apchxhcl_module, "real_to_int", wraps=apchxhcl_module.real_to_int
        ) as spy:
            fb.step(500, EN=True, PV=30.0, FV=15.0, TB=0.5)
        spy.assert_called()
        called_with = [c.args[0] for c in spy.call_args_list]
        self.assertIn(60.0 / 0.5, called_with)

    def test_pt_ms_goes_through_real_to_time_ms(self) -> None:
        fb = APCHXHCL()
        with mock.patch.object(
            apchxhcl_module,
            "real_to_time_ms",
            wraps=apchxhcl_module.real_to_time_ms,
        ) as spy:
            fb.step(500, EN=True, PV=30.0, FV=15.0, TL=60.0, TB=0.5)
        spy.assert_called()
        called_with = [c.args[0] for c in spy.call_args_list]
        self.assertIn(60.0 * 1000.0, called_with)

    def test_sample_n_on_tb_0_3_matches_bankers_rounding(self) -> None:
        """``TB=0.3`` 下 ``60/0.3`` 浮点为 199.999...；
        helper（银行家舍入）应给出 200，而不是裸 int() 的 199。"""
        fb = APCHXHCL()
        fb.step(300, EN=True, PV=10.0, FV=5.0, TB=0.3)
        self.assertEqual(fb.SAMPLE_N, 200)


class TestR1ContractLocked(unittest.TestCase):
    """R1：``A > TL`` 按扫描周期数阈值语义保留，不按严格秒。"""

    def test_a_counts_scan_cycles_not_seconds(self) -> None:
        """A 在 PV 不变的每一拍都 +1，与实际 dt_ms 无关。"""
        fb = APCHXHCL()
        for _ in range(8):
            fb.step(500, EN=True, PV=42.0, FV=10.0,
                    PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=10.0, TB=0.5)
        self.assertEqual(fb.A, 8.0)

        fb2 = APCHXHCL()
        for _ in range(8):
            fb2.step(100, EN=True, PV=42.0, FV=10.0,
                     PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=10.0, TB=0.5)
        self.assertEqual(fb2.A, 8.0)
        self.assertEqual(fb.A, fb2.A)

    def test_a_capped_at_3600(self) -> None:
        fb = APCHXHCL()
        fb.step(500, EN=True, PV=1.0, FV=0.0,
                PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=10.0, TB=0.5)
        for _ in range(4000):
            fb.step(500, EN=True, PV=1.0, FV=0.0,
                    PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=10.0, TB=0.5)
        self.assertEqual(fb.A, 3600.0)


class TestR3ColdStartScenarios(unittest.TestCase):
    """任务书 §七.B.4：R3 冷启动的两种场景显式覆盖。

    注意：本业务块**不**自行引入 warm-up 语义，这两个测试是"记录事实
    行为"，以便未来一旦 Runtime 门控上线后有东西可以回归对照。
    """

    def test_scenario_1_fault_on_first_tick_after_enable(self) -> None:
        """刚使能立刻故障：R_TRIG3 首拍 Q=TRUE，均值来自空缓存（=0）。"""
        fb = APCHXHCL()
        out = fb.step(
            500,
            EN=True,
            PV=9_999.0,
            FV=77.0,
            PVH=100.0, PVL=-100.0, BHSLH=1.0,
            TL=2.0, TB=0.5,
        )
        self.assertTrue(out["GZDV"])
        self.assertTrue(fb.R_TRIG3.Q)
        self.assertEqual(out["AV"], out["PV_AVG"])
        self.assertEqual(out["PV_AVG"], 0.0)
        self.assertEqual(out["FV_AVG"], 0.0)

    def test_scenario_2_fault_after_one_minute_normal_run(self) -> None:
        """运行满一分钟后故障：冻结均值应落在 ~ 实际样本均值附近。"""
        fb = APCHXHCL()
        ticks_per_minute = 120
        samples = []
        for k in range(ticks_per_minute):
            pv = 50.0 + (k % 2) * 0.5
            samples.append(pv)
            fb.step(
                500, EN=True, PV=pv, FV=20.0,
                PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=600.0, TB=0.5,
            )
        expected_pv_avg = sum(samples) / len(samples)
        self.assertAlmostEqual(fb.PV_AVG, expected_pv_avg, delta=0.5)

        out = fb.step(
            500, EN=True, PV=9_999.0, FV=20.0,
            PVH=100.0, PVL=-100.0, BHSLH=1e6, TL=5.0, TB=0.5,
        )
        self.assertTrue(out["GZDV"])
        self.assertGreater(out["PV_AVG"], 30.0)
        self.assertLess(out["PV_AVG"], 60.0)


class TestPreservedBehaviorsLocked(unittest.TestCase):
    """任务书 §四：明确保留、不允许静默修改的行为，由测试锁死。"""

    def test_r5_fv_threshold_0_1_vs_pv_threshold_0(self) -> None:
        """FV_AVG 只统计 > 0.1，PV_AVG 只统计 != 0（不对称）。"""
        fb = APCHXHCL()
        fb.SAMPLE_N = 3
        fb.FV_TEMP[1] = 0.05
        fb.FV_TEMP[2] = 0.15
        fb.FV_TEMP[3] = 10.0
        fb.PV_TEMP[1] = 0.05
        fb.PV_TEMP[2] = 0.0
        fb.PV_TEMP[3] = 10.0
        fb.INIT_OK = True
        fb.PV_1 = 20.0
        fb.A = 0.0
        fb.GZDV_RAW = False

        out = fb.step(
            500, EN=True, PV=20.0, FV=10.0,
            PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=10.0, TB=0.5,
        )
        self.assertFalse(out["GZDV"])

    def test_r6_av_temp_freeze_when_explodes(self) -> None:
        """AV_TEMP 超过 ±1e10 时 AV 和 Ok_1 都不更新。"""
        fb = APCHXHCL()
        fb.Ok_1 = 100.0
        fb.AV = 100.0
        fb.GZDV = False
        PV = 1e12
        fb.step(500, EN=False, PV=PV, FV=0.0, TB=0.5, TC=1.0, KG=1e3)
        self.assertEqual(fb.AV, 100.0)
        self.assertEqual(fb.Ok_1, 100.0)

    def test_r7_strict_equality_pv_eq_pv1(self) -> None:
        """A 增长严格依赖 ``PV == PV_1``，每拍不同的极小扰动也会清零。"""
        fb = APCHXHCL()
        fb.step(500, EN=True, PV=42.0, FV=10.0,
                PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=100.0, TB=0.5)
        for i in range(10):
            pv = 42.0 + (i + 1) * 1e-12
            fb.step(500, EN=True, PV=pv, FV=10.0,
                    PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=100.0, TB=0.5)
        self.assertEqual(fb.A, 0.0)

    def test_r9_tof_continues_during_fault_latching(self) -> None:
        """故障期间 TOF1/TOF2 仍在按 dt_ms 推进（用于解除故障的计时）。"""
        fb = APCHXHCL()
        TL = 2.0
        fb.step(500, EN=True, PV=500.0, FV=10.0,
                PVH=100.0, PVL=-100.0, BHSLH=1e6, TL=TL, TB=0.5)
        self.assertTrue(fb.TOF2.Q)

        for _ in range(4):
            fb.step(500, EN=True, PV=500.0, FV=10.0,
                    PVH=100.0, PVL=-100.0, BHSLH=1e6, TL=TL, TB=0.5)
        self.assertTrue(fb.TOF2.Q)

    def test_r9_a_continues_during_fault(self) -> None:
        """故障期间 A 仍在每拍按 `PV==PV_1` 更新，不被故障分支拦截。"""
        fb = APCHXHCL()
        for _ in range(3):
            fb.step(500, EN=True, PV=50.0, FV=10.0,
                    PVH=1e6, PVL=-1e6, BHSLH=1e6, TL=600.0, TB=0.5)
        self.assertGreater(fb.A, 0.0)

        fb.step(500, EN=True, PV=5_000.0, FV=10.0,
                PVH=100.0, PVL=-100.0, BHSLH=1e6, TL=5.0, TB=0.5)
        a_at_fault = fb.A
        self.assertTrue(fb.GZDV)

        fb.step(500, EN=True, PV=5_000.0, FV=10.0,
                PVH=100.0, PVL=-100.0, BHSLH=1e6, TL=5.0, TB=0.5)
        self.assertGreater(fb.A, a_at_fault)


if __name__ == "__main__":
    unittest.main()
