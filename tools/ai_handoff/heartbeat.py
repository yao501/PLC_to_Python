"""把宿主协调器状态安全投影为项目内的只读心跳文件。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any


class CoordinatorHeartbeat:
    """原子更新项目内心跳；它只证明存活，不授予或恢复执行权。"""

    def __init__(
        self,
        path: str | Path,
        status_provider: Callable[[], Mapping[str, Any]],
        *,
        interval_seconds: float = 2.0,
        stale_after_seconds: float = 10.0,
    ):
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval 必须大于 0")
        if stale_after_seconds < interval_seconds * 2:
            raise ValueError("heartbeat stale_after 至少应为 interval 的 2 倍")
        self.path = Path(path)
        self.status_provider = status_provider
        self.interval_seconds = float(interval_seconds)
        self.stale_after_seconds = float(stale_after_seconds)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at_epoch: float | None = None
        self._sequence = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_at_epoch = time.time()
        self._sequence = 0
        # 首次写入失败时拒绝假装服务已经可被另一侧观测。
        self._write("live")
        self._thread = threading.Thread(
            target=self._run,
            name="ai-handoff-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        if self._started_at_epoch is not None:
            self._write("stopped")
        self._thread = None
        self._started_at_epoch = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._write("live")
            except OSError:
                # 后续写入失败会自然表现为心跳过期；消费者必须失败关闭。
                continue

    def _write(self, state: str) -> None:
        with self._lock:
            now = time.time()
            self._sequence += 1
            system = dict(self.status_provider())
            payload = {
                "schema_version": 1,
                "state": state,
                "coordinator_live": state == "live",
                "pid": os.getpid(),
                "started_at": self._iso(self._started_at_epoch or now),
                "updated_at": self._iso(now),
                "updated_at_epoch": now,
                "valid_until_epoch": (
                    now + self.stale_after_seconds if state == "live" else now
                ),
                "heartbeat_sequence": self._sequence,
                "heartbeat_interval_seconds": self.interval_seconds,
                "stale_after_seconds": self.stale_after_seconds,
                "watcher_mode": system.get("watcher_mode"),
                "external_processes_enabled": bool(
                    system.get("external_processes_enabled")
                ),
                "execution_failure_alert": system.get("execution_failure_alert"),
                # 心跳只能要求旧轮询保持暂停，永远不能自行授权恢复。
                "legacy_polling_must_remain_paused": True,
                "legacy_polling_resume_authorized": False,
                "semantics": (
                    "仅当 coordinator_live=true 且当前时间不晚于 valid_until_epoch 时，"
                    "才可判定协调器存活；缺失、损坏、stopped 或过期都必须失败关闭并告警，"
                    "不得据此恢复旧轮询。"
                ),
            }
            self._atomic_replace(payload)

    def _atomic_replace(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o444)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _iso(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
