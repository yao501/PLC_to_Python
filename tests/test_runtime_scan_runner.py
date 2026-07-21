"""WP-20260720-008：外层安全扫描运行器与扫描/看门狗故障安全提交测试。

逐条对应任务书"最低测试"清单：
1. 正常一拍/连续两拍与直接 ``ScanEngine.scan`` 等价，业务提交每拍一次、
   ``prev`` 只在成功后前移；
2. 输入锁存 / IR 执行 / 正常策略 staging 的代表性异常各触发安全提交，全通道
   （含非零 ``safe_value``）写入、业务提交未发生、安全提交恰一次；
3. 故障态非法/非有限 request 复现 WP-007 边界：正常 OutputPolicy 失败但外层经
   专用恢复入口提交安全映像，pending 空、``prev`` 不前移、策略历史与安全值一致；
4. 正常提交端口抛错 → 总调用一次、不追加安全提交、不误报为 ``scan_fault``；
5. 安全恢复链任一阶段失败（提交端口抛错 / 提交前 staging 阶段失败）→ 结构化信号
   保留原始 + fallback 异常、``failed_stage`` 定位、零重试、**策略历史不前移**、
   不冒充已安全提交；提交成功才经两阶段 ``confirm_safe_image`` 前移历史；
6. 显式 watchdog 事件跳过 IR/业务路径、全通道安全提交一次、``watchdog_ok=False``
   保持锁存，无真实等待或线程；
7. 运行器并发/递归重入失败关闭，锁与 pending 异常后可恢复使用；
8. 装配期共享同一策略/提交端口校验；包边界稳定导出。

诚实边界：本文件锁定的是当前 Python 外层运行器行为——真实周期计时、后台线程、
硬件 watchdog、shadow、``commit_fault``/``channel_fault`` 重试与复位、真实 HAL
均**不在本包**；这些测试不构成与 CODESYS PLC 语义一致的证据。
"""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from src.runtime import (
    BinOp,
    CommitPort,
    Executor,
    IOMap,
    IRExecutionError,
    LoadConst,
    LoadVar,
    OuterScanRunner,
    OutputPolicy,
    OutputPolicyError,
    OutputPolicyService,
    POUDefinition,
    ProgramInstance,
    SafeCommitSignal,
    SafetySnapshot,
    SafetyStateService,
    ScanEngine,
    ScanFaultSafeCommit,
    ScanRunnerConfigError,
    ScanRunnerReentryError,
    StoreVar,
    Task,
    VarDecl,
    WatchdogSafeCommit,
    build_runtime_store,
)
from src.runtime.process_image import InputImageError


# ---------------------------------------------------------------------------
# 提交端口测试替身
# ---------------------------------------------------------------------------

class _RecordingCommitter:
    def __init__(self):
        self.received = []

    def commit(self, outputs):
        self.received.append(dict(outputs))


class _BoomCommitter:
    """每次提交都抛错——用于 commit_fault / 安全提交失败路径。"""

    def __init__(self):
        self.calls = 0

    def commit(self, outputs):
        self.calls += 1
        raise RuntimeError("commit boom")


class _BlockingCommitter:
    """提交时阻塞在事件上——用于跨线程并发重入测试。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.received = []

    def commit(self, outputs):
        self.received.append(dict(outputs))
        self.entered.set()
        self.release.wait(timeout=5)


class _ReentrantCommitter:
    """提交期间递归回调运行器，触发失败关闭。"""

    def __init__(self, method="scan_cycle"):
        self.runner = None
        self.method = method
        self.reentry_error = None
        self.received = []

    def commit(self, outputs):
        if self.runner is not None and self.reentry_error is None:
            try:
                getattr(self.runner, self.method)(*(
                    ({"DI0": True},) if self.method == "scan_cycle" else ()))
            except ScanRunnerReentryError as exc:
                self.reentry_error = exc
        self.received.append(dict(outputs))


# ---------------------------------------------------------------------------
# 装配辅助
# ---------------------------------------------------------------------------

def _build(*, av_type="INT", av_safe=0, av_rate=None, motor_safe=False,
           code=None, committer=None, safety=None):
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("AV", av_type, section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=OutputPolicy("Motor", "BOOL", motor_safe)),
        IOMap("AV", "AO0", "OUT",
              policy=OutputPolicy("AV", av_type, av_safe, rate_limit=av_rate)),
    ]
    if code is None:
        code = [
            LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL"),
            LoadConst(100, "INT"), StoreVar("AV", "INT"),
        ]
    main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
    task = Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                io_map=io_map, pou_lib={"Main": main})
    layout = build_runtime_store(task)
    executor = Executor(task, layout)
    safety = safety or SafetyStateService(SafetySnapshot.all_ok())
    policy = OutputPolicyService(layout.store, task.io_map, safety)
    committer = _RecordingCommitter() if committer is None else committer
    port = CommitPort(committer)
    engine = ScanEngine(task, layout, executor, policy, port)
    runner = OuterScanRunner(engine, policy, port)
    return SimpleNamespace(task=task, layout=layout, executor=executor,
                           safety=safety, policy=policy, committer=committer,
                           port=port, engine=engine, runner=runner)


# ---------------------------------------------------------------------------
# 1) 正常路径：与直接 ScanEngine.scan 等价
# ---------------------------------------------------------------------------

class TestNormalPathEquivalence(unittest.TestCase):

    def test_single_cycle_equals_direct_engine(self):
        runner_wire = _build(av_safe=0, av_rate=5)
        direct_wire = _build(av_safe=0, av_rate=5)
        via_runner = runner_wire.runner.scan_cycle({"DI0": True})
        via_engine = direct_wire.engine.scan({"DI0": True})
        self.assertEqual(via_runner.outputs(), via_engine.outputs())
        self.assertEqual(via_runner.outputs(), {"DO0": True, "AO0": 5})
        # 业务提交恰一次
        self.assertEqual(runner_wire.committer.received, [{"DO0": True, "AO0": 5}])
        self.assertEqual(runner_wire.port.attempts, 1)

    def test_two_cycles_single_commit_each_and_prev_advances(self):
        w = _build(av_safe=0, av_rate=5)
        r1 = w.runner.scan_cycle({"DI0": True})
        prev_after_1 = w.engine.prev
        r2 = w.runner.scan_cycle({"DI0": False})
        self.assertEqual(r1.outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(r2.outputs(), {"DO0": False, "AO0": 10})
        self.assertEqual(w.committer.received,
                         [{"DO0": True, "AO0": 5}, {"DO0": False, "AO0": 10}])
        # prev 每拍成功后前移（不同快照对象）
        self.assertIsNot(w.engine.prev, prev_after_1)
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": False, "AO0": 10})

    def test_reset_between_cycles_isolates_commit_attempts(self):
        w = _build(av_safe=0, av_rate=5)
        w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.port.attempts, 1)      # 单拍单次提交
        w.runner.scan_cycle({"DI0": False})
        self.assertEqual(w.port.attempts, 1)      # 每拍进入前清零


# ---------------------------------------------------------------------------
# 2) 代表性扫描异常 → 安全提交（含非零 safe_value 全通道、业务未提交）
# ---------------------------------------------------------------------------

class TestScanFaultSafeCommit(unittest.TestCase):

    def _assert_safe_committed(self, w, sig, expected_image):
        self.assertTrue(sig.safe_commit_succeeded)
        self.assertEqual(sig.safe_image, expected_image)
        self.assertEqual(w.committer.received, [expected_image])   # 恰一次
        self.assertEqual(w.port.attempts, 1)
        # 策略历史与真正提交的安全映像一致
        self.assertEqual(w.policy.diagnostic_last_effective(), expected_image)
        # 扫描故障已锁存 scan_ok=False
        self.assertFalse(w.safety.read().scan_ok)

    def test_input_latch_error_triggers_safe_commit(self):
        w = _build(av_safe=7, av_rate=5)
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"BADCH": True})   # 未知通道 → 锁存前失败
        self.assertIsInstance(cm.exception.original_exception, InputImageError)
        self._assert_safe_committed(w, cm.exception, {"DO0": False, "AO0": 7})

    def test_ir_execution_error_triggers_safe_commit(self):
        code = [LoadConst(1, "INT"), LoadConst(0, "INT"),
                BinOp("DIV", "INT"), StoreVar("AV", "INT")]
        w = _build(av_safe=7, code=code)
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})     # 执行期除零
        self.assertIsInstance(cm.exception.original_exception, IRExecutionError)
        self._assert_safe_committed(w, cm.exception, {"DO0": False, "AO0": 7})

    def test_normal_policy_staging_error_triggers_safe_commit(self):
        # 业务不写 AV；预置越界 request 使正常策略 staging 失败（提交前）
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="USINT", av_safe=9, code=code)
        w.layout.store.write("AV", 999)            # 结构合法、数值越界
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(cm.exception.original_exception, OutputPolicyError)
        self._assert_safe_committed(w, cm.exception, {"DO0": False, "AO0": 9})


# ---------------------------------------------------------------------------
# 3) 非法/非有限 request 复现 WP-007 边界（专用恢复入口绕过 request）
# ---------------------------------------------------------------------------

class TestIllegalRequestBoundary(unittest.TestCase):

    def test_non_finite_request_safe_commit_via_recovery_entry(self):
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="REAL", av_safe=3.5, code=code)
        w.layout.store.write("AV", float("nan"))   # 非有限 REAL request
        prev_before = w.engine.prev
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        sig = cm.exception
        self.assertTrue(sig.safe_commit_succeeded)
        self.assertIsInstance(sig.original_exception, OutputPolicyError)
        self.assertEqual(sig.safe_image, {"DO0": False, "AO0": 3.5})
        # 安全提交恰一次；prev 不前移；引擎 pending 无残留；策略历史 == 安全值
        self.assertEqual(w.committer.received, [{"DO0": False, "AO0": 3.5}])
        self.assertIs(w.engine.prev, prev_before)
        self.assertEqual(w.engine._pending.staged(), {})
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": False, "AO0": 3.5})

    def test_out_of_range_int_request_safe_commit(self):
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="SINT", av_safe=-5, code=code)
        w.layout.store.write("AV", 128)            # 越 SINT 上界
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        self.assertEqual(cm.exception.safe_image, {"DO0": False, "AO0": -5})
        self.assertEqual(w.committer.received, [{"DO0": False, "AO0": -5}])


# ---------------------------------------------------------------------------
# 4) 提交异常不得误分类为扫描异常（commit_fault，§4.4）
# ---------------------------------------------------------------------------

class TestCommitFaultNotMisclassified(unittest.TestCase):

    def test_normal_commit_error_reraised_no_safe_commit(self):
        boom = _BoomCommitter()
        w = _build(av_safe=7, av_rate=5, committer=boom)
        with self.assertRaises(RuntimeError) as cm:
            w.runner.scan_cycle({"DI0": True})     # 正常路径到提交才失败
        self.assertEqual(str(cm.exception), "commit boom")
        # 不被包成 scan_fault 安全落值信号
        self.assertNotIsInstance(cm.exception, SafeCommitSignal)
        # 提交端口总调用一次，不追加安全提交
        self.assertEqual(boom.calls, 1)
        self.assertEqual(w.port.attempts, 1)
        # 未锁存 scan_ok=False（这不是扫描故障）
        self.assertTrue(w.safety.read().scan_ok)


# ---------------------------------------------------------------------------
# 5) 外层安全提交自身失败：保留双异常、零重试、不冒充成功
# ---------------------------------------------------------------------------

class TestSafeCommitFailure(unittest.TestCase):

    def test_safe_commit_failure_preserves_both_exceptions(self):
        boom = _BoomCommitter()
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="USINT", av_safe=9, code=code, committer=boom)
        w.layout.store.write("AV", 999)            # 提交前扫描失败 → 进入安全提交
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        sig = cm.exception
        self.assertFalse(sig.safe_commit_succeeded)
        self.assertEqual(sig.failed_stage, "commit")
        self.assertIsInstance(sig.original_exception, OutputPolicyError)
        self.assertIsInstance(sig.fallback_exception, RuntimeError)
        self.assertIsInstance(sig.commit_exception, RuntimeError)   # 兼容属性
        # 零重试：底层提交只被调用一次（安全提交那一次）
        self.assertEqual(boom.calls, 1)
        self.assertEqual(w.port.attempts, 1)
        # Codex Round 1 反证修复：安全提交失败 → 策略历史**未前移**为安全映像，
        # 绝不冒充已安全落值（两阶段事务未 confirm，last_effective 保持冷启动 None）。
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": None, "AO0": None})

    def test_safe_commit_success_advances_history_only_after_commit(self):
        # 正例配对：安全提交**成功**才把策略历史前移为真正提交的安全映像。
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="USINT", av_safe=9, code=code)
        w.layout.store.write("AV", 999)
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        self.assertTrue(cm.exception.safe_commit_succeeded)
        self.assertIsNone(cm.exception.failed_stage)
        self.assertIsNone(cm.exception.fallback_exception)
        self.assertEqual(w.committer.received, [{"DO0": False, "AO0": 9}])
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": False, "AO0": 9})

    def test_confirm_stage_failure_after_commit_is_structured(self):
        # Codex Round 2 反证 1：物理安全提交**已成功**，但确认阶段抛错时，绝不能
        # 漏出普通异常或静默留下"已提交、历史未前移"的失配——必须结构化上报，保留
        # 提交成功证据 + 原始 + fallback 异常，failed_stage="confirm"，零重试。
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="USINT", av_safe=9, code=code)
        w.layout.store.write("AV", 999)            # 提交前扫描失败 → 进入安全提交

        def _boom_confirm(ticket):
            raise RuntimeError("confirm boom")
        w.policy.confirm_safe_image = _boom_confirm   # 确认入口稳定抛错

        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        sig = cm.exception
        self.assertIsInstance(sig, ScanFaultSafeCommit)   # 非普通 RuntimeError
        self.assertEqual(sig.failed_stage, "confirm")
        # 物理安全提交已成功——确凿证据须保留，安全映像已真正落到端口
        self.assertTrue(sig.safe_commit_succeeded)
        self.assertEqual(w.committer.received, [{"DO0": False, "AO0": 9}])
        self.assertEqual(w.port.attempts, 1)              # 恰一次、零重试
        # 原始扫描异常 + 确认阶段 fallback 异常同时保留
        self.assertIsInstance(sig.original_exception, OutputPolicyError)
        self.assertIsInstance(sig.fallback_exception, RuntimeError)
        self.assertEqual(str(sig.fallback_exception), "confirm boom")
        self.assertIsNone(sig.commit_exception)           # commit 阶段未失败
        # 失配可审计：确认失败 → 策略历史未前移（由信号显式暴露，非静默）
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": None, "AO0": None})


# ---------------------------------------------------------------------------
# 5b) 安全恢复链 staging 阶段失败：仍产生结构化信号（Codex Round 1 反证修复）
# ---------------------------------------------------------------------------

class TestSafeStagingFailure(unittest.TestCase):
    """安全恢复链在**提交前的 staging 阶段**失败（如装配后 safe_value 漂移），
    也必须产生结构化 ``SafeCommitSignal``：保留原始扫描异常 + 具体 fallback 异常、
    明确未安全提交、零重试，绝不让普通异常漏出；锁/pending 异常后可恢复。"""

    @staticmethod
    def _drift_av_safe_value(w, bad=999):
        # 绕过 frozen 校验篡改 AV 通道 safe_value 为越界值 → 安全 staging 阶段失败。
        object.__setattr__(w.task.io_map[2].policy, "safe_value", bad)

    def test_scan_fault_staging_failure_preserves_structured_signal(self):
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="USINT", av_safe=9, code=code)
        w.layout.store.write("AV", 999)            # 正常策略越界 → scan_fault
        self._drift_av_safe_value(w)               # 安全 staging 也失败
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        sig = cm.exception
        self.assertFalse(sig.safe_commit_succeeded)
        self.assertEqual(sig.failed_stage, "stage_safe_image")
        self.assertIsInstance(sig.original_exception, OutputPolicyError)  # 原始扫描失败
        self.assertIsInstance(sig.fallback_exception, OutputPolicyError)  # staging 失败
        self.assertIsNone(sig.commit_exception)    # 非 commit 阶段
        self.assertIsNone(sig.safe_image)          # staging 未完成
        # 零提交、未冒充安全落值、策略历史未前移；scan_ok 已锁存（latch 在 staging 前）
        self.assertEqual(w.committer.received, [])
        self.assertEqual(w.port.attempts, 0)
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": None, "AO0": None})
        self.assertFalse(w.safety.read().scan_ok)
        # 锁/pending 可恢复：修正漂移 + 显式恢复安全状态后仍可正常提交
        object.__setattr__(w.task.io_map[2].policy, "safe_value", 9)
        w.safety.replace(SafetySnapshot.all_ok())
        w.layout.store.write("AV", 5)
        r = w.runner.scan_cycle({"DI0": True})
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 5})

    def test_watchdog_staging_failure_preserves_fallback_without_original(self):
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        w = _build(av_type="USINT", av_safe=9, code=code)
        self._drift_av_safe_value(w)               # 安全 staging 失败
        with self.assertRaises(WatchdogSafeCommit) as cm:
            w.runner.trigger_watchdog()
        sig = cm.exception
        self.assertFalse(sig.safe_commit_succeeded)
        self.assertEqual(sig.failed_stage, "stage_safe_image")
        self.assertIsNone(sig.original_exception)  # watchdog 无原始业务异常
        self.assertIsInstance(sig.fallback_exception, OutputPolicyError)  # 仍保留 fallback
        self.assertEqual(w.committer.received, [])
        self.assertEqual(w.port.attempts, 0)
        self.assertFalse(w.safety.read().watchdog_ok)   # 锁存仍成立
        # 锁可恢复：修正漂移后 watchdog 安全提交成功
        object.__setattr__(w.task.io_map[2].policy, "safe_value", 9)
        with self.assertRaises(WatchdogSafeCommit) as cm2:
            w.runner.trigger_watchdog()
        self.assertTrue(cm2.exception.safe_commit_succeeded)
        self.assertEqual(w.committer.received, [{"DO0": False, "AO0": 9}])


# ---------------------------------------------------------------------------
# 6) 显式软件 watchdog 信号响应
# ---------------------------------------------------------------------------

class TestWatchdog(unittest.TestCase):

    def test_watchdog_skips_business_and_safe_commits_once(self):
        w = _build(av_safe=7, av_rate=5)
        av_before = w.layout.store.read("AV")
        prev_before = w.engine.prev
        with self.assertRaises(WatchdogSafeCommit) as cm:
            w.runner.trigger_watchdog()
        sig = cm.exception
        self.assertTrue(sig.safe_commit_succeeded)
        self.assertIsNone(sig.original_exception)
        self.assertEqual(sig.safe_image, {"DO0": False, "AO0": 7})
        # 全通道安全提交恰一次
        self.assertEqual(w.committer.received, [{"DO0": False, "AO0": 7}])
        # 业务 IR 未跑：业务 Store 未变、engine.prev 未前移
        self.assertEqual(w.layout.store.read("AV"), av_before)
        self.assertIs(w.engine.prev, prev_before)
        # watchdog_ok 锁存 False
        self.assertFalse(w.safety.read().watchdog_ok)

    def test_watchdog_flag_not_auto_cleared(self):
        w = _build(av_safe=7)
        with self.assertRaises(WatchdogSafeCommit):
            w.runner.trigger_watchdog()
        self.assertFalse(w.safety.read().watchdog_ok)
        # 再触发仍锁存 False——运行器绝不自动复位
        with self.assertRaises(WatchdogSafeCommit):
            w.runner.trigger_watchdog()
        self.assertFalse(w.safety.read().watchdog_ok)
        self.assertEqual(len(w.committer.received), 2)


# ---------------------------------------------------------------------------
# 7) 并发/递归重入失败关闭，锁与 pending 异常后可恢复
# ---------------------------------------------------------------------------

class TestReentrancy(unittest.TestCase):

    def test_recursive_scan_cycle_fails_closed(self):
        rc = _ReentrantCommitter("scan_cycle")
        w = _build(av_safe=0, av_rate=5, committer=rc)
        rc.runner = w.runner
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(rc.reentry_error, ScanRunnerReentryError)
        # 外层拍仍成功完成、提交一次，锁释放后可复用
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(rc.received, [{"DO0": True, "AO0": 5}])
        r2 = w.runner.scan_cycle({"DI0": False})
        self.assertEqual(r2.outputs(), {"DO0": False, "AO0": 10})

    def test_recursive_watchdog_during_scan_cycle_fails_closed(self):
        rc = _ReentrantCommitter("trigger_watchdog")
        w = _build(av_safe=0, av_rate=5, committer=rc)
        rc.runner = w.runner
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(rc.reentry_error, ScanRunnerReentryError)
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 5})

    def test_concurrent_scan_cycle_fails_closed(self):
        blocking = _BlockingCommitter()
        w = _build(av_safe=0, av_rate=5, committer=blocking)
        results = {}

        def worker():
            try:
                results["ok"] = w.runner.scan_cycle({"DI0": True})
            except Exception as exc:               # pragma: no cover
                results["err"] = exc

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(blocking.entered.wait(timeout=5))
        # worker 持锁在提交中 → 主线程并发进入失败关闭
        with self.assertRaises(ScanRunnerReentryError):
            w.runner.scan_cycle({"DI0": True})
        blocking.release.set()
        t.join(timeout=5)
        self.assertNotIn("err", results)
        self.assertEqual(results["ok"].outputs(), {"DO0": True, "AO0": 5})
        # 锁释放后运行器可复用
        r = w.runner.scan_cycle({"DI0": False})
        self.assertEqual(r.outputs(), {"DO0": False, "AO0": 10})


# ---------------------------------------------------------------------------
# 8) 装配期共享校验 + 包边界导出
# ---------------------------------------------------------------------------

class TestRunnerConfig(unittest.TestCase):

    def test_commit_port_requires_commit_callable(self):
        with self.assertRaises(ScanRunnerConfigError):
            CommitPort(object())

    def test_rejects_non_engine(self):
        w = _build()
        with self.assertRaises(ScanRunnerConfigError):
            OuterScanRunner(object(), w.policy, w.port)

    def test_rejects_non_commitport(self):
        w = _build()
        with self.assertRaises(ScanRunnerConfigError):
            OuterScanRunner(w.engine, w.policy, _RecordingCommitter())

    def test_rejects_policy_not_shared_with_engine(self):
        w = _build()
        other_safety = SafetyStateService(SafetySnapshot.all_ok())
        other_policy = OutputPolicyService(w.layout.store, w.task.io_map,
                                           other_safety)
        with self.assertRaises(ScanRunnerConfigError):
            OuterScanRunner(w.engine, other_policy, w.port)

    def test_rejects_port_not_shared_with_engine(self):
        w = _build()
        other_port = CommitPort(_RecordingCommitter())
        with self.assertRaises(ScanRunnerConfigError):
            OuterScanRunner(w.engine, w.policy, other_port)


class TestPackageExports(unittest.TestCase):

    def test_runner_api_exported(self):
        import src.runtime as rt
        for name in ("CommitPort", "OuterScanRunner", "ScanRunnerError",
                     "ScanRunnerConfigError", "ScanRunnerReentryError",
                     "SafeCommitSignal", "ScanFaultSafeCommit",
                     "WatchdogSafeCommit"):
            self.assertIn(name, rt.__all__)
            self.assertTrue(hasattr(rt, name))

    def test_existing_exports_not_regressed(self):
        import src.runtime as rt
        for name in ("ScanEngine", "OutputPolicyService", "Executor", "Store"):
            self.assertIn(name, rt.__all__)

    def test_module_does_not_import_prototype(self):
        import src.runtime.scan_runner as mod
        with open(mod.__file__, encoding="utf-8") as fh:
            self.assertNotIn("prototype_05", fh.read())


if __name__ == "__main__":
    unittest.main()
