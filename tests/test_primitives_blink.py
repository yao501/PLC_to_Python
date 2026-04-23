"""BLINK 基础原语的契约测试。

覆盖要点（按 01-basic-primitives + 00a-runtime-contract + 本轮行动单）：

1. 冷启动 ``OUT=False``
2. ``ENABLE=False`` 在 ``OUT=False`` / ``OUT=True`` 两种状态下保持输出并冻结相位
3. ``ENABLE`` 重置为 True 后从冻结点续跑（不是从 0 重启）
4. 标准完整波形（对称占空比）
5. 非对称占空比（1:4 / 4:1）
6. **单拍跨多相位**（``dt_ms > threshold`` 甚至跨多个 low/high 段）
7. 变步长扫描仍精确
8. 长周期无漂移
9. 退化情形：``TIMELOW_ms + TIMEHIGH_ms <= 0`` 时块内护栏（不推进相位）
"""

from __future__ import annotations

import unittest

from src.primitives import BLINK


DT_MS = 100


class TestBlinkInit(unittest.TestCase):
    def test_out_starts_false(self):
        """冷启动 OUT=FALSE（文档 "starts with FALSE"）。"""
        b = BLINK()
        self.assertFalse(b.OUT)
        self.assertEqual(b._elapsed_ms, 0)


class TestBlinkEnableFalseKeepsState(unittest.TestCase):
    """任务书 §4.2.2：ENABLE=False 时 OUT 保持、_elapsed_ms 冻结。"""

    def test_disabled_from_cold_start_keeps_false(self):
        """OUT=False 状态下 ENABLE=False：OUT 恒 False，_elapsed_ms 不推进。"""
        b = BLINK()
        for _ in range(20):
            out = b.step(DT_MS, ENABLE=False, TIMELOW_ms=300, TIMEHIGH_ms=300)
            self.assertFalse(out)
        self.assertEqual(b._elapsed_ms, 0)

    def test_disabled_while_high_keeps_true(self):
        """OUT=True 状态下 ENABLE=False：OUT 恒 True，_elapsed_ms 冻结。"""
        b = BLINK()
        for _ in range(3):
            b.step(DT_MS, ENABLE=True, TIMELOW_ms=300, TIMEHIGH_ms=500)
        self.assertTrue(b.OUT)
        elapsed_before = b._elapsed_ms

        for _ in range(100):
            out = b.step(DT_MS, ENABLE=False, TIMELOW_ms=300, TIMEHIGH_ms=500)
            self.assertTrue(out)
        self.assertEqual(b._elapsed_ms, elapsed_before)


class TestBlinkReenableResumesFromFrozenPhase(unittest.TestCase):
    """任务书 §4.2.3：重新 ENABLE 从冻结点续跑。"""

    def test_reenable_resumes_phase_without_reset(self):
        b = BLINK()
        outs1 = [b.step(DT_MS, ENABLE=True, TIMELOW_ms=300, TIMEHIGH_ms=300) for _ in range(2)]
        self.assertEqual(outs1, [False, False])
        elapsed_before_disable = b._elapsed_ms

        for _ in range(50):
            b.step(DT_MS, ENABLE=False, TIMELOW_ms=300, TIMEHIGH_ms=300)
        self.assertEqual(b._elapsed_ms, elapsed_before_disable)
        self.assertFalse(b.OUT)

        out = b.step(DT_MS, ENABLE=True, TIMELOW_ms=300, TIMEHIGH_ms=300)
        self.assertTrue(out)


class TestBlinkStandardWaveform(unittest.TestCase):
    """TIMELOW=300, TIMEHIGH=500, dt=100：LOW 3 拍，HIGH 5 拍。"""

    LOW_MS = 300
    HIGH_MS = 500

    def test_low_phase_then_high_phase(self):
        b = BLINK()
        outs = [b.step(DT_MS, ENABLE=True, TIMELOW_ms=self.LOW_MS, TIMEHIGH_ms=self.HIGH_MS) for _ in range(10)]
        expected = [False, False, True, True, True, True, True, False, False, False]
        self.assertEqual(outs, expected)

    def test_multiple_cycles_identical(self):
        b = BLINK()
        single = [False, False, True, True, True, True, True, False]
        full = [b.step(DT_MS, ENABLE=True, TIMELOW_ms=self.LOW_MS, TIMEHIGH_ms=self.HIGH_MS) for _ in range(10 * len(single))]
        self.assertEqual(full[: 2 * len(single)], single * 2)
        self.assertEqual(full[-len(single):], single)


class TestBlinkAsymmetry(unittest.TestCase):
    def test_short_low_long_high(self):
        """LOW=100, HIGH=400, dt=100：LOW 1 拍，HIGH 4 拍。"""
        b = BLINK()
        outs = [b.step(DT_MS, ENABLE=True, TIMELOW_ms=100, TIMEHIGH_ms=400) for _ in range(10)]
        expected = [True, True, True, True, False, True, True, True, True, False]
        self.assertEqual(outs, expected)

    def test_long_low_short_high(self):
        """LOW=400, HIGH=100, dt=100：LOW 4 拍，HIGH 1 拍。"""
        b = BLINK()
        outs = [b.step(DT_MS, ENABLE=True, TIMELOW_ms=400, TIMEHIGH_ms=100) for _ in range(10)]
        expected = [False, False, False, True, False, False, False, False, True, False]
        self.assertEqual(outs, expected)


class TestBlinkMultiPhaseCrossing(unittest.TestCase):
    """任务书 §3.2.B + §4.2.6：单拍可跨多个相位。"""

    def test_dt_equals_full_period_returns_to_start(self):
        """dt=1000, TIMELOW=TIMEHIGH=100：单拍内应翻 10 次回到起始状态。"""
        b = BLINK()
        out = b.step(1000, ENABLE=True, TIMELOW_ms=100, TIMEHIGH_ms=100)
        self.assertFalse(out)
        self.assertEqual(b._elapsed_ms, 0)

    def test_dt_spans_many_phases_waveform(self):
        """dt=450, TIMELOW=TIMEHIGH=100：每拍翻 4 或 5 次。"""
        b = BLINK()
        outs = [b.step(450, ENABLE=True, TIMELOW_ms=100, TIMEHIGH_ms=100) for _ in range(4)]
        self.assertEqual(outs, [False, True, True, False])

    def test_large_dt_duty_cycle_correct_long_run(self):
        """dt=450, TIMELOW=TIMEHIGH=100，1000 拍内 TRUE / FALSE 拍数应接近 50/50。"""
        b = BLINK()
        n_true = sum(b.step(450, ENABLE=True, TIMELOW_ms=100, TIMEHIGH_ms=100) for _ in range(1000))
        self.assertLess(abs(n_true - 500), 5)

    def test_crossing_multiple_phases_matches_cumulative_time_model(self):
        """dt=350, TIMELOW=TIMEHIGH=100（period=200）：单拍跨 3.5 个相位。
        第 n 拍结束时 OUT 应满足 ``parity(floor(n*dt / threshold)) == 1``。"""
        b = BLINK()
        dt = 350
        threshold = 100
        outs = []
        for n in range(1, 8):
            outs.append(b.step(dt, ENABLE=True, TIMELOW_ms=threshold, TIMEHIGH_ms=threshold))
            expected = ((n * dt) // threshold) % 2 == 1
            self.assertEqual(outs[-1], expected, f"tick {n}: expected {expected}, got {outs[-1]}")


class TestBlinkVariableDt(unittest.TestCase):
    def test_variable_dt_ms(self):
        """非等距 dt_ms：按累计 ms 判定相位。"""
        b = BLINK()
        dts = [50, 50, 100, 50, 150, 100, 100, 100, 100, 100]
        outs = [b.step(dt, ENABLE=True, TIMELOW_ms=300, TIMEHIGH_ms=300) for dt in dts]
        expected = [False, False, False, False, True, True, False, False, False, True]
        self.assertEqual(outs, expected)


class TestBlinkLongRunStability(unittest.TestCase):
    """00a R5：长周期累积无漂移。"""

    def test_no_drift_over_10000_ticks_symmetric(self):
        b = BLINK()
        n_true = sum(b.step(DT_MS, ENABLE=True, TIMELOW_ms=300, TIMEHIGH_ms=300) for _ in range(10000))
        self.assertEqual(n_true, 5000)

    def test_no_drift_with_non_divisible_dt(self):
        """dt=30, TIMELOW=TIMEHIGH=100：阈值非 dt 整数倍，余数保留法维持 50% 占空比。"""
        b = BLINK()
        n = 10000
        n_true = sum(b.step(30, ENABLE=True, TIMELOW_ms=100, TIMEHIGH_ms=100) for _ in range(n))
        self.assertLess(abs(n_true - n / 2), 2)


class TestBlinkDegenerateZeroPeriod(unittest.TestCase):
    """块内唯一护栏：``TIMELOW_ms + TIMEHIGH_ms <= 0`` 时不推进相位。"""

    def test_both_zero_keeps_out_and_freezes_elapsed(self):
        b = BLINK()
        for _ in range(50):
            out = b.step(DT_MS, ENABLE=True, TIMELOW_ms=0, TIMEHIGH_ms=0)
            self.assertFalse(out)
        self.assertEqual(b._elapsed_ms, 0)

    def test_both_zero_after_high_state_keeps_true(self):
        b = BLINK()
        for _ in range(3):
            b.step(DT_MS, ENABLE=True, TIMELOW_ms=300, TIMEHIGH_ms=500)
        self.assertTrue(b.OUT)
        elapsed_before = b._elapsed_ms

        for _ in range(50):
            out = b.step(DT_MS, ENABLE=True, TIMELOW_ms=0, TIMEHIGH_ms=0)
            self.assertTrue(out)
        self.assertEqual(b._elapsed_ms, elapsed_before)


if __name__ == "__main__":
    unittest.main()
