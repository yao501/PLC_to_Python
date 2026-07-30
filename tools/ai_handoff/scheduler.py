"""Dry-run 事件调度骨架：默认永不启动外部进程。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlparse

from .parser import (
    HandoffParser,
    STATUS_MAP,
    WorkPackage,
    canonical_actor,
    canonical_status,
    self_review_gate,
)


# TRIGGER_MAP 的键使用**规范化后的** owner/handoff（实施方=claude）。
# 历史 owner=fable5 会在查表前规范化为 claude，因此历史与新记录都能命中。
TRIGGER_MAP = {
    ("CLAUDE_WORKING", "claude", "claude"): ("start_claude_implementation", "启动 Claude 首轮实施"),
    ("FABLE_WORKING", "claude", "claude"): ("start_claude_implementation", "启动 Claude 首轮实施"),
    ("READY_FOR_CODEX", "codex", "codex"): ("start_codex_review", "启动 Codex 审核"),
    ("CHANGES_REQUESTED", "claude", "claude"): ("start_claude_rework", "启动 Claude 返修"),
    ("APPROVED", "user", "user"): ("notify_user_approved", "通知用户：工作包已通过"),
    ("BLOCKED", "user", "user"): ("notify_user_blocked", "通知用户：工作包已阻塞"),
}


@dataclass
class DispatchResult:
    outcome: str
    dry_run: bool
    work_package_id: str | None
    round: int | None
    action: str | None = None
    action_label: str | None = None
    idempotency_key: str | None = None
    reason: str | None = None
    external_process_started: bool = False
    notification_candidate: dict[str, str | bool] | None = None
    scope_current_sha256: str | None = None
    scope_expected_sha256: str | None = None
    scope_hash_basis: str | None = None
    adapter: str | None = None
    adapter_status: str | None = None
    adapter_reason: str | None = None
    execution_plan: dict | None = None
    lifecycle: dict | None = None
    failure_alert: dict[str, str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScopeHashResult:
    digest: str | None
    manifest: list[str]
    errors: list[str]


@dataclass(frozen=True)
class ExecutionPlan:
    """第二阶段可执行契约的只读预览；本类本身不启动进程。"""

    actor: str
    action: str
    command: list[str]
    cwd: str
    timeout_seconds: float
    permission_summary: str
    environment: dict[str, str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProcessRunResult:
    """一次受控子进程演练的脱敏结果；不包含命令正文或完整环境变量。"""

    outcome: str
    returncode: int | None
    timed_out: bool
    duration_seconds: float
    process_id: int | None
    stdout_tail: str
    stderr_tail: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class SafeProcessRunner:
    """无 shell、进程组隔离、超时必清理的第二阶段故障注入运行器。"""

    _REDACTIONS = (
        re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
        re.compile(
            r"(?i)([\"']?(?:access_token|refresh_token|oauth_code|api_key)[\"']?"
            r"\s*[:=]\s*[\"']?)[^\"',\s]+"
        ),
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]+\b"),
    )

    def __init__(self, *, output_tail_bytes: int = 64 * 1024, terminate_grace_seconds: float = 1.0):
        if output_tail_bytes <= 0:
            raise ValueError("output_tail_bytes 必须大于 0")
        if terminate_grace_seconds < 0:
            raise ValueError("terminate_grace_seconds 不得为负数")
        self.output_tail_bytes = output_tail_bytes
        self.terminate_grace_seconds = terminate_grace_seconds

    def run(
        self,
        plan: ExecutionPlan,
        *,
        on_started: Callable[[int], None] | None = None,
    ) -> ProcessRunResult:
        if not plan.command:
            raise ValueError("执行计划命令为空")
        if plan.timeout_seconds <= 0:
            raise ValueError("执行超时必须大于 0")
        cwd = Path(plan.cwd)
        if not cwd.is_dir():
            raise ValueError(f"执行目录不存在或不是目录: {cwd}")

        environment = os.environ.copy()
        environment.update(plan.environment)
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        launch_error: str | None = None
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(
                    plan.command,
                    cwd=str(cwd),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    start_new_session=True,
                )
                if on_started is not None:
                    try:
                        on_started(process.pid)
                    except Exception as exc:
                        self._terminate_process_group(process)
                        launch_error = f"启动登记失败，已终止子进程: {type(exc).__name__}: {exc}"
                if launch_error is None:
                    try:
                        process.wait(timeout=plan.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        self._terminate_process_group(process)
            except OSError as exc:
                launch_error = f"{type(exc).__name__}: {exc}"

            duration = time.monotonic() - started
            stdout_tail = self._redact(self._read_tail(stdout))
            stderr_tail = self._redact(self._read_tail(stderr))

        if launch_error is not None:
            return ProcessRunResult(
                outcome="launch-failed", returncode=None, timed_out=False,
                duration_seconds=duration, process_id=None,
                stdout_tail=stdout_tail, stderr_tail=stderr_tail, error=launch_error,
            )
        assert process is not None
        if timed_out:
            outcome = "timed-out"
        elif process.returncode == 0:
            outcome = "completed"
        else:
            outcome = "failed"
        return ProcessRunResult(
            outcome=outcome,
            returncode=process.returncode,
            timed_out=timed_out,
            duration_seconds=duration,
            process_id=process.pid,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )

    def _terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self.terminate_grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()

    def _read_tail(self, stream: object) -> str:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - self.output_tail_bytes))
        return stream.read().decode("utf-8", errors="replace")

    def _redact(self, text: str) -> str:
        redacted = text
        for pattern in self._REDACTIONS:
            if pattern.groups:
                redacted = pattern.sub(r"\1[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        return redacted


class AsyncExecutionCoordinator:
    """跨线程/跨进程串行化真实 AI 执行，并持久化可恢复生命周期。"""

    TERMINAL_OUTCOMES = {
        "completed", "failed", "timed-out", "launch-failed", "cancelled",
        "postcondition-failed",
    }
    FAILURE_OUTCOMES = {
        "failed", "timed-out", "launch-failed", "cancelled",
        "blocked-corrupt-state", "blocked-orphan-process", "postcondition-failed",
    }

    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        runner: SafeProcessRunner | None = None,
        recovery_grace_seconds: float = 30.0,
        on_update: Callable[[], None] | None = None,
    ):
        if recovery_grace_seconds < 0:
            raise ValueError("recovery_grace_seconds 不得为负数")
        self.runtime_dir = Path(runtime_dir)
        self.runner = runner or SafeProcessRunner()
        self.recovery_grace_seconds = recovery_grace_seconds
        self._on_update = on_update
        self._thread_lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._shutting_down = False

    @property
    def lock_path(self) -> Path:
        return self.runtime_dir / "execution.lock"

    @property
    def lease_path(self) -> Path:
        return self.runtime_dir / "execution_lease.json"

    @property
    def history_path(self) -> Path:
        return self.runtime_dir / "executions.jsonl"

    @property
    def block_path(self) -> Path:
        return self.runtime_dir / "execution_block.json"

    def set_on_update(self, callback: Callable[[], None] | None) -> None:
        self._on_update = callback

    def start(
        self,
        *,
        idempotency_key: str,
        plan: ExecutionPlan,
        work_package_id: str,
        round_number: int,
        completion_validator: Callable[[], tuple[bool, str]] | None = None,
    ) -> dict:
        """登记全局租约并异步启动；任何不可信持久状态都失败关闭。"""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if self._shutting_down:
                    return self._result("blocked-shutting-down", "调度器正在停止，拒绝新任务")
                recovery = self._reconcile_locked()
                if recovery is not None:
                    return recovery
                try:
                    records = self._read_history_locked()
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    return self._persistent_block_locked(
                        "blocked-corrupt-state", f"执行历史不可信，已安全停止: {exc}",
                        idempotency_key=idempotency_key,
                    )
                last = self._latest_by_key(records).get(idempotency_key)
                if last and last.get("outcome") in self.TERMINAL_OUTCOMES | {"scheduled", "running"}:
                    outcome = "ignored-active" if last.get("outcome") in {"scheduled", "running"} else "ignored-terminal"
                    return self._result(
                        outcome,
                        "同一幂等键已在运行" if outcome == "ignored-active" else "同一幂等键已有终态，拒绝自动重复执行",
                        active=last,
                    )
                lease = {
                    "schema_version": 1,
                    "idempotency_key": idempotency_key,
                    "work_package_id": work_package_id,
                    "round": round_number,
                    "actor": plan.actor,
                    "action": plan.action,
                    "owner_pid": os.getpid(),
                    "child_pid": None,
                    "state": "scheduled",
                    "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "deadline_epoch": now + plan.timeout_seconds + self.recovery_grace_seconds,
                }
                self._atomic_write_json(self.lease_path, lease)
                self._append_history_locked({**lease, "outcome": "scheduled"})
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        thread = threading.Thread(
            target=self._worker,
            args=(idempotency_key, plan, completion_validator),
            name=f"ai-handoff-{plan.actor}-{round_number}",
            daemon=True,
        )
        with self._thread_lock:
            self._threads[idempotency_key] = thread
        try:
            thread.start()
        except RuntimeError as exc:
            self._finish_without_worker(idempotency_key, f"异步线程启动失败: {exc}")
            return self._result("launch-failed", f"异步线程启动失败: {exc}", alert=self._latest_alert())
        self._notify_update()
        return self._result("scheduled", "已取得全局租约，外部进程将在后台异步启动", active=lease)

    def snapshot(self) -> dict:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                recovery = self._reconcile_locked()
                lease, lease_error = self._read_lease_locked()
                try:
                    records = self._read_history_locked()
                    history_error = None
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    records = []
                    history_error = str(exc)
                alert = self._last_failure(records)
                if recovery and recovery.get("alert"):
                    alert = recovery["alert"]
                if (lease_error or history_error) and alert is None:
                    alert = {
                        "severity": "error",
                        "code": "corrupt-runtime-state",
                        "message": lease_error or history_error or "运行状态损坏",
                    }
                return {
                    "enabled": True,
                    "active": lease,
                    "last_event": records[-1] if records else None,
                    "failure_alert": alert,
                    "history_path": str(self.history_path),
                }
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def shutdown(self, *, wait_timeout: float = 5.0) -> None:
        """停止面板时终止本进程拥有的子进程，避免留下隐形 AI。"""
        with self._thread_lock:
            self._shutting_down = True
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        owned_child_pid: int | None = None
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                lease, _ = self._read_lease_locked()
                if lease and lease.get("owner_pid") == os.getpid():
                    child_pid = lease.get("child_pid")
                    if isinstance(child_pid, int) and child_pid > 1 and self._pid_alive(child_pid):
                        owned_child_pid = child_pid
                        try:
                            os.killpg(child_pid, signal.SIGTERM)
                        except (ProcessLookupError, PermissionError):
                            pass
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        if owned_child_pid is not None:
            grace = max(0.0, float(getattr(self.runner, "terminate_grace_seconds", 1.0)))
            grace_deadline = time.monotonic() + grace
            while self._pid_alive(owned_child_pid) and time.monotonic() < grace_deadline:
                time.sleep(0.02)
            if self._pid_alive(owned_child_pid):
                # 再次核对租约，避免对子进程已退出后复用的无关 PID 发送信号。
                with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    try:
                        lease, _ = self._read_lease_locked()
                        still_owned = bool(
                            lease
                            and lease.get("owner_pid") == os.getpid()
                            and lease.get("child_pid") == owned_child_pid
                        )
                        if still_owned:
                            try:
                                os.killpg(owned_child_pid, signal.SIGKILL)
                            except (ProcessLookupError, PermissionError):
                                pass
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + max(0.0, wait_timeout)
        for thread in list(self._threads.values()):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _worker(
        self,
        key: str,
        plan: ExecutionPlan,
        completion_validator: Callable[[], tuple[bool, str]] | None,
    ) -> None:
        try:
            result = self.runner.run(plan, on_started=lambda pid: self._mark_started(key, pid))
            if result.outcome == "completed" and completion_validator is not None:
                try:
                    valid, reason = completion_validator()
                except Exception as exc:
                    valid = False
                    reason = f"执行后置条件校验异常: {type(exc).__name__}: {exc}"
                if not valid:
                    result = ProcessRunResult(
                        outcome="postcondition-failed",
                        returncode=result.returncode,
                        timed_out=result.timed_out,
                        duration_seconds=result.duration_seconds,
                        process_id=result.process_id,
                        stdout_tail=result.stdout_tail,
                        stderr_tail=result.stderr_tail,
                        error=reason or "外部进程退出码为 0，但协议后置条件未成立",
                    )
            self._finish(key, result)
        except Exception as exc:
            fallback = ProcessRunResult(
                outcome="launch-failed", returncode=None, timed_out=False,
                duration_seconds=0.0, process_id=None, stdout_tail="", stderr_tail="",
                error=f"协调器未捕获异常: {type(exc).__name__}: {exc}",
            )
            self._finish(key, fallback)
        finally:
            with self._thread_lock:
                self._threads.pop(key, None)
            self._notify_update()

    def _mark_started(self, key: str, child_pid: int) -> None:
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if self._shutting_down:
                    raise RuntimeError("调度器正在停止")
                lease, error = self._read_lease_locked()
                if error or not lease or lease.get("idempotency_key") != key:
                    raise RuntimeError(error or "启动租约缺失或已被替换")
                lease.update({
                    "child_pid": child_pid,
                    "state": "running",
                    "launched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                })
                self._atomic_write_json(self.lease_path, lease)
                self._append_history_locked({**lease, "outcome": "running"})
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        self._notify_update()

    def _finish(self, key: str, result: ProcessRunResult) -> None:
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                lease, _ = self._read_lease_locked()
                base = lease if lease and lease.get("idempotency_key") == key else {"idempotency_key": key}
                record = {
                    **base,
                    **result.to_dict(),
                    "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                if self._shutting_down and result.outcome == "failed" and result.returncode in {-15, -9}:
                    record["outcome"] = "cancelled"
                    record["error"] = "面板停止时已终止外部进程"
                self._append_history_locked(record)
                if lease and lease.get("idempotency_key") == key:
                    self.lease_path.unlink(missing_ok=True)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _finish_without_worker(self, key: str, reason: str) -> None:
        self._finish(key, ProcessRunResult(
            outcome="launch-failed", returncode=None, timed_out=False,
            duration_seconds=0.0, process_id=None, stdout_tail="", stderr_tail="", error=reason,
        ))

    def _reconcile_locked(self) -> dict | None:
        blocked = self._read_block_locked()
        if blocked is not None:
            if blocked.get("outcome") in {"blocked-orphan-process", "blocked-corrupt-state"}:
                active = blocked.get("active")
                if isinstance(active, dict):
                    owner_pid = active.get("owner_pid")
                    child_pid = active.get("child_pid")
                    owner_alive = isinstance(owner_pid, int) and self._pid_alive(owner_pid)
                    child_alive = isinstance(child_pid, int) and self._pid_alive(child_pid)
                    if not owner_alive and not child_alive and blocked.get("recoverable", False):
                        self._append_history_locked({
                            **active,
                            "outcome": "recovered-stale",
                            "recovered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "reason": "阻塞记录关联进程均已消失，已自动恢复",
                        })
                        self.block_path.unlink(missing_ok=True)
                        self.lease_path.unlink(missing_ok=True)
                    else:
                        return self._result(
                            str(blocked.get("outcome")),
                            str(blocked.get("reason")),
                            active=active,
                            alert=blocked.get("alert"),
                        )
                else:
                    return self._result(
                        str(blocked.get("outcome")),
                        str(blocked.get("reason")),
                        alert=blocked.get("alert"),
                    )
        lease, error = self._read_lease_locked()
        if error:
            return self._persistent_block_locked("blocked-corrupt-state", error)
        if lease is None:
            return self._recover_missing_lease_locked()
        owner_pid = lease.get("owner_pid")
        child_pid = lease.get("child_pid")
        if isinstance(owner_pid, int) and self._pid_alive(owner_pid):
            return self._result("ignored-global-running", "另一个执行生命周期仍持有全局租约", active=lease)
        if isinstance(child_pid, int) and child_pid > 1 and self._pid_alive(child_pid):
            return self._persistent_block_locked(
                "blocked-orphan-process",
                f"宿主进程已退出，但外部进程 PID {child_pid} 仍存活；为防止并行写入已安全阻塞",
                idempotency_key=lease.get("idempotency_key"), active=lease, recoverable=True,
            )
        self._append_history_locked({
            **lease,
            "outcome": "recovered-stale",
            "recovered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": "宿主与外部进程均已消失，已回收陈旧租约",
        })
        self.lease_path.unlink(missing_ok=True)
        return None

    def _recover_missing_lease_locked(self) -> dict | None:
        try:
            records = self._read_history_locked()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._persistent_block_locked("blocked-corrupt-state", f"执行历史不可信: {exc}")
        latest = self._latest_by_key(records)
        for key, record in latest.items():
            if record.get("outcome") not in {"scheduled", "running"}:
                continue
            owner_pid = record.get("owner_pid")
            child_pid = record.get("child_pid")
            if isinstance(owner_pid, int) and self._pid_alive(owner_pid):
                return self._persistent_block_locked(
                    "blocked-corrupt-state",
                    "发现活跃执行记录但全局租约缺失；为避免重复启动已安全阻塞",
                    idempotency_key=key, active=record, recoverable=True,
                )
            if isinstance(child_pid, int) and child_pid > 1 and self._pid_alive(child_pid):
                return self._persistent_block_locked(
                    "blocked-orphan-process",
                    f"租约缺失且外部进程 PID {child_pid} 仍存活；已安全阻塞",
                    idempotency_key=key, active=record, recoverable=True,
                )
            self._append_history_locked({
                **record,
                "outcome": "recovered-stale",
                "recovered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "reason": "运行记录无租约且进程已消失，已恢复",
            })
        return None

    def _persistent_block_locked(
        self,
        outcome: str,
        reason: str,
        *,
        idempotency_key: str | None = None,
        active: dict | None = None,
        recoverable: bool = False,
    ) -> dict:
        alert = {"severity": "error", "code": outcome, "message": reason}
        record = {
            "outcome": outcome,
            "idempotency_key": idempotency_key,
            "reason": reason,
            "alert": alert,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        block = {**record, "active": active, "recoverable": recoverable}
        try:
            self._atomic_write_json(self.block_path, block)
        except OSError:
            pass
        try:
            existing = self._read_history_locked()
        except Exception:
            existing = []
        last = existing[-1] if existing else None
        if not last or (last.get("outcome"), last.get("idempotency_key"), last.get("reason")) != (
            outcome, idempotency_key, reason,
        ):
            try:
                self._append_history_locked(record)
            except OSError:
                pass
        return self._result(outcome, reason, active=active, alert=alert)

    def _read_block_locked(self) -> dict | None:
        if not self.block_path.exists():
            return None
        try:
            value = json.loads(self.block_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "outcome": "blocked-corrupt-state",
                "reason": f"执行阻塞记录损坏: {exc}",
                "alert": {
                    "severity": "error",
                    "code": "blocked-corrupt-state",
                    "message": f"执行阻塞记录损坏: {exc}",
                },
                "recoverable": False,
            }
        return value if isinstance(value, dict) else None

    def _read_lease_locked(self) -> tuple[dict | None, str | None]:
        if not self.lease_path.exists():
            return None, None
        try:
            value = json.loads(self.lease_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"执行租约损坏，已安全停止: {exc}"
        required = {"idempotency_key", "owner_pid", "state", "deadline_epoch"}
        if not isinstance(value, dict) or not required.issubset(value):
            return None, "执行租约字段不完整，已安全停止"
        return value, None

    def _read_history_locked(self) -> list[dict]:
        if not self.history_path.exists():
            return []
        records: list[dict] = []
        for number, line in enumerate(self.history_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"executions.jsonl 第 {number} 行损坏") from exc
            if not isinstance(record, dict):
                raise ValueError(f"executions.jsonl 第 {number} 行不是对象")
            records.append(record)
        return records

    def _append_history_locked(self, record: dict) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        value = dict(record)
        value.setdefault("recorded_at", datetime.now().astimezone().isoformat(timespec="seconds"))
        with self.history_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _latest_by_key(records: list[dict]) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for record in records:
            key = record.get("idempotency_key")
            if isinstance(key, str) and key:
                latest[key] = record
        return latest

    def _last_failure(self, records: list[dict]) -> dict[str, str] | None:
        for record in reversed(records):
            outcome = record.get("outcome")
            if outcome in {"completed", "recovered-stale", "retry-authorized"}:
                return None
            if outcome in self.FAILURE_OUTCOMES:
                message = record.get("error") or record.get("reason") or "外部执行失败"
                return {
                    "severity": "error",
                    "code": str(outcome),
                    "message": str(message),
                    "recorded_at": str(record.get("finished_at") or record.get("recorded_at") or ""),
                    "idempotency_key": str(record.get("idempotency_key") or ""),
                }
        return None

    def authorize_retry(self, idempotency_key: str) -> dict:
        """用户明确处置失败后，允许同一幂等键重试一次；绝不清除成功终态。"""
        if not idempotency_key.strip():
            raise ValueError("幂等键不能为空")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                recovery = self._reconcile_locked()
                if recovery is not None and recovery.get("active"):
                    raise RuntimeError(str(recovery.get("reason")))
                records = self._read_history_locked()
                last = self._latest_by_key(records).get(idempotency_key)
                if not last or last.get("outcome") not in self.FAILURE_OUTCOMES:
                    raise ValueError("该幂等键没有可重试的失败终态")
                record = {
                    "idempotency_key": idempotency_key,
                    "outcome": "retry-authorized",
                    "reason": "用户已确认处置失败，允许同一动作重试一次",
                }
                self._append_history_locked(record)
                if self.block_path.exists():
                    blocked = self._read_block_locked()
                    if blocked and blocked.get("idempotency_key") == idempotency_key:
                        self.block_path.unlink(missing_ok=True)
                return record
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _latest_alert(self) -> dict[str, str] | None:
        try:
            return self.snapshot().get("failure_alert")
        except Exception:
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _result(
        outcome: str,
        reason: str,
        *,
        active: dict | None = None,
        alert: dict[str, str] | None = None,
    ) -> dict:
        return {"outcome": outcome, "reason": reason, "active": active, "alert": alert}

    def _notify_update(self) -> None:
        callback = self._on_update
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # UI 刷新失败不能改变执行器终态；下一次文件事件会重建视图。
            pass


def calculate_scope_sha256(package: WorkPackage, project_root: str | Path) -> ScopeHashResult:
    """按交接协议的 scope 顺序重算聚合 SHA-256，不写任何文件。"""
    root = Path(project_root).resolve()
    manifest: list[str] = []
    errors: list[str] = []
    if not package.scope:
        return ScopeHashResult(None, manifest, ["scope 文件列表为空"])
    for declared in package.scope:
        relative = Path(declared)
        if relative.is_absolute():
            errors.append(f"scope 路径必须是项目内相对路径: {declared}")
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            errors.append(f"scope 路径越出项目根目录: {declared}")
            continue
        try:
            if not path.exists() and canonical_status(package.status) == "CLAUDE_WORKING":
                manifest.append(f"ABSENT  {declared}\n")
                continue
            if not path.is_file():
                errors.append(f"scope 文件缺失或不是普通文件: {declared}")
                continue
            file_hash = hashlib.sha256()
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                while chunk := stream.read(1024 * 1024):
                    file_hash.update(chunk)
                after = os.fstat(stream.fileno())
            current = path.stat()
            before_signature = (before.st_ino, before.st_size, before.st_mtime_ns)
            after_signature = (after.st_ino, after.st_size, after.st_mtime_ns)
            current_signature = (current.st_ino, current.st_size, current.st_mtime_ns)
            if before_signature != after_signature or after_signature != current_signature:
                errors.append(f"scope 文件在哈希计算期间发生变化: {declared}")
                continue
            manifest.append(f"{file_hash.hexdigest()}  {declared}\n")
        except OSError as exc:
            errors.append(f"scope 文件不可读: {declared}: {exc}")
    if errors:
        return ScopeHashResult(None, manifest, errors)
    aggregate = hashlib.sha256("".join(manifest).encode("utf-8")).hexdigest()
    return ScopeHashResult(aggregate, manifest, [])


def _resolved_executable(explicit: str | Path | None, name: str, fallback: Path) -> str | None:
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    discovered = shutil.which(name)
    if discovered:
        return discovered
    return str(fallback) if fallback.is_file() and os.access(fallback, os.X_OK) else None


class CodexCommandAdapter:
    """Codex 非交互命令契约；默认禁用，只有显式生产开关才能启用。"""

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        project_root: str | Path | None = None,
        timeout_seconds: int = 1800,
        enabled: bool = False,
    ):
        self.executable = _resolved_executable(
            executable,
            "codex",
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        )
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.timeout_seconds = timeout_seconds
        self.available = self.executable is not None
        self.enabled = bool(enabled and self.available)
        self.reason = (
            ("Codex CLI 生产事件入口已显式启用" if self.enabled else
             "Codex CLI 已发现并通过隔离演练；生产执行生命周期安全门尚未打开")
            if self.available else "未发现可执行的 Codex CLI"
        )

    def command_for(self, package: WorkPackage, action: str = "start_codex_review") -> ExecutionPlan:
        if not self.executable:
            raise RuntimeError(self.reason)
        prompt = (
            f"审核工作包 {package.work_package_id}。先完整读取 CODEX_GUIDE.md 与 "
            "docs/AI_REVIEW_HANDOFF.md，严格核验五字段、round/max_rounds、scope 声明与独立 "
            "SHA-256 证据。只按协议执行 Codex 审核和写回审核结论；禁止 Git 暂存、提交、推送、"
            "建 PR、合并或修改 scope 外文件。出现授权边界或证据异常时安全停止并报告。"
        )
        return ExecutionPlan(
            actor="codex",
            action=action,
            command=[
                self.executable, "exec", "-C", str(self.project_root),
                "--sandbox", "workspace-write", "--ephemeral", prompt,
            ],
            cwd=str(self.project_root),
            timeout_seconds=self.timeout_seconds,
            permission_summary="workspace-write；Git 元数据不授权；异常时失败关闭",
            environment={},
        )

    def execute(self, package: WorkPackage) -> None:
        raise RuntimeError("dry-run 安全锁禁止执行 Codex 命令")


# ---- Claude 实施方长期稳定 prompt 片段（首轮实施与返修共用，避免两条路径漂移）----
# 任一修改必须同时与 docs/CLAUDE_IMPLEMENTATION_RUNBOOK.md 和 command_for() 实际下发的
# --allowedTools / --disallowedTools 保持一致；关键失败关闭条件必须直接写进 prompt，
# 不得只靠“去读 Runbook”单点引用隐藏。首轮与返修只允许在开头任务动词上不同。

CLAUDE_RUNBOOK_PATH = "docs/CLAUDE_IMPLEMENTATION_RUNBOOK.md"

_CLAUDE_REQUIRED_READING = (
    "任何写入前必须先完整读取以下必读文件（顺序即优先级）："
    "① {runbook}（实施方长期纪律，第一必读）、② CODEX_GUIDE.md、"
    "③ docs/AI_REVIEW_HANDOFF.md 的协议区与当前工作包 {wp} 全文；"
    "再按当前包读取 docs/AI_HANDOFF_OPERATIONS.md、docs/PROJECT_STATE.md、"
    "docs/PLATFORM_ROADMAP.md、docs/COMPONENT_CONTRACT.md 与适用规格、scope 源码与测试；"
    "必读发生在任何写入之前，不得用旧对话快照覆盖仓库实盘。"
)

_CLAUDE_ZERO_WRITE_CHECK = (
    "完成必读后、任何写入前，先核验当前包 "
    "work_package_id/status/owner/handoff_to/round/max_rounds/handoff_protocol、"
    "main==origin/main==HEAD、scope 清单与聚合 SHA-256、冻结依赖与协调器/租约状态；"
    "scope 聚合的比对基准随接手状态而定：首轮 CLAUDE_WORKING 与 scope_baseline_sha256 比对，"
    "CHANGES_REQUESTED 返修先确认上一轮 review_started_sha256==review_finished_sha256，"
    "再与 review_finished_sha256 比对（不得再统一拿 baseline，否则合法返修会被误判漂移）；"
    "任一与任务书不符立即停笔并报告，不猜测、不擅自修复。"
)

_CLAUDE_COMMAND_DISCIPLINE = (
    "允许命令：文件用 Read/Edit/Write/Glob/Grep；哈希、manifest、真实宿主时间与测试只用"
    "单条 python/python3 -c 或单条 PYTHONDONTWRITEBYTECODE=1 python -m unittest；"
    "禁止使用 git、gh、shasum、sha256sum、rm、sudo，禁止管道、命令替换、shell 循环、"
    "&& 或 ; 串联及任何复合 Bash（与执行计划的 --allowedTools/--disallowedTools 完全一致）。"
)

_CLAUDE_V2_TEMPLATE = (
    "交接证据必须逐字使用结构化字段名 `- 实际测试命令与结果:`、`- self_review_manifest:`、"
    "`- 是否满足交接条件: 是`，字段名后不得加括号、附注或改成表格/小标题；"
    "每条 unittest 结果在同一行写 `Ran N tests, OK`（真实计数，出现 FAILED/ERROR 即失败关闭）；"
    "真实时间只能由允许的单条 Python 命令在宿主读取，禁止估算或沿用旧时间。"
)

_CLAUDE_STOP_AND_HANDOFF = (
    "只改工作包 scope，先写反证再修复，运行必要测试。"
    "scope/冻结哈希漂移、需扩 scope、规格或默认值不明确、测试真实失败未定位、"
    "允许命令被拒、触及 Git/删除/外部系统、轮次耗尽或无法取得真实时间/真实测试计数时，"
    "必须安全停笔并报告，不得伪造 PASS 或创建恢复包。"
    "禁止任何 Git/GitHub 写操作（暂存、提交、推送、建 PR、合并），"
    "禁止越权修改 scope 外或任务书未授权的项目状态；"
    "仅在自审 PASS 后原子转为 READY_FOR_CODEX/owner=codex/handoff_to=codex 并立即停止写 scope。"
)

_CLAUDE_HANDOFF_TITLE = (
    "实施交接标题必须精确使用 `### Claude 实施交接（Round N）`，"
    "并至少包含两个可机器解析的独立字段行："
    "`- scope_sha256: <64位小写十六进制>` 和 "
    "`- implementation_finished_at: <带时区时间>`；不得改写成小标题、表格或仅放在正文中。"
)


def build_claude_prompt(work_package_id: str, action: str) -> str:
    """首轮实施与返修共用的实施方 prompt；只在开头任务动词上区分，其余纪律片段完全一致，
    避免两条执行路径漂移。关键失败关闭条件（必读顺序、零写入核验、允许/禁止命令、
    三个精确 v2 字段、`Ran N tests, OK`、真实时间、scope/规格歧义停笔、禁止 Git/GitHub 写、
    交接标题与字段）全部内联，不依赖 Runbook 单点引用。"""
    task = "按最近 Codex 审核意见返修" if action == "start_claude_rework" else "实施当前工作包"
    return " ".join(
        [
            f"{task} {work_package_id}。"
            + _CLAUDE_REQUIRED_READING.format(runbook=CLAUDE_RUNBOOK_PATH, wp=work_package_id),
            _CLAUDE_ZERO_WRITE_CHECK,
            _CLAUDE_COMMAND_DISCIPLINE,
            _CLAUDE_V2_TEMPLATE,
            _CLAUDE_STOP_AND_HANDOFF,
            _CLAUDE_HANDOFF_TITLE,
        ]
    )


class ClaudeEndpointAdapter:
    """Claude Code 非交互命令契约；默认禁用，只有显式生产开关才能启用。"""

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        project_root: str | Path | None = None,
        timeout_seconds: int = 1800,
        model: str = "opus",
        authenticated: bool | None = None,
        proxy_url: str | None = None,
        enabled: bool = False,
        max_turns: int = 80,
    ):
        self.executable = _resolved_executable(
            executable,
            "claude",
            Path.home() / ".local" / "bin" / "claude",
        )
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_turns = self._validated_max_turns(max_turns)
        self.model = model
        self.authenticated = authenticated
        self.proxy_url = self._validated_proxy_url(proxy_url)
        self.available = self.executable is not None
        self.enabled = bool(enabled and self.available and authenticated is not False)
        if not self.available:
            self.reason = "未发现可执行的 Claude Code CLI"
        elif authenticated is False:
            self.reason = "Claude Code CLI 已安装但尚未登录"
        elif self.enabled:
            self.reason = "Claude Code CLI 生产事件入口已显式启用"
        elif authenticated is True:
            self.reason = "Claude Code CLI 已安装并登录；生产执行生命周期安全门尚未打开"
        else:
            self.reason = "Claude Code CLI 已发现；启动时不读取凭据，生产执行生命周期安全门尚未打开"

    @staticmethod
    def _validated_max_turns(max_turns: int) -> int:
        # 单个 Claude CLI 外部进程允许的最大 turns；与 timeout_seconds、工作包
        # max_rounds 相互独立，任一先到即停止。必须是真正的正整数，构造期失败关闭，
        # 不静默取整、字符串转数或回退默认值。
        if isinstance(max_turns, bool) or not isinstance(max_turns, int):
            raise ValueError("Claude max_turns 必须是 int 正整数，禁止 bool 或非整数类型")
        if max_turns <= 0:
            raise ValueError("Claude max_turns 必须是正整数（> 0）")
        return max_turns

    @staticmethod
    def _validated_proxy_url(proxy_url: str | None) -> str | None:
        if proxy_url is None:
            return None
        parsed = urlparse(proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port is None:
            raise ValueError("Claude 代理必须是显式 http(s)://host:port 地址")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Claude 代理地址不得内嵌账号或密码，避免在面板/API 中泄露凭据")
        return proxy_url

    @property
    def execution_environment(self) -> dict[str, str]:
        if self.proxy_url is None:
            return {}
        # Claude Code 官方支持 HTTP_PROXY/HTTPS_PROXY，不支持 SOCKS 代理。
        return {"HTTP_PROXY": self.proxy_url, "HTTPS_PROXY": self.proxy_url}

    def probe_authenticated(self, *, timeout_seconds: float = 15.0) -> bool:
        """只在显式生产启用时检查 CLI 登录态；不持久化账号信息或命令输出。"""
        if not self.executable:
            return False
        plan = ExecutionPlan(
            actor="claude",
            action="probe_authentication",
            command=[self.executable, "auth", "status"],
            cwd=str(self.project_root),
            timeout_seconds=timeout_seconds,
            permission_summary="只读查询 Claude Code 登录状态",
            environment=self.execution_environment,
        )
        result = SafeProcessRunner(output_tail_bytes=16 * 1024).run(plan)
        if result.outcome != "completed":
            return False
        try:
            payload = json.loads(result.stdout_tail)
        except json.JSONDecodeError:
            return False
        return payload.get("loggedIn") is True

    def command_for(self, package: WorkPackage, action: str = "start_claude_rework") -> ExecutionPlan:
        if not self.executable:
            raise RuntimeError(self.reason)
        prompt = build_claude_prompt(package.work_package_id, action)
        return ExecutionPlan(
            actor="claude",
            action=action,
            command=[
                self.executable, "-p", prompt,
                "--output-format", "json", "--max-turns", str(self.max_turns),
                "--model", self.model, "--permission-mode", "dontAsk",
                "--allowedTools",
                "Read,Edit,Write,Glob,Grep,Bash(python *),Bash(python3 *),"
                "Bash(PYTHONDONTWRITEBYTECODE=1 python *),Bash(PYTHONDONTWRITEBYTECODE=1 python3 *)",
                "--disallowedTools", "Bash(git *),Bash(gh *),Bash(rm *),Bash(sudo *)",
                "--no-session-persistence",
            ],
            cwd=str(self.project_root),
            timeout_seconds=self.timeout_seconds,
            permission_summary="仅文件读写与 Python 测试；Git/gh/rm/sudo 禁止；dontAsk 失败关闭",
            environment=self.execution_environment,
        )

    def execute(self, package: WorkPackage) -> None:
        raise RuntimeError(self.reason)


# 只读 deprecated 兼容别名：历史导入路径 `Fable5EndpointAdapter` 仍可用，
# 但指向新的 `ClaudeEndpointAdapter`；新代码不得再引用旧名。
Fable5EndpointAdapter = ClaudeEndpointAdapter  # deprecated alias


class NotificationAdapter:
    available = True
    enabled = False

    SUPPORTED_EVENTS = {
        "handoff_to_codex", "returned_to_claude", "approved", "blocked",
        "invalid_fields", "invalid_scope_hash", "watcher_degraded",
        # 三阶段协议：Claude 交接前自审门禁未通过（与 Codex 独立审核区分）
        "self_review_gate_failed",
        # deprecated 只读兼容别名（新代码不再产生）：
        "returned_to_fable5",
    }

    def preview(self, event: str, title: str, message: str) -> dict[str, str | bool]:
        if event not in self.SUPPORTED_EVENTS:
            raise ValueError(f"未知通知事件: {event}")
        return {"event": event, "title": title, "message": message, "dry_run": True}

    def send(self, title: str, message: str) -> None:
        raise RuntimeError("v1 dry-run 禁止发送系统通知")


def default_runtime_dir(source_path: str | Path) -> Path:
    digest = hashlib.sha256(str(Path(source_path).resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"ai-handoff-{digest}"


class DryRunScheduler:
    def __init__(
        self,
        source_path: str | Path,
        runtime_dir: str | Path | None = None,
        *,
        dry_run: bool = True,
        codex: CodexCommandAdapter | None = None,
        claude: ClaudeEndpointAdapter | None = None,
        notifier: NotificationAdapter | None = None,
        project_root: str | Path | None = None,
        scope_hash_resolver: Callable[[WorkPackage], ScopeHashResult] | None = None,
    ):
        if not dry_run:
            raise ValueError("DryRunScheduler 只允许 dry-run；真实执行必须使用 EventDrivenScheduler")
        self.source_path = Path(source_path)
        self.runtime_dir = Path(runtime_dir) if runtime_dir else default_runtime_dir(source_path)
        self.dry_run = True
        resolved_source = self.source_path.resolve()
        default_root = resolved_source.parent.parent if resolved_source.parent.name == "docs" else resolved_source.parent
        self.project_root = Path(project_root).resolve() if project_root else default_root
        self.codex = codex or CodexCommandAdapter(project_root=self.project_root)
        self.claude = claude or ClaudeEndpointAdapter(project_root=self.project_root)
        self.notifier = notifier or NotificationAdapter()
        self.scope_hash_resolver = scope_hash_resolver or (
            lambda package: calculate_scope_sha256(package, self.project_root)
        )
        self._thread_lock = threading.Lock()
        self._running: set[str] = set()

    @property
    def log_path(self) -> Path:
        return self.runtime_dir / "runs.jsonl"

    @property
    def failure_log_path(self) -> Path:
        return self.runtime_dir / "failures.jsonl"

    def dispatch(self, package: WorkPackage) -> DispatchResult:
        result = self._prepare_dispatch(package, dry_run=True)
        if result.outcome != "dry-run-candidate":
            self._record(result)
            return result
        assert result.action and result.action_label and result.idempotency_key
        action, label, key = result.action, result.action_label, result.idempotency_key
        scope_info = {
            "scope_current_sha256": result.scope_current_sha256,
            "scope_expected_sha256": result.scope_expected_sha256,
            "scope_hash_basis": result.scope_hash_basis,
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.runtime_dir / "scheduler.lock"
        with self._thread_lock, lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if key in self._running:
                    return DispatchResult(
                        outcome="ignored-running", dry_run=True,
                        work_package_id=package.work_package_id, round=package.round,
                        action=action, action_label=label, idempotency_key=key,
                        reason="相同动作已在运行",
                        **scope_info,
                    )
                try:
                    states = self._action_states()
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    failed = DispatchResult(
                        outcome="failed", dry_run=True,
                        work_package_id=package.work_package_id, round=package.round,
                        action=action, action_label=label, idempotency_key=key,
                        reason=f"幂等记录不可信，已安全停止: {exc}",
                        **scope_info,
                    )
                    self._append_failure(failed)
                    return failed
                if states.get(key) == "running":
                    return DispatchResult(
                        outcome="ignored-running", dry_run=True,
                        work_package_id=package.work_package_id, round=package.round,
                        action=action, action_label=label, idempotency_key=key,
                        reason="运行记录显示相同动作尚未完成；为避免重入已安全忽略",
                        **scope_info,
                    )
                if states.get(key) == "dry-run-candidate":
                    return DispatchResult(
                        outcome="ignored-duplicate", dry_run=True,
                        work_package_id=package.work_package_id, round=package.round,
                        action=action, action_label=label, idempotency_key=key,
                        reason="同一幂等键已处理，重复事件已忽略",
                        **scope_info,
                    )
                self._running.add(key)
                try:
                    running = DispatchResult(
                        outcome="running", dry_run=True,
                        work_package_id=package.work_package_id, round=package.round,
                        action=action, action_label=label, idempotency_key=key,
                        reason="已获得文件锁，正在生成 dry-run 候选记录",
                        **scope_info,
                    )
                    self._append_record(running)
                    # 不调用任何 adapter.execute；dry-run 是结构性保证。
                    self._append_record(result)
                except Exception as exc:
                    failed = DispatchResult(
                        outcome="failed", dry_run=True,
                        work_package_id=package.work_package_id, round=package.round,
                        action=action, action_label=label, idempotency_key=key,
                        reason=f"调度记录异常退出: {exc}",
                        **scope_info,
                    )
                    try:
                        self._append_failure(failed)
                    finally:
                        return failed
                finally:
                    self._running.discard(key)
                return result
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _prepare_dispatch(self, package: WorkPackage, *, dry_run: bool) -> DispatchResult:
        validation = self._validate_basic(package, dry_run=dry_run)
        if validation is not None:
            return validation
        # 先重算当前 scope 清单，供自审 manifest 逐项密码学比对（覆盖文件内容漂移）。
        integrity = self.scope_hash_resolver(package)
        validation = self._validate_self_review(
            package, dry_run=dry_run, current_manifest=integrity.manifest or None
        )
        if validation is not None:
            return validation
        validation = self._validate_scope_integrity(package, integrity, dry_run=dry_run)
        if validation is not None:
            return validation
        assert package.status and package.owner and package.handoff_to and package.round is not None
        action, label = TRIGGER_MAP[
            (package.status, canonical_actor(package.owner), canonical_actor(package.handoff_to))
        ]
        key = f"{package.work_package_id}:{package.round}:{action}"
        expected_hash, hash_basis = self._expected_scope_hash(package)
        return DispatchResult(
            outcome="dry-run-candidate" if dry_run else "execution-candidate",
            dry_run=dry_run,
            work_package_id=package.work_package_id,
            round=package.round,
            action=action,
            action_label=label,
            idempotency_key=key,
            reason=(
                "dry-run：已记录候选动作，未启动外部执行器"
                if dry_run else "状态、权属、轮次与 scope 证据已通过执行前校验"
            ),
            notification_candidate=self._status_notification(package),
            scope_current_sha256=integrity.digest,
            scope_expected_sha256=expected_hash,
            scope_hash_basis=hash_basis,
            **self._adapter_preview(package, action),
        )

    def _validate_basic(self, package: WorkPackage, *, dry_run: bool = True) -> DispatchResult | None:
        common = dict(dry_run=dry_run, work_package_id=package.work_package_id or None, round=package.round)
        if package.errors:
            return DispatchResult(
                outcome="rejected-invalid", reason="；".join(package.errors),
                notification_candidate=self.notifier.preview("invalid_fields", "AI 交接字段异常", "；".join(package.errors)), **common,
            )
        missing = [name for name, value in (
            ("work_package_id", package.work_package_id), ("status", package.status),
            ("owner", package.owner), ("handoff_to", package.handoff_to),
            ("round", package.round), ("max_rounds", package.max_rounds),
        ) if value is None or value == ""]
        if missing:
            reason = "缺少调度字段: " + ", ".join(missing)
            return DispatchResult(outcome="rejected-invalid", reason=reason, notification_candidate=self.notifier.preview("invalid_fields", "AI 交接字段异常", reason), **common)
        if package.status not in STATUS_MAP:
            reason = f"非法状态: {package.status}"
            return DispatchResult(outcome="rejected-invalid", reason=reason, notification_candidate=self.notifier.preview("invalid_fields", "AI 交接字段异常", reason), **common)
        expected = STATUS_MAP[package.status][:2]
        # 历史 owner=fable5 规范化为 claude 后再比对，兼容历史与新记录。
        if (canonical_actor(package.owner), canonical_actor(package.handoff_to)) != expected:
            reason = "owner/handoff_to 与状态映射不一致"
            return DispatchResult(outcome="rejected-invalid", reason=reason, notification_candidate=self.notifier.preview("invalid_fields", "AI 交接字段异常", reason), **common)
        assert package.round is not None and package.max_rounds is not None
        if package.round > package.max_rounds:
            return DispatchResult(
                outcome="dry-run-user-action" if dry_run else "user-action", action="notify_user_round_exceeded",
                action_label="通知用户：自动轮次已超限",
                reason="round 超过 max_rounds，仅生成需要用户处理的候选动作",
                notification_candidate=self.notifier.preview("invalid_fields", "AI 交接轮次超限", f"{package.work_package_id}: {package.round}/{package.max_rounds}"),
                **common,
            )
        if package.status == "CHANGES_REQUESTED" and package.round >= package.max_rounds:
            return DispatchResult(
                outcome="dry-run-user-action" if dry_run else "user-action", action="notify_user_round_exceeded",
                action_label="通知用户：返修接手将超过最大轮次",
                reason="CHANGES_REQUESTED 接手需要 round+1，当前已达 max_rounds，不得触发 Claude",
                notification_candidate=self.notifier.preview(
                    "invalid_fields", "AI 交接返修轮次已用尽",
                    f"{package.work_package_id}: {package.round}/{package.max_rounds}",
                ),
                **common,
            )
        key = (package.status, canonical_actor(package.owner), canonical_actor(package.handoff_to))
        if key not in TRIGGER_MAP:
            return DispatchResult(outcome="no-action", reason="当前合法状态没有 v1 触发动作", **common)
        return None

    def _validate_self_review(
        self, package: WorkPackage, *, dry_run: bool = True,
        current_manifest: list[str] | None = None,
    ) -> DispatchResult | None:
        """交接门禁：v2 工作包必须先有 PASS 的结构化自审，才允许生成 Codex 审核候选。

        Claude 自审属于 CLAUDE_WORKING 阶段，本身不产生 Codex 审核候选；
        自审缺失 / BLOCKED / 测试证据不足 / 自审哈希与交接哈希漂移时一律拒绝交接。
        """
        reason = self_review_gate(package, current_manifest=current_manifest)
        if reason is None:
            return None
        return DispatchResult(
            outcome="rejected-self-review",
            dry_run=dry_run,
            work_package_id=package.work_package_id or None,
            round=package.round,
            reason=f"交接门禁拒绝：{reason}；应保持 CLAUDE_WORKING 并补齐自审证据",
            notification_candidate=self.notifier.preview(
                "self_review_gate_failed", "Claude 交接前自审未通过", reason
            ),
        )

    def _adapter_preview(self, package: WorkPackage, action: str) -> dict[str, object]:
        adapter: CodexCommandAdapter | ClaudeEndpointAdapter | None
        if action == "start_codex_review":
            adapter = self.codex
        elif action in {"start_claude_implementation", "start_claude_rework"}:
            adapter = self.claude
        else:
            adapter = None
        if adapter is None:
            return {}
        status = "enabled" if adapter.enabled else ("available-disabled" if adapter.available else "unavailable")
        plan = adapter.command_for(package, action).to_dict() if adapter.available else None
        return {
            "adapter": "codex" if adapter is self.codex else "claude",
            "adapter_status": status,
            "adapter_reason": adapter.reason,
            "execution_plan": plan,
        }

    def _validate_scope_integrity(
        self, package: WorkPackage, integrity: ScopeHashResult, *, dry_run: bool = True
    ) -> DispatchResult | None:
        common = dict(
            dry_run=dry_run,
            work_package_id=package.work_package_id or None,
            round=package.round,
            scope_current_sha256=integrity.digest,
        )
        required = {"scope_baseline_sha256": package.scope_baseline_sha256}
        if canonical_status(package.status) != "CLAUDE_WORKING":
            required["implementation scope_sha256"] = package.implementation_scope_sha256
        if package.status in {"CHANGES_REQUESTED", "APPROVED", "BLOCKED"}:
            required.update({
                "review_started_sha256": package.review_started_sha256,
                "review_finished_sha256": package.review_finished_sha256,
            })
        missing = [name for name, value in required.items() if not value]
        if missing:
            return self._scope_rejection("缺少独立哈希证据: " + ", ".join(missing), **common)
        if integrity.errors or not integrity.digest:
            reason = "；".join(integrity.errors) if integrity.errors else "当前 scope 聚合 SHA-256 计算失败"
            return self._scope_rejection(reason, **common)
        if package.status in {"CHANGES_REQUESTED", "APPROVED", "BLOCKED"}:
            if package.review_started_sha256 != package.review_finished_sha256:
                return self._scope_rejection(
                    "审核开始/结束 SHA-256 不一致，审核期间 scope 已漂移",
                    scope_expected_sha256=package.review_finished_sha256,
                    scope_hash_basis="review_finished_sha256",
                    **common,
                )
        expected, basis = self._expected_scope_hash(package)
        if integrity.digest != expected:
            return self._scope_rejection(
                f"当前 scope SHA-256 与 {basis} 不一致",
                scope_expected_sha256=expected,
                scope_hash_basis=basis,
                **common,
            )
        return None

    def _expected_scope_hash(self, package: WorkPackage) -> tuple[str | None, str]:
        if canonical_status(package.status) == "CLAUDE_WORKING":
            return package.scope_baseline_sha256, "scope_baseline_sha256"
        if package.status == "READY_FOR_CODEX":
            return package.implementation_scope_sha256, "implementation scope_sha256"
        return package.review_finished_sha256, "review_finished_sha256"

    def _scope_rejection(self, reason: str, **common: object) -> DispatchResult:
        return DispatchResult(
            outcome="rejected-invalid",
            reason=reason,
            notification_candidate=self.notifier.preview("invalid_scope_hash", "AI 交接 scope 哈希异常", reason),
            **common,
        )

    def _status_notification(self, package: WorkPackage) -> dict[str, str | bool] | None:
        mapping = {
            "READY_FOR_CODEX": ("handoff_to_codex", "Claude 已交接 Codex"),
            "CHANGES_REQUESTED": ("returned_to_claude", "Codex 已退回 Claude"),
            "APPROVED": ("approved", "AI 工作包已通过"),
            "BLOCKED": ("blocked", "AI 工作包已 BLOCKED"),
        }
        item = mapping.get(package.status or "")
        if not item:
            return None
        event, title = item
        return self.notifier.preview(event, title, f"{package.work_package_id} · Round {package.round}")

    def _action_states(self) -> dict[str, str]:
        if not self.log_path.exists():
            return {}
        states: dict[str, str] = {}
        for number, line in enumerate(self.log_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"runs.jsonl 第 {number} 行损坏") from exc
            key = record.get("idempotency_key")
            outcome = record.get("outcome")
            if key and outcome in {"running", "dry-run-candidate"}:
                states[key] = outcome
        return states

    def _record(self, result: DispatchResult) -> None:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            with (self.runtime_dir / "scheduler.lock").open("a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    self._append_record(result)
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            # 拒绝结果本身不应因诊断日志失败而变成触发。
            pass

    def _append_record(self, result: DispatchResult) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        record = result.to_dict()
        record["recorded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()

    def _append_failure(self, result: DispatchResult) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        record = result.to_dict()
        record["recorded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.failure_log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()


class EventDrivenScheduler(DryRunScheduler):
    """显式启用后才会异步启动外部 AI；其余校验规则与 dry-run 完全相同。"""

    EXTERNAL_ACTIONS = {
        "start_claude_implementation", "start_claude_rework", "start_codex_review",
    }

    def __init__(
        self,
        source_path: str | Path,
        runtime_dir: str | Path | None = None,
        *,
        codex: CodexCommandAdapter,
        claude: ClaudeEndpointAdapter,
        notifier: NotificationAdapter | None = None,
        project_root: str | Path | None = None,
        scope_hash_resolver: Callable[[WorkPackage], ScopeHashResult] | None = None,
        coordinator: AsyncExecutionCoordinator | None = None,
    ):
        if not codex.enabled or not claude.enabled:
            raise ValueError("事件驱动调度要求 Claude 与 Codex 两个 adapter 都显式 enabled=True")
        super().__init__(
            source_path,
            runtime_dir,
            dry_run=True,
            codex=codex,
            claude=claude,
            notifier=notifier,
            project_root=project_root,
            scope_hash_resolver=scope_hash_resolver,
        )
        self.dry_run = False
        self.coordinator = coordinator or AsyncExecutionCoordinator(self.runtime_dir)

    def dispatch(self, package: WorkPackage) -> DispatchResult:
        result = self._prepare_dispatch(package, dry_run=False)
        if result.outcome != "execution-candidate":
            result.lifecycle = self.coordinator.snapshot()
            result.failure_alert = result.lifecycle.get("failure_alert")
            self._record(result)
            return result
        assert result.action and result.idempotency_key and result.round is not None
        if result.action not in self.EXTERNAL_ACTIONS:
            result.outcome = "user-action"
            result.reason = "当前状态只需要通知用户，不启动外部 AI"
            result.lifecycle = self.coordinator.snapshot()
            result.failure_alert = result.lifecycle.get("failure_alert")
            return result
        adapter = self.codex if result.action == "start_codex_review" else self.claude
        if not adapter.available or not adapter.enabled:
            result.outcome = "rejected-adapter-disabled"
            result.reason = adapter.reason
            result.failure_alert = {
                "severity": "error",
                "code": "adapter-disabled",
                "message": adapter.reason,
            }
            return result
        plan = adapter.command_for(package, result.action)
        lifecycle = self.coordinator.start(
            idempotency_key=result.idempotency_key,
            plan=plan,
            work_package_id=package.work_package_id,
            round_number=result.round,
            completion_validator=self._completion_validator(package, result.action),
        )
        mapping = {
            "scheduled": "execution-scheduled",
            "ignored-global-running": "ignored-global-running",
            "ignored-active": "ignored-running",
            "ignored-terminal": "ignored-terminal",
            "blocked-shutting-down": "blocked-runtime",
            "blocked-corrupt-state": "blocked-runtime",
            "blocked-orphan-process": "blocked-runtime",
            "launch-failed": "failed",
        }
        result.outcome = mapping.get(str(lifecycle.get("outcome")), "blocked-runtime")
        result.reason = str(lifecycle.get("reason") or "执行生命周期状态未知，已安全停止")
        result.lifecycle = lifecycle
        result.failure_alert = lifecycle.get("alert")
        active = lifecycle.get("active")
        result.external_process_started = bool(
            isinstance(active, dict)
            and active.get("child_pid")
            and active.get("state") in {"scheduled", "running"}
            and lifecycle.get("outcome") in {"scheduled", "ignored-global-running", "ignored-active"}
        )
        return result

    def lifecycle_snapshot(self) -> dict:
        return self.coordinator.snapshot()

    def _completion_validator(
        self,
        initial: WorkPackage,
        action: str,
    ) -> Callable[[], tuple[bool, str]]:
        """退出码 0 不等于交接成功；必须重读权威文件并验证目标状态与哈希证据。"""
        expected_round = (initial.round or 0) + (1 if action == "start_claude_rework" else 0)

        def validate() -> tuple[bool, str]:
            parsed = HandoffParser(self.source_path).parse_file()
            if parsed.source_error:
                return False, f"外部进程退出码为 0，但交接文件不可验证: {parsed.source_error}"
            matches = [item for item in parsed.packages if item.work_package_id == initial.work_package_id]
            if len(matches) != 1:
                return False, f"外部进程退出码为 0，但工作包唯一性校验失败: {initial.work_package_id}"
            current = matches[0]
            if current.errors:
                return False, "外部进程退出码为 0，但交接字段无效: " + "；".join(current.errors)

            if action in {"start_claude_implementation", "start_claude_rework"}:
                expected_statuses = {"READY_FOR_CODEX"}
                expected_owner = expected_handoff = "codex"
            elif action == "start_codex_review":
                expected_statuses = {"CHANGES_REQUESTED", "APPROVED", "BLOCKED"}
                expected_owner = expected_handoff = None
            else:
                return False, f"未知的执行后置条件动作: {action}"

            if current.status not in expected_statuses:
                return False, (
                    f"外部进程退出码为 0，但状态未完成交接: "
                    f"{current.status!r}，期望 {sorted(expected_statuses)}"
                )
            if current.round != expected_round:
                return False, (
                    f"外部进程退出码为 0，但轮次后置条件失败: "
                    f"{current.round!r}，期望 {expected_round}"
                )
            if expected_owner is not None and (
                canonical_actor(current.owner), canonical_actor(current.handoff_to)
            ) != (expected_owner, expected_handoff):
                return False, "外部进程退出码为 0，但 owner/handoff_to 未交给 Codex"

            expected_mapping = STATUS_MAP.get(current.status or "")
            if expected_mapping is None or (
                canonical_actor(current.owner), canonical_actor(current.handoff_to)
            ) != expected_mapping[:2]:
                return False, "外部进程退出码为 0，但状态与权属映射不一致"

            integrity = self.scope_hash_resolver(current)
            rejection = self._validate_scope_integrity(current, integrity, dry_run=False)
            if rejection is not None:
                return False, (
                    "外部进程退出码为 0，但 scope 后置条件失败: "
                    + str(rejection.reason or "未知哈希错误")
                )
            return True, "交接状态、权属、轮次与 scope 证据后置条件全部成立"

        return validate

    def set_on_update(self, callback: Callable[[], None] | None) -> None:
        self.coordinator.set_on_update(callback)

    def shutdown(self) -> None:
        self.coordinator.shutdown()
