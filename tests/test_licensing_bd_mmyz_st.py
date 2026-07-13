"""BD_MMYZ_ST 周期复验测试（任务书 §12.E）。

用可控的假 validator 隔离"时间 / 周期复验状态机"语义（与哈希解耦），并配
ManualDateTimeProvider 显式控制时间。

覆盖：初始 OK=9000、失败立即重试、失败改对可恢复、成功后 10 秒周期验证、
totalSeconds%10==7 的 10000 标记、同一秒余数 7 内不重复强制验证、离开时段去标记、
ERR/YZTG/ERR_N 行为、ERR_N 回绕、同一时间点重复调用不自动推进时间。
"""

from __future__ import annotations

import unittest

from src.licensing.bd_mmyz_st import BD_MMYZ_ST
from src.licensing.providers import ManualDateTimeProvider


class FakeValidator:
    """可控返回码、可计数调用次数的假 BD_MMYZ()。"""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return self.code


# 时间常量：秒余数。ms // 1000 % 65536 % 10。
MS_SEC5 = 5000   # totalSeconds=5 → %10=5（非时段）
MS_SEC7 = 7000   # totalSeconds=7 → %10=7（时段）
MS_SEC8 = 8000   # totalSeconds=8 → %10=8（非时段）
MS_SEC17 = 17000  # totalSeconds=17 → %10=7（时段）


class TestColdStartAndFailure(unittest.TestCase):
    def test_initial_state(self):
        st = BD_MMYZ_ST(FakeValidator(0), ManualDateTimeProvider(MS_SEC5))
        self.assertEqual(st.OK, 9000)
        self.assertFalse(st.YZTG)
        self.assertEqual(st.ERR, 0)
        self.assertEqual(st.ERR_N, 0.0)

    def test_cold_start_verifies_first_scan(self):
        v = FakeValidator(0)
        st = BD_MMYZ_ST(v, ManualDateTimeProvider(MS_SEC5))
        out = st.step()
        self.assertEqual(v.calls, 1)
        self.assertEqual(out["OK"], 0)
        self.assertEqual(out["ERR"], 0)
        self.assertTrue(out["YZTG"])

    def test_failure_reverifies_every_cycle(self):
        v = FakeValidator(1011)
        st = BD_MMYZ_ST(v, ManualDateTimeProvider(MS_SEC5))
        st.step()
        st.step()
        st.step()
        self.assertEqual(v.calls, 3)  # 每拍都重验
        self.assertEqual(st.ERR, 1011)
        self.assertFalse(st.YZTG)

    def test_failure_then_fix_recovers_next_tick(self):
        v = FakeValidator(1011)
        st = BD_MMYZ_ST(v, ManualDateTimeProvider(MS_SEC5))
        st.step()
        self.assertEqual(st.ERR, 1011)
        v.code = 0
        out = st.step()
        self.assertEqual(out["ERR"], 0)
        self.assertTrue(out["YZTG"])


class TestPeriodicVerification(unittest.TestCase):
    def test_success_then_no_reverify_outside_window(self):
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC5)
        st = BD_MMYZ_ST(v, dtp)
        st.step()  # cold start verify → OK=0, calls=1
        self.assertEqual(v.calls, 1)
        dtp.set_ms(8000)  # 非时段
        st.step()
        self.assertEqual(v.calls, 1)  # 成功态非时段不重验
        self.assertEqual(st.OK, 0)

    def test_window_sets_10000_marker(self):
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC5)
        st = BD_MMYZ_ST(v, dtp)
        st.step()  # OK=0
        dtp.set_ms(MS_SEC7)
        out = st.step()
        self.assertEqual(out["OK"], 10000)  # 强制验证 + 标志
        self.assertEqual(out["ERR"], 0)
        self.assertTrue(out["YZTG"])
        self.assertEqual(v.calls, 2)

    def test_same_window_only_one_forced_verify(self):
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC7)
        st = BD_MMYZ_ST(v, dtp)
        st.step()  # cold start: OK%10000=9000!=0 → reverify → OK=0, calls=1
        self.assertEqual(st.OK, 0)
        st.step()  # 同时段 OK=0 → 强制验证 → OK=10000, calls=2
        self.assertEqual(st.OK, 10000)
        self.assertEqual(v.calls, 2)
        st.step()  # 同时段 OK=10000，不再重验
        self.assertEqual(st.OK, 10000)
        self.assertEqual(v.calls, 2)

    def test_leave_window_removes_marker(self):
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC7)
        st = BD_MMYZ_ST(v, dtp)
        st.step()  # OK=0
        st.step()  # OK=10000
        self.assertEqual(st.OK, 10000)
        dtp.set_ms(MS_SEC8)
        out = st.step()  # 离开时段 → OK=OK%10000=0
        self.assertEqual(out["OK"], 0)
        self.assertTrue(out["YZTG"])

    def test_seconds17_also_in_window(self):
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC5)
        st = BD_MMYZ_ST(v, dtp)
        st.step()  # OK=0
        dtp.set_ms(MS_SEC17)  # 17%10=7
        out = st.step()
        self.assertEqual(out["OK"], 10000)


class TestErrNAndErrYZTG(unittest.TestCase):
    def test_err_n_increments_only_on_failure(self):
        v = FakeValidator(1)
        st = BD_MMYZ_ST(v, ManualDateTimeProvider(MS_SEC5))
        st.step()
        self.assertEqual(st.ERR_N, 1.0)
        st.step()
        self.assertEqual(st.ERR_N, 2.0)
        # 改对后成功，ERR_N 不清零（保持累计）。
        v.code = 0
        st.step()
        self.assertTrue(st.YZTG)
        self.assertEqual(st.ERR_N, 2.0)

    def test_err_n_wraps_at_threshold(self):
        v = FakeValidator(1)
        st = BD_MMYZ_ST(v, ManualDateTimeProvider(MS_SEC5))
        st.OK = 1  # 使 OK%10000 != 0，进入每拍重验分支
        st.ERR_N = 999999999.0
        st.step()  # 999999999 + 1 = 1e9 > 999999999 → 回写 1e8
        self.assertEqual(st.ERR_N, 100000000.0)


class TestNoAutoAdvanceTime(unittest.TestCase):
    def test_repeated_calls_same_time_point(self):
        """同一 Runtime 时间点被重复调用时，step 不自行推进时间。"""
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC7)
        st = BD_MMYZ_ST(v, dtp)
        before = dtp.get_datetime_ms()
        st.step()
        st.step()
        st.step()
        self.assertEqual(dtp.get_datetime_ms(), before)  # 时间未被 step 改动

    def test_dt_ms_does_not_advance_time(self):
        v = FakeValidator(0)
        dtp = ManualDateTimeProvider(MS_SEC5)
        st = BD_MMYZ_ST(v, dtp)
        st.step(dt_ms=500)
        st.step(dt_ms=999999)
        self.assertEqual(dtp.get_datetime_ms(), MS_SEC5)


if __name__ == "__main__":
    unittest.main()
