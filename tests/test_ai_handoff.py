from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.request import urlopen

from tools.ai_handoff.parser import (
    HandoffParser,
    LEGACY_WORK_PACKAGE_IDS,
    WorkPackage,
    canonical_actor,
    canonical_status,
)
from tools.ai_handoff.heartbeat import CoordinatorHeartbeat
from tools.ai_handoff.scheduler import (
    AsyncExecutionCoordinator,
    ClaudeEndpointAdapter,
    CodexCommandAdapter,
    DryRunScheduler,
    EventDrivenScheduler,
    Fable5EndpointAdapter,
    ExecutionPlan,
    ProcessRunResult,
    SafeProcessRunner,
    ScopeHashResult,
    calculate_scope_sha256,
)
from tools.ai_handoff.server import DashboardApplication, StateStore
from tools.ai_handoff.watcher import HandoffWatcher


HASH_A = "a" * 64
HASH_B = "b" * 64


def package_text(
    wp_id: str = "WP-20260714-003",
    *,
    status: str = "READY_FOR_CODEX",
    owner: str = "codex",
    handoff: str = "codex",
    round_number: int = 1,
    max_rounds: int = 3,
    baseline_hash: str | None = HASH_A,
    implementation_hash: str | None = HASH_A,
    review_started_hash: str | None = HASH_A,
    review_finished_hash: str | None = HASH_A,
    blocked: bool = False,
    impl_actor: str = "Claude",
) -> str:
    baseline_line = f"- scope_baseline_sha256: {baseline_hash}\n" if baseline_hash else ""
    implementation_line = f"- scope_sha256: {implementation_hash}\n" if implementation_hash else ""
    blocked_line = "- 必须返修 / 阻塞原因: 需要用户裁决规格边界。\n" if blocked else "- 必须返修: 修复边界检查。\n"
    return f"""
## {wp_id}

- title: 测试工作包
- status: {status}
- owner: {owner}
- handoff_to: {handoff}
- round: {round_number}
- max_rounds: {max_rounds}
- base_commit: abc123
{baseline_line}- scope:
  - src/example.py

### {impl_actor} 实施交接（Round 1）

- 完成内容: 新增解析与边界防御。
- 实际测试命令与结果: `python -m unittest` → Ran **12** tests, OK。
{implementation_line}- implementation_finished_at: 2026-07-14 10:00 CST

### Codex 审核结论（Round 1）

- verdict: {"BLOCKED" if blocked else "CHANGES_REQUESTED"}
- 已验证事实: 实现存在且测试通过。
{blocked_line}- 审核证据: review_started_sha256={review_started_hash or "missing"}, review_finished_sha256={review_finished_hash or "missing"}
- reviewed_at: 2026-07-14 10:05 CST
"""


class ParserTests(unittest.TestCase):
    def parse(self, text: str):
        return HandoffParser("memory.md").parse_text(text)

    def test_multiple_work_packages_and_current_top_level_state(self):
        text = package_text("WP-20260714-003", status="CLOSED", owner="user", handoff="user")
        text += package_text("WP-TEST-002")
        result = self.parse(text)
        self.assertEqual(2, len(result.packages))
        self.assertEqual("WP-TEST-002", result.current.work_package_id)
        self.assertEqual("READY_FOR_CODEX", result.current.status)

    def test_multiple_round_history_uses_latest_records(self):
        text = package_text().replace(
            "- reviewed_at: 2026-07-14 10:05 CST",
            f"- reviewed_at: 2026-07-14 10:05 CST\n\n### Fable5 实施交接（Round 2，返修）\n\n"
            f"- 完成内容: 第二轮修复。\n- scope_sha256: {HASH_B}\n"
            "- implementation_finished_at: 2026-07-14 11:00 CST",
        )
        package = self.parse(text).packages[0]
        self.assertEqual(3, len(package.records))
        self.assertEqual(2, package.records[-1].round)
        self.assertEqual("第二轮修复。", package.latest_implementation_summary)
        self.assertEqual(HASH_B, package.implementation_scope_sha256)

    def test_status_mapping_is_valid(self):
        package = self.parse(package_text()).packages[0]
        self.assertTrue(package.valid)
        self.assertEqual("Codex", package.waiting_for)
        self.assertIn("只读审核", package.write_access)

    def test_owner_handoff_mismatch_is_explicit_error(self):
        package = self.parse(package_text(owner="fable5", handoff="codex")).packages[0]
        self.assertFalse(package.valid)
        self.assertTrue(any("映射异常" in error for error in package.errors))

    def test_round_over_limit_is_recognized(self):
        package = self.parse(package_text(round_number=4, max_rounds=3)).packages[0]
        self.assertTrue(any("round 超限" in warning for warning in package.warnings))

    def test_missing_scope_hash_is_recognized(self):
        package = self.parse(package_text(
            baseline_hash=None,
            implementation_hash=None,
            review_started_hash=None,
            review_finished_hash=None,
        )).packages[0]
        self.assertIsNone(package.scope_baseline_sha256)
        self.assertIsNone(package.implementation_scope_sha256)
        self.assertIsNone(package.review_started_sha256)
        self.assertIsNone(package.review_finished_sha256)
        self.assertEqual(4, len([warning for warning in package.warnings if "sha256" in warning.lower()]))

    def test_blocked_reason_extraction(self):
        package = self.parse(package_text(status="BLOCKED", owner="user", handoff="user", blocked=True)).packages[0]
        self.assertIn("用户裁决", package.blocked_reason)

    def test_test_result_and_verdict_extraction(self):
        package = self.parse(package_text()).packages[0]
        self.assertEqual(12, package.latest_test_count)
        self.assertEqual("12/12 通过", package.latest_test_result)
        self.assertEqual("CHANGES_REQUESTED", package.latest_review_verdict)
        self.assertIn("修复边界", package.latest_review_summary)

    def test_hash_evidence_is_retained_in_separate_fields(self):
        package = self.parse(package_text(
            baseline_hash=HASH_A,
            implementation_hash=HASH_B,
            review_started_hash="c" * 64,
            review_finished_hash="d" * 64,
        )).packages[0]
        self.assertEqual(HASH_A, package.scope_baseline_sha256)
        self.assertEqual(HASH_B, package.implementation_scope_sha256)
        self.assertEqual("c" * 64, package.review_started_sha256)
        self.assertEqual("d" * 64, package.review_finished_sha256)

    def test_duplicate_and_missing_fields_are_not_guessed(self):
        text = package_text().replace("- status: READY_FOR_CODEX", "- status: READY_FOR_CODEX\n- status: CODEX_REVIEWING")
        package = self.parse(text).packages[0]
        self.assertFalse(package.valid)
        self.assertTrue(any("顶层字段重复: status" == error for error in package.errors))
        self.assertIsNone(package.status)

    def test_temporarily_unreadable_file_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = HandoffParser(Path(directory) / "missing.md").parse_file()
        self.assertFalse(result.ok)
        self.assertIn("暂时不可读", result.source_error)

    def test_real_handoff_read_only_smoke(self):
        source = Path(__file__).resolve().parents[1] / "docs" / "AI_REVIEW_HANDOFF.md"
        result = HandoffParser(source).parse_file()
        self.assertTrue(result.ok, result.source_error)
        self.assertGreaterEqual(len(result.packages), 2)
        self.assertIsNotNone(result.current)
        self.assertTrue(result.current.work_package_id.startswith("WP-"))


class WatcherTests(unittest.TestCase):
    def test_in_place_write_triggers_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(), encoding="utf-8")
            changed = threading.Event()
            watcher = HandoffWatcher(source, changed.set, debounce_seconds=0.05, fallback_interval=2.0)
            watcher.start()
            try:
                # apply_patch 与部分编辑器会保留 inode 原地写入；这类变化不会
                # 稳定地产生目录 NOTE_WRITE，必须由文件级 vnode 监听捕获。
                source.write_text(package_text(status="CODEX_REVIEWING"), encoding="utf-8")
                self.assertTrue(changed.wait(4.0), f"watcher mode={watcher.mode}")
                self.assertEqual("CODEX_REVIEWING", HandoffParser(source).parse_file().current.status)
            finally:
                watcher.stop()

    def test_start_returns_only_after_watcher_is_ready(self):
        """开始调用返回后立即原地写入，首个事件不得落在注册窗口。"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(), encoding="utf-8")
            changed = threading.Event()
            watcher = HandoffWatcher(
                source, changed.set, debounce_seconds=0.01,
                fallback_interval=0.05,
            )
            watcher.start()
            try:
                self.assertTrue(watcher._ready.is_set())
                source.write_text(
                    package_text(status="CODEX_REVIEWING"), encoding="utf-8"
                )
                self.assertTrue(changed.wait(2.0), f"watcher mode={watcher.mode}")
            finally:
                watcher.stop()

    def test_atomic_replace_triggers_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(), encoding="utf-8")
            changed = threading.Event()
            watcher = HandoffWatcher(source, changed.set, debounce_seconds=0.05, fallback_interval=2.0)
            watcher.start()
            try:
                replacement = Path(directory) / "replacement.md"
                replacement.write_text(package_text(status="CODEX_REVIEWING"), encoding="utf-8")
                os.replace(replacement, source)
                self.assertTrue(changed.wait(4.0), f"watcher mode={watcher.mode}")
                self.assertEqual("CODEX_REVIEWING", HandoffParser(source).parse_file().current.status)
            finally:
                watcher.stop()

    def test_mode_explicitly_reports_native_or_degraded(self):
        watcher = HandoffWatcher("handoff.md", lambda: None)
        if hasattr(select, "kqueue"):
            self.assertEqual("native-kqueue", watcher.mode)
        else:
            self.assertTrue(watcher.mode.startswith("degraded"))
            self.assertIsNotNone(watcher.degraded_reason)


class NeverExecuteCodex(CodexCommandAdapter):
    def __init__(self):
        super().__init__(executable=sys.executable, project_root=Path.cwd())
        self.calls = 0

    def execute(self, package: WorkPackage) -> None:
        self.calls += 1
        raise AssertionError("dry-run 不应调用外部命令")


class ScopeHashTests(unittest.TestCase):
    def test_aggregate_hash_uses_declared_order_and_manifest_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_bytes(b"alpha\n")
            (root / "src" / "b.py").write_bytes(b"beta\n")
            package = WorkPackage(work_package_id="WP-TEST", scope=["src/b.py", "src/a.py"])
            result = calculate_scope_sha256(package, root)
        beta_hash = hashlib.sha256(b"beta\n").hexdigest()
        alpha_hash = hashlib.sha256(b"alpha\n").hexdigest()
        expected_manifest = (
            f"{beta_hash}  src/b.py\n"
            f"{alpha_hash}  src/a.py\n"
        )
        self.assertEqual([], result.errors)
        self.assertEqual(expected_manifest.splitlines(keepends=True), result.manifest)
        self.assertEqual(hashlib.sha256(expected_manifest.encode()).hexdigest(), result.digest)

    def test_missing_scope_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = calculate_scope_sha256(
                WorkPackage(work_package_id="WP-TEST", scope=["src/missing.py"]), directory
            )
        self.assertIsNone(result.digest)
        self.assertTrue(any("缺失" in error for error in result.errors))

    def test_new_claude_working_package_hashes_absent_scope_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            package = WorkPackage(
                work_package_id="WP-NEW", status="CLAUDE_WORKING",
                scope=["src/new_runtime.py", "tests/test_new_runtime.py"],
            )
            result = calculate_scope_sha256(package, directory)
        expected = "ABSENT  src/new_runtime.py\nABSENT  tests/test_new_runtime.py\n"
        self.assertEqual(expected.splitlines(keepends=True), result.manifest)
        self.assertEqual([], result.errors)
        self.assertEqual(hashlib.sha256(expected.encode()).hexdigest(), result.digest)

    def test_unreadable_scope_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scope.py").write_text("x = 1\n", encoding="utf-8")
            package = WorkPackage(work_package_id="WP-TEST", scope=["scope.py"])
            with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
                result = calculate_scope_sha256(package, root)
        self.assertIsNone(result.digest)
        self.assertTrue(any("不可读" in error for error in result.errors))


class SafeProcessRunnerTests(unittest.TestCase):
    def plan(
        self, directory: str, code: str, *, timeout: float = 2.0,
        environment: dict[str, str] | None = None,
    ) -> ExecutionPlan:
        return ExecutionPlan(
            actor="test", action="fault-injection",
            command=[sys.executable, "-c", code], cwd=directory,
            timeout_seconds=timeout, permission_summary="临时目录；无 shell",
            environment=environment or {},
        )

    def test_success_and_environment_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            result = SafeProcessRunner().run(self.plan(
                directory,
                "import os; print(os.environ['AI_HANDOFF_TEST_VALUE'])",
                environment={"AI_HANDOFF_TEST_VALUE": "injected"},
            ))
        self.assertEqual("completed", result.outcome)
        self.assertEqual(0, result.returncode)
        self.assertEqual("injected", result.stdout_tail.strip())

    def test_nonzero_exit_is_failed_and_stderr_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            result = SafeProcessRunner().run(self.plan(
                directory, "import sys; print('controlled failure', file=sys.stderr); sys.exit(7)",
            ))
        self.assertEqual("failed", result.outcome)
        self.assertEqual(7, result.returncode)
        self.assertIn("controlled failure", result.stderr_tail)

    def test_timeout_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            result = SafeProcessRunner(terminate_grace_seconds=0.05).run(self.plan(
                directory, "import time; time.sleep(30)", timeout=0.1,
            ))
        self.assertEqual("timed-out", result.outcome)
        self.assertTrue(result.timed_out)
        self.assertLess(result.duration_seconds, 2.0)
        with self.assertRaises(ProcessLookupError):
            os.kill(result.process_id, 0)

    def test_output_is_bounded_and_credentials_are_redacted(self):
        code = (
            "print('x' * 5000); "
            "print('Authorization: Bearer top-secret'); "
            "print('{\\\"access_token\\\":\\\"token-value\\\"}'); "
            "print('sk-ant-example-secret')"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = SafeProcessRunner(output_tail_bytes=1024).run(self.plan(directory, code))
        self.assertLessEqual(len(result.stdout_tail.encode()), 1100)
        self.assertNotIn("top-secret", result.stdout_tail)
        self.assertNotIn("token-value", result.stdout_tail)
        self.assertNotIn("sk-ant-example-secret", result.stdout_tail)
        self.assertIn("[REDACTED]", result.stdout_tail)

    def test_missing_executable_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.plan(directory, "pass")
            missing = ExecutionPlan(
                actor=plan.actor, action=plan.action,
                command=[str(Path(directory) / "missing-command")], cwd=plan.cwd,
                timeout_seconds=plan.timeout_seconds,
                permission_summary=plan.permission_summary, environment={},
            )
            result = SafeProcessRunner().run(missing)
        self.assertEqual("launch-failed", result.outcome)
        self.assertIsNotNone(result.error)


class AsyncExecutionCoordinatorTests(unittest.TestCase):
    def plan(self, directory: str, code: str, *, timeout: float = 2.0) -> ExecutionPlan:
        return ExecutionPlan(
            actor="test", action="test-action",
            command=[sys.executable, "-c", code], cwd=directory,
            timeout_seconds=timeout, permission_summary="临时目录；无 shell", environment={},
        )

    def wait_until(self, predicate, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        self.fail("异步生命周期未在期限内达到预期状态")

    def start(self, coordinator, plan, key="WP:1:test"):
        return coordinator.start(
            idempotency_key=key,
            plan=plan,
            work_package_id="WP",
            round_number=1,
        )

    def test_success_runs_asynchronously_and_persists_terminal_state(self):
        updates = []
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(directory, on_update=lambda: updates.append(1))
            started = time.monotonic()
            result = self.start(coordinator, self.plan(directory, "import time; time.sleep(.15); print('ok')"))
            self.assertEqual("scheduled", result["outcome"])
            self.assertLess(time.monotonic() - started, 0.12)
            snapshot = self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "completed" and s
            )
            self.assertIsNone(snapshot["active"])
            self.assertIsNone(snapshot["failure_alert"])
            self.assertGreaterEqual(len(updates), 2)

    def test_zero_exit_without_protocol_postcondition_is_persistent_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(directory)
            coordinator.start(
                idempotency_key="WP:1:semantic-noop",
                plan=self.plan(directory, "print('stopped safely without handoff')"),
                work_package_id="WP",
                round_number=1,
                completion_validator=lambda: (False, "状态仍为 CLAUDE_WORKING，未交给 Codex"),
            )
            snapshot = self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "postcondition-failed" and s
            )
            self.assertIsNone(snapshot["active"])
            self.assertEqual("postcondition-failed", snapshot["failure_alert"]["code"])
            self.assertIn("未交给 Codex", snapshot["failure_alert"]["message"])
            retry = coordinator.authorize_retry("WP:1:semantic-noop")
            self.assertEqual("retry-authorized", retry["outcome"])

    def test_global_lease_blocks_a_different_coordinator_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            first = AsyncExecutionCoordinator(directory)
            second = AsyncExecutionCoordinator(directory)
            self.start(first, self.plan(directory, "import time; time.sleep(30)", timeout=40))
            self.wait_until(lambda: first.snapshot()["active"] and first.snapshot()["active"].get("child_pid"))
            blocked = self.start(second, self.plan(directory, "print('must not run')"), key="WP:2:other")
            self.assertEqual("ignored-global-running", blocked["outcome"])
            first.shutdown(wait_timeout=2.0)
            final = self.wait_until(
                lambda: (s := first.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "cancelled" and s
            )
            self.assertEqual("cancelled", final["last_event"]["outcome"])

    def test_dead_owner_and_dead_child_recover_stale_lease_then_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = AsyncExecutionCoordinator(root)
            stale = {
                "schema_version": 1, "idempotency_key": "old", "work_package_id": "OLD",
                "round": 1, "actor": "test", "action": "old", "owner_pid": 99999999,
                "child_pid": 99999998, "state": "running", "started_at": "old",
                "deadline_epoch": 0,
            }
            coordinator._atomic_write_json(coordinator.lease_path, stale)
            result = self.start(coordinator, self.plan(directory, "print('recovered')"), key="new")
            self.assertEqual("scheduled", result["outcome"])
            self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "completed"
            )
            history = coordinator.history_path.read_text(encoding="utf-8")
            self.assertIn('"outcome": "recovered-stale"', history)

    def test_live_orphan_process_fails_closed_and_surfaces_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(directory)
            orphan = {
                "schema_version": 1, "idempotency_key": "old", "work_package_id": "OLD",
                "round": 1, "actor": "test", "action": "old", "owner_pid": 99999999,
                "child_pid": os.getpid(), "state": "running", "started_at": "old",
                "deadline_epoch": 0,
            }
            coordinator._atomic_write_json(coordinator.lease_path, orphan)
            result = self.start(coordinator, self.plan(directory, "raise SystemExit('must not run')"))
            self.assertEqual("blocked-orphan-process", result["outcome"])
            snapshot = coordinator.snapshot()
            self.assertEqual("blocked-orphan-process", snapshot["failure_alert"]["code"])
            self.assertTrue(coordinator.block_path.exists())

    def test_orphan_block_recovers_only_after_child_really_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                start_new_session=True,
            )
            self.addCleanup(lambda: child.poll() is None and child.kill())
            coordinator = AsyncExecutionCoordinator(directory)
            orphan = {
                "schema_version": 1, "idempotency_key": "old", "work_package_id": "OLD",
                "round": 1, "actor": "test", "action": "old", "owner_pid": 99999999,
                "child_pid": child.pid, "state": "running", "started_at": "old",
                "deadline_epoch": 0,
            }
            coordinator._atomic_write_json(coordinator.lease_path, orphan)
            blocked = self.start(coordinator, self.plan(directory, "print('must wait')"), key="new")
            self.assertEqual("blocked-orphan-process", blocked["outcome"])
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=2)
            recovered = self.start(coordinator, self.plan(directory, "print('now safe')"), key="new")
            self.assertEqual("scheduled", recovered["outcome"])
            self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "completed"
            )
            self.assertFalse(coordinator.block_path.exists())

    def test_timeout_is_persisted_as_visible_failure_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(
                directory, runner=SafeProcessRunner(terminate_grace_seconds=0.05),
            )
            self.start(
                coordinator,
                self.plan(directory, "import time; time.sleep(30)", timeout=0.1),
            )
            snapshot = self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "timed-out" and s
            )
            self.assertEqual("timed-out", snapshot["failure_alert"]["code"])
            self.assertIsNone(snapshot["active"])

    def test_shutdown_force_kills_child_that_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(
                directory, runner=SafeProcessRunner(terminate_grace_seconds=0.05),
            )
            self.start(coordinator, self.plan(
                directory,
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(30)",
                timeout=40,
            ))
            active = self.wait_until(
                lambda: (s := coordinator.snapshot())["active"]
                and s["active"].get("child_pid") and s["active"]
            )
            child_pid = active["child_pid"]
            time.sleep(0.1)
            coordinator.shutdown(wait_timeout=2.0)
            self.assertFalse(coordinator._pid_alive(child_pid))
            final = self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "cancelled" and s
            )
            self.assertEqual("cancelled", final["failure_alert"]["code"])

    def test_corrupt_lease_remains_blocked_and_alerted(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(directory)
            coordinator.runtime_dir.mkdir(parents=True, exist_ok=True)
            coordinator.lease_path.write_text("{broken", encoding="utf-8")
            first = self.start(coordinator, self.plan(directory, "print('must not run')"))
            second = self.start(coordinator, self.plan(directory, "print('still must not run')"), key="other")
            self.assertEqual("blocked-corrupt-state", first["outcome"])
            self.assertEqual("blocked-corrupt-state", second["outcome"])
            self.assertEqual("blocked-corrupt-state", coordinator.snapshot()["failure_alert"]["code"])

    def test_nonzero_exit_is_persistent_alert_and_same_key_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = AsyncExecutionCoordinator(directory)
            plan = self.plan(directory, "import sys; print('boom', file=sys.stderr); sys.exit(9)")
            self.start(coordinator, plan)
            snapshot = self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "failed" and s
            )
            self.assertEqual("failed", snapshot["failure_alert"]["code"])
            self.assertIn("boom", snapshot["last_event"]["stderr_tail"])
            again = self.start(coordinator, plan)
            self.assertEqual("ignored-terminal", again["outcome"])
            authorized = coordinator.authorize_retry("WP:1:test")
            self.assertEqual("retry-authorized", authorized["outcome"])
            retried = self.start(coordinator, self.plan(directory, "print('retry succeeds')"))
            self.assertEqual("scheduled", retried["outcome"])
            final = self.wait_until(
                lambda: (s := coordinator.snapshot())["last_event"]
                and s["last_event"].get("outcome") == "completed" and s
            )
            self.assertIsNone(final["failure_alert"])


class SchedulerTests(unittest.TestCase):
    def package(self, **overrides) -> WorkPackage:
        values = dict(
            work_package_id="WP-20260714-003", title="test", status="READY_FOR_CODEX",
            owner="codex", handoff_to="codex", round=1, max_rounds=3,
            scope=["src/example.py"], base_commit="abc",
            scope_baseline_sha256=HASH_A,
            implementation_scope_sha256=HASH_A,
            review_started_sha256=HASH_A,
            review_finished_sha256=HASH_A,
        )
        values.update(overrides)
        return WorkPackage(**values)

    def scheduler(self, runtime: str | Path, digest: str = HASH_A, **kwargs) -> DryRunScheduler:
        return DryRunScheduler(
            "source.md",
            runtime,
            scope_hash_resolver=lambda package: ScopeHashResult(digest, [], []),
            **kwargs,
        )

    def test_duplicate_event_generates_one_action(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            first = scheduler.dispatch(self.package())
            second = scheduler.dispatch(self.package())
        self.assertEqual("dry-run-candidate", first.outcome)
        self.assertEqual("ignored-duplicate", second.outcome)
        self.assertEqual(HASH_A, second.scope_current_sha256)
        self.assertEqual(HASH_A, second.scope_expected_sha256)

    def test_different_round_generates_new_action(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            first = scheduler.dispatch(self.package(round=1))
            second = scheduler.dispatch(self.package(round=2))
        self.assertEqual("dry-run-candidate", first.outcome)
        self.assertEqual("dry-run-candidate", second.outcome)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)

    def test_same_action_already_running_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            key = "WP-20260714-003:1:start_codex_review"
            (runtime / "runs.jsonl").write_text(
                json.dumps({"outcome": "running", "idempotency_key": key}) + "\n",
                encoding="utf-8",
            )
            result = self.scheduler(runtime).dispatch(self.package())
        self.assertEqual("ignored-running", result.outcome)

    def test_corrupt_runtime_record_stops_safely_and_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "runs.jsonl").write_text("not-json\n", encoding="utf-8")
            scheduler = self.scheduler(runtime)
            result = scheduler.dispatch(self.package())
            failure = scheduler.failure_log_path.read_text(encoding="utf-8")
        self.assertEqual("failed", result.outcome)
        self.assertIn("安全停止", result.reason)
        self.assertIn('"outcome": "failed"', failure)

    def test_scheduler_lock_is_released_and_can_recover_after_record_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "runs.jsonl").write_text("not-json\n", encoding="utf-8")
            scheduler = self.scheduler(runtime)
            failed = scheduler.dispatch(self.package())
            (runtime / "runs.jsonl").write_text("", encoding="utf-8")
            recovered = scheduler.dispatch(self.package())
        self.assertEqual("failed", failed.outcome)
        self.assertEqual("dry-run-candidate", recovered.outcome)

    def test_invalid_state_never_triggers(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            result = scheduler.dispatch(self.package(status="READY_FOR_CODEX", owner="fable5"))
        self.assertEqual("rejected-invalid", result.outcome)
        self.assertIsNone(result.action)

    def test_round_over_limit_becomes_user_candidate_without_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            result = scheduler.dispatch(self.package(round=4, max_rounds=3))
        self.assertEqual("dry-run-user-action", result.outcome)
        self.assertEqual("notify_user_round_exceeded", result.action)

    def test_changes_requested_at_max_rounds_does_not_trigger_claude(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(
                directory,
                claude=ClaudeEndpointAdapter(
                    executable=sys.executable, project_root=directory, authenticated=True,
                ),
            ).dispatch(self.package(
                status="CHANGES_REQUESTED", owner="claude", handoff_to="claude",
                round=3, max_rounds=3,
            ))
        self.assertEqual("dry-run-user-action", result.outcome)
        self.assertEqual("notify_user_round_exceeded", result.action)
        self.assertNotEqual("start_claude_rework", result.action)

    def test_claude_working_initial_package_uses_baseline_and_needs_no_implementation_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(
                directory,
                claude=ClaudeEndpointAdapter(
                    executable=sys.executable, project_root=directory, authenticated=True,
                ),
            ).dispatch(self.package(
                status="CLAUDE_WORKING", owner="claude", handoff_to="claude",
                implementation_scope_sha256=None,
                review_started_sha256=None,
                review_finished_sha256=None,
            ))
        self.assertEqual("dry-run-candidate", result.outcome)
        self.assertEqual("start_claude_implementation", result.action)
        self.assertEqual("scope_baseline_sha256", result.scope_hash_basis)
        self.assertEqual("claude", result.adapter)
        self.assertEqual("available-disabled", result.adapter_status)

    def test_ready_for_codex_at_max_rounds_still_allows_review(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(self.package(round=3, max_rounds=3))
        self.assertEqual("dry-run-candidate", result.outcome)
        self.assertEqual("start_codex_review", result.action)

    def test_ready_for_codex_compares_current_hash_with_implementation(self):
        adapter = NeverExecuteCodex()
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory, digest=HASH_B, codex=adapter).dispatch(self.package())
        self.assertEqual("rejected-invalid", result.outcome)
        self.assertEqual("implementation scope_sha256", result.scope_hash_basis)
        self.assertEqual(HASH_B, result.scope_current_sha256)
        self.assertEqual(HASH_A, result.scope_expected_sha256)
        self.assertEqual(0, adapter.calls)

    def test_changes_requested_compares_current_hash_with_review_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory, digest=HASH_B).dispatch(self.package(
                status="CHANGES_REQUESTED", owner="claude", handoff_to="claude",
                round=2, implementation_scope_sha256=HASH_A,
                review_started_sha256=HASH_B, review_finished_sha256=HASH_B,
            ))
        self.assertEqual("dry-run-candidate", result.outcome)
        self.assertEqual("start_claude_rework", result.action)
        self.assertEqual("review_finished_sha256", result.scope_hash_basis)

    def test_review_start_finish_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(self.package(
                status="CHANGES_REQUESTED", owner="claude", handoff_to="claude", round=2,
                review_started_sha256=HASH_A, review_finished_sha256=HASH_B,
            ))
        self.assertEqual("rejected-invalid", result.outcome)
        self.assertIn("审核开始/结束", result.reason)

    def test_missing_baseline_or_review_hash_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            missing_baseline = scheduler.dispatch(self.package(scope_baseline_sha256=None))
            missing_review = scheduler.dispatch(self.package(
                status="CHANGES_REQUESTED", owner="claude", handoff_to="claude", round=2,
                review_finished_sha256=None,
            ))
        self.assertEqual("rejected-invalid", missing_baseline.outcome)
        self.assertIn("scope_baseline_sha256", missing_baseline.reason)
        self.assertEqual("rejected-invalid", missing_review.outcome)
        self.assertIn("review_finished_sha256", missing_review.reason)

    def test_scope_read_error_rejected_before_action(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = DryRunScheduler(
                "source.md", directory,
                scope_hash_resolver=lambda package: ScopeHashResult(None, [], ["scope 文件不可读: src/example.py"]),
            )
            result = scheduler.dispatch(self.package())
        self.assertEqual("rejected-invalid", result.outcome)
        self.assertIsNone(result.action)
        self.assertIn("不可读", result.reason)

    def test_missing_implementation_scope_hash_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            result = scheduler.dispatch(self.package(implementation_scope_sha256=None))
        self.assertEqual("rejected-invalid", result.outcome)
        self.assertIn("implementation scope_sha256", result.reason)
        self.assertEqual("invalid_scope_hash", result.notification_candidate["event"])

    def test_dry_run_never_starts_external_process(self):
        adapter = NeverExecuteCodex()
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory, codex=adapter).dispatch(self.package())
        self.assertEqual(0, adapter.calls)
        self.assertFalse(result.external_process_started)
        self.assertTrue(result.dry_run)
        self.assertEqual("handoff_to_codex", result.notification_candidate["event"])

    def test_codex_execution_plan_is_auditable_but_disabled(self):
        adapter = CodexCommandAdapter(
            executable=sys.executable, project_root="/tmp/project", timeout_seconds=321,
        )
        plan = adapter.command_for(self.package())
        self.assertTrue(adapter.available)
        self.assertFalse(adapter.enabled)
        self.assertEqual(str(Path("/tmp/project").resolve()), plan.cwd)
        self.assertEqual(321, plan.timeout_seconds)
        self.assertIn("workspace-write", plan.command)
        self.assertIn("--ephemeral", plan.command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", plan.command)

    def test_dispatch_exposes_plan_without_executing_it(self):
        adapter = NeverExecuteCodex()
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory, codex=adapter).dispatch(self.package())
        self.assertEqual("available-disabled", result.adapter_status)
        self.assertEqual("codex", result.adapter)
        self.assertEqual(0, adapter.calls)
        self.assertIsNotNone(result.execution_plan)
        self.assertFalse(result.external_process_started)


class EventDrivenSchedulerTests(unittest.TestCase):
    def package(self, *, status="READY_FOR_CODEX", owner="codex", handoff="codex"):
        return WorkPackage(
            work_package_id="WP-20260716-006", title="live", status=status,
            owner=owner, handoff_to=handoff, round=1, max_rounds=3,
            scope=["src/example.py"], scope_baseline_sha256=HASH_A,
            implementation_scope_sha256=HASH_A,
            review_started_sha256=HASH_A, review_finished_sha256=HASH_A,
        )

    def adapters(self, directory):
        return (
            CodexCommandAdapter(executable=sys.executable, project_root=directory, enabled=True),
            ClaudeEndpointAdapter(
                executable=sys.executable, project_root=directory,
                authenticated=True, enabled=True,
            ),
        )

    def test_requires_both_adapters_to_be_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            EventDrivenScheduler(
                "source.md", directory,
                codex=CodexCommandAdapter(executable=sys.executable, enabled=False),
                claude=ClaudeEndpointAdapter(
                    executable=sys.executable, authenticated=True, enabled=True,
                ),
            )

    def test_dispatch_is_async_and_same_terminal_key_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            codex, claude = self.adapters(directory)
            plan = ExecutionPlan(
                actor="codex", action="start_codex_review",
                command=[sys.executable, "-c", "print('reviewed')"], cwd=directory,
                timeout_seconds=2, permission_summary="test", environment={},
            )
            with (
                mock.patch.object(codex, "command_for", return_value=plan),
                mock.patch.object(
                    EventDrivenScheduler,
                    "_completion_validator",
                    return_value=lambda: (True, "test postcondition"),
                ),
            ):
                coordinator = AsyncExecutionCoordinator(Path(directory) / "runtime")
                scheduler = EventDrivenScheduler(
                    "source.md", Path(directory) / "runtime",
                    codex=codex, claude=claude, project_root=directory,
                    coordinator=coordinator,
                    scope_hash_resolver=lambda package: ScopeHashResult(HASH_A, [], []),
                )
                first = scheduler.dispatch(self.package())
                self.assertEqual("execution-scheduled", first.outcome)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    last = coordinator.snapshot()["last_event"]
                    if last and last.get("outcome") == "completed":
                        break
                    time.sleep(0.02)
                else:
                    self.fail("事件执行未完成")
                second = scheduler.dispatch(self.package())
            self.assertFalse(first.dry_run)
            self.assertEqual("ignored-terminal", second.outcome)
            self.assertFalse(second.external_process_started)

    def test_user_terminal_state_never_starts_external_process(self):
        with tempfile.TemporaryDirectory() as directory:
            codex, claude = self.adapters(directory)
            scheduler = EventDrivenScheduler(
                "source.md", Path(directory) / "runtime",
                codex=codex, claude=claude, project_root=directory,
                scope_hash_resolver=lambda package: ScopeHashResult(HASH_A, [], []),
            )
            result = scheduler.dispatch(self.package(status="APPROVED", owner="user", handoff="user"))
        self.assertEqual("user-action", result.outcome)
        self.assertFalse(result.external_process_started)
        self.assertFalse(result.dry_run)

    def test_state_store_receives_async_lifecycle_updates_without_file_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            scope = root / "src" / "example.py"
            scope.write_text("value = 1\n", encoding="utf-8")
            file_hash = hashlib.sha256(scope.read_bytes()).hexdigest()
            aggregate = hashlib.sha256(f"{file_hash}  src/example.py\n".encode()).hexdigest()
            source = root / "handoff.md"
            source.write_text(package_text(
                baseline_hash=aggregate,
                implementation_hash=aggregate,
                review_started_hash=aggregate,
                review_finished_hash=aggregate,
            ), encoding="utf-8")
            codex, claude = self.adapters(directory)
            plan = ExecutionPlan(
                actor="codex", action="start_codex_review",
                command=[sys.executable, "-c", "print('done')"], cwd=directory,
                timeout_seconds=2, permission_summary="test", environment={},
            )
            with (
                mock.patch.object(codex, "command_for", return_value=plan),
                mock.patch.object(
                    EventDrivenScheduler,
                    "_completion_validator",
                    return_value=lambda: (True, "test postcondition"),
                ),
            ):
                coordinator = AsyncExecutionCoordinator(root / "runtime")
                scheduler = EventDrivenScheduler(
                    source, root / "runtime", codex=codex, claude=claude,
                    project_root=root, coordinator=coordinator,
                )
                store = StateStore(source, scheduler=scheduler)
                scheduler.set_on_update(store.refresh)
                store.refresh()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    snapshot = store.snapshot()
                    last = snapshot["execution_lifecycle"]["last_event"]
                    if last and last.get("outcome") == "completed":
                        break
                    time.sleep(0.02)
                else:
                    self.fail("StateStore 未收到异步完成更新")
            self.assertFalse(snapshot["system"]["dry_run"])
            self.assertTrue(snapshot["system"]["external_processes_enabled"])
            self.assertIsNone(snapshot["system"]["execution_failure_alert"])
            self.assertGreaterEqual(snapshot["version"], 2)

    def test_protocol_completion_validator_rejects_zero_exit_without_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            scope = root / "src" / "example.py"
            scope.write_text("value = 1\n", encoding="utf-8")
            file_hash = hashlib.sha256(scope.read_bytes()).hexdigest()
            aggregate = hashlib.sha256(f"{file_hash}  src/example.py\n".encode()).hexdigest()
            source = root / "handoff.md"
            source.write_text(package_text(
                baseline_hash=aggregate,
                implementation_hash=aggregate,
                review_started_hash=aggregate,
                review_finished_hash=aggregate,
            ), encoding="utf-8")
            codex, claude = self.adapters(directory)
            scheduler = EventDrivenScheduler(
                source, root / "runtime", codex=codex, claude=claude,
                project_root=root,
            )
            current = HandoffParser(source).parse_file().current
            valid, reason = scheduler._completion_validator(
                current, "start_codex_review"
            )()
        self.assertFalse(valid)
        self.assertIn("状态未完成交接", reason)

    def test_protocol_completion_validator_accepts_review_with_matching_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            scope = root / "src" / "example.py"
            scope.write_text("value = 1\n", encoding="utf-8")
            file_hash = hashlib.sha256(scope.read_bytes()).hexdigest()
            aggregate = hashlib.sha256(f"{file_hash}  src/example.py\n".encode()).hexdigest()
            source = root / "handoff.md"
            source.write_text(package_text(
                status="APPROVED", owner="user", handoff="user",
                baseline_hash=aggregate,
                implementation_hash=aggregate,
                review_started_hash=aggregate,
                review_finished_hash=aggregate,
            ), encoding="utf-8")
            codex, claude = self.adapters(directory)
            scheduler = EventDrivenScheduler(
                source, root / "runtime", codex=codex, claude=claude,
                project_root=root,
            )
            initial = WorkPackage(
                work_package_id="WP-20260714-003", title="test", status="READY_FOR_CODEX",
                owner="codex", handoff_to="codex", round=1, max_rounds=3,
                scope=["src/example.py"], scope_baseline_sha256=aggregate,
                implementation_scope_sha256=aggregate,
            )
            valid, reason = scheduler._completion_validator(
                initial, "start_codex_review"
            )()
        self.assertTrue(valid, reason)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        scope_file = self.root / "src" / "example.py"
        scope_file.write_text("value = 1\n", encoding="utf-8")
        file_hash = hashlib.sha256(scope_file.read_bytes()).hexdigest()
        manifest = f"{file_hash}  src/example.py\n"
        self.scope_hash = hashlib.sha256(manifest.encode()).hexdigest()
        self.source = self.root / "handoff.md"
        self.source.write_text(package_text(
            baseline_hash=self.scope_hash,
            implementation_hash=self.scope_hash,
            review_started_hash=self.scope_hash,
            review_finished_hash=self.scope_hash,
        ), encoding="utf-8")
        # 注入短 fallback interval，使无 kqueue 平台（Linux 降级）也能确定性检测文件变化。
        self.app = DashboardApplication(self.source, port=0, fallback_interval=0.15)
        self.app.start(background=True)
        host, port = self.app.address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.app.stop()
        self.temp.cleanup()

    def read_business_event(self, response) -> tuple[str, dict]:
        event: str | None = None
        data_lines: list[str] = []
        while True:
            raw = response.readline()
            if not raw:
                self.fail("SSE 连接在收到业务事件前关闭")
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if event is not None:
                    return event, json.loads("\n".join(data_lines))
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                data_lines.append(line.partition(":")[2].lstrip())

    def test_status_api(self):
        with urlopen(self.base + "/api/status", timeout=3) as response:
            data = json.load(response)
        self.assertEqual("WP-20260714-003", data["current_work_package_id"])
        self.assertEqual("READY_FOR_CODEX", data["current"]["status"])
        self.assertTrue(data["system"]["read_only"])
        self.assertTrue(data["system"]["dry_run"])
        self.assertEqual(self.scope_hash, data["dispatch"]["scope_current_sha256"])
        self.assertEqual(self.scope_hash, data["dispatch"]["scope_expected_sha256"])

    def test_project_local_heartbeat_exposes_fresh_fail_closed_signal(self):
        heartbeat_path = self.root / ".ai-handoff-runtime" / "coordinator_status.json"
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        self.assertEqual("live", payload["state"])
        self.assertTrue(payload["coordinator_live"])
        self.assertGreater(payload["valid_until_epoch"], time.time())
        self.assertEqual(self.app.watcher.mode, payload["watcher_mode"])
        self.assertFalse(payload["external_processes_enabled"])
        self.assertTrue(payload["legacy_polling_must_remain_paused"])
        self.assertFalse(payload["legacy_polling_resume_authorized"])

    def test_atomic_replace_of_temporary_copy_updates_status_api(self):
        version = self.app.state.version
        replacement = self.source.with_name("replacement.md")
        replacement.write_text(package_text(status="CODEX_REVIEWING"), encoding="utf-8")
        os.replace(replacement, self.source)
        changed_version = self.app.state.wait_for_change(version, 3.0)
        self.assertGreater(changed_version, version)
        with urlopen(self.base + "/api/status", timeout=3) as response:
            data = json.load(response)
        self.assertEqual("CODEX_REVIEWING", data["current"]["status"])

    def test_sse_sends_new_status_after_atomic_replace(self):
        with urlopen(self.base + "/api/events", timeout=8) as response:
            initial_event, initial_data = self.read_business_event(response)
            self.assertEqual("status", initial_event)
            self.assertEqual("READY_FOR_CODEX", initial_data["current"]["status"])

            replacement = self.source.with_name("replacement-sse.md")
            replacement.write_text(package_text(
                status="CODEX_REVIEWING",
                baseline_hash=self.scope_hash,
                implementation_hash=self.scope_hash,
                review_started_hash=self.scope_hash,
                review_finished_hash=self.scope_hash,
            ), encoding="utf-8")
            os.replace(replacement, self.source)

            next_event, next_data = self.read_business_event(response)
            self.assertEqual("status", next_event)
            self.assertEqual("CODEX_REVIEWING", next_data["current"]["status"])
            self.assertGreater(next_data["version"], initial_data["version"])

    def test_page_has_disconnect_reconnect_theme_and_narrow_layout(self):
        with urlopen(self.base + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
        self.assertIn("连接已断开", html)
        self.assertIn("stream.onerror", html)
        self.assertIn("new EventSource('/api/events')", html)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn("@media (max-width:560px)", html)


class CoordinatorHeartbeatTests(unittest.TestCase):
    def test_sequence_advances_and_clean_stop_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime" / "coordinator_status.json"
            heartbeat = CoordinatorHeartbeat(
                path,
                lambda: {
                    "watcher_mode": "native-kqueue",
                    "external_processes_enabled": True,
                    "execution_failure_alert": None,
                },
                interval_seconds=0.02,
                stale_after_seconds=0.08,
            )
            heartbeat.start()
            first = json.loads(path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 1.0
            current = first
            while current["heartbeat_sequence"] == first["heartbeat_sequence"]:
                if time.monotonic() >= deadline:
                    self.fail("心跳序号没有推进")
                time.sleep(0.01)
                current = json.loads(path.read_text(encoding="utf-8"))
            heartbeat.stop()
            stopped = json.loads(path.read_text(encoding="utf-8"))

        self.assertGreater(
            current["heartbeat_sequence"], first["heartbeat_sequence"]
        )
        self.assertEqual("stopped", stopped["state"])
        self.assertFalse(stopped["coordinator_live"])
        self.assertFalse(stopped["legacy_polling_resume_authorized"])
        self.assertLessEqual(stopped["valid_until_epoch"], time.time())


class DashboardWatcherPathTests(unittest.TestCase):
    """两条监听路径都有确定性证据:强制 fallback（任意 OS）与原生 kqueue（仅 macOS/BSD）。"""

    def _make_app(self, **kwargs) -> tuple[DashboardApplication, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / "handoff.md"
        source.write_text(package_text(), encoding="utf-8")
        app = DashboardApplication(source, port=0, **kwargs)
        app.start(background=True)
        self.addCleanup(app.stop)  # 无论断言是否失败都关闭监听 socket
        return app, source

    def _atomic_replace(self, source: Path, status: str) -> None:
        replacement = source.with_name("replacement.md")
        replacement.write_text(package_text(status=status), encoding="utf-8")
        os.replace(replacement, source)

    def test_forced_fallback_path_reloads_deterministically(self):
        app, source = self._make_app(force_fallback=True, fallback_interval=0.15)
        self.assertTrue(app.watcher.mode.startswith("degraded"), app.watcher.mode)
        version = app.state.version
        self._atomic_replace(source, "CODEX_REVIEWING")
        changed = app.state.wait_for_change(version, 3.0)
        self.assertGreater(changed, version)
        host, port = app.address
        with urlopen(f"http://{host}:{port}/api/status", timeout=3) as response:
            self.assertEqual("CODEX_REVIEWING", json.load(response)["current"]["status"])

    @unittest.skipUnless(hasattr(select, "kqueue"), "原生 kqueue 仅在 macOS/BSD 可用")
    def test_native_kqueue_path_reloads(self):
        app, source = self._make_app()  # 默认，不强制 fallback → macOS 使用 native-kqueue
        self.assertEqual("native-kqueue", app.watcher.mode)
        version = app.state.version
        self._atomic_replace(source, "CODEX_REVIEWING")
        changed = app.state.wait_for_change(version, 4.0)
        self.assertGreater(changed, version)
        host, port = app.address
        with urlopen(f"http://{host}:{port}/api/status", timeout=3) as response:
            self.assertEqual("CODEX_REVIEWING", json.load(response)["current"]["status"])


class NeverExecuteClaude(ClaudeEndpointAdapter):
    def __init__(self):
        super().__init__(
            executable=sys.executable, project_root=Path.cwd(), authenticated=True,
        )
        self.calls = 0

    def execute(self, package: WorkPackage) -> None:
        self.calls += 1
        raise AssertionError("Claude 入口不可用时不应调用外部进程")


class ClaudeNamingTests(unittest.TestCase):
    """任务一/二命名统一 + 入口不可用行为。"""

    def parse(self, text: str):
        return HandoffParser("memory.md").parse_text(text)

    def scheduler(self, runtime, digest: str = HASH_A, **kwargs) -> DryRunScheduler:
        return DryRunScheduler(
            "source.md", runtime,
            scope_hash_resolver=lambda package: ScopeHashResult(digest, [], []),
            **kwargs,
        )

    def package(self, **overrides) -> WorkPackage:
        values = dict(
            work_package_id="WP-TEST-CLAUDE", title="t", status="CHANGES_REQUESTED",
            owner="claude", handoff_to="claude", round=2, max_rounds=3,
            scope=["src/example.py"], base_commit="abc",
            scope_baseline_sha256=HASH_A, implementation_scope_sha256=HASH_A,
            review_started_sha256=HASH_A, review_finished_sha256=HASH_A,
        )
        values.update(overrides)
        return WorkPackage(**values)

    def test_new_claude_working_mapping_is_valid(self):
        package = self.parse(package_text(
            status="CLAUDE_WORKING", owner="claude", handoff="claude",
        )).packages[0]
        self.assertTrue(package.valid, package.errors)
        self.assertEqual("CLAUDE_WORKING", package.canonical_status)
        self.assertFalse(package.status_is_legacy)
        self.assertEqual("Claude", package.waiting_for)
        self.assertIn("Claude", package.write_access)

    def test_new_changes_requested_generates_claude_rework_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(self.package())
        self.assertEqual("dry-run-candidate", result.outcome)
        self.assertEqual("start_claude_rework", result.action)
        self.assertEqual("returned_to_claude", result.notification_candidate["event"])

    def test_legacy_fable_working_parses_read_only_but_not_new_output(self):
        package = self.parse(package_text(
            status="FABLE_WORKING", owner="fable5", handoff="fable5", impl_actor="Fable5",
        )).packages[0]
        # 仍能只读解析且合法
        self.assertTrue(package.valid, package.errors)
        # 规范化到新状态；标记为历史兼容且带 deprecated 警告
        self.assertEqual("CLAUDE_WORKING", package.canonical_status)
        self.assertTrue(package.status_is_legacy)
        self.assertTrue(any("历史兼容状态" in w for w in package.warnings))
        # 页面统一显示 Claude，不原样输出旧名
        self.assertEqual("Claude", package.waiting_for)
        self.assertEqual("Claude", package.current_handler)
        self.assertEqual("claude", canonical_actor("fable5"))
        self.assertEqual("CLAUDE_WORKING", canonical_status("FABLE_WORKING"))

    def test_new_and_legacy_impl_titles_both_recognized_and_display_claude(self):
        for actor in ("Claude", "Fable5"):
            package = self.parse(package_text(
                status="CLAUDE_WORKING", owner="claude", handoff="claude", impl_actor=actor,
            )).packages[0]
            implementations = [r for r in package.records if r.kind == "implementation"]
            self.assertEqual(1, len(implementations), actor)
            self.assertEqual("Claude", package.waiting_for)

    def test_claude_endpoint_available_but_disabled_never_starts_process(self):
        adapter = NeverExecuteClaude()
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory, claude=adapter).dispatch(self.package())
        self.assertEqual(0, adapter.calls)
        self.assertFalse(result.external_process_started)
        self.assertTrue(result.dry_run)
        self.assertTrue(adapter.available)
        self.assertFalse(adapter.enabled)
        self.assertEqual("available-disabled", result.adapter_status)
        self.assertEqual("claude", result.adapter)
        self.assertIsNotNone(result.execution_plan)

    def test_claude_endpoint_execute_raises_when_disabled(self):
        with self.assertRaises(RuntimeError):
            ClaudeEndpointAdapter(
                executable=sys.executable, project_root=Path.cwd(), authenticated=True,
            ).execute(self.package())

    def test_claude_execution_plan_is_fail_closed_and_blocks_git(self):
        adapter = ClaudeEndpointAdapter(
            executable=sys.executable,
            project_root="/tmp/project",
            timeout_seconds=654,
            authenticated=True,
            proxy_url="http://127.0.0.1:6789",
        )
        plan = adapter.command_for(self.package())
        joined = " ".join(plan.command)
        prompt = plan.command[2]
        self.assertEqual(str(Path("/tmp/project").resolve()), plan.cwd)
        self.assertEqual(654, plan.timeout_seconds)
        self.assertIn("--permission-mode dontAsk", joined)
        self.assertIn("Bash(git *)", joined)
        self.assertIn("Bash(python3 *)", joined)
        self.assertIn("- scope_sha256: <64位小写十六进制>", prompt)
        self.assertIn("- implementation_finished_at: <带时区时间>", prompt)
        self.assertIn("--no-session-persistence", plan.command)
        self.assertNotIn("--dangerously-skip-permissions", plan.command)
        self.assertEqual(
            {
                "HTTP_PROXY": "http://127.0.0.1:6789",
                "HTTPS_PROXY": "http://127.0.0.1:6789",
            },
            plan.environment,
        )

    def test_claude_proxy_rejects_credentials_and_socks(self):
        for proxy in ("socks5://127.0.0.1:6789", "http://user:secret@127.0.0.1:6789"):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                ClaudeEndpointAdapter(executable=sys.executable, proxy_url=proxy)

    def test_claude_auth_probe_requires_completed_logged_in_json(self):
        adapter = ClaudeEndpointAdapter(
            executable=sys.executable, project_root=Path.cwd(),
            proxy_url="http://127.0.0.1:6789",
        )
        ok = ProcessRunResult(
            outcome="completed", returncode=0, timed_out=False, duration_seconds=0,
            process_id=1, stdout_tail='{"loggedIn": true, "email": "not-persisted"}',
            stderr_tail="",
        )
        with mock.patch.object(SafeProcessRunner, "run", return_value=ok) as run:
            self.assertTrue(adapter.probe_authenticated())
        plan = run.call_args.args[0]
        self.assertEqual([sys.executable, "auth", "status"], plan.command)
        self.assertEqual("http://127.0.0.1:6789", plan.environment["HTTPS_PROXY"])
        failed = ProcessRunResult(
            outcome="failed", returncode=1, timed_out=False, duration_seconds=0,
            process_id=1, stdout_tail='{"loggedIn": true}', stderr_tail="error",
        )
        with mock.patch.object(SafeProcessRunner, "run", return_value=failed):
            self.assertFalse(adapter.probe_authenticated())

    def test_fable5_adapter_is_deprecated_alias_of_claude_adapter(self):
        self.assertIs(Fable5EndpointAdapter, ClaudeEndpointAdapter)

    def test_server_snapshot_reports_both_triggers_available_but_disabled(self):
        # 用 StateStore 而非 DashboardApplication:不绑定监听 socket，避免 unclosed socket 泄漏。
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(), encoding="utf-8")
            scheduler = DryRunScheduler(
                source,
                Path(directory) / "runtime",
                project_root=directory,
                codex=CodexCommandAdapter(executable=sys.executable, project_root=directory),
                claude=ClaudeEndpointAdapter(
                    executable=sys.executable, project_root=directory, authenticated=True,
                ),
                scope_hash_resolver=lambda package: ScopeHashResult(HASH_A, [], []),
            )
            store = StateStore(source, scheduler=scheduler)
            store.refresh()
            snapshot = store.snapshot()
        self.assertEqual("available-disabled", snapshot["system"]["claude_trigger"])
        self.assertEqual("available-disabled", snapshot["system"]["codex_trigger"])
        self.assertFalse(snapshot["system"]["external_processes_enabled"])
        # deprecated 只读别名仍在，保证向后兼容
        self.assertEqual("available-disabled", snapshot["system"]["fable5_trigger"])

    def test_dashboard_injects_claude_http_proxy_into_execution_plan_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            scope = root / "src" / "example.py"
            scope.write_text("value = 1\n", encoding="utf-8")
            digest = hashlib.sha256(scope.read_bytes()).hexdigest()
            aggregate = hashlib.sha256(f"{digest}  src/example.py\n".encode()).hexdigest()
            source = root / "handoff.md"
            source.write_text(package_text(
                status="CHANGES_REQUESTED",
                owner="claude",
                handoff="claude",
                baseline_hash=aggregate,
                implementation_hash=aggregate,
                review_started_hash=aggregate,
                review_finished_hash=aggregate,
            ), encoding="utf-8")
            app = DashboardApplication(
                source, port=0, claude_proxy="http://127.0.0.1:6789",
            )
            self.addCleanup(app.server.server_close)
            app.state.refresh()
            snapshot = app.state.snapshot()
        self.assertEqual(
            "http://127.0.0.1:6789",
            snapshot["dispatch"]["execution_plan"]["environment"]["HTTPS_PROXY"],
        )
        self.assertFalse(snapshot["system"]["external_processes_enabled"])

    def test_live_dashboard_fails_closed_when_claude_auth_probe_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(), encoding="utf-8")
            with mock.patch.object(ClaudeEndpointAdapter, "probe_authenticated", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "登录态核验失败"):
                    DashboardApplication(source, port=0, enable_external_processes=True)

    def test_live_dashboard_reports_explicit_execution_mode_after_auth_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(), encoding="utf-8")
            with mock.patch.object(ClaudeEndpointAdapter, "probe_authenticated", return_value=True):
                app = DashboardApplication(source, port=0, enable_external_processes=True)
            self.addCleanup(app.server.server_close)
            snapshot = app.state.snapshot()
        self.assertFalse(snapshot["system"]["dry_run"])
        self.assertTrue(snapshot["system"]["external_processes_enabled"])
        self.assertEqual("enabled", snapshot["system"]["claude_trigger"])
        self.assertEqual("enabled", snapshot["system"]["codex_trigger"])

    def test_panel_display_fields_normalize_legacy_status(self):
        # 历史 FABLE_WORKING / fable5 / fable5 可被读取，但面板展示字段一律规范化为 Claude / CLAUDE_WORKING。
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "handoff.md"
            source.write_text(package_text(
                status="FABLE_WORKING", owner="fable5", handoff="fable5", impl_actor="Fable5",
            ), encoding="utf-8")
            store = StateStore(source)
            store.refresh()
            snapshot = store.snapshot()
        cur = snapshot["current"]
        # 原始值仍保留（诊断/来源用），但标记为历史兼容
        self.assertEqual("FABLE_WORKING", cur["status"])
        self.assertTrue(cur["status_is_legacy"])
        # 标准展示字段规范化
        self.assertEqual("CLAUDE_WORKING", cur["canonical_status"])
        self.assertEqual("Claude", cur["waiting_for"])
        self.assertEqual("Claude", cur["current_handler"])
        self.assertEqual("Claude 正在实施", cur["status_explanation"])

    def test_dashboard_html_uses_canonical_status_for_display(self):
        html = (
            Path(__file__).resolve().parents[1] / "tools" / "ai_handoff" / "dashboard.html"
        ).read_text(encoding="utf-8")
        # 面板必须用 canonical_status 归一化，而不是直接输出原始 p.status
        self.assertIn("p.canonical_status || p.status", html)
        self.assertIn("p.status_explanation || p.canonical_status || p.status", html)


def manifest_digest(entries: list[str]) -> str:
    """按交接协议由 manifest 条目重建规范文本并求聚合 SHA-256。"""
    return hashlib.sha256("".join(f"{e}\n" for e in entries).encode("utf-8")).hexdigest()


SCOPE_ENTRY = f"{HASH_A}  src/example.py"
DEFAULT_SR_DIGEST = manifest_digest([SCOPE_ENTRY])


def three_phase_text(
    wp_id: str = "WP-TEST-009",
    *,
    status: str = "READY_FOR_CODEX",
    owner: str = "codex",
    handoff: str = "codex",
    round_number: int = 1,
    max_rounds: int = 3,
    protocol: str | None = "v2",
    with_self_review: bool = True,
    self_review_round: int | None = None,
    verdict: str = "PASS",
    self_review_hash: str | None = None,   # None → 由 manifest 真实重建
    implementation_hash: str | None = None,  # None → 与自审哈希一致
    with_implementation_hash: bool = True,   # False → 完全不写 scope_sha256 行
    baseline_hash: str | None = HASH_A,
    tests_line: str = "`python -m unittest` → Ran **1108** tests, OK",
    with_review: bool = False,
    self_review_round_heading: bool = True,
    implementation_round: int | None = None,
    implementation_before_self_review: bool = False,
    with_manifest: bool = True,
    manifest_lines: list[str] | None = None,
    ready_line: str = "是",
    started_at: str = "2026-07-20 16:11 CST",
    finished_at: str = "2026-07-20 16:16 CST",
    known_issue_line: str = "无",
) -> str:
    """新三阶段（v2）工作包：自审 / 实施交接 / Codex 独立审核 各自独立成段。"""
    protocol_line = f"- handoff_protocol: {protocol}\n" if protocol else ""
    baseline_line = f"- scope_baseline_sha256: {baseline_hash}\n" if baseline_hash else ""
    sr_round = self_review_round if self_review_round is not None else round_number
    entries_for_digest = manifest_lines if manifest_lines is not None else [SCOPE_ENTRY]
    if self_review_hash is None:
        self_review_hash = manifest_digest(entries_for_digest)
    if implementation_hash is None:
        implementation_hash = self_review_hash
    sr_hash_line = f"- self_review_scope_sha256: {self_review_hash}\n" if self_review_hash else ""
    self_review = ""
    if with_self_review:
        heading = f"Claude 交接前自审（Round {sr_round}）" if self_review_round_heading else "Claude 交接前自审"
        entries = manifest_lines if manifest_lines is not None else [f"{HASH_A}  src/example.py"]
        manifest_block = ""
        if with_manifest:
            rendered = "".join(f"  - `{line}`\n" for line in entries)
            manifest_block = f"- self_review_manifest:\n{rendered}"
        self_review = f"""
### {heading}

- self_review_started_at: {started_at}
- self_review_finished_at: {finished_at}
- self_review_verdict: {verdict}
{sr_hash_line}{manifest_block}- 实际测试命令与结果: {tests_line}
- 首次失败: 无
- 失败根因: 不适用
- 修复内容: 不适用
- 修复后重跑结果: 与首次一致
- 已知疑问: {known_issue_line}
- 未验证边界: 真机未验证
- 是否满足交接条件: {ready_line}
"""
    impl_hash_line = (
        f"- scope_sha256: {implementation_hash}\n"
        if (with_implementation_hash and implementation_hash) else ""
    )
    impl_round = implementation_round if implementation_round is not None else round_number
    implementation = f"""
### Claude 实施交接（Round {impl_round}）

- 完成内容: 实现三阶段结构。
{impl_hash_line}- implementation_finished_at: 2026-07-20 16:16 CST
"""
    review = ""
    if with_review:
        review = f"""
### Codex 审核结论（Round {round_number}）

- verdict: CHANGES_REQUESTED
- 已验证事实: 独立复核。
- 必须返修: 修边界。
- 审核证据: review_started_sha256={HASH_A}, review_finished_sha256={HASH_A}
- reviewed_at: 2026-07-20 16:24 CST
"""
    return f"""
## {wp_id}

- title: 三阶段测试工作包
- status: {status}
- owner: {owner}
- handoff_to: {handoff}
- round: {round_number}
- max_rounds: {max_rounds}
- base_commit: abc123
{protocol_line}{baseline_line}- scope:
  - src/example.py
{implementation + self_review if implementation_before_self_review else self_review + implementation}{review}"""


class ThreePhaseHandoffTests(unittest.TestCase):
    """自审 / 实施交接 / Codex 独立审核 三阶段拆分与交接门禁。"""

    def parse(self, text: str):
        return HandoffParser("memory.md").parse_text(text).packages[0]

    def scheduler(self, runtime, digest: str = DEFAULT_SR_DIGEST, **kwargs) -> DryRunScheduler:
        return DryRunScheduler(
            "source.md", runtime,
            scope_hash_resolver=lambda package: ScopeHashResult(digest, [], []),
            **kwargs,
        )

    # 1. 自审 PASS 后允许原子交接
    def test_self_review_pass_allows_handoff(self):
        package = self.parse(three_phase_text())
        self.assertTrue(package.protocol_is_v2)
        self.assertEqual("PASS", package.self_review_verdict)
        self.assertTrue(package.handoff_gate_ok, package.handoff_gate_reason)
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(package)
        self.assertEqual("dry-run-candidate", result.outcome)
        self.assertEqual("start_codex_review", result.action)

    # 2. 自审 BLOCKED 时拒绝交接
    def test_self_review_blocked_rejects_handoff(self):
        package = self.parse(three_phase_text(verdict="BLOCKED"))
        self.assertFalse(package.handoff_gate_ok)
        self.assertIn("BLOCKED", package.handoff_gate_reason)
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(package)
        self.assertEqual("rejected-self-review", result.outcome)
        self.assertIsNone(result.action)
        self.assertIn("CLAUDE_WORKING", result.reason)

    # 3. 缺少自审记录时拒绝新格式工作包交接
    def test_missing_self_review_rejects_v2_handoff(self):
        package = self.parse(three_phase_text(with_self_review=False))
        self.assertTrue(package.protocol_is_v2)  # 由 handoff_protocol: v2 显式声明
        self.assertFalse(package.handoff_gate_ok)
        self.assertIn("缺少 Claude 交接前自审记录", package.handoff_gate_reason)
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(package)
        self.assertEqual("rejected-self-review", result.outcome)

    # 4. 自审哈希与交接哈希不一致时拒绝交接（manifest 绑定合法，但交接哈希漂移）
    def test_self_review_hash_drift_rejects_handoff(self):
        package = self.parse(three_phase_text(implementation_hash=HASH_B))
        self.assertFalse(package.handoff_gate_ok)
        self.assertIn("不一致", package.handoff_gate_reason)
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(package)
        self.assertEqual("rejected-self-review", result.outcome)

    # 5. 自审测试证据缺失时拒绝交接
    def test_self_review_without_test_evidence_rejects_handoff(self):
        package = self.parse(three_phase_text(tests_line="已经跑过，结果良好"))
        self.assertIsNone(package.self_review_test_count)
        self.assertFalse(package.handoff_gate_ok)
        self.assertIn("测试命令", package.handoff_gate_reason)

    def test_self_review_command_without_real_count_rejects_handoff(self):
        # 有命令、也有明确成功标记，但没有真实计数 → 仍然拒绝
        package = self.parse(three_phase_text(tests_line="`python -m unittest discover` → 全部通过"))
        self.assertIsNone(package.self_review_test_count)
        self.assertFalse(package.handoff_gate_ok)
        self.assertIn("真实测试计数", package.handoff_gate_reason)

    # 6. Claude 交接完成后才生成 Codex 审核候选
    def test_codex_candidate_only_after_handoff(self):
        package = self.parse(three_phase_text(status="READY_FOR_CODEX", owner="codex", handoff="codex"))
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(package)
        self.assertEqual("start_codex_review", result.action)

    # 7. Claude 自审（CLAUDE_WORKING）不会生成 Codex 审核候选
    def test_self_review_phase_does_not_create_codex_candidate(self):
        package = self.parse(three_phase_text(
            status="CLAUDE_WORKING", owner="claude", handoff="claude",
        ))
        # CLAUDE_WORKING 阶段当前哈希对比的是 scope_baseline_sha256
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory, digest=HASH_A).dispatch(package)
        self.assertNotEqual("start_codex_review", result.action)
        self.assertEqual("start_claude_implementation", result.action)

    # 8. 面板分别显示自审、交接和独立审核
    def test_dashboard_separates_three_phases(self):
        html = (
            Path(__file__).resolve().parents[1] / "tools" / "ai_handoff" / "dashboard.html"
        ).read_text(encoding="utf-8")
        self.assertIn("① Claude 交接前自审", html)
        self.assertIn("② Claude 实施交接", html)
        self.assertIn("③ Codex 独立审核结论", html)
        self.assertIn("交接门禁", html)
        # 自审不得被显示成 Codex 审核
        self.assertIn("Claude 交接前自审", html)
        self.assertIn("self_review_finished_at", html)

    # 9. 历史记录仍可解析，并明确标为 legacy
    def test_legacy_record_parses_and_is_marked(self):
        package = self.parse(package_text())  # 旧两段式：无自审段
        self.assertFalse(package.protocol_is_v2)
        self.assertTrue(package.self_review_is_legacy)
        self.assertEqual("历史格式：自审证据未独立结构化", package.self_review_note)
        # 历史格式不因缺少自审被拒绝调度
        self.assertTrue(package.handoff_gate_ok)
        # 且不得把旧正文里的测试/哈希冒充成结构化自审证据
        self.assertIsNone(package.self_review_verdict)
        self.assertIsNone(package.self_review_scope_sha256)

    def test_real_handoff_file_respects_protocol_generation_boundary(self):
        source = Path(__file__).resolve().parents[1] / "docs" / "AI_REVIEW_HANDOFF.md"
        result = HandoffParser(source).parse_file()
        self.assertTrue(result.ok, result.source_error)
        package_ids = {package.work_package_id for package in result.packages}
        self.assertTrue(LEGACY_WORK_PACKAGE_IDS.issubset(package_ids))
        for package in result.packages:
            if package.work_package_id in LEGACY_WORK_PACKAGE_IDS:
                self.assertFalse(package.protocol_is_v2, package.work_package_id)
                self.assertTrue(package.self_review_is_legacy, package.work_package_id)
                self.assertTrue(package.handoff_gate_ok, package.work_package_id)
            else:
                self.assertTrue(package.valid, package.errors)
                self.assertTrue(package.protocol_is_v2, package.work_package_id)
                self.assertFalse(package.self_review_is_legacy, package.work_package_id)

    # 10. 旧 Fable5 名称只读兼容，新记录统一为 Claude
    def test_legacy_fable5_names_still_readable_in_three_phase_world(self):
        package = self.parse(package_text(
            status="FABLE_WORKING", owner="fable5", handoff="fable5", impl_actor="Fable5",
        ))
        self.assertTrue(package.valid, package.errors)
        self.assertEqual("CLAUDE_WORKING", package.canonical_status)
        self.assertEqual("Claude", package.waiting_for)
        self.assertTrue(package.self_review_is_legacy)
        new_package = self.parse(three_phase_text())
        self.assertFalse(new_package.self_review_is_legacy)

    # 11. 重复事件不会生成两次自审或两次审核
    def test_duplicate_event_does_not_double_dispatch(self):
        package = self.parse(three_phase_text())
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory)
            first = scheduler.dispatch(package)
            second = scheduler.dispatch(package)
        self.assertEqual("dry-run-candidate", first.outcome)
        self.assertEqual("ignored-duplicate", second.outcome)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_self_review_phase_duplicate_is_also_idempotent(self):
        package = self.parse(three_phase_text(
            status="CLAUDE_WORKING", owner="claude", handoff="claude",
        ))
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.scheduler(directory, digest=HASH_A)
            first = scheduler.dispatch(package)
            second = scheduler.dispatch(package)
        self.assertEqual("dry-run-candidate", first.outcome)
        self.assertEqual("ignored-duplicate", second.outcome)

    # 结构化字段完整投影
    def test_structured_self_review_fields_are_projected(self):
        package = self.parse(three_phase_text())
        self.assertEqual("2026-07-20 16:11 CST", package.self_review_started_at)
        self.assertEqual("2026-07-20 16:16 CST", package.self_review_finished_at)
        self.assertEqual(DEFAULT_SR_DIGEST, package.self_review_scope_sha256)
        self.assertEqual(1108, package.self_review_test_count)
        self.assertEqual("无", package.self_review_first_failure)
        self.assertEqual("不适用", package.self_review_root_cause)
        self.assertEqual("与首次一致", package.self_review_rerun)
        self.assertEqual("真机未验证", package.self_review_unverified)
        self.assertEqual("是", package.self_review_ready)
        self.assertEqual([f"{HASH_A}  src/example.py"], package.self_review_manifest)

    def test_three_record_kinds_are_distinct(self):
        package = self.parse(three_phase_text(with_review=True))
        kinds = [record.kind for record in package.records]
        self.assertEqual(["self_review", "implementation", "review"], kinds)
        # Codex 审核的 verdict 不得被自审 verdict 覆盖
        self.assertEqual("PASS", package.self_review_verdict)
        self.assertEqual("CHANGES_REQUESTED", package.latest_review_verdict)

    def test_self_review_round_mismatch_rejects_handoff(self):
        package = self.parse(three_phase_text(round_number=2, self_review_round=1))
        self.assertFalse(package.handoff_gate_ok)
        self.assertIn("轮次", package.handoff_gate_reason)


class SelfReviewGateBypassTests(unittest.TestCase):
    """Codex CHANGES_REQUESTED 指出的可复现绕过路径，逐条反例锁定。"""

    def parse(self, text: str):
        return HandoffParser("memory.md").parse_text(text).packages[0]

    def scheduler(self, runtime, digest: str = DEFAULT_SR_DIGEST) -> DryRunScheduler:
        return DryRunScheduler(
            "source.md", runtime,
            scope_hash_resolver=lambda package: ScopeHashResult(digest, [], []),
        )

    def assert_rejected(self, package, fragment: str):
        self.assertFalse(package.handoff_gate_ok, "门禁本应拒绝但通过了")
        self.assertIn(fragment, package.handoff_gate_reason)
        with tempfile.TemporaryDirectory() as directory:
            result = self.scheduler(directory).dispatch(package)
        self.assertEqual("rejected-self-review", result.outcome)
        self.assertIsNone(result.action)
        return result

    # 反例 1：自审 round 缺失（标题无 Round N）
    def test_self_review_round_missing_is_rejected(self):
        package = self.parse(three_phase_text(self_review_round_heading=False))
        self.assertIsNone(package.self_review_round)
        self.assert_rejected(package, "缺少明确 Round 编号")

    # 反例 2：自审 round 不匹配
    def test_self_review_round_mismatch_is_rejected(self):
        package = self.parse(three_phase_text(round_number=2, self_review_round=1))
        self.assert_rejected(package, "自审轮次(1)与当前轮次(2)不一致")

    # 反例 3：实施交接 round 过期（当前2 + 自审2 + 交接1）
    def test_stale_implementation_round_is_rejected(self):
        package = self.parse(three_phase_text(
            round_number=2, self_review_round=2, implementation_round=1,
        ))
        self.assertEqual(2, package.self_review_round)
        self.assertEqual(1, package.implementation_round)
        self.assert_rejected(package, "实施交接轮次(1)与当前轮次(2)不一致")

    # 反例 4：实施交接早于自审（先交接后补自审）
    def test_implementation_before_self_review_is_rejected(self):
        package = self.parse(three_phase_text(implementation_before_self_review=True))
        self.assertFalse(package.implementation_after_self_review)
        self.assert_rejected(package, "记录顺序非法")

    # 反例 5：测试计数藏在"已知疑问"里，不得满足门禁
    def test_count_hidden_in_known_issues_is_not_evidence(self):
        package = self.parse(three_phase_text(
            tests_line="待补充",
            known_issue_line="上轮 `python -m unittest` → Ran **1108** tests, OK",
        ))
        self.assertIsNone(package.self_review_test_count)
        self.assert_rejected(package, "测试命令")

    # 反例 6：新工作包漏写 handoff_protocol: v2 → 拒绝，不得降级 legacy
    def test_new_package_without_v2_declaration_is_rejected_not_downgraded(self):
        package = self.parse(three_phase_text(wp_id="WP-20260721-009", protocol=None))
        self.assertFalse(package.is_legacy_package)
        self.assertFalse(package.self_review_is_legacy)
        self.assertEqual("v2-invalid", package.self_review_state)
        self.assert_rejected(package, "必须显式声明 handoff_protocol: v2")

    # 反例 7：显式 v2 但缺自审 → 不得标为"历史格式"
    def test_declared_v2_missing_self_review_is_not_labelled_legacy(self):
        package = self.parse(three_phase_text(with_self_review=False))
        self.assertFalse(package.self_review_is_legacy)
        self.assertEqual("v2-missing", package.self_review_state)
        self.assertIn("v2 自审缺失", package.self_review_note)
        self.assertNotIn("历史格式", package.self_review_note)
        self.assert_rejected(package, "缺少 Claude 交接前自审记录")

    # 其余门禁项
    def test_missing_manifest_is_rejected(self):
        self.assert_rejected(self.parse(three_phase_text(with_manifest=False)), "逐文件 SHA-256")

    def test_ready_flag_not_true_is_rejected(self):
        self.assert_rejected(self.parse(three_phase_text(ready_line="否")), "是否满足交接条件")

    def test_reversed_self_review_timestamps_are_rejected(self):
        package = self.parse(three_phase_text(
            started_at="2026-07-20 16:20 CST", finished_at="2026-07-20 16:05 CST",
        ))
        self.assert_rejected(package, "时间顺序非法")

    def test_missing_self_review_timestamps_are_rejected(self):
        self.assert_rejected(self.parse(three_phase_text(started_at="", finished_at="")), "self_review_started_at")

    def test_missing_implementation_hash_is_rejected(self):
        self.assert_rejected(
            self.parse(three_phase_text(with_implementation_hash=False)),
            "实施交接缺少 scope_sha256",
        )

    # 历史白名单包仍然放行（只读兼容不被本次收紧破坏）
    def test_legacy_allowlisted_package_still_passes(self):
        package = self.parse(package_text())  # 默认 wp_id 已是历史白名单 ID
        self.assertTrue(package.is_legacy_package)
        self.assertEqual("legacy", package.self_review_state)
        self.assertTrue(package.handoff_gate_ok)

    # ===== Codex 第二轮复审：三类新绕过 =====

    # 绕过 1：时间戳必须可解析为合法日期时间
    def test_unparseable_timestamp_is_rejected(self):
        package = self.parse(three_phase_text(started_at="not-a-time", finished_at="also-bad"))
        self.assert_rejected(package, "无法解析为合法时间")

    def test_impossible_calendar_date_is_rejected(self):
        # 2026-02-30 格式合规但日期不存在
        package = self.parse(three_phase_text(
            started_at="2026-02-30 10:00 CST", finished_at="2026-02-30 11:00 CST",
        ))
        self.assert_rejected(package, "无法解析为合法时间")

    def test_impossible_clock_time_is_rejected(self):
        package = self.parse(three_phase_text(
            started_at="2026-07-20 25:00 CST", finished_at="2026-07-20 26:00 CST",
        ))
        self.assert_rejected(package, "无法解析为合法时间")

    def test_finished_before_started_is_rejected(self):
        package = self.parse(three_phase_text(
            started_at="2026-07-20 16:20 CST", finished_at="2026-07-20 16:05 CST",
        ))
        self.assert_rejected(package, "时间顺序非法")

    def test_valid_timestamps_pass(self):
        from tools.ai_handoff.parser import parse_timestamp
        self.assertIsNotNone(parse_timestamp("2026-07-20 16:11 CST"))
        self.assertIsNotNone(parse_timestamp("2026-07-20T16:11:30+08:00"))
        self.assertIsNone(parse_timestamp("2026-02-30 10:00"))
        self.assertIsNone(parse_timestamp("not-a-time"))
        self.assertTrue(self.parse(three_phase_text()).handoff_gate_ok)

    # 绕过 2：manifest 内容必须校验
    def test_manifest_with_fake_sha_is_rejected(self):
        package = self.parse(three_phase_text(manifest_lines=["zzzz  src/example.py"]))
        self.assert_rejected(package, "条目格式非法")

    def test_manifest_with_wrong_path_is_rejected(self):
        package = self.parse(three_phase_text(manifest_lines=[f"{HASH_A}  src/WRONG.py"]))
        self.assert_rejected(package, "缺少 scope 文件")

    def test_manifest_missing_scope_entry_is_rejected(self):
        text = three_phase_text(manifest_lines=[f"{HASH_A}  src/example.py"]).replace(
            "- scope:\n  - src/example.py",
            "- scope:\n  - src/example.py\n  - src/second.py",
        )
        package = self.parse(text)
        self.assert_rejected(package, "缺少 scope 文件")

    def test_manifest_with_duplicate_entry_is_rejected(self):
        package = self.parse(three_phase_text(manifest_lines=[
            f"{HASH_A}  src/example.py", f"{HASH_B}  src/example.py",
        ]))
        self.assert_rejected(package, "重复路径")

    def test_manifest_with_extra_unrelated_path_is_rejected(self):
        package = self.parse(three_phase_text(manifest_lines=[
            f"{HASH_A}  src/example.py", f"{HASH_B}  src/extra.py",
        ]))
        self.assert_rejected(package, "scope 之外的无关路径")

    def test_manifest_garbage_entry_is_rejected(self):
        package = self.parse(three_phase_text(manifest_lines=["hello world"]))
        self.assert_rejected(package, "条目格式非法")

    # 绕过 3：测试结果必须明确成功
    def test_equal_counts_with_failed_marker_is_rejected(self):
        package = self.parse(three_phase_text(
            tests_line="`python -m unittest` → 1108/1108 FAILED",
        ))
        self.assert_rejected(package, "失败标记")

    def test_equal_counts_with_error_marker_is_rejected(self):
        package = self.parse(three_phase_text(
            tests_line="`python -m unittest` → 1108/1108 ERROR",
        ))
        self.assert_rejected(package, "失败标记")

    def test_equal_counts_without_success_marker_is_rejected(self):
        package = self.parse(three_phase_text(
            tests_line="`python -m unittest` → 1108/1108",
        ))
        self.assert_rejected(package, "缺少明确成功标记")

    def test_chinese_failure_marker_is_rejected(self):
        package = self.parse(three_phase_text(
            tests_line="`python -m unittest` → Ran **1108** tests，其中 3 项失败",
        ))
        self.assert_rejected(package, "失败标记")

    def test_real_success_formats_pass(self):
        for line in (
            "`python -m unittest` → Ran **1108** tests, OK",
            "`python -m unittest discover -s tests -t .` → 1108/1108 通过",
            "`python -m unittest` → 1108/1108 PASSED",
        ):
            package = self.parse(three_phase_text(tests_line=line))
            self.assertTrue(package.handoff_gate_ok, f"{line} -> {package.handoff_gate_reason}")

    # ===== Codex 第三轮复审：P1 信任链 =====

    # P1-1 时区感知：跨时区实际倒序必须拒绝
    def test_cross_timezone_reversal_is_rejected(self):
        # 10:00+00:00 = 10:00Z；16:00+08:00 = 08:00Z → 结束实际早于开始
        package = self.parse(three_phase_text(
            started_at="2026-07-20T10:00:00+00:00",
            finished_at="2026-07-20T16:00:00+08:00",
        ))
        self.assert_rejected(package, "时间顺序非法")

    def test_cross_timezone_forward_is_accepted(self):
        # 10:00+08:00 = 02:00Z；05:00+00:00 = 05:00Z → 顺序正确
        package = self.parse(three_phase_text(
            started_at="2026-07-20T10:00:00+08:00",
            finished_at="2026-07-20T05:00:00+00:00",
        ))
        self.assertTrue(package.handoff_gate_ok, package.handoff_gate_reason)

    def test_trailing_nonsense_after_valid_time_is_rejected(self):
        package = self.parse(three_phase_text(
            started_at="2026-07-20 16:11 nonsense", finished_at="2026-07-20 16:16 CST",
        ))
        self.assert_rejected(package, "无法解析为合法时间")

    def test_leading_garbage_before_valid_time_is_rejected(self):
        package = self.parse(three_phase_text(
            started_at="xx2026-07-20 16:11", finished_at="2026-07-20 16:16",
        ))
        self.assert_rejected(package, "无法解析为合法时间")

    def test_unknown_timezone_is_rejected(self):
        package = self.parse(three_phase_text(
            started_at="2026-07-20 16:11 XYZ", finished_at="2026-07-20 16:16 XYZ",
        ))
        self.assert_rejected(package, "无法解析为合法时间")

    def test_mixed_aware_and_naive_is_rejected(self):
        package = self.parse(three_phase_text(
            started_at="2026-07-20 16:11", finished_at="2026-07-20T16:16:00+08:00",
        ))
        self.assert_rejected(package, "时区标注不一致")

    def test_timestamp_parser_unit_behaviour(self):
        from tools.ai_handoff.parser import PROJECT_TZ, parse_timestamp, to_utc
        self.assertIsNone(parse_timestamp("2026-07-20 16:11 nonsense"))
        self.assertIsNone(parse_timestamp("xx2026-07-20 16:11"))
        self.assertIsNone(parse_timestamp("2026-07-20 16:11 XYZ"))
        self.assertIsNone(parse_timestamp("2026-02-30 10:00"))
        self.assertIsNone(parse_timestamp("2026-07-20 25:00"))
        # CST 明确解释为 Asia/Shanghai (+08:00)
        self.assertEqual(PROJECT_TZ, parse_timestamp("2026-07-20 16:11 CST").tzinfo)
        self.assertEqual(
            to_utc(parse_timestamp("2026-07-20T16:00:00+08:00")),
            to_utc(parse_timestamp("2026-07-20T08:00:00Z")),
        )

    # P1-2 manifest 与 scope 证据的密码学绑定
    def test_manifest_with_forged_but_wellformed_sha_is_rejected(self):
        # 路径正确、格式正确，但文件 SHA 全部伪造 → 聚合哈希无法重建
        package = self.parse(three_phase_text(
            manifest_lines=[f"{HASH_B}  src/example.py"],
            self_review_hash=DEFAULT_SR_DIGEST,
        ))
        self.assert_rejected(package, "无法由清单重建")

    def test_manifest_correct_but_declared_digest_mismatched_is_rejected(self):
        package = self.parse(three_phase_text(self_review_hash=HASH_B))
        self.assert_rejected(package, "无法由清单重建")

    def test_manifest_order_must_match_scope_declaration_order(self):
        entry_a = f"{HASH_A}  src/a.py"
        entry_b = f"{HASH_B}  src/b.py"
        text = three_phase_text(
            manifest_lines=[entry_b, entry_a],  # 顺序与 scope 声明相反
            self_review_hash=manifest_digest([entry_b, entry_a]),
        ).replace("- scope:\n  - src/example.py", "- scope:\n  - src/a.py\n  - src/b.py")
        self.assert_rejected(self.parse(text), "顺序与 scope 声明顺序不一致")

    def test_multi_file_manifest_in_declared_order_passes(self):
        entry_a = f"{HASH_A}  src/a.py"
        entry_b = f"{HASH_B}  src/b.py"
        digest = manifest_digest([entry_a, entry_b])
        text = three_phase_text(
            manifest_lines=[entry_a, entry_b], self_review_hash=digest,
        ).replace("- scope:\n  - src/example.py", "- scope:\n  - src/a.py\n  - src/b.py")
        package = self.parse(text)
        self.assertTrue(package.handoff_gate_ok, package.handoff_gate_reason)

    def test_stale_manifest_rejected_when_current_files_changed(self):
        # 调度器重算的当前 manifest 与自审记录不一致 → 逐项比对必须拒绝
        package = self.parse(three_phase_text())
        self.assertTrue(package.handoff_gate_ok)
        current = [f"{HASH_B}  src/example.py\n"]  # 文件内容已变化
        from tools.ai_handoff.parser import self_review_gate
        reason = self_review_gate(package, current_manifest=current)
        self.assertIsNotNone(reason)
        self.assertIn("与当前实际文件不一致", reason)

    def test_exact_real_manifest_passes_end_to_end(self):
        package = self.parse(three_phase_text())
        current = [f"{HASH_A}  src/example.py\n"]
        from tools.ai_handoff.parser import self_review_gate
        self.assertIsNone(self_review_gate(package, current_manifest=current))
        with tempfile.TemporaryDirectory() as directory:
            scheduler = DryRunScheduler(
                "source.md", directory,
                scope_hash_resolver=lambda p: ScopeHashResult(
                    DEFAULT_SR_DIGEST, [f"{HASH_A}  src/example.py\n"], []
                ),
            )
            result = scheduler.dispatch(package)
        self.assertEqual("dry-run-candidate", result.outcome)
        self.assertEqual("start_codex_review", result.action)

    def test_legacy_id_declaring_v2_is_held_to_v2_rules(self):
        # 历史 ID 一旦显式声明 v2，就不再享受 legacy 豁免
        package = self.parse(three_phase_text(
            wp_id="WP-20260714-003", protocol="v2", with_self_review=False,
        ))
        self.assertFalse(package.handoff_gate_ok)
        self.assertEqual("v2-missing", package.self_review_state)


if __name__ == "__main__":
    unittest.main()
