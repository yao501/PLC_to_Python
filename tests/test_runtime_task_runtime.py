"""WP-20260730-049：阶段 1 单任务运行栈纵向装配与手搭 TON 最小程序端到端验收。

逐条对应任务书"手搭最小程序与纵向验收 / 装配失败关闭 / 对象图身份"清单
（``git diff --check`` 由 Codex 交接后独立执行）：

1. **默认 shadow 冷启动**：``initial_safety=None`` → 冷启动失败关闭快照使全部输出走
   ``safe_value``；默认 shadow 首拍返回 ``ShadowScanResult``、底层驱动 / 监督器零调用。
2. **手搭 TON 最小程序 N 拍**：内存 IR 单任务（``Start/Stop`` BOOL 输入、``Motor`` BOOL
   输出、Registry 中 ``TON`` 实例，``Motor = TON.Q AND NOT Stop``），all-ok 后按固定
   500ms 推进，对照直接 ``TON.step(500,...)`` 证明 ``Q/ET_ms/Motor``、Stop 抑制、``prev``
   前移确定可重复。
3. **显式退出 shadow**：先把请求回到安全状态，``set_write_enabled(True)`` 后首个实写拍
   只经同一 ``CommitSupervisor`` / 驱动提交恰一次并得确认回执。
4. **对象图身份**：``runtime.task is task``、``layout``、``executor``、policy/port/runner
   共享、``policy.safety_state is assembly.safety_state``、``monitor.cycle_ns`` 精确来自
   ``Task.cycle_ms``；无第二套 Store / 策略 / 提交监督 / 安全状态。
5. **双实例隔离**：同一 ``Task`` / ``Registry`` 建两套 assembly，Store / Executor / TON
   状态 / 安全 / 策略 / 监督器 / 端口 / runner / monitor 均不共享，交错推进不串拍。
6. **monitor E2E**：手工 exact-int 纳秒时钟，正常 begin→scan→finish 无事件；超时锁存并
   ``dispatch_pending(runner.trigger_watchdog)`` 恰消费一次得 ``WatchdogSafeCommit``，业务
   IR/prev 不因事件推进、shadow 下驱动零调用；第二次 dispatch 不重放。
7. **scan fault / commit fault 分层**：扫描前/执行错误经既有 runner 形成
   ``ScanFaultSafeCommit``；提交已尝试后失败按既有 ``commit_fault`` 路径原样上抛。
8. **装配失败原子性**：非法 Task / 缺失 OutputPolicy / 非法 driver / watchdog / initial_safety
   / numeric_mode 由既有层稳定拒绝；全路径 driver 调用为 0、无 assembly 返回。

诚实边界：本文件锁定的是当前 Python 单任务生产形态对象图与确定性 E2E 契约——真实
已覆盖调用方显式注入 readiness 的确定性 startup inhibit 计时 / 释放子范围；
真实调度、多任务、外部信号源、HAL / 硬件 / 现场均**不在本包**；
这些测试**不构成**与 CODESYS PLC 语义、实时、硬件 watchdog 或现场安全一致的证据。
"""
from __future__ import annotations

import time
import unittest

from src.runtime import (
    BinOp,
    CallFb,
    COLD_START_SAFETY,
    CommitSupervisorConfigError,
    InstanceDecl,
    IOMap,
    IRExecutionError,
    LoadConst,
    LoadVar,
    MonitorConfigError,
    OutputPolicy,
    OutputPolicyConfigError,
    PartialCommitError,
    POUDefinition,
    ProgramInstance,
    SafetySnapshot,
    SafetyStateService,
    ReadinessSnapshot,
    ReadinessConfigError,
    ReadinessClockError,
    SafetyStateError,
    ScanFaultSafeCommit,
    ScanResult,
    StartupValidationError,
    StoreVar,
    Task,
    TaskRuntimeAssembly,
    UnOp,
    VarDecl,
    WatchdogSafeCommit,
    build_default_registry,
    build_task_runtime,
    persistent_key,
)
from src.runtime.scan_runner import ShadowScanResult, WriteGate
from src.primitives.timers import TON


# ---------------------------------------------------------------------------
# 测试替身：逐批确认回执驱动（回显=全成功）+ 可控失败驱动 + 手工纳秒时钟
# ---------------------------------------------------------------------------

class _ConfirmingDriver:
    """回显命令值（全通道确认成功）并记录每次物理提交。"""

    def __init__(self):
        self.commands = []

    def commit(self, commands):
        self.commands.append(dict(commands))
        return dict(commands)          # 逐通道确认值严格等于命令值 → 可信成功


class _RaisingDriver:
    """物理提交时整批抛异常（用于 commit_fault 归类）。"""

    def __init__(self):
        self.commands = []

    def commit(self, commands):
        self.commands.append(dict(commands))
        raise RuntimeError("driver boom")


class _ManualClock:
    """手工 exact-int 纳秒时钟（单调不回退），供 monitor E2E 注入。"""

    def __init__(self):
        self.now_ns = 0
        self.mutate = None

    def __call__(self):
        if self.mutate is not None:
            self.mutate()
        return self.now_ns

    def advance_ms(self, ms):
        self.now_ns += int(ms) * 1_000_000


# ---------------------------------------------------------------------------
# 手搭单任务最小程序（内存 IR）：Motor = TON.Q AND NOT Stop
# ---------------------------------------------------------------------------

def _min_task(*, motor_safe=False):
    """cycle_ms=500 固定单任务：Start/Stop BOOL 输入、Motor BOOL 输出、TON 实例。"""
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Stop", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Stop", "DI1", "IN"),
        IOMap("Motor", "DO0", "OUT",
              policy=OutputPolicy("Motor", "BOOL", motor_safe)),
    ]
    code = [
        LoadVar("Start", "BOOL"), StoreVar("T1.IN", "BOOL"),
        LoadConst(1000, "TIME"), StoreVar("T1.PT_ms", "TIME"),
        CallFb("T1"),
        LoadVar("T1.Q", "BOOL"),
        LoadVar("Stop", "BOOL"), UnOp("NOT", "BOOL"),
        BinOp("AND", "BOOL"),
        StoreVar("Motor", "BOOL"),
    ]
    main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code,
                         instances=[InstanceDecl("T1", "TON", kind="library")])
    return Task(cycle_ms=500, programs=[ProgramInstance("Main", "PLC_PRG")],
                gvl=gvl, io_map=io_map, pou_lib={"Main": main})


def _build(*, driver=None, watchdog_timeout_ms=2000, initial_safety=None,
           clock_ns=time.monotonic_ns, startup_inhibit_ms=None,
           task=None, registry=None):
    task = task if task is not None else _min_task()
    registry = registry if registry is not None else build_default_registry()
    driver = driver if driver is not None else _ConfirmingDriver()
    a = build_task_runtime(task, registry, driver=driver,
                           watchdog_timeout_ms=watchdog_timeout_ms,
                           initial_safety=initial_safety, clock_ns=clock_ns,
                           startup_inhibit_ms=startup_inhibit_ms)
    return a, driver


def _readiness(**changes):
    values = dict(io_ready=True, bus_ready=True, comm_ready=True,
                  safety_ok=True, interlock_ok=True, output_enable=True)
    values.update(changes)
    return ReadinessSnapshot(**values)


_QK = persistent_key("PLC_PRG.T1", "Q")
_EK = persistent_key("PLC_PRG.T1", "ET_ms")


# ---------------------------------------------------------------------------
# 1) 默认 shadow 冷启动：安全值、诚实 shadow、零物理调用
# ---------------------------------------------------------------------------

class TestDefaultShadowColdStart(unittest.TestCase):

    def test_cold_start_snapshot_frozen_fields(self):
        s = COLD_START_SAFETY
        self.assertIsInstance(s, SafetySnapshot)
        self.assertFalse(s.system_ready)
        self.assertFalse(s.output_enable)
        self.assertFalse(s.comm_ok)
        self.assertFalse(s.safety_ok)
        self.assertFalse(s.interlock_ok)
        self.assertTrue(s.scan_ok)
        self.assertTrue(s.watchdog_ok)

    def test_default_initial_safety_is_cold_start(self):
        a, _ = _build()
        self.assertEqual(a.safety_state.read(), COLD_START_SAFETY)

    def test_cold_start_forces_safe_value_shadow_zero_driver(self):
        a, drv = _build()
        self.assertTrue(a.runner.shadow)
        self.assertFalse(a.runner.writes_enabled)
        r = a.runner.scan_cycle({"DI0": False, "DI1": False})
        self.assertIsInstance(r, ShadowScanResult)
        self.assertFalse(r.physically_committed)
        # 冷启动强制 safe：Motor = safe_value False；零物理提交
        self.assertEqual(r.logical_outputs(), {"DO0": False})
        self.assertEqual(drv.commands, [])
        self.assertEqual(a.commit_port.attempts, 0)
        for st in a.commit_supervisor.diagnostics().values():
            self.assertIsNone(st.last_physical_committed)

    def test_cold_start_forces_safe_even_if_request_would_be_true(self):
        # 非零 safe_value：即便业务逻辑本会算出 True，冷启动安全快照仍强制落 safe_value。
        a, drv = _build(task=_min_task(motor_safe=True))
        # Start=True 两拍后 Q 本会为 True，但冷启动 safety_trip 强制 safe_value=True
        r1 = a.runner.scan_cycle({"DI0": True, "DI1": False})
        r2 = a.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(r1.logical_outputs(), {"DO0": True})   # == safe_value
        self.assertEqual(r2.logical_outputs(), {"DO0": True})
        self.assertEqual(drv.commands, [])


class TestStartupReadinessAssembly(unittest.TestCase):
    """WP-060 双状态域原子接入与 WP-055 TOCTOU 反证。"""

    def test_unmodified_failures_preserve_exact_safety_snapshot_identity(self):
        class _ClockAbort(BaseException):
            pass

        def invalid_readiness(_assembly, _clock):
            readiness = _readiness()
            object.__setattr__(readiness, "io_ready", 1)
            return readiness

        failure_factories = (
            (ReadinessConfigError, invalid_readiness),
            (ReadinessClockError,
             lambda a, clock: (setattr(clock, "now_ns", True), _readiness())[1]),
            (ReadinessClockError,
             lambda a, clock: (setattr(clock, "mutate",
                                       lambda: (_ for _ in ()).throw(RuntimeError("clock"))),
                               _readiness())[1]),
            (ReadinessClockError,
             lambda a, clock: (setattr(clock, "mutate",
                                       lambda: (_ for _ in ()).throw(_ClockAbort())),
                               _readiness())[1]),
        )
        for expected, arrange in failure_factories:
            with self.subTest(expected=expected, arrange=arrange):
                clock = _ManualClock()
                a, _ = _build(clock_ns=clock)
                before = a.safety_state.read()
                readiness = arrange(a, clock)
                with self.assertRaises(expected):
                    a.apply_readiness(readiness)
                self.assertIs(a.safety_state.read(), before)
                self.assertEqual(a.startup_controller.last_seen_ns, 0)

    def test_instance_replace_exception_wrappers_are_not_observed(self):
        class _CallbackAbort(BaseException):
            pass

        for error in (RuntimeError("callback"), _CallbackAbort()):
            with self.subTest(error_type=type(error)):
                a, _ = _build(startup_inhibit_ms=0)
                replace_calls = 0

                def fail_replace(_snapshot, error=error):
                    nonlocal replace_calls
                    replace_calls += 1
                    raise error

                a.safety_state.replace = fail_replace
                self.assertTrue(a.apply_readiness(_readiness()).system_ready)
                self.assertEqual(replace_calls, 0)
                self.assertTrue(
                    SafetyStateService.read(a.safety_state).system_ready)

    def test_explicit_readiness_releases_after_window_and_preserves_fault_latches(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        a.safety_state.replace(SafetySnapshot(True, True, True, True, True,
                                              False, False))
        self.assertFalse(a.apply_readiness(_readiness()).system_ready)
        clock.advance_ms(500)
        result = a.apply_readiness(_readiness())
        self.assertTrue(result.system_ready)
        state = a.safety_state.read()
        self.assertTrue(state.system_ready)
        self.assertTrue(state.output_enable)
        self.assertFalse(state.scan_ok)
        self.assertFalse(state.watchdog_ok)

    def test_output_enable_is_independent_from_readiness_release_and_gates_output(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)

        self.assertFalse(a.apply_readiness(
            _readiness(output_enable=False)).system_ready)
        clock.advance_ms(500)
        result = a.apply_readiness(_readiness(output_enable=False))
        self.assertTrue(result.system_ready)
        self.assertFalse(result.output_enable)

        safety = a.safety_state.read()
        self.assertTrue(safety.system_ready)
        self.assertFalse(safety.output_enable)
        self.assertTrue(safety.comm_ok)
        self.assertTrue(safety.safety_ok)
        self.assertTrue(safety.interlock_ok)

        # 前五项只决定 startup 释放；独立操作员门仍使输出落 safe_value。
        a.runner.scan_cycle({"DI0": True, "DI1": False})
        scan = a.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(scan.logical_outputs(), {"DO0": False})

    def test_clock_reentry_rejects_ordinary_and_base_exceptions_without_progress(self):
        class _ClockAbort(BaseException):
            pass

        for mode in ("ordinary", "base"):
            with self.subTest(mode=mode):
                clock = _ManualClock()
                a, _ = _build(clock_ns=clock)
                before_safety = a.safety_state.read()
                before_controller = (a.startup_controller._last_seen_ns,
                                     a.startup_controller._window_start_ns,
                                     a.startup_controller._released)

                def reenter():
                    try:
                        a.apply_readiness(_readiness())
                    except ReadinessConfigError:
                        if mode == "base":
                            raise _ClockAbort()
                        raise

                clock.mutate = reenter

                with self.assertRaises(ReadinessClockError) as raised:
                    a.apply_readiness(_readiness())

                expected_cause = (_ClockAbort if mode == "base"
                                  else ReadinessConfigError)
                self.assertIsInstance(raised.exception.__cause__, expected_cause)
                self.assertEqual((a.startup_controller._last_seen_ns,
                                  a.startup_controller._window_start_ns,
                                  a.startup_controller._released),
                                 before_controller)
                self.assertIs(a.safety_state.read(), before_safety)

                clock.mutate = None
                self.assertFalse(a.apply_readiness(_readiness()).system_ready)

    def test_zero_inhibit_clock_reentry_cannot_half_commit_and_guard_recovers(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock, startup_inhibit_ms=0)
        before_safety = a.safety_state.read()
        clock.mutate = lambda: a.apply_readiness(_readiness())

        with self.assertRaises(ReadinessClockError):
            a.apply_readiness(_readiness())

        self.assertEqual((a.startup_controller._last_seen_ns,
                          a.startup_controller._window_start_ns,
                          a.startup_controller._released),
                         (0, None, False))
        self.assertIs(a.safety_state.read(), before_safety)
        self.assertFalse(before_safety.system_ready)

        clock.mutate = None
        self.assertTrue(a.apply_readiness(_readiness()).system_ready)
        self.assertTrue(a.safety_state.read().system_ready)

    def test_instance_replace_reentry_wrapper_is_not_observed(self):
        a, _ = _build(startup_inhibit_ms=0)
        replace_calls = 0

        def reentrant_replace(_snapshot):
            nonlocal replace_calls
            replace_calls += 1
            a.apply_readiness(_readiness())

        a.safety_state.replace = reentrant_replace
        self.assertTrue(a.apply_readiness(_readiness()).system_ready)
        self.assertEqual(replace_calls, 0)
        self.assertTrue(SafetyStateService.read(a.safety_state).system_ready)

    def test_instance_read_replace_collusion_cannot_fake_commit(self):
        a, _ = _build(startup_inhibit_ms=0)
        forged = SafetySnapshot.all_ok()
        read_calls = 0
        replace_calls = 0

        def fake_read():
            nonlocal read_calls
            read_calls += 1
            return forged

        def fake_replace(_snapshot):
            nonlocal replace_calls
            replace_calls += 1

        a.safety_state.read = fake_read
        a.safety_state.replace = fake_replace

        self.assertTrue(a.apply_readiness(_readiness()).system_ready)
        self.assertEqual((read_calls, replace_calls), (0, 0))
        committed = SafetyStateService.read(a.safety_state)
        self.assertTrue(committed.system_ready)
        self.assertTrue(committed.output_enable)

    def test_recovery_ignores_instance_wrappers_for_ordinary_and_base_exception(self):
        class _ClockAbort(BaseException):
            pass

        for error in (RuntimeError("clock"), _ClockAbort()):
            with self.subTest(error_type=type(error)):
                clock = _ManualClock()
                a, _ = _build(clock_ns=clock)
                trusted = SafetyStateService.read(a.safety_state)
                read_calls = 0
                replace_calls = 0

                def fake_read():
                    nonlocal read_calls
                    read_calls += 1
                    return trusted

                def fake_replace(_snapshot):
                    nonlocal replace_calls
                    replace_calls += 1
                    raise AssertionError("instance replace wrapper was observed")

                def pollute_then_abort(error=error):
                    SafetyStateService.replace(
                        a.safety_state, SafetySnapshot.all_ok())
                    raise error

                a.safety_state.read = fake_read
                a.safety_state.replace = fake_replace
                clock.mutate = pollute_then_abort

                with self.assertRaises(ReadinessClockError) as raised:
                    a.apply_readiness(_readiness())

                self.assertIsInstance(raised.exception.__cause__, type(error))
                self.assertEqual((read_calls, replace_calls), (0, 0))
                self.assertEqual(
                    SafetyStateService.read(a.safety_state), trusted)
                self.assertEqual((a.startup_controller._last_seen_ns,
                                  a.startup_controller._window_start_ns,
                                  a.startup_controller._released),
                                 (0, None, False))

    def test_initial_instance_read_fake_view_cannot_replace_real_latches(self):
        initial = SafetySnapshot(False, False, False, False, False, False, True)
        a, _ = _build(initial_safety=initial, startup_inhibit_ms=0)
        fake = SafetySnapshot.all_ok()
        read_calls = 0

        def fake_read():
            nonlocal read_calls
            read_calls += 1
            return fake

        a.safety_state.read = fake_read

        self.assertTrue(a.apply_readiness(_readiness()).system_ready)
        self.assertEqual(read_calls, 0)
        committed = SafetyStateService.read(a.safety_state)
        self.assertFalse(committed.scan_ok)
        self.assertTrue(committed.watchdog_ok)

    def test_real_clock_drift_cannot_be_hidden_by_instance_read(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        trusted = SafetyStateService.read(a.safety_state)
        read_calls = 0
        replace_calls = 0

        def fake_read():
            nonlocal read_calls
            read_calls += 1
            return trusted

        def fake_replace(_snapshot):
            nonlocal replace_calls
            replace_calls += 1

        a.safety_state.read = fake_read
        a.safety_state.replace = fake_replace
        clock.mutate = lambda: SafetyStateService.replace(
            a.safety_state, SafetySnapshot.all_ok())

        with self.assertRaises(ReadinessConfigError):
            a.apply_readiness(_readiness())

        self.assertEqual((read_calls, replace_calls), (0, 0))
        self.assertEqual(SafetyStateService.read(a.safety_state), trusted)
        self.assertEqual((a.startup_controller._last_seen_ns,
                          a.startup_controller._window_start_ns,
                          a.startup_controller._released),
                         (0, None, False))

    def test_clock_safety_replacement_aborts_both_domains_before_controller_commit(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        baseline = a.safety_state.read()
        injected = SafetySnapshot.all_ok()
        def replace_state():
            a.safety_state.replace(injected)
        clock.mutate = replace_state
        with self.assertRaises(ReadinessConfigError):
            a.apply_readiness(_readiness())
        self.assertFalse(a.startup_controller.released)
        self.assertEqual(a.safety_state.read(), baseline)
        self.assertIsNot(a.safety_state.read(), injected)

    def test_clock_base_exception_after_safety_pollution_restores_both_domains(self):
        class _ClockAbort(BaseException):
            pass
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        before = a.safety_state.read()
        def corrupt_then_abort():
            object.__setattr__(before, "scan_ok", False)
            raise _ClockAbort()
        clock.mutate = corrupt_then_abort
        with self.assertRaises(ReadinessClockError):
            a.apply_readiness(_readiness())
        self.assertFalse(a.startup_controller.released)
        self.assertEqual(a.startup_controller.last_seen_ns, 0)
        self.assertEqual(a.safety_state.read(),
                         SafetySnapshot(False, False, False, False, False, True, True))

    def test_clock_exception_after_safety_replacement_restores_both_domains(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        def replace_then_abort():
            a.safety_state.replace(SafetySnapshot.all_ok())
            raise RuntimeError("clock failure")
        clock.mutate = replace_then_abort
        with self.assertRaises(ReadinessClockError):
            a.apply_readiness(_readiness())
        self.assertFalse(a.startup_controller.system_ready)
        self.assertEqual(a.safety_state.read(),
                         SafetySnapshot(False, False, False, False, False, True, True))

    def test_clock_original_snapshot_delete_restores_before_semantics(self):
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        before = a.safety_state.read()
        def delete_latch():
            object.__delattr__(before, "watchdog_ok")
        clock.mutate = delete_latch
        with self.assertRaises(ReadinessConfigError):
            a.apply_readiness(_readiness())
        self.assertEqual(a.safety_state.read(),
                         SafetySnapshot(False, False, False, False, False, True, True))
        self.assertFalse(a.startup_controller.released)

    def test_clock_watchdog_latch_during_readiness_is_not_resurrected(self):
        # 反证（Codex WP-20260804-071 Round 1 P1）：readiness 时钟窗口内经真实公开
        # 路径 runner.trigger_watchdog() 锁存 watchdog_ok=False（受信 replace 装入
        # 新快照）后，_read_clock 把 WatchdogSafeCommit 包成 ReadinessClockError；
        # 旧候选的异常恢复整包恢复到时钟前副本，把真实 watchdog 故障锁存复位为 True。
        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)
        self.assertTrue(a.safety_state.read().watchdog_ok)     # 冷启动 watchdog_ok=True

        def latch_watchdog():
            clock.mutate = None                                # 防经本时钟递归
            a.runner.trigger_watchdog()                        # 锁存后抛 WatchdogSafeCommit

        clock.mutate = latch_watchdog
        with self.assertRaises(ReadinessClockError):
            a.apply_readiness(_readiness())
        # 真实 watchdog 故障锁存必须保留，绝不被 readiness 异常恢复复位为真。
        self.assertFalse(a.safety_state.read().watchdog_ok)
        self.assertTrue(a.safety_state.read().scan_ok)         # 本路径未触碰 scan_ok
        # readiness 失败不提交 controller。
        self.assertFalse(a.startup_controller.released)
        self.assertEqual(a.startup_controller.last_seen_ns, 0)

    def test_clock_scan_fault_latch_during_readiness_is_not_resurrected(self):
        # 同一缺陷的 scan_fault 路径：runner.scan_cycle({}) 执行期异常锁存
        # scan_ok=False 后抛 ScanFaultSafeCommit → ReadinessClockError。
        clock = _ManualClock()
        a, _ = _build(task=_fault_task(), clock_ns=clock)
        self.assertTrue(a.safety_state.read().scan_ok)

        def latch_scan_fault():
            clock.mutate = None
            a.runner.scan_cycle({})                            # 锁存 scan_ok=False 后抛

        clock.mutate = latch_scan_fault
        with self.assertRaises(ReadinessClockError):
            a.apply_readiness(_readiness())
        self.assertFalse(a.safety_state.read().scan_ok)
        self.assertTrue(a.safety_state.read().watchdog_ok)
        self.assertFalse(a.startup_controller.released)
        self.assertEqual(a.startup_controller.last_seen_ns, 0)

    def test_base_exception_after_public_watchdog_latch_preserves_latch(self):
        # BaseException 恢复路径也必须保留真实锁存（不只普通异常）。
        class _ClockAbort(BaseException):
            pass

        clock = _ManualClock()
        a, _ = _build(clock_ns=clock)

        def latch_then_base_abort():
            clock.mutate = None
            try:
                a.runner.trigger_watchdog()
            except WatchdogSafeCommit:
                pass
            raise _ClockAbort()

        clock.mutate = latch_then_base_abort
        with self.assertRaises(ReadinessClockError) as raised:
            a.apply_readiness(_readiness())
        self.assertIsInstance(raised.exception.__cause__, _ClockAbort)
        self.assertFalse(a.safety_state.read().watchdog_ok)
        self.assertFalse(a.startup_controller.released)
        self.assertEqual(a.startup_controller.last_seen_ns, 0)

    def test_default_snapshots_are_independent_of_public_constant_pollution(self):
        a1, _ = _build()
        original = COLD_START_SAFETY.scan_ok
        try:
            object.__setattr__(COLD_START_SAFETY, "scan_ok", False)
            a2, _ = _build()
            self.assertTrue(a1.safety_state.read().scan_ok)
            self.assertTrue(a2.safety_state.read().scan_ok)
        finally:
            object.__setattr__(COLD_START_SAFETY, "scan_ok", original)

    def test_two_assemblies_have_independent_startup_windows(self):
        clock_a, clock_b = _ManualClock(), _ManualClock()
        a, _ = _build(clock_ns=clock_a)
        b, _ = _build(clock_ns=clock_b)
        a.apply_readiness(_readiness())
        clock_a.advance_ms(500)
        self.assertTrue(a.apply_readiness(_readiness()).system_ready)
        self.assertFalse(b.startup_controller.system_ready)


# ---------------------------------------------------------------------------
# 2) 手搭 TON 最小程序 N 拍：对照直接 TON.step，Stop 抑制、prev 前移
# ---------------------------------------------------------------------------

class TestTonMinimalProgramE2E(unittest.TestCase):

    def test_ton_matches_direct_step_with_stop_suppression(self):
        a, drv = _build()
        a.safety_state.replace(SafetySnapshot.all_ok())        # 显式建立 all-ok
        ref = TON()
        prev_prev = a.engine.prev
        # 5 拍：Stop 在第 4 拍拉起验证抑制；对照参考 TON 逐拍
        stop_plan = [False, False, False, True, False]
        for i, stop in enumerate(stop_plan):
            r = a.runner.scan_cycle({"DI0": True, "DI1": stop})
            q, et = ref.step(500, IN=True, PT_ms=1000)
            self.assertEqual(a.store.read(_QK), q, "cycle %d Q" % i)
            self.assertEqual(a.store.read(_EK), et, "cycle %d ET" % i)
            expected_motor = q and not stop
            self.assertEqual(r.logical_outputs(), {"DO0": expected_motor},
                             "cycle %d Motor" % i)
            # prev 每拍前移（shadow 第 5 步只算不写仍推进）
            self.assertIsNot(a.engine.prev, prev_prev)
            prev_prev = a.engine.prev
        # 到点后 Q=True；第 4 拍 Stop 抑制 Motor 为 False
        self.assertIs(a.store.read(_QK), True)
        self.assertEqual(a.store.read(_EK), 1000)

    def test_repeatable_two_fresh_assemblies_same_trajectory(self):
        # 可重复：两套全新 assembly 同输入序列得到同一 Q/ET 轨迹
        traj = []
        for _ in range(2):
            a, _ = _build()
            a.safety_state.replace(SafetySnapshot.all_ok())
            seq = []
            for _ in range(3):
                a.runner.scan_cycle({"DI0": True, "DI1": False})
                seq.append((a.store.read(_QK), a.store.read(_EK)))
            traj.append(seq)
        self.assertEqual(traj[0], traj[1])


# ---------------------------------------------------------------------------
# 3) 显式退出 shadow：安全请求 → set_write_enabled(True) → 恰一次物理提交 + 回执
# ---------------------------------------------------------------------------

class TestExitShadowRealWrite(unittest.TestCase):

    def test_first_real_write_single_commit_with_receipt(self):
        a, drv = _build()
        a.safety_state.replace(SafetySnapshot.all_ok())
        # 先把请求回到安全状态（Start=False → TON 复位、Motor False）
        a.runner.scan_cycle({"DI0": False, "DI1": False})
        self.assertEqual(drv.commands, [])                     # 仍 shadow，零物理写
        # 显式退出 shadow
        a.runner.set_write_enabled(True)
        self.assertTrue(a.runner.writes_enabled)
        r = a.runner.scan_cycle({"DI0": False, "DI1": False})
        self.assertIsInstance(r, ScanResult)                   # 实写返回 ScanResult
        self.assertNotIsInstance(r, ShadowScanResult)
        self.assertEqual(r.outputs(), {"DO0": False})
        # 恰一次物理提交，经同一 CommitSupervisor / 驱动，得确认回执
        self.assertEqual(drv.commands, [{"DO0": False}])
        self.assertEqual(a.commit_port.attempts, 1)
        diag = a.commit_supervisor.diagnostics()["DO0"]
        self.assertIs(diag.last_physical_committed, False)
        self.assertFalse(diag.commit_fault)
        self.assertFalse(diag.channel_fault)
        self.assertTrue(a.commit_supervisor.last_commit_receipts()["DO0"].ok)


# ---------------------------------------------------------------------------
# 4) 对象图身份：同一对象图、无第二套状态
# ---------------------------------------------------------------------------

class TestObjectGraphIdentity(unittest.TestCase):

    def test_single_object_graph_shared_instances(self):
        task = _min_task()
        a, drv = _build(task=task, watchdog_timeout_ms=1234)
        # runtime / engine 绑定同一 task / layout / executor
        self.assertIs(a.runtime.task, task)
        self.assertIs(a.engine.task, task)
        self.assertIs(a.runtime.layout, a.engine.layout)
        self.assertIs(a.layout, a.engine.layout)
        self.assertIs(a.runtime.executor, a.engine.executor)
        self.assertIs(a.executor, a.engine.executor)
        self.assertIs(a.store, a.layout.store)
        # 策略 / 端口 / 安全状态共享（引擎、runner、监督器读到同一实例）
        self.assertIs(a.engine._policy, a.output_policy)
        self.assertIs(a.engine._committer, a.commit_port)
        self.assertIs(a.runner._policy, a.output_policy)
        self.assertIs(a.runner._port, a.commit_port)
        self.assertIs(a.output_policy.safety_state, a.safety_state)
        self.assertIs(a.commit_supervisor.policy, a.output_policy)
        # monitor cycle_ns 精确来自 Task.cycle_ms；timeout 来自入参
        self.assertEqual(a.monitor.cycle_ns, task.cycle_ms * 1_000_000)
        self.assertEqual(a.monitor.timeout_ns, 1234 * 1_000_000)
        # 默认 shadow：端口自带 WriteGate、runner 采用同一门
        self.assertIsInstance(a.commit_port.write_gate, WriteGate)
        self.assertFalse(a.commit_port.write_gate.writes_enabled)

    def test_convenience_properties_forward_not_copy(self):
        a, _ = _build()
        self.assertIs(a.task, a.runtime.task)
        self.assertIs(a.layout, a.runtime.layout)
        self.assertIs(a.store, a.runtime.store)
        self.assertIs(a.executor, a.runtime.executor)
        self.assertEqual(a.startup_inhibit_ms, a.runtime.startup_inhibit_ms)
        self.assertIs(a.warnings, a.runtime.warnings)


# ---------------------------------------------------------------------------
# 5) 双实例隔离：同一 Task / Registry 两套 assembly 不共享、交错不串拍
# ---------------------------------------------------------------------------

class TestDualInstanceIsolation(unittest.TestCase):

    def test_two_assemblies_share_nothing(self):
        task = _min_task()
        reg = build_default_registry()
        a1, _ = _build(task=task, registry=reg, driver=_ConfirmingDriver())
        a2, _ = _build(task=task, registry=reg, driver=_ConfirmingDriver())
        # 不共享任意生产组件
        self.assertIsNot(a1.store, a2.store)
        self.assertIsNot(a1.executor, a2.executor)
        self.assertIsNot(a1.safety_state, a2.safety_state)
        self.assertIsNot(a1.output_policy, a2.output_policy)
        self.assertIsNot(a1.commit_supervisor, a2.commit_supervisor)
        self.assertIsNot(a1.commit_port, a2.commit_port)
        self.assertIsNot(a1.engine, a2.engine)
        self.assertIsNot(a1.runner, a2.runner)
        self.assertIsNot(a1.monitor, a2.monitor)
        self.assertIsNot(a1.layout, a2.layout)

    def test_interleaved_ton_state_does_not_cross(self):
        task = _min_task()
        reg = build_default_registry()
        a1, _ = _build(task=task, registry=reg, driver=_ConfirmingDriver())
        a2, _ = _build(task=task, registry=reg, driver=_ConfirmingDriver())
        a1.safety_state.replace(SafetySnapshot.all_ok())
        a2.safety_state.replace(SafetySnapshot.all_ok())
        # a1 推进 3 拍、a2 推进 1 拍（交错）；TON ET 独立累计
        a1.runner.scan_cycle({"DI0": True, "DI1": False})
        a2.runner.scan_cycle({"DI0": True, "DI1": False})
        a1.runner.scan_cycle({"DI0": True, "DI1": False})
        a1.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(a1.store.read(_EK), 1000)             # 3 拍 → 到点
        self.assertEqual(a2.store.read(_EK), 500)              # 1 拍
        self.assertIs(a1.store.read(_QK), True)
        self.assertIs(a2.store.read(_QK), False)


# ---------------------------------------------------------------------------
# 6) monitor E2E：正常无事件 / 超时一次性派发 / 不重放 / 业务不推进
# ---------------------------------------------------------------------------

class TestMonitorE2E(unittest.TestCase):

    def test_normal_cycle_no_event(self):
        clock = _ManualClock()
        a, drv = _build(watchdog_timeout_ms=1000, clock_ns=clock)
        a.safety_state.replace(SafetySnapshot.all_ok())
        tok = a.monitor.begin_cycle()
        a.runner.scan_cycle({"DI0": True, "DI1": False})
        clock.advance_ms(500)                                  # < timeout 1000ms
        obs = a.monitor.finish_cycle(tok)
        self.assertFalse(obs.timed_out)
        self.assertFalse(a.monitor.has_pending_event)

    def test_timeout_latches_and_dispatches_once(self):
        clock = _ManualClock()
        a, drv = _build(watchdog_timeout_ms=1000, clock_ns=clock)
        a.safety_state.replace(SafetySnapshot.all_ok())
        # 建立业务基线：先跑一正常拍推进 TON 到 ET=500
        base_tok = a.monitor.begin_cycle()
        a.runner.scan_cycle({"DI0": True, "DI1": False})
        clock.advance_ms(500)
        a.monitor.finish_cycle(base_tok)
        et_before = a.store.read(_EK)
        prev_before = a.engine.prev
        # 新周期严重超时（runner 空闲、未 finish）
        a.monitor.begin_cycle()
        clock.advance_ms(1200)                                 # 越过 1000ms 阈值
        self.assertIsNotNone(a.monitor.poll_timeout())
        self.assertTrue(a.monitor.has_pending_event)
        # 一次性派发到 runner.trigger_watchdog → WatchdogSafeCommit
        with self.assertRaises(WatchdogSafeCommit) as cm:
            a.monitor.dispatch_pending(a.runner.trigger_watchdog)
        sig = cm.exception
        self.assertTrue(sig.shadow)
        self.assertTrue(sig.write_suppressed_by_shadow)
        self.assertFalse(sig.safe_commit_succeeded)            # shadow 无物理提交
        self.assertIsNone(sig.original_exception)
        # 业务 IR/prev 不因 watchdog 事件推进；shadow 下驱动零调用
        self.assertEqual(a.store.read(_EK), et_before)
        self.assertIs(a.engine.prev, prev_before)
        self.assertEqual(drv.commands, [])
        self.assertFalse(a.safety_state.read().watchdog_ok)    # 已锁存
        # 事件已消费，第二次 dispatch 不重放（返回 False，不再触发 callback）
        self.assertFalse(a.monitor.has_pending_event)
        self.assertFalse(a.monitor.dispatch_pending(a.runner.trigger_watchdog))


# ---------------------------------------------------------------------------
# 7) scan fault / commit fault 分层
# ---------------------------------------------------------------------------

def _fault_task():
    """执行期 INT 除零 → IRExecutionError（scan fault）；Motor BOOL OUT 带策略。"""
    gvl = [
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("N", "INT", section="VAR_GLOBAL"),
    ]
    io_map = [IOMap("Motor", "DO0", "OUT",
                    policy=OutputPolicy("Motor", "BOOL", False))]
    code = [
        LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL"),
        LoadConst(1, "INT"), LoadConst(0, "INT"), BinOp("DIV", "INT"),
        StoreVar("N", "INT"),
    ]
    main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
    return Task(cycle_ms=500, programs=[ProgramInstance("Main", "PLC_PRG")],
                gvl=gvl, io_map=io_map, pou_lib={"Main": main})


class TestFaultLayering(unittest.TestCase):

    def test_execution_error_becomes_scan_fault_safe_commit(self):
        a, drv = _build(task=_fault_task())
        a.safety_state.replace(SafetySnapshot.all_ok())
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            a.runner.scan_cycle({})
        sig = cm.exception
        self.assertIsInstance(sig.original_exception, IRExecutionError)
        self.assertEqual(sig.safe_image, {"DO0": False})       # 全通道安全映像
        self.assertTrue(sig.shadow)
        self.assertFalse(sig.safe_commit_succeeded)            # shadow 无物理提交
        self.assertEqual(a.commit_port.attempts, 0)
        self.assertFalse(a.safety_state.read().scan_ok)        # 已锁存
        self.assertEqual(drv.commands, [])

    def test_commit_fault_propagates_after_write_enabled(self):
        # 提交已尝试后失败：real-write 下驱动抛异常 → PartialCommitError 原样上抛，
        # 归 commit_fault（非 scan_fault），提交尝试证据已记。
        a, drv = _build(task=_min_task(), driver=_RaisingDriver())
        a.safety_state.replace(SafetySnapshot.all_ok())
        a.runner.set_write_enabled(True)
        with self.assertRaises(PartialCommitError):
            a.runner.scan_cycle({"DI0": False, "DI1": False})
        self.assertEqual(a.commit_port.attempts, 1)            # 提交已尝试
        self.assertTrue(a.safety_state.read().scan_ok)         # 非扫描故障，未锁存
        self.assertTrue(a.commit_supervisor.diagnostics()["DO0"].commit_fault)


# ---------------------------------------------------------------------------
# 8) 装配失败原子性：既有层稳定拒绝、driver 零调用、无 assembly 返回
# ---------------------------------------------------------------------------

class TestAssemblyFailureAtomicity(unittest.TestCase):

    def _assert_no_assembly(self, exc_type, **kw):
        drv = _ConfirmingDriver()
        with self.assertRaises(exc_type):
            build_task_runtime(driver=drv, **kw)
        self.assertEqual(drv.commands, [])                     # 全路径 driver 零调用
        return drv

    def test_mismatched_output_policy_type_rejected(self):
        # 策略存在但 iec_type 与 Store 声明不一致：通过 validate_task 占位在场校验，
        # 却被 OutputPolicyService 装配期稳定拒绝（本包新增的门控对齐层）。
        gvl = [VarDecl("Motor", "BOOL", section="VAR_GLOBAL")]
        io_map = [IOMap("Motor", "DO0", "OUT",
                        policy=OutputPolicy("Motor", "INT", 0))]   # 声明 BOOL，策略 INT
        code = [LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")]
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST",
                             code=code)
        task = Task(cycle_ms=500, programs=[ProgramInstance("Main", "PLC_PRG")],
                    gvl=gvl, io_map=io_map, pou_lib={"Main": main})
        self._assert_no_assembly(OutputPolicyConfigError, task=task,
                                 registry=build_default_registry(),
                                 watchdog_timeout_ms=1000)

    def test_invalid_driver_rejected_by_supervisor(self):
        with self.assertRaises(CommitSupervisorConfigError):
            build_task_runtime(_min_task(), build_default_registry(),
                               driver=object(), watchdog_timeout_ms=1000)

    def test_invalid_watchdog_timeout_rejected_by_monitor(self):
        for bad in (0, -1, 1.0, True):
            self._assert_no_assembly(MonitorConfigError, task=_min_task(),
                                     registry=build_default_registry(),
                                     watchdog_timeout_ms=bad)

    def test_invalid_initial_safety_rejected(self):
        self._assert_no_assembly(SafetyStateError, task=_min_task(),
                                 registry=build_default_registry(),
                                 watchdog_timeout_ms=1000,
                                 initial_safety=object())

    def test_invalid_numeric_mode_rejected(self):
        self._assert_no_assembly(StartupValidationError, task=_min_task(),
                                 registry=build_default_registry(),
                                 watchdog_timeout_ms=1000,
                                 numeric_mode="F1")

    def test_invalid_task_rejected_by_startup_validation(self):
        # IR 非法：StoreVar 目标未声明 → validate_task 汇总失败
        gvl = [VarDecl("Motor", "BOOL", section="VAR_GLOBAL")]
        io_map = [IOMap("Motor", "DO0", "OUT",
                        policy=OutputPolicy("Motor", "BOOL", False))]
        code = [LoadConst(True, "BOOL"), StoreVar("Ghost", "BOOL")]
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST",
                             code=code)
        task = Task(cycle_ms=500, programs=[ProgramInstance("Main", "PLC_PRG")],
                    gvl=gvl, io_map=io_map, pou_lib={"Main": main})
        self._assert_no_assembly(StartupValidationError, task=task,
                                 registry=build_default_registry(),
                                 watchdog_timeout_ms=1000)

    def test_failed_assembly_does_not_pollute_registry(self):
        # 失败装配后同一 registry 仍能成功装配（键集合 / 解析未被污染）。
        reg = build_default_registry()
        with self.assertRaises(MonitorConfigError):
            build_task_runtime(_min_task(), reg, driver=_ConfirmingDriver(),
                               watchdog_timeout_ms=0)
        a, _ = _build(registry=reg)                            # 复用同一 registry
        self.assertIsInstance(a, TaskRuntimeAssembly)


# ---------------------------------------------------------------------------
# 9) 公开导入契约
# ---------------------------------------------------------------------------

class TestPublicExport(unittest.TestCase):

    def test_exported_from_package(self):
        import src.runtime as rt
        self.assertIn("TaskRuntimeAssembly", rt.__all__)
        self.assertIn("build_task_runtime", rt.__all__)
        self.assertIn("COLD_START_SAFETY", rt.__all__)
        self.assertIs(rt.TaskRuntimeAssembly, TaskRuntimeAssembly)
        self.assertIs(rt.build_task_runtime, build_task_runtime)
        for name in ("ReadinessSnapshot", "StartupState", "ReadinessError",
                     "ReadinessConfigError", "ReadinessClockError",
                     "StartupReadinessController"):
            self.assertIn(name, rt.__all__)


if __name__ == "__main__":
    unittest.main()
