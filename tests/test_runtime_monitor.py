"""WP-20260729-043：软件周期监视器 + 一次性 watchdog 超时事件源测试。

逐条对应任务书"验收要求"：

1. 配置反证：``cycle_ms`` / ``timeout_ms`` 的 ``True`` / ``False`` / 浮点 / 字符串 /
   零 / 负数全部拒绝；合法正整数精确转换为纳秒；两者无强制大小关系。
2. 手工时钟：阈值前不触发、精确阈值触发、阈值后触发；重复 poll 事件身份 / 内容稳定、
   序号不增加。
3. ``finish_cycle()`` 正常完成 / 超时完成 / 观测值 / 待处理事件保留 / 下一周期准入。
4. active reentry / 错误 token / 陈旧 token / double finish / pending 未消费即 begin
   的失败关闭具有稳定异常类型与可断言消息。
5. 非整数 / 负值 / 回退时钟反证：状态不被静默修复，且不伪造 / 覆盖 / 重复 timeout
   event。
6. 派发反证：无事件不调用 callback；有事件只调用一次；callback 抛
   ``WatchdogSafeCommit`` / 普通异常时不重放；第二次派发不造成第二次调用。
7. 与真实 ``OuterScanRunner`` 集成（shadow 与已启用物理提交两条策略）：在 runner 执行域
   空闲后派发，验证 watchdog 锁存、业务 IR 旁路、安全镜像与 shadow / 物理提交诚实边界。
8. 现有无超时扫描 / ``trigger_watchdog()`` 等回归由本包 10 条命令覆盖（此处集成锚定）。
9. 无 ``threading`` / ``asyncio`` / ``sleep`` / 忙等 / OS 定时器 / 后台任务。
10. 公开类型具最小类型标注与 docstring；不暴露测试时钟或 mutable 内部状态。

诚实边界：本文件锁定的是当前 Python 软件事件源契约——真实实时扫描循环、在途扫描卡死的
异步抢占、进程 / OS 崩溃、硬件 watchdog、HAL / 物理 I/O 与现场安全均**不在本包**；这些
测试**不构成**与 CODESYS PLC 语义、硬件或现场一致的证据。
"""
from __future__ import annotations

import inspect
import time
import unittest
from types import SimpleNamespace

import src.runtime.monitor as monitor_module
from src.runtime import (
    CommitPort,
    CycleObservation,
    CycleToken,
    Executor,
    IOMap,
    LoadConst,
    LoadVar,
    MonitorClockError,
    MonitorConfigError,
    MonitorStateError,
    OuterScanRunner,
    OutputPolicy,
    OutputPolicyService,
    POUDefinition,
    ProgramInstance,
    SafetySnapshot,
    SafetyStateService,
    ScanEngine,
    SoftwareCycleMonitor,
    StoreVar,
    Task,
    VarDecl,
    WatchdogSafeCommit,
    WatchdogTimeoutEvent,
    build_runtime_store,
)

_MS = 1_000_000


class _ManualClock:
    """手工整数纳秒时钟：``advance`` 显式推进，测试全程确定、无真实等待。"""

    def __init__(self, start: int = 0):
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ns: int) -> None:
        self.now += ns


class _ScriptedClock:
    """按脚本逐次返回值的时钟（含非法值），用于时钟契约反证。"""

    def __init__(self, values):
        self._values = list(values)
        self.reads = 0

    def __call__(self):
        value = self._values[self.reads]
        self.reads += 1
        return value


# ---------------------------------------------------------------------------
# 1) 配置反证
# ---------------------------------------------------------------------------

class TestConfigValidation(unittest.TestCase):

    def test_valid_positive_int_converted_to_ns(self):
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ManualClock())
        self.assertEqual(mon.cycle_ns, 10 * _MS)
        self.assertEqual(mon.timeout_ns, 50 * _MS)

    def test_no_forced_relation_between_cycle_and_timeout(self):
        # timeout 小于 cycle 也合法——不得私自规定大小关系。
        m1 = SoftwareCycleMonitor(cycle_ms=100, timeout_ms=1, clock_ns=_ManualClock())
        m2 = SoftwareCycleMonitor(cycle_ms=1, timeout_ms=1, clock_ns=_ManualClock())
        self.assertEqual((m1.cycle_ns, m1.timeout_ns), (100 * _MS, 1 * _MS))
        self.assertEqual((m2.cycle_ns, m2.timeout_ns), (1 * _MS, 1 * _MS))

    def test_bool_rejected(self):
        for bad in (True, False):
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=bad, timeout_ms=50)
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=10, timeout_ms=bad)

    def test_float_str_zero_negative_rejected(self):
        for bad in (10.0, "10", 0, -1):
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=bad, timeout_ms=50)
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=10, timeout_ms=bad)

    def test_non_callable_clock_rejected(self):
        with self.assertRaises(MonitorConfigError):
            SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=123)

    def test_default_clock_is_monotonic_ns(self):
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50)
        self.assertIs(mon._clock_ns, time.monotonic_ns)


# ---------------------------------------------------------------------------
# 2) 手工时钟阈值语义
# ---------------------------------------------------------------------------

class TestThreshold(unittest.TestCase):

    def _mon(self, clock, timeout_ms=50):
        return SoftwareCycleMonitor(cycle_ms=10, timeout_ms=timeout_ms, clock_ns=clock)

    def test_no_event_before_threshold(self):
        clock = _ManualClock()
        mon = self._mon(clock)
        mon.begin_cycle()
        clock.advance(50 * _MS - 1)             # 恰好差 1ns 未到阈值
        self.assertIsNone(mon.poll_timeout())
        self.assertFalse(mon.has_pending_event)

    def test_event_at_exact_threshold(self):
        clock = _ManualClock()
        mon = self._mon(clock)
        mon.begin_cycle()
        clock.advance(50 * _MS)                 # elapsed == timeout
        event = mon.poll_timeout()
        self.assertIsInstance(event, WatchdogTimeoutEvent)
        self.assertEqual(event.elapsed_ns, 50 * _MS)
        self.assertEqual(event.timeout_ns, 50 * _MS)
        self.assertEqual(event.overrun_ns, 0)
        self.assertEqual(event.sequence, 1)

    def test_event_after_threshold(self):
        clock = _ManualClock()
        mon = self._mon(clock)
        mon.begin_cycle()
        clock.advance(80 * _MS)
        event = mon.poll_timeout()
        self.assertEqual(event.overrun_ns, 30 * _MS)

    def test_repeated_poll_same_event_identity_and_sequence(self):
        clock = _ManualClock()
        mon = self._mon(clock)
        mon.begin_cycle()
        clock.advance(60 * _MS)
        first = mon.poll_timeout()
        clock.advance(999 * _MS)                # 继续推进也不得重复生成
        second = mon.poll_timeout()
        self.assertIs(first, second)            # 同一实例
        self.assertEqual(second.sequence, 1)    # 序号不增加
        self.assertEqual(second.observed_ns, first.observed_ns)

    def test_poll_without_active_cycle_returns_none(self):
        mon = self._mon(_ManualClock())
        self.assertIsNone(mon.poll_timeout())


# ---------------------------------------------------------------------------
# 3) finish_cycle 观测值与待处理事件保留
# ---------------------------------------------------------------------------

class TestFinishCycle(unittest.TestCase):

    def test_normal_finish_no_timeout(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(8 * _MS)
        obs = mon.finish_cycle(tok)
        self.assertIsInstance(obs, CycleObservation)
        self.assertEqual(obs.sequence, 1)
        self.assertEqual(obs.elapsed_ns, 8 * _MS)
        self.assertEqual(obs.cycle_ns, 10 * _MS)
        self.assertEqual(obs.timeout_ns, 50 * _MS)
        self.assertEqual(obs.deviation_ns, -2 * _MS)    # 比配置周期短 2ms
        self.assertFalse(obs.overran)
        self.assertFalse(obs.timed_out)
        self.assertFalse(mon.has_pending_event)
        self.assertFalse(mon.active)

    def test_finish_discovers_timeout_without_poll(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(70 * _MS)
        obs = mon.finish_cycle(tok)             # 从未 poll
        self.assertTrue(obs.timed_out)
        self.assertTrue(obs.overran)
        self.assertEqual(obs.deviation_ns, 60 * _MS)
        # finish 依据结束时钟发现超时并保留 pending 事件（不清除）
        self.assertTrue(mon.has_pending_event)
        event = mon.poll_timeout()
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.elapsed_ns, 70 * _MS)

    def test_finish_keeps_poll_generated_event_identity(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(55 * _MS)
        polled = mon.poll_timeout()
        clock.advance(20 * _MS)
        mon.finish_cycle(tok)                   # 不得覆盖 / 重复既有事件
        self.assertIs(mon.poll_timeout(), polled)
        self.assertEqual(polled.elapsed_ns, 55 * _MS)   # 保留 poll 时的检出值

    def test_next_cycle_admission_after_normal_finish(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        t1 = mon.begin_cycle()
        clock.advance(5 * _MS)
        mon.finish_cycle(t1)
        clock.advance(1 * _MS)
        t2 = mon.begin_cycle()                  # 正常完成后可开始下一周期
        self.assertEqual(t2.sequence, 2)
        self.assertGreater(t2.start_ns, t1.start_ns)

    def test_next_cycle_blocked_while_event_pending(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        t1 = mon.begin_cycle()
        clock.advance(60 * _MS)
        mon.finish_cycle(t1)                    # pending 保留
        with self.assertRaises(MonitorStateError) as cm:
            mon.begin_cycle()
        self.assertIn("dispatch_pending", str(cm.exception))
        # 派发消费后方可开始下一周期
        self.assertTrue(mon.dispatch_pending(lambda: None))
        t2 = mon.begin_cycle()
        self.assertEqual(t2.sequence, 2)


# ---------------------------------------------------------------------------
# 4) 状态机失败关闭（稳定异常类型 + 可断言消息）
# ---------------------------------------------------------------------------

class TestFailClosed(unittest.TestCase):

    def _mon(self):
        return SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=_ManualClock())

    def test_active_reentry_begin(self):
        mon = self._mon()
        mon.begin_cycle()
        with self.assertRaises(MonitorStateError) as cm:
            mon.begin_cycle()
        self.assertIn("active cycle", str(cm.exception))

    def test_wrong_token_type(self):
        mon = self._mon()
        mon.begin_cycle()
        with self.assertRaises(MonitorStateError) as cm:
            mon.finish_cycle("not-a-token")
        self.assertIn("CycleToken", str(cm.exception))

    def test_stale_token_from_previous_cycle(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        t1 = mon.begin_cycle()
        clock.advance(1 * _MS)
        mon.finish_cycle(t1)
        clock.advance(1 * _MS)
        mon.begin_cycle()                       # 新周期 active
        with self.assertRaises(MonitorStateError) as cm:
            mon.finish_cycle(t1)                # 用陈旧 token
        self.assertIn("陈旧", str(cm.exception))

    def test_stale_token_from_other_monitor(self):
        mon_a = self._mon()
        mon_b = self._mon()
        tok_a = mon_a.begin_cycle()
        mon_b.begin_cycle()
        with self.assertRaises(MonitorStateError):
            mon_b.finish_cycle(tok_a)           # 跨监视器 token 失败关闭

    def test_double_finish(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(1 * _MS)
        mon.finish_cycle(tok)
        with self.assertRaises(MonitorStateError) as cm:
            mon.finish_cycle(tok)
        self.assertIn("无 active cycle", str(cm.exception))


# ---------------------------------------------------------------------------
# 5) 时钟契约反证：状态不被静默修复、事件不被伪造 / 覆盖 / 重复
# ---------------------------------------------------------------------------

class TestClockContract(unittest.TestCase):

    def test_bool_clock_rejected(self):
        # begin 首读即返回 bool → 失败关闭，序号 / active 不推进
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([True]))
        with self.assertRaises(MonitorClockError):
            mon.begin_cycle()
        self.assertFalse(mon.active)
        self.assertIsNone(mon.active_sequence)

    def test_float_clock_rejected_on_poll_state_unchanged(self):
        clock = _ScriptedClock([0, 12.5])       # begin=0, poll=12.5(float)
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        with self.assertRaises(MonitorClockError):
            mon.poll_timeout()
        # 状态未被静默修复：仍是同一 active cycle、无 pending 事件
        self.assertTrue(mon.active)
        self.assertEqual(mon.active_sequence, tok.sequence)
        self.assertFalse(mon.has_pending_event)

    def test_negative_clock_rejected(self):
        clock = _ScriptedClock([0, -5])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        mon.begin_cycle()
        with self.assertRaises(MonitorClockError):
            mon.poll_timeout()
        self.assertFalse(mon.has_pending_event)

    def test_regressing_clock_rejected_no_state_repair(self):
        clock = _ScriptedClock([100 * _MS, 40 * _MS])   # 回退
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        with self.assertRaises(MonitorClockError) as cm:
            mon.poll_timeout()
        self.assertIn("回退", str(cm.exception))
        # active 未清除、可在时钟恢复后继续
        self.assertEqual(mon.active_sequence, tok.sequence)

    def test_bad_clock_at_finish_keeps_active_and_pending(self):
        # 先 poll 生成事件，再让 finish 读到非法时钟 → active 不清、pending 不被覆盖
        clock = _ScriptedClock([0, 60 * _MS, "bad"])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        event = mon.poll_timeout()
        self.assertIsNotNone(event)
        with self.assertRaises(MonitorClockError):
            mon.finish_cycle(tok)
        self.assertTrue(mon.active)                     # 未清除
        self.assertIs(mon.poll_timeout(), event)        # pending 事件未被覆盖 / 重复

    def test_bad_clock_does_not_fabricate_event(self):
        clock = _ScriptedClock([0, 3.14])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        mon.begin_cycle()
        with self.assertRaises(MonitorClockError):
            mon.poll_timeout()
        self.assertFalse(mon.has_pending_event)         # 未伪造事件


# ---------------------------------------------------------------------------
# 6) 一次性派发反证
# ---------------------------------------------------------------------------

class _CountingCallback:
    def __init__(self, raises=None):
        self.calls = 0
        self.raises = raises

    def __call__(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises


class TestDispatch(unittest.TestCase):

    def _timed_out_mon(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(60 * _MS)
        mon.finish_cycle(tok)
        return mon

    def test_no_event_no_call(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        cb = _CountingCallback()
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 0)

    def test_event_dispatched_once(self):
        mon = self._timed_out_mon()
        cb = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)
        # 第二次派发不造成第二次调用
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)

    def test_callback_watchdog_signal_propagates_no_replay(self):
        mon = self._timed_out_mon()
        cb = _CountingCallback(raises=WatchdogSafeCommit(
            safe_image={"DO0": False}, safe_commit_succeeded=True))
        with self.assertRaises(WatchdogSafeCommit):
            mon.dispatch_pending(cb)
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)         # 调用前已消费，不可重放
        # 再次派发同一 callback 不会二次触发安全提交
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)

    def test_callback_plain_exception_propagates_no_replay(self):
        mon = self._timed_out_mon()
        cb = _CountingCallback(raises=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            mon.dispatch_pending(cb)
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)

    def test_non_callable_callback_rejected(self):
        mon = self._timed_out_mon()
        with self.assertRaises(MonitorConfigError):
            mon.dispatch_pending(123)
        self.assertTrue(mon.has_pending_event)          # 事件未被消费


# ---------------------------------------------------------------------------
# 7) 与真实 OuterScanRunner 集成（shadow + 已启用物理提交）
# ---------------------------------------------------------------------------

class _RecordingCommitter:
    def __init__(self):
        self.received = []

    def commit(self, outputs):
        self.received.append(dict(outputs))


def _build_runner(*, av_safe=7, motor_safe=False, legacy_unshadowed):
    """构造真实 ScanEngine + OuterScanRunner（复用 WP-008 装配口径）。"""
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("AV", "INT", section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=OutputPolicy("Motor", "BOOL", motor_safe)),
        IOMap("AV", "AO0", "OUT", policy=OutputPolicy("AV", "INT", av_safe)),
    ]
    code = [
        LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL"),
        LoadConst(100, "INT"), StoreVar("AV", "INT"),
    ]
    main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
    task = Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                io_map=io_map, pou_lib={"Main": main})
    layout = build_runtime_store(task)
    executor = Executor(task, layout)
    safety = SafetyStateService(SafetySnapshot.all_ok())
    policy = OutputPolicyService(layout.store, task.io_map, safety)
    committer = _RecordingCommitter()
    if legacy_unshadowed:
        port = CommitPort(committer, legacy_unshadowed=True)
    else:
        port = CommitPort(committer)            # 默认 shadow
    engine = ScanEngine(task, layout, executor, policy, port)
    runner = OuterScanRunner(engine, policy, port)
    return SimpleNamespace(task=task, layout=layout, safety=safety, policy=policy,
                           committer=committer, port=port, engine=engine,
                           runner=runner)


class TestOuterScanRunnerIntegration(unittest.TestCase):

    def _timed_out_monitor(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(70 * _MS)
        obs = mon.finish_cycle(tok)
        self.assertTrue(obs.timed_out)
        self.assertTrue(mon.has_pending_event)
        return mon

    def test_physical_commit_watchdog_after_idle(self):
        w = _build_runner(av_safe=7, legacy_unshadowed=True)
        # 先跑一正常拍，runner 执行域随后空闲
        w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.committer.received[-1], {"DO0": True, "AO0": 100})
        prev_before = w.engine.prev
        av_before = w.layout.store.read("AV")

        mon = self._timed_out_monitor()
        # 空闲后派发到既有 trigger_watchdog 路径
        with self.assertRaises(WatchdogSafeCommit) as cm:
            mon.dispatch_pending(w.runner.trigger_watchdog)
        sig = cm.exception
        self.assertEqual(sig.cause, "watchdog")
        self.assertTrue(sig.safe_commit_succeeded)              # 物理安全提交成功
        self.assertFalse(sig.shadow)
        self.assertEqual(sig.safe_image, {"DO0": False, "AO0": 7})
        # 安全镜像物理提交恰一次；业务 IR 被旁路：Store / prev 未前移
        self.assertEqual(w.committer.received[-1], {"DO0": False, "AO0": 7})
        self.assertEqual(w.layout.store.read("AV"), av_before)
        self.assertIs(w.engine.prev, prev_before)
        # watchdog_ok 锁存 False，不自动清除
        self.assertFalse(w.safety.read().watchdog_ok)
        self.assertFalse(mon.has_pending_event)                 # 事件已一次性消费

    def test_shadow_watchdog_write_disabled_honest_flags(self):
        w = _build_runner(av_safe=7, legacy_unshadowed=False)   # 默认 shadow
        self.assertTrue(w.runner.shadow)
        shadow_result = w.runner.scan_cycle({"DI0": True})
        self.assertFalse(shadow_result.physically_committed)
        self.assertEqual(w.committer.received, [])              # shadow 零物理写

        mon = self._timed_out_monitor()
        with self.assertRaises(WatchdogSafeCommit) as cm:
            mon.dispatch_pending(w.runner.trigger_watchdog)
        sig = cm.exception
        # shadow 诚实边界：写出被抑制、逻辑采用、无物理提交成功
        self.assertTrue(sig.shadow)
        self.assertTrue(sig.write_suppressed_by_shadow)
        self.assertTrue(sig.shadow_logic_adopted)
        self.assertFalse(sig.safe_commit_succeeded)
        self.assertEqual(w.committer.received, [])              # 全程零物理写
        self.assertFalse(w.safety.read().watchdog_ok)           # 逻辑安全状态仍锁存

    def test_enabled_physical_via_set_write_enabled(self):
        # 默认 shadow 装配经 runner 自身模式切换启用物理提交后派发。
        w = _build_runner(av_safe=7, legacy_unshadowed=False)
        w.runner.set_write_enabled(True)
        self.assertTrue(w.runner.writes_enabled)
        mon = self._timed_out_monitor()
        with self.assertRaises(WatchdogSafeCommit) as cm:
            mon.dispatch_pending(w.runner.trigger_watchdog)
        sig = cm.exception
        self.assertTrue(sig.safe_commit_succeeded)
        self.assertFalse(sig.shadow)
        self.assertEqual(w.committer.received[-1], {"DO0": False, "AO0": 7})
        self.assertFalse(w.safety.read().watchdog_ok)


# ---------------------------------------------------------------------------
# 9/10) 无线程 / 无 sleep；API 卫生
# ---------------------------------------------------------------------------

class TestApiHygiene(unittest.TestCase):

    def test_no_threading_asyncio_sleep_in_source(self):
        # 只匹配**实际代码用法**（import / 调用），不误伤文档串里的 “无 sleep” 叙述。
        src = inspect.getsource(monitor_module)
        for forbidden in ("import threading", "import asyncio", "threading.",
                          "asyncio.", ".sleep(", "Thread(", "os.times", "signal."):
            self.assertNotIn(forbidden, src,
                             "monitor 源码不得含 %r（本包不引入实时调度 / 后台任务）"
                             % (forbidden,))

    def test_public_types_have_docstrings(self):
        for cls in (SoftwareCycleMonitor, CycleToken, CycleObservation,
                    WatchdogTimeoutEvent, MonitorClockError, MonitorStateError,
                    MonitorConfigError):
            self.assertTrue((cls.__doc__ or "").strip(),
                            "%s 缺 docstring" % (cls.__name__,))

    def test_clock_and_mutable_state_not_public(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        public = [n for n in dir(mon) if not n.startswith("_")]
        # 注入时钟不作为公共属性暴露
        for name in public:
            self.assertIsNot(getattr(mon, name), clock)
        # __slots__ 冻结：无 __dict__ 可注入 mutable 公共状态
        self.assertFalse(hasattr(mon, "__dict__"))

    def test_immutable_value_types_frozen(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        with self.assertRaises(Exception):
            tok.sequence = 99                   # frozen dataclass
        clock.advance(60 * _MS)
        event = mon.poll_timeout()
        with self.assertRaises(Exception):
            event.sequence = 99


# ---------------------------------------------------------------------------
# 11) WP-046 返修①：同一 active sequence 至多锁存 / 派发一次（终态独立于 pending 槽）
#
#     WP-044 Codex Round 1 反证：同一 active cycle 在精确阈值 poll → dispatch → 再次
#     poll → dispatch 会得到两个身份不同的 sequence=1 事件、callback 被调用两次
#     （同一超时周期二次安全提交）。以下反证锁定“事件消费后同一 active sequence 不得
#     重放”，覆盖 callback 成功 / 抛 WatchdogSafeCommit / 抛普通异常三条路径，以及
#     终态不会永久抑制后续合法周期。
# ---------------------------------------------------------------------------

class TestOneShotAcrossDispatchWithinActiveCycle(unittest.TestCase):

    def _armed(self, timeout_ms=50, elapse=50 * _MS):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=timeout_ms, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(elapse)                   # 达到 / 越过阈值但**不** finish
        return mon, tok, clock

    def test_poll_dispatch_poll_finish_single_event_single_call(self):
        # 精确阈值：poll → dispatch 成功 → 再次 poll → finish
        mon, tok, clock = self._armed(elapse=50 * _MS)
        first = mon.poll_timeout()
        self.assertIsInstance(first, WatchdogTimeoutEvent)
        cb = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)
        # active cycle 尚未 finish：再次 poll 不得为同一 sequence 重新生成事件
        clock.advance(500 * _MS)
        self.assertIsNone(mon.poll_timeout())
        self.assertFalse(mon.has_pending_event)
        # 再次 dispatch 不造成第二次调用（无二次安全提交）
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)
        # finish 不补锁第二个事件
        obs = mon.finish_cycle(tok)
        self.assertTrue(obs.timed_out)
        self.assertFalse(mon.has_pending_event)
        self.assertIsNone(mon.poll_timeout())   # 无 active、无 pending
        self.assertEqual(cb.calls, 1)

    def test_callback_watchdog_signal_no_replay_within_active_cycle(self):
        # dispatch callback 抛 WatchdogSafeCommit：异常原样传播且不可重放
        mon, tok, clock = self._armed(elapse=60 * _MS)
        self.assertIsInstance(mon.poll_timeout(), WatchdogTimeoutEvent)
        cb = _CountingCallback(raises=WatchdogSafeCommit(
            safe_image={"DO0": False}, safe_commit_succeeded=True))
        with self.assertRaises(WatchdogSafeCommit):
            mon.dispatch_pending(cb)
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)
        self.assertTrue(mon.active)             # active 未清，但不得重放
        clock.advance(500 * _MS)
        self.assertIsNone(mon.poll_timeout())
        other = _CountingCallback()             # 另一 callback 也不得被再次调用
        self.assertFalse(mon.dispatch_pending(other))
        self.assertEqual(other.calls, 0)
        mon.finish_cycle(tok)
        self.assertFalse(mon.has_pending_event)
        self.assertEqual(cb.calls, 1)

    def test_callback_plain_exception_no_replay_within_active_cycle(self):
        # dispatch callback 抛普通异常：同样不可重放
        mon, tok, clock = self._armed(elapse=70 * _MS)
        self.assertIsInstance(mon.poll_timeout(), WatchdogTimeoutEvent)
        cb = _CountingCallback(raises=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            mon.dispatch_pending(cb)
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)
        clock.advance(500 * _MS)
        self.assertIsNone(mon.poll_timeout())
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)
        mon.finish_cycle(tok)
        self.assertEqual(cb.calls, 1)

    def test_finish_after_dispatch_generates_no_second_event(self):
        # finish 补锁路径也受终态约束：dispatch 后 finish 不得再补一个事件
        mon, tok, clock = self._armed(elapse=80 * _MS)
        mon.poll_timeout()
        self.assertTrue(mon.dispatch_pending(lambda: None))
        clock.advance(40 * _MS)
        obs = mon.finish_cycle(tok)             # 从 finish 补锁路径进入
        self.assertTrue(obs.timed_out)
        self.assertFalse(mon.has_pending_event)

    def test_terminal_state_does_not_suppress_next_cycle(self):
        # 上一超时周期已消费并 finish 后，新合法周期仍可触发自己的新 sequence 事件
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        t1 = mon.begin_cycle()
        clock.advance(60 * _MS)
        self.assertIsInstance(mon.poll_timeout(), WatchdogTimeoutEvent)
        cb1 = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb1))      # 消费
        clock.advance(5 * _MS)
        mon.finish_cycle(t1)                             # finish 不补第二个事件
        self.assertFalse(mon.has_pending_event)
        # 新周期 seq=2 达阈值 → 生成属于自己的事件并可派发
        clock.advance(5 * _MS)
        t2 = mon.begin_cycle()
        self.assertEqual(t2.sequence, 2)
        clock.advance(50 * _MS)
        ev2 = mon.poll_timeout()
        self.assertIsInstance(ev2, WatchdogTimeoutEvent)
        self.assertEqual(ev2.sequence, 2)
        self.assertIsNot(ev2, None)
        cb2 = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb2))
        self.assertEqual(cb2.calls, 1)


# ---------------------------------------------------------------------------
# 12) WP-046 返修②：配置值与时钟只接受 exact Python int（拒绝 bool / int 子类）
#
#     WP-044 Codex Round 1 反证：重载 <= / * 的负 int 子类被构造器接受、使
#     cycle_ns/timeout_ns 变成字符串；重载 < / - 的 int 子类让时钟回退仍被接受并伪造
#     elapsed 超时事件。以下反证锁定 exact-int 失败关闭，并确认状态 / 事件不被伪造。
# ---------------------------------------------------------------------------

class _EvilConfigInt(int):
    """恶意 int 子类：重载比较 / 算术企图绕过正值校验与纳秒换算。"""

    def __le__(self, other):    # 让 ``value <= 0`` 恒假，绕过正值检查
        return False

    def __lt__(self, other):
        return False

    def __mul__(self, other):   # 让 ``value * _NS_PER_MS`` 返回任意对象
        return "pwned"

    def __rmul__(self, other):
        return "pwned"


class _EvilClockInt(int):
    """恶意 int 子类：重载 < / - 企图绕过回退检测并伪造 elapsed 超时。"""

    def __lt__(self, other):    # 让回退比较恒假（不被判回退）
        return False

    def __sub__(self, other):   # 让 ``now - start_ns`` 返回越阈值的 elapsed
        return 60 * _MS

    def __rsub__(self, other):
        return 60 * _MS


class TestExactIntConfig(unittest.TestCase):

    def test_config_rejects_int_subclass_all_signs(self):
        for bad in (_EvilConfigInt(-1), _EvilConfigInt(0), _EvilConfigInt(10)):
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=bad, timeout_ms=50,
                                     clock_ns=_ManualClock())
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=10, timeout_ms=bad,
                                     clock_ns=_ManualClock())

    def test_config_int_subclass_builds_no_half_monitor(self):
        # WP-044 反证：重载 <= / * 的负 int 子类曾构造出 cycle_ns='pwned' 的半成品
        with self.assertRaises(MonitorConfigError) as cm:
            SoftwareCycleMonitor(cycle_ms=_EvilConfigInt(-1), timeout_ms=50,
                                 clock_ns=_ManualClock())
        self.assertIn("int", str(cm.exception))     # 稳定说明只接受 Python int
        # 不构造半成品：没有任何 monitor 对象逃逸（构造直接抛出）

    def test_exact_builtin_int_still_accepted(self):
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=_ManualClock())
        self.assertEqual((mon.cycle_ns, mon.timeout_ns), (10 * _MS, 50 * _MS))


class TestExactIntClock(unittest.TestCase):

    def test_first_clock_read_int_subclass_rejected(self):
        # begin 首读返回 int 子类 → 失败关闭，序号 / active 不推进
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([_EvilClockInt(0)]))
        with self.assertRaises(MonitorClockError):
            mon.begin_cycle()
        self.assertFalse(mon.active)
        self.assertIsNone(mon.active_sequence)
        self.assertFalse(mon.has_pending_event)

    def test_midcycle_clock_int_subclass_cannot_fabricate_event(self):
        # begin 用合法大值；poll 时时钟返回重载 < / - 的 int 子类回退，不得伪造事件
        clock = _ScriptedClock([100 * _MS, _EvilClockInt(1)])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        with self.assertRaises(MonitorClockError):
            mon.poll_timeout()
        # 序号 / active / _last_seen_ns / pending / 事件身份均不被推进、覆盖或伪造
        self.assertTrue(mon.active)
        self.assertEqual(mon.active_sequence, tok.sequence)
        self.assertEqual(mon.active_sequence, 1)
        self.assertFalse(mon.has_pending_event)
        self.assertEqual(mon._last_seen_ns, 100 * _MS)  # 未被非法值覆盖

    def test_finish_clock_int_subclass_keeps_active_and_pending(self):
        # 先 poll 生成合法事件，再让 finish 读到 int 子类时钟 → active 不清、pending 不覆盖
        clock = _ScriptedClock([0, 60 * _MS, _EvilClockInt(1)])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        event = mon.poll_timeout()
        self.assertIsInstance(event, WatchdogTimeoutEvent)
        with self.assertRaises(MonitorClockError):
            mon.finish_cycle(tok)
        self.assertTrue(mon.active)
        self.assertIs(mon.poll_timeout(), event)        # pending 未被覆盖 / 重复


# ---------------------------------------------------------------------------
# 13) WP-046 Round 2 返修：exact-int 拒绝路径不得对不可信值取 repr
#
#     Codex Round 1 反证：exact-int 拒绝分支用 %r 格式化被拒的 int 子类；子类可重载
#     __repr__ 抛异常，令拒绝阶段再次执行攻击者代码，稳定的 MonitorConfigError /
#     MonitorClockError 被 repr 侧 RuntimeError 替换逃逸。以下反证锁定：拒绝路径只报告
#     可信类型名、绝不 repr 不可信值；异常分层稳定，且 _seq / _active / _last_seen_ns /
#     pending / _latched_seq 均不被推进、覆盖或伪造。
# ---------------------------------------------------------------------------

class _ReprBombInt(int):
    """__repr__ / __str__ 抛异常的恶意 int 子类：验证 exact-int 拒绝路径**不**对不可信值
    取 repr（否则拒绝阶段会再次执行攻击者代码，使稳定的 Monitor* 异常被 repr 异常替换）。"""

    def __repr__(self):
        raise RuntimeError("repr-bomb")

    def __str__(self):
        raise RuntimeError("str-bomb")


class TestExactIntRejectionDoesNotReprUntrusted(unittest.TestCase):

    def test_config_repr_bomb_int_raises_config_error_not_repr(self):
        # cycle_ms / timeout_ms 为 __repr__ 抛异常的 int 子类 → 稳定 MonitorConfigError，
        # 不逃逸 RuntimeError('repr-bomb')，也不构造半成品 monitor。
        with self.assertRaises(MonitorConfigError):
            SoftwareCycleMonitor(cycle_ms=_ReprBombInt(10), timeout_ms=50,
                                 clock_ns=_ManualClock())
        with self.assertRaises(MonitorConfigError):
            SoftwareCycleMonitor(cycle_ms=10, timeout_ms=_ReprBombInt(10),
                                 clock_ns=_ManualClock())

    def test_config_repr_bomb_error_message_formats_safely(self):
        # 异常消息本身可安全 str 化（不触发 repr-bomb / str-bomb），且**零观察**：
        # WP-047 起拒绝路径只含固定可信文本，不得读取被拒对象的值 / 表示 / 类型名。
        with self.assertRaises(MonitorConfigError) as cm:
            SoftwareCycleMonitor(cycle_ms=_ReprBombInt(10), timeout_ms=50,
                                 clock_ns=_ManualClock())
        msg = str(cm.exception)                     # 不得抛 RuntimeError
        self.assertNotIn("_ReprBombInt", msg)       # 不得泄露攻击者类型名
        self.assertIn("int", msg)                   # 稳定说明只接受 Python int

    def test_first_clock_repr_bomb_int_raises_clock_error_state_unchanged(self):
        # begin 首读返回 __repr__ 抛异常的 int 子类 → 稳定 MonitorClockError，状态不推进
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([_ReprBombInt(0)]))
        with self.assertRaises(MonitorClockError):
            mon.begin_cycle()
        self.assertFalse(mon.active)
        self.assertIsNone(mon.active_sequence)
        self.assertFalse(mon.has_pending_event)
        self.assertEqual(mon._seq, 0)               # 序号未推进
        self.assertEqual(mon._last_seen_ns, 0)      # 未被非法值覆盖
        self.assertIsNone(mon._latched_seq)

    def test_first_clock_repr_bomb_error_message_formats_safely(self):
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([_ReprBombInt(0)]))
        with self.assertRaises(MonitorClockError) as cm:
            mon.begin_cycle()
        msg = str(cm.exception)                     # 不得抛 RuntimeError
        self.assertNotIn("_ReprBombInt", msg)       # 零观察：不泄露攻击者类型名
        self.assertIn("int", msg)

    def test_midcycle_clock_repr_bomb_int_raises_clock_error_state_unchanged(self):
        # begin 用合法大值；poll 时时钟返回 __repr__ 抛异常的 int 子类 → 不得伪造事件
        clock = _ScriptedClock([100 * _MS, _ReprBombInt(1)])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        with self.assertRaises(MonitorClockError):
            mon.poll_timeout()
        self.assertTrue(mon.active)
        self.assertEqual(mon.active_sequence, tok.sequence)
        self.assertEqual(mon._seq, 1)
        self.assertEqual(mon._last_seen_ns, 100 * _MS)  # 未被非法值覆盖
        self.assertFalse(mon.has_pending_event)         # 未伪造事件
        self.assertIsNone(mon._latched_seq)             # 终态未被推进

    def test_finish_clock_repr_bomb_int_keeps_active_and_pending(self):
        # 先 poll 生成合法事件，再让 finish 读到 __repr__ 抛异常的 int 子类时钟
        clock = _ScriptedClock([0, 60 * _MS, _ReprBombInt(1)])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        event = mon.poll_timeout()
        self.assertIsInstance(event, WatchdogTimeoutEvent)
        with self.assertRaises(MonitorClockError):
            mon.finish_cycle(tok)
        self.assertTrue(mon.active)                     # active 未清
        self.assertIs(mon.poll_timeout(), event)        # pending 未被覆盖 / 重复
        self.assertEqual(mon._latched_seq, 1)           # 既有终态不被非法时钟改变


# ---------------------------------------------------------------------------
# 14) WP-047 收口：非法外部对象拒绝路径“零观察”（不得读取动态类型名 / 触发类型对象代码）
#
#     WP-046 Round 3 Codex 反证：Round 3 的 _safe_type_name 仍 type.__getattribute__
#     (type(value), "__name__")，一旦 metaclass 把 __name__ 定义成数据描述符，其 __get__
#     在**类型判定之前**即被触发——既能抛 BaseException 绕过 `except Exception` 逃逸，也能
#     在返回 exact str 时留下可观察副作用（“执行后捕获”并非零观察）。WP-047 裁决：彻底删除
#     动态类型名探测，拒绝路径只用固定、可信、与对象身份及动态类型无关的文本。
#     以下反证覆盖三类恶意 metaclass 变体 ×（配置 / 首次时钟 / active 中途时钟）：
#       A) __getattribute__ 拦截 __name__ 返回 __str__ 抛异常的恶意对象；
#       B) __getattribute__ 访问 __name__ 直接抛异常；
#       C) metaclass 把 __name__ 定义为返回恶意对象的数据描述符。
#     锁定：稳定命中分层异常、异常消息可安全 str 化且不含攻击者类名，且 _seq / _active /
#     _last_seen_ns / pending / _latched_seq 不被推进、覆盖或伪造。
#     BaseException 逃逸与副作用零观察的专项反证见第 15 节。
# ---------------------------------------------------------------------------

class _TypeNameBomb:
    """metaclass __name__ 返回的恶意对象：__str__ / __repr__ 抛异常，验证诊断格式化
    绝不对其取 str / repr（否则拒绝阶段再次执行攻击者代码）。"""

    def __str__(self):
        raise RuntimeError("type-name-str-bomb")

    def __repr__(self):
        raise RuntimeError("type-name-repr-bomb")


class _EvilMetaReturnObj(type):
    """重载类型对象 __getattribute__：读 __name__ 时返回 __str__ 抛异常的恶意对象。"""

    def __getattribute__(cls, name):
        if name == "__name__":
            return _TypeNameBomb()
        return super().__getattribute__(name)


class _EvilMetaRaise(type):
    """重载类型对象 __getattribute__：读 __name__ 时直接抛异常。"""

    def __getattribute__(cls, name):
        if name == "__name__":
            raise RuntimeError("type-name-access-bomb")
        return super().__getattribute__(name)


class _NameDescriptorBomb:
    """数据描述符：__get__ 返回 __str__ 抛异常的恶意对象。"""

    def __get__(self, obj, objtype=None):
        return _TypeNameBomb()


class _EvilMetaDescriptor(type):
    """metaclass 把 __name__ 定义成返回恶意对象的数据描述符。"""

    __name__ = _NameDescriptorBomb()


class _MetaBombIntReturnObj(int, metaclass=_EvilMetaReturnObj):
    pass


class _MetaBombIntRaise(int, metaclass=_EvilMetaRaise):
    pass


class _MetaBombIntDescriptor(int, metaclass=_EvilMetaDescriptor):
    pass


_META_BOMB_INT_CLASSES = (
    _MetaBombIntReturnObj,
    _MetaBombIntRaise,
    _MetaBombIntDescriptor,
)


class TestExactIntRejectionResistsMaliciousMetaclass(unittest.TestCase):

    def test_config_metaclass_bomb_raises_config_error(self):
        # cycle_ms / timeout_ms 为恶意 metaclass int 子类 → 稳定 MonitorConfigError，
        # 不逃逸 RuntimeError('type-name-*-bomb')，也不构造半成品 monitor。
        for cls in _META_BOMB_INT_CLASSES:
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=cls(10), timeout_ms=50,
                                     clock_ns=_ManualClock())
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=10, timeout_ms=cls(10),
                                     clock_ns=_ManualClock())

    def test_config_metaclass_bomb_error_message_formats_safely(self):
        # 异常消息可安全 str 化（不触发 type-name-*-bomb），只含固定可信文本、不泄露类名。
        for cls in _META_BOMB_INT_CLASSES:
            with self.assertRaises(MonitorConfigError) as cm:
                SoftwareCycleMonitor(cycle_ms=cls(10), timeout_ms=50,
                                     clock_ns=_ManualClock())
            msg = str(cm.exception)                 # 不得抛 RuntimeError
            self.assertIn("int", msg)               # 稳定说明只接受 Python int
            self.assertNotIn("MetaBomb", msg)       # 零观察：不含攻击者类名

    def test_first_clock_metaclass_bomb_raises_clock_error_state_unchanged(self):
        # begin 首读返回恶意 metaclass int 子类 → 稳定 MonitorClockError，状态不推进。
        for cls in _META_BOMB_INT_CLASSES:
            mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                       clock_ns=_ScriptedClock([cls(0)]))
            with self.assertRaises(MonitorClockError):
                mon.begin_cycle()
            self.assertFalse(mon.active)
            self.assertIsNone(mon.active_sequence)
            self.assertFalse(mon.has_pending_event)
            self.assertEqual(mon._seq, 0)           # 序号未推进
            self.assertEqual(mon._last_seen_ns, 0)  # 未被非法值覆盖
            self.assertIsNone(mon._latched_seq)     # 终态未被推进

    def test_first_clock_metaclass_bomb_error_message_formats_safely(self):
        for cls in _META_BOMB_INT_CLASSES:
            mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                       clock_ns=_ScriptedClock([cls(0)]))
            with self.assertRaises(MonitorClockError) as cm:
                mon.begin_cycle()
            msg = str(cm.exception)                 # 不得抛 RuntimeError
            self.assertIn("int", msg)
            self.assertNotIn("MetaBomb", msg)       # 零观察：不含攻击者类名

    def test_midcycle_clock_metaclass_bomb_does_not_forge_event(self):
        # begin 用合法大值；poll 时时钟返回恶意 metaclass int 子类 → 不得伪造事件。
        for cls in _META_BOMB_INT_CLASSES:
            clock = _ScriptedClock([100 * _MS, cls(1)])
            mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
            tok = mon.begin_cycle()
            with self.assertRaises(MonitorClockError):
                mon.poll_timeout()
            self.assertTrue(mon.active)
            self.assertEqual(mon.active_sequence, tok.sequence)
            self.assertEqual(mon._seq, 1)
            self.assertEqual(mon._last_seen_ns, 100 * _MS)  # 未被非法值覆盖
            self.assertFalse(mon.has_pending_event)         # 未伪造事件
            self.assertIsNone(mon._latched_seq)             # 终态未被推进

    def test_finish_clock_metaclass_bomb_keeps_active_and_pending(self):
        # 先 poll 生成合法事件，再让 finish 读到恶意 metaclass int 子类时钟。
        for cls in _META_BOMB_INT_CLASSES:
            clock = _ScriptedClock([0, 60 * _MS, cls(1)])
            mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
            tok = mon.begin_cycle()
            event = mon.poll_timeout()
            self.assertIsInstance(event, WatchdogTimeoutEvent)
            with self.assertRaises(MonitorClockError):
                mon.finish_cycle(tok)
            self.assertTrue(mon.active)                     # active 未清
            self.assertIs(mon.poll_timeout(), event)        # pending 未被覆盖 / 重复
            self.assertEqual(mon._latched_seq, 1)           # 既有终态不被非法时钟改变


# ---------------------------------------------------------------------------
# 15) WP-047 专项：BaseException 逃逸 / 副作用零观察 / 非法对象零观察 / 源码卫生
#
#     WP-046 Round 3 Codex 未预告反证：metaclass __name__ 数据描述符的 __get__ 在类型
#     判定之前即被触发——(A) 抛自定义 BaseException 时 `except Exception` 兜不住、稳定的
#     Monitor* 异常被替换逃逸；(B) 即使返回 exact str，其副作用也已发生（“执行后捕获”不是
#     零观察）。WP-047 删除动态类型名探测后，以下反证锁定：配置与首次/中途/finish 三条时钟
#     拒绝路径都不再触发描述符、不逃逸攻击者异常、副作用计数恒为 0；不可调用时钟 / 非 exact
#     CycleToken / 不可调用 callback 的拒绝亦零观察、零状态推进；生产源码不含动态类型名获取
#     与不可信格式化。
# ---------------------------------------------------------------------------


class _MonBaseBoom(BaseException):
    """自定义 BaseException：`except Exception` 无法捕获，用于验证零观察不触发描述符。"""


# 描述符 __get__ 的“武装”开关：默认关闭，确保类定义 / 实例构造阶段不触发攻击，
# 仅在测试体内针对拒绝路径显式武装，避免污染收集期。
_BASE_BOOM_ARMED = {"on": False}
_SIDE_EFFECT_LOG = []


class _NameDescBaseBoom:
    """metaclass __name__ 数据描述符：武装后 __get__ 抛自定义 BaseException。"""

    def __get__(self, obj, objtype=None):
        if _BASE_BOOM_ARMED["on"]:
            raise _MonBaseBoom("name-descriptor-base-exception-boom")
        return "disarmed"


class _EvilMetaBaseBoom(type):
    __name__ = _NameDescBaseBoom()


class _MetaBombIntBaseBoom(int, metaclass=_EvilMetaBaseBoom):
    pass


class _NameDescSideEffect:
    """metaclass __name__ 数据描述符：__get__ 记录可观察副作用并返回 exact str，
    证明零观察不是“执行后捕获”。"""

    def __get__(self, obj, objtype=None):
        _SIDE_EFFECT_LOG.append(1)
        return "totally-safe-str"


class _EvilMetaSideEffect(type):
    __name__ = _NameDescSideEffect()


class _MetaBombIntSideEffect(int, metaclass=_EvilMetaSideEffect):
    pass


class TestBaseExceptionDescriptorDoesNotEscape(unittest.TestCase):
    """metaclass __name__ 数据描述符 __get__ 抛 BaseException 时，拒绝路径仍稳定命中
    分层 Monitor* 异常，绝不逃逸攻击者的 BaseException。"""

    def test_config_base_exception_does_not_escape(self):
        bad = _MetaBombIntBaseBoom(10)              # 构造在武装前完成
        _BASE_BOOM_ARMED["on"] = True
        try:
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=bad, timeout_ms=50,
                                     clock_ns=_ManualClock())
            with self.assertRaises(MonitorConfigError):
                SoftwareCycleMonitor(cycle_ms=10, timeout_ms=bad,
                                     clock_ns=_ManualClock())
        finally:
            _BASE_BOOM_ARMED["on"] = False

    def test_first_clock_base_exception_does_not_escape(self):
        bad = _MetaBombIntBaseBoom(0)
        _BASE_BOOM_ARMED["on"] = True
        try:
            mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                       clock_ns=_ScriptedClock([bad]))
            with self.assertRaises(MonitorClockError):
                mon.begin_cycle()
            self.assertFalse(mon.active)
            self.assertEqual(mon._seq, 0)
            self.assertEqual(mon._last_seen_ns, 0)
        finally:
            _BASE_BOOM_ARMED["on"] = False

    def test_midcycle_clock_base_exception_does_not_escape(self):
        bad = _MetaBombIntBaseBoom(1)
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([100 * _MS, bad]))
        tok = mon.begin_cycle()
        _BASE_BOOM_ARMED["on"] = True
        try:
            with self.assertRaises(MonitorClockError):
                mon.poll_timeout()
        finally:
            _BASE_BOOM_ARMED["on"] = False
        self.assertTrue(mon.active)
        self.assertEqual(mon.active_sequence, tok.sequence)
        self.assertEqual(mon._last_seen_ns, 100 * _MS)  # 未被非法值覆盖
        self.assertFalse(mon.has_pending_event)         # 未伪造事件
        self.assertIsNone(mon._latched_seq)

    def test_finish_clock_base_exception_does_not_escape(self):
        bad = _MetaBombIntBaseBoom(1)
        clock = _ScriptedClock([0, 60 * _MS, bad])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        event = mon.poll_timeout()                      # 合法事件先生成
        self.assertIsInstance(event, WatchdogTimeoutEvent)
        _BASE_BOOM_ARMED["on"] = True
        try:
            with self.assertRaises(MonitorClockError):
                mon.finish_cycle(tok)
        finally:
            _BASE_BOOM_ARMED["on"] = False
        self.assertTrue(mon.active)                     # active 未清
        self.assertIs(mon.poll_timeout(), event)        # pending 未被覆盖 / 重复
        self.assertEqual(mon._latched_seq, 1)


class TestDescriptorSideEffectZeroObservation(unittest.TestCase):
    """metaclass __name__ 数据描述符即使返回 exact str 也带副作用；零观察要求拒绝路径
    从不触发它，副作用计数在配置与三条时钟路径下恒为 0（证明不是“执行后捕获”）。"""

    def test_config_side_effect_never_observed(self):
        bad = _MetaBombIntSideEffect(10)
        _SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorConfigError):
            SoftwareCycleMonitor(cycle_ms=bad, timeout_ms=50,
                                 clock_ns=_ManualClock())
        with self.assertRaises(MonitorConfigError):
            SoftwareCycleMonitor(cycle_ms=10, timeout_ms=bad,
                                 clock_ns=_ManualClock())
        self.assertEqual(_SIDE_EFFECT_LOG, [])          # 从未读取动态类型名

    def test_first_clock_side_effect_never_observed(self):
        bad = _MetaBombIntSideEffect(0)
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([bad]))
        _SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorClockError):
            mon.begin_cycle()
        self.assertEqual(_SIDE_EFFECT_LOG, [])

    def test_midcycle_clock_side_effect_never_observed(self):
        bad = _MetaBombIntSideEffect(1)
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                   clock_ns=_ScriptedClock([100 * _MS, bad]))
        mon.begin_cycle()
        _SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorClockError):
            mon.poll_timeout()
        self.assertEqual(_SIDE_EFFECT_LOG, [])
        self.assertFalse(mon.has_pending_event)

    def test_finish_clock_side_effect_never_observed(self):
        bad = _MetaBombIntSideEffect(1)
        clock = _ScriptedClock([0, 60 * _MS, bad])
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        event = mon.poll_timeout()
        _SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorClockError):
            mon.finish_cycle(tok)
        self.assertEqual(_SIDE_EFFECT_LOG, [])
        self.assertIs(mon.poll_timeout(), event)


class _ReprBombNonCallable:
    """不可调用对象：__repr__ / __str__ 抛异常，验证不可调用拒绝路径不取其表示。"""

    def __repr__(self):
        raise RuntimeError("non-callable-repr-bomb")

    def __str__(self):
        raise RuntimeError("non-callable-str-bomb")


class _ReprBombFakeToken:
    """伪 token：非 CycleToken，且 __repr__ / __str__ 抛异常。"""

    def __repr__(self):
        raise RuntimeError("token-repr-bomb")

    def __str__(self):
        raise RuntimeError("token-str-bomb")


class _EvilTokenSubclass(CycleToken):
    """CycleToken 恶意子类：非 exact 类型，须被拒绝，避免其字段诊断执行用户代码。"""


class TestIllegalTokenClockCallbackZeroObservation(unittest.TestCase):
    """不可调用时钟 / 非 exact CycleToken / 不可调用 callback 的拒绝：稳定命中分层异常，
    诊断零观察（不取被拒对象表示 / 类型名），状态零推进。"""

    def _timed_out_mon(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(60 * _MS)
        mon.finish_cycle(tok)
        return mon

    def test_non_callable_clock_with_repr_bomb_rejected_safely(self):
        with self.assertRaises(MonitorConfigError) as cm:
            SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50,
                                 clock_ns=_ReprBombNonCallable())
        self.assertIsInstance(str(cm.exception), str)   # 不得抛 repr-bomb

    def test_fake_token_with_repr_bomb_rejected_no_state_change(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        with self.assertRaises(MonitorStateError) as cm:
            mon.finish_cycle(_ReprBombFakeToken())
        self.assertIn("CycleToken", str(cm.exception))  # 可安全 str 化
        # 零状态推进：active cycle 与序号不变，合法 token 仍可正常 finish
        self.assertTrue(mon.active)
        self.assertEqual(mon.active_sequence, tok.sequence)
        clock.advance(1 * _MS)
        self.assertIsInstance(mon.finish_cycle(tok), CycleObservation)

    def test_cycletoken_subclass_rejected_as_non_exact(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        real = mon.begin_cycle()
        evil = _EvilTokenSubclass(sequence=real.sequence, start_ns=real.start_ns,
                                  _owner_id=real._owner_id)
        with self.assertRaises(MonitorStateError):      # 非 exact CycleToken 拒绝
            mon.finish_cycle(evil)
        self.assertTrue(mon.active)                     # 状态零推进
        self.assertEqual(mon.active_sequence, real.sequence)
        # 真实 exact token 仍可正常 finish
        self.assertIsInstance(mon.finish_cycle(real), CycleObservation)

    def test_non_callable_callback_with_repr_bomb_rejected_event_kept(self):
        mon = self._timed_out_mon()
        with self.assertRaises(MonitorConfigError) as cm:
            mon.dispatch_pending(_ReprBombNonCallable())
        self.assertIsInstance(str(cm.exception), str)   # 不得抛 repr-bomb
        self.assertTrue(mon.has_pending_event)          # 事件未被消费


_TOKEN_FIELD_SIDE_EFFECT_LOG = []


class _TokenFieldBaseBoom:
    """exact CycleToken 的恶意字段：任何数值化 / 表示尝试都抛自定义 BaseException——
    验证非活动 token 拒绝路径**不读取字段**（否则 `%d` / repr 会逃逸该 BaseException）。"""

    def __int__(self):
        raise _MonBaseBoom("token-field-int-base-boom")

    def __index__(self):
        raise _MonBaseBoom("token-field-index-base-boom")

    def __repr__(self):
        raise _MonBaseBoom("token-field-repr-base-boom")

    def __str__(self):
        raise _MonBaseBoom("token-field-str-base-boom")


class _TokenFieldSideEffect:
    """exact CycleToken 的恶意字段：数值化 / 表示留可观察副作用后返回正常值——
    证明零观察不是“执行后捕获”（拒绝路径从不触发这些钩子，计数恒为 0）。"""

    def __int__(self):
        _TOKEN_FIELD_SIDE_EFFECT_LOG.append("__int__")
        return 0

    def __index__(self):
        _TOKEN_FIELD_SIDE_EFFECT_LOG.append("__index__")
        return 0

    def __repr__(self):
        _TOKEN_FIELD_SIDE_EFFECT_LOG.append("__repr__")
        return "0"

    def __str__(self):
        _TOKEN_FIELD_SIDE_EFFECT_LOG.append("__str__")
        return "0"


class TestExactForgedTokenZeroObservation(unittest.TestCase):
    """WP-047 Round 2：``CycleToken`` 是公开 dataclass，exact 类型不约束字段——外部可构造
    exact 类型但**非当前 active**、且字段恶意的伪票据。对它的拒绝必须固定可信文本、零观察、
    稳定 ``MonitorStateError``、状态零推进，且真实 active token 之后仍可正常完成。"""

    def _active_mon(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        real = mon.begin_cycle()                        # active 序号 1，_last_seen_ns=0
        return mon, clock, real

    def _assert_state_unadvanced(self, mon, real):
        self.assertTrue(mon.active)                     # active 未被清 / 换
        self.assertEqual(mon.active_sequence, real.sequence)
        self.assertEqual(mon._seq, 1)                   # 序号未推进
        self.assertEqual(mon._last_seen_ns, 0)          # 拒绝先于 _read_clock，未推进
        self.assertIsNone(mon._pending)                 # 未伪造事件
        self.assertIsNone(mon._latched_seq)             # 终态未推进

    def test_exact_forged_token_base_exception_does_not_escape(self):
        mon, clock, real = self._active_mon()
        forged = CycleToken(sequence=_TokenFieldBaseBoom(),
                            start_ns=_TokenFieldBaseBoom(),
                            _owner_id=_TokenFieldBaseBoom())
        self.assertIs(type(forged), CycleToken)         # exact 类型通过第一道门
        with self.assertRaises(MonitorStateError) as cm:
            mon.finish_cycle(forged)                    # 绝不逃逸 _MonBaseBoom
        msg = str(cm.exception)                         # 消息可安全 str 化
        self.assertIn("陈旧", msg)
        self.assertNotIn("_TokenFieldBaseBoom", msg)    # 零观察：不泄露攻击者字段类名
        self._assert_state_unadvanced(mon, real)
        # 真实 active token 之后仍可正常 finish
        clock.advance(1 * _MS)
        self.assertIsInstance(mon.finish_cycle(real), CycleObservation)

    def test_exact_forged_token_side_effect_never_observed(self):
        mon, clock, real = self._active_mon()
        forged = CycleToken(sequence=_TokenFieldSideEffect(),
                            start_ns=_TokenFieldSideEffect(),
                            _owner_id=_TokenFieldSideEffect())
        _TOKEN_FIELD_SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorStateError):
            mon.finish_cycle(forged)
        self.assertEqual(_TOKEN_FIELD_SIDE_EFFECT_LOG, [])  # 从未读取被拒 token 字段
        self._assert_state_unadvanced(mon, real)
        clock.advance(1 * _MS)
        self.assertIsInstance(mon.finish_cycle(real), CycleObservation)


# --- WP-047 Round 3：object.__setattr__ 篡改**当前 active** token 字段的零观察反证 ---
# CycleToken 虽 frozen=True，object.__setattr__ 仍可改写已交给调用方的 active 票据字段；
# 监视器须用不暴露给调用方的可信 _seq / _active_start_ns，内部计算 / 诊断绝不读取（哪怕是
# active 票据的）字段——否则攻击者字段钩子会逃逸 BaseException 并部分推进 _last_seen_ns。

class _ActiveFieldBaseBoom:
    """篡改后的 active token 字段：数值化 / 相减 / 表示尝试都抛自定义 BaseException——
    验证 finish / poll / 重复 begin / active_sequence 内部计算均不读取被篡改字段。"""

    def __int__(self):
        raise _MonBaseBoom("active-field-int-base-boom")

    def __index__(self):
        raise _MonBaseBoom("active-field-index-base-boom")

    def __rsub__(self, other):
        raise _MonBaseBoom("active-field-rsub-base-boom")

    def __sub__(self, other):
        raise _MonBaseBoom("active-field-sub-base-boom")

    def __repr__(self):
        raise _MonBaseBoom("active-field-repr-base-boom")

    def __str__(self):
        raise _MonBaseBoom("active-field-str-base-boom")


_ACTIVE_FIELD_SIDE_EFFECT_LOG = []


class _ActiveFieldSideEffect:
    """篡改后的 active token 字段：数值化 / 相减 / 表示留可观察副作用后返回正常值——
    证明零观察不是“执行后捕获”（内部计算从不触发这些钩子，计数恒为 0）。"""

    def __int__(self):
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.append("__int__")
        return 0

    def __index__(self):
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.append("__index__")
        return 0

    def __rsub__(self, other):
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.append("__rsub__")
        return 0

    def __sub__(self, other):
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.append("__sub__")
        return 0

    def __repr__(self):
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.append("__repr__")
        return "0"

    def __str__(self):
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.append("__str__")
        return "0"


class TestActiveTokenMutationZeroObservation(unittest.TestCase):
    """WP-047 Round 3：``CycleToken`` 虽 ``frozen=True``，``object.__setattr__`` 仍可改写
    已交给调用方的**当前 active** 票据字段。监视器保存不暴露给调用方的可信 ``_seq`` /
    ``_active_start_ns``，内部周期 / 超时计算与诊断绝不读取 token 字段：篡改 active token
    字段不得逃逸攻击者 ``BaseException``、不得留下副作用、不得部分推进 ``_last_seen_ns`` 等
    状态；``finish`` 用可信起点算出正确观测，真实 active token 全程仍可正常完成。"""

    def _active(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        real = mon.begin_cycle()                        # seq 1, start_ns 0, _last_seen 0
        return mon, clock, real

    # ---- finish_cycle：身份匹配→合法完成，但只用可信内部快照，不触发被篡改字段 ----

    def test_finish_ignores_mutated_active_fields_base_exception(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "start_ns", _ActiveFieldBaseBoom())
        object.__setattr__(real, "sequence", _ActiveFieldBaseBoom())
        clock.advance(5 * _MS)
        obs = mon.finish_cycle(real)                    # 绝不逃逸 _MonBaseBoom
        self.assertIsInstance(obs, CycleObservation)
        self.assertEqual(obs.sequence, 1)               # 内部可信序号，非被篡改字段
        self.assertEqual(obs.start_ns, 0)               # 内部可信起点
        self.assertEqual(obs.finish_ns, 5 * _MS)
        self.assertEqual(obs.elapsed_ns, 5 * _MS)
        self.assertFalse(obs.timed_out)
        self.assertFalse(mon.active)                    # 正常完成，active 清除

    def test_finish_never_observes_mutated_active_fields_side_effect(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "start_ns", _ActiveFieldSideEffect())
        object.__setattr__(real, "sequence", _ActiveFieldSideEffect())
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.clear()
        clock.advance(5 * _MS)
        obs = mon.finish_cycle(real)
        self.assertEqual(_ACTIVE_FIELD_SIDE_EFFECT_LOG, [])  # 从未读取被篡改字段
        self.assertEqual(obs.start_ns, 0)
        self.assertEqual(obs.sequence, 1)
        self.assertEqual(obs.elapsed_ns, 5 * _MS)

    # ---- poll_timeout：用可信起点 / 序号锁存事件，不触发被篡改字段 ----

    def test_poll_timeout_ignores_mutated_active_fields_base_exception(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "start_ns", _ActiveFieldBaseBoom())
        object.__setattr__(real, "sequence", _ActiveFieldBaseBoom())
        clock.advance(60 * _MS)                          # 越阈值
        ev = mon.poll_timeout()                          # 绝不逃逸 _MonBaseBoom
        self.assertIsInstance(ev, WatchdogTimeoutEvent)
        self.assertEqual(ev.sequence, 1)                 # 可信序号
        self.assertEqual(ev.start_ns, 0)                 # 可信起点
        self.assertEqual(ev.elapsed_ns, 60 * _MS)

    def test_poll_timeout_never_observes_mutated_active_fields_side_effect(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "start_ns", _ActiveFieldSideEffect())
        object.__setattr__(real, "sequence", _ActiveFieldSideEffect())
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.clear()
        clock.advance(60 * _MS)
        ev = mon.poll_timeout()
        self.assertEqual(_ACTIVE_FIELD_SIDE_EFFECT_LOG, [])
        self.assertEqual(ev.start_ns, 0)
        self.assertEqual(ev.sequence, 1)

    # ---- 重复 begin 诊断：用内部 _seq，不读被篡改 sequence，状态零推进 ----

    def test_duplicate_begin_diag_ignores_mutated_active_sequence(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "sequence", _ActiveFieldBaseBoom())
        before = (mon._seq, mon._last_seen_ns, mon._pending, mon._latched_seq)
        with self.assertRaises(MonitorStateError) as cm:
            mon.begin_cycle()                            # 已有 active → 拒绝
        self.assertNotIn("_ActiveFieldBaseBoom", str(cm.exception))
        self.assertEqual(
            (mon._seq, mon._last_seen_ns, mon._pending, mon._latched_seq), before)
        self.assertTrue(mon.active)

    def test_duplicate_begin_diag_side_effect_never_observed(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "sequence", _ActiveFieldSideEffect())
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorStateError):
            mon.begin_cycle()
        self.assertEqual(_ACTIVE_FIELD_SIDE_EFFECT_LOG, [])

    # ---- active_sequence 属性：返回可信 int，不读被篡改 sequence ----

    def test_active_sequence_property_returns_trusted_int(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "sequence", _ActiveFieldBaseBoom())
        self.assertEqual(mon.active_sequence, 1)         # 内部可信 _seq

    # ---- 篡改后真实 active token 仍可正常 finish，下一合法周期不受影响 ----

    def test_real_active_token_still_finishes_after_field_mutation(self):
        mon, clock, real = self._active()
        object.__setattr__(real, "start_ns", _ActiveFieldSideEffect())
        _ACTIVE_FIELD_SIDE_EFFECT_LOG.clear()
        clock.advance(3 * _MS)
        obs = mon.finish_cycle(real)                     # 身份仍匹配 → 合法完成
        self.assertEqual(obs.elapsed_ns, 3 * _MS)
        self.assertEqual(_ACTIVE_FIELD_SIDE_EFFECT_LOG, [])
        clock.advance(1 * _MS)
        tok2 = mon.begin_cycle()                         # 下一合法周期
        self.assertEqual(mon.active_sequence, 2)
        clock.advance(1 * _MS)
        self.assertIsInstance(mon.finish_cycle(tok2), CycleObservation)


class TestExactPathsRemainGreen(unittest.TestCase):
    """零观察收口后，exact 内建 int / 真实 CycleToken / 正常时钟 callback 的既有语义不退化。"""

    def test_exact_builtin_int_and_real_token_lifecycle(self):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        self.assertIs(type(tok), CycleToken)            # exact CycleToken
        clock.advance(60 * _MS)
        obs = mon.finish_cycle(tok)
        self.assertTrue(obs.timed_out)
        cb = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)


class TestSourceHygieneZeroObservation(unittest.TestCase):
    """源码卫生：生产 monitor 不再含动态类型名获取与不可信格式化。
    （测试自身的断言消息不受此限制——本检查只扫描 monitor 模块源码。）"""

    def test_no_dynamic_type_name_or_untrusted_format_in_source(self):
        src = inspect.getsource(monitor_module)
        for forbidden in ("_safe_type_name", "__getattribute__", ".__name__",
                          "%r", "repr("):
            self.assertNotIn(forbidden, src,
                             "monitor 生产源码不得含 %r（零观察：拒绝路径不读取动态类型名 / "
                             "不取不可信表示）" % (forbidden,))


# ---------------------------------------------------------------------------
# 16) WP-20260729-048 收口：pending ``WatchdogTimeoutEvent`` 外部别名零观察
#
#     WP-047 Round 3 Codex 独立审核确认的残留缺陷：``poll_timeout()`` / ``finish_cycle()``
#     生成并向调用方**返回**公开 ``WatchdogTimeoutEvent``，同时内部 ``_pending`` 保留**同一
#     实例**；``@dataclass(frozen=True)`` 仍可被 ``object.__setattr__`` 强制改写字段。因此
#     任何已暴露给调用方的事件字段都**不是内部可信状态**。唯一生产读取点是 ``begin_cycle()``
#     的 pending 拒绝分支——旧实现以 ``%d`` 格式化 ``self._pending.sequence``：把该字段换成
#     ``__int__`` / ``__index__`` 抛自定义 ``BaseException`` 的对象后，攻击者异常逃逸而非稳定
#     ``MonitorStateError``；换成带副作用返回内建 int 的对象后，消息被污染且攻击者钩子被调用。
#     以下反证锁定：pending 准入拒绝只用不暴露给调用方、由 exact-int 内部路径维护的可信
#     ``_latched_seq``，绝不读取任何公开事件字段；一次性派发 / 状态原子性 / 新周期隔离 /
#     重复 poll 同一实例的既有语义不退化；生产源码不再出现 ``_pending`` 的公开字段读取。
# ---------------------------------------------------------------------------

_PENDING_FIELD_SIDE_EFFECT_LOG = []

# WP-047 起被公开事件字段可承载的所有可能被内部误用的钩子：数值化（``%d`` / ``int()``）、
# 相减（elapsed 计算）与表示（repr / str）。零观察要求内部一次都不触发它们。
_PENDING_PUBLIC_FIELDS = (
    "sequence", "start_ns", "observed_ns", "elapsed_ns", "timeout_ns", "overrun_ns")


class _PendingFieldBaseBoom:
    """被 ``object.__setattr__`` 塞进公开 pending 事件的恶意字段：任何数值化 / 相减 / 表示
    尝试都抛自定义 ``BaseException``——验证 pending 准入拒绝路径**不读取事件任何字段**
    （否则 ``%d`` / ``repr`` 会逃逸该 ``BaseException``，绕过稳定 ``MonitorStateError``）。"""

    def __int__(self):
        raise _MonBaseBoom("pending-field-int-base-boom")

    def __index__(self):
        raise _MonBaseBoom("pending-field-index-base-boom")

    def __sub__(self, other):
        raise _MonBaseBoom("pending-field-sub-base-boom")

    def __rsub__(self, other):
        raise _MonBaseBoom("pending-field-rsub-base-boom")

    def __repr__(self):
        raise _MonBaseBoom("pending-field-repr-base-boom")

    def __str__(self):
        raise _MonBaseBoom("pending-field-str-base-boom")


class _PendingFieldSideEffect:
    """被 ``object.__setattr__`` 塞进公开 pending 事件的恶意字段：数值化 / 相减 / 表示
    留可观察副作用后返回正常值——证明零观察不是“执行后捕获”（拒绝路径从不触发这些钩子，
    副作用计数恒为 0）。"""

    def __int__(self):
        _PENDING_FIELD_SIDE_EFFECT_LOG.append("__int__")
        return 0

    def __index__(self):
        _PENDING_FIELD_SIDE_EFFECT_LOG.append("__index__")
        return 0

    def __sub__(self, other):
        _PENDING_FIELD_SIDE_EFFECT_LOG.append("__sub__")
        return 0

    def __rsub__(self, other):
        _PENDING_FIELD_SIDE_EFFECT_LOG.append("__rsub__")
        return 0

    def __repr__(self):
        _PENDING_FIELD_SIDE_EFFECT_LOG.append("__repr__")
        return "0"

    def __str__(self):
        _PENDING_FIELD_SIDE_EFFECT_LOG.append("__str__")
        return "0"


class TestPendingEventAliasZeroObservation(unittest.TestCase):
    """WP-048：内部保留的 pending ``WatchdogTimeoutEvent`` 与已交给调用方的是**同一实例**，
    ``frozen=True`` 仍可被 ``object.__setattr__`` 篡改字段。``begin_cycle()`` 的 pending 准入
    拒绝只用不暴露给调用方的可信 ``_latched_seq``，绝不读取任何公开事件字段：篡改后既不逃逸
    攻击者 ``BaseException``、不留副作用、不部分推进状态，也不丢失原 pending 身份。"""

    def _pending_mon(self, elapse=60 * _MS):
        clock = _ManualClock()
        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50, clock_ns=clock)
        tok = mon.begin_cycle()
        clock.advance(elapse)
        mon.finish_cycle(tok)                       # pending 保留、active 清除
        self.assertTrue(mon.has_pending_event)
        self.assertEqual(mon._latched_seq, 1)
        return mon, clock

    def _snapshot(self, mon):
        return (mon._seq, mon._active, mon._active_start_ns,
                mon._last_seen_ns, mon._pending, mon._latched_seq)

    # ---- 反证 1 + 3：篡改 sequence 为抛 BaseException 的对象，begin 拒绝零观察、状态原子 ----

    def test_begin_rejection_ignores_tampered_sequence_base_exception(self):
        mon, _clock = self._pending_mon()
        ev = mon._pending
        object.__setattr__(ev, "sequence", _PendingFieldBaseBoom())
        before = self._snapshot(mon)
        with self.assertRaises(MonitorStateError) as cm:     # 绝不逃逸 _MonBaseBoom
            mon.begin_cycle()
        msg = str(cm.exception)                              # 消息可安全 str 化
        self.assertIn("dispatch_pending", msg)
        self.assertIn("序号 1", msg)                          # 可信 _latched_seq，非被篡改字段
        self.assertNotIn("_PendingFieldBaseBoom", msg)       # 零观察：不泄露攻击者字段类名
        self.assertEqual(self._snapshot(mon), before)        # 全状态零推进
        self.assertIs(mon._pending, ev)                      # 原 pending 身份保留

    # ---- 反证 2 + 3：篡改 sequence 为带副作用对象，拒绝消息与字段无关、副作用恒空 ----

    def test_begin_rejection_never_observes_tampered_sequence_side_effect(self):
        mon, _clock = self._pending_mon()
        ev = mon._pending
        object.__setattr__(ev, "sequence", _PendingFieldSideEffect())
        _PENDING_FIELD_SIDE_EFFECT_LOG.clear()
        before = self._snapshot(mon)
        with self.assertRaises(MonitorStateError) as cm:
            mon.begin_cycle()
        self.assertEqual(_PENDING_FIELD_SIDE_EFFECT_LOG, [])  # 从未读取被篡改字段
        self.assertIn("序号 1", str(cm.exception))            # 拒绝消息只依赖可信 _latched_seq
        self.assertEqual(self._snapshot(mon), before)
        self.assertIs(mon._pending, ev)

    # ---- 反证 4：篡改**全部**公开字段，证明准入拒绝不只对 sequence 打补丁 ----

    def test_begin_rejection_ignores_all_tampered_public_fields_base_exception(self):
        mon, _clock = self._pending_mon()
        ev = mon._pending
        for field in _PENDING_PUBLIC_FIELDS:
            object.__setattr__(ev, field, _PendingFieldBaseBoom())
        before = self._snapshot(mon)
        with self.assertRaises(MonitorStateError):           # 绝不逃逸 _MonBaseBoom
            mon.begin_cycle()
        self.assertEqual(self._snapshot(mon), before)
        self.assertIs(mon._pending, ev)

    def test_begin_rejection_never_observes_all_tampered_public_fields_side_effect(self):
        mon, _clock = self._pending_mon()
        ev = mon._pending
        for field in _PENDING_PUBLIC_FIELDS:
            object.__setattr__(ev, field, _PendingFieldSideEffect())
        _PENDING_FIELD_SIDE_EFFECT_LOG.clear()
        with self.assertRaises(MonitorStateError):
            mon.begin_cycle()
        self.assertEqual(_PENDING_FIELD_SIDE_EFFECT_LOG, [])  # 任何公开字段都未被读取

    # ---- 反证 5：拒绝后一次性派发仍恰调用一次、调用前消费；三条 callback 语义不退化 ----

    def test_dispatch_after_tampered_rejection_calls_once_consume_before(self):
        mon, _clock = self._pending_mon()
        object.__setattr__(mon._pending, "sequence", _PendingFieldBaseBoom())
        with self.assertRaises(MonitorStateError):
            mon.begin_cycle()
        cb = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)              # 调用前已消费
        self.assertFalse(mon.dispatch_pending(cb))           # 第二次派发不再调用
        self.assertEqual(cb.calls, 1)

    def test_dispatch_after_tampered_rejection_watchdog_signal_no_replay(self):
        mon, _clock = self._pending_mon()
        object.__setattr__(mon._pending, "sequence", _PendingFieldBaseBoom())
        with self.assertRaises(MonitorStateError):
            mon.begin_cycle()
        cb = _CountingCallback(raises=WatchdogSafeCommit(
            safe_image={"DO0": False}, safe_commit_succeeded=True))
        with self.assertRaises(WatchdogSafeCommit):
            mon.dispatch_pending(cb)
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)
        self.assertFalse(mon.dispatch_pending(cb))           # 不重放二次安全提交
        self.assertEqual(cb.calls, 1)

    def test_dispatch_after_tampered_rejection_plain_exception_no_replay(self):
        mon, _clock = self._pending_mon()
        object.__setattr__(mon._pending, "sequence", _PendingFieldBaseBoom())
        with self.assertRaises(MonitorStateError):
            mon.begin_cycle()
        cb = _CountingCallback(raises=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            mon.dispatch_pending(cb)
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.dispatch_pending(cb))
        self.assertEqual(cb.calls, 1)

    # ---- 反证 6：pending 消费后下一合法周期可开始，序号单调、新周期独立锁存自己的事件 ----

    def test_next_cycle_independent_after_tampered_event_consumed(self):
        mon, clock = self._pending_mon()
        object.__setattr__(mon._pending, "sequence", _PendingFieldBaseBoom())
        with self.assertRaises(MonitorStateError):
            mon.begin_cycle()
        self.assertTrue(mon.dispatch_pending(lambda: None))  # 消费被篡改的旧事件
        clock.advance(5 * _MS)
        t2 = mon.begin_cycle()                               # 下一合法周期可开始
        self.assertEqual(t2.sequence, 2)                     # 内部序号单调推进
        self.assertEqual(mon.active_sequence, 2)
        clock.advance(50 * _MS)
        ev2 = mon.poll_timeout()                             # 新周期独立锁存自己的事件
        self.assertIsInstance(ev2, WatchdogTimeoutEvent)
        self.assertEqual(ev2.sequence, 2)
        self.assertEqual(mon._latched_seq, 2)                # 终态切到新序号，不受旧事件影响
        cb2 = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb2))
        self.assertEqual(cb2.calls, 1)

    # ---- 反证 7：重复 poll 仍返回同一 pending 实例、不生成第二事件；以可信状态判正确性 ----

    def test_repeated_poll_returns_same_tampered_instance_no_second_event(self):
        mon, clock = self._pending_mon()
        ev = mon._pending
        object.__setattr__(ev, "sequence", _PendingFieldBaseBoom())
        # 调用方看到自己篡改后的字段不等于内部污染：内部正确性以可信 _latched_seq /
        # 派发次数 / 状态推进为准，而非事件公开字段。
        self.assertIs(mon.poll_timeout(), ev)                # 同一实例（active 已 None）
        clock.advance(999 * _MS)
        self.assertIs(mon.poll_timeout(), ev)                # 仍不生成第二事件
        self.assertEqual(mon._latched_seq, 1)                # 可信终态未推进
        cb = _CountingCallback()
        self.assertTrue(mon.dispatch_pending(cb))            # 只有一个事件可派发
        self.assertEqual(cb.calls, 1)
        self.assertFalse(mon.has_pending_event)


class TestPendingAliasSourceHygiene(unittest.TestCase):
    """WP-048 源码卫生：生产 ``monitor.py`` 不再对内部保留的 pending ``WatchdogTimeoutEvent``
    做公开字段读取（如 ``self._pending`` 后接属性访问）。若未来确需读取，必须先建立不暴露给
    调用方的可信快照并新增相应反证——彼时应先更新本卫生断言。"""

    def test_no_internal_pending_public_field_read_in_source(self):
        src = inspect.getsource(monitor_module)
        # ``_pending`` 只允许作身份判断（``is None`` / ``is not None``）、赋值与整体返回；
        # 任何 ``_pending`` 后紧跟 ``.`` 的属性访问都是对公开事件字段的内部读取，一律禁止。
        self.assertNotIn(
            "_pending.", src,
            "monitor 生产源码不得出现 _pending 后接属性访问（公开事件字段对内部不可信）")


if __name__ == "__main__":
    unittest.main()
