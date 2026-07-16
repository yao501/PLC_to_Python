"""仅绑定回环地址的只读状态面板。"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

from .heartbeat import CoordinatorHeartbeat
from .parser import HandoffParser, ParseResult
from .scheduler import (
    ClaudeEndpointAdapter,
    CodexCommandAdapter,
    DispatchResult,
    DryRunScheduler,
    EventDrivenScheduler,
)
from .watcher import HandoffWatcher


class StateStore:
    def __init__(
        self,
        source_path: str | Path,
        scheduler: DryRunScheduler | EventDrivenScheduler | None = None,
    ):
        self.source_path = Path(source_path)
        self.parser = HandoffParser(self.source_path)
        self.scheduler = scheduler or DryRunScheduler(self.source_path)
        self._condition = threading.Condition()
        self._version = 0
        self._result = ParseResult(source=str(self.source_path), source_error="尚未读取交接文件")
        self._dispatch: DispatchResult | None = None

    @property
    def version(self) -> int:
        with self._condition:
            return self._version

    def refresh(self) -> None:
        result = self.parser.parse_file()
        dispatch: DispatchResult | None = None
        if result.current is not None:
            dispatch = self.scheduler.dispatch(result.current)
        with self._condition:
            self._result = result
            self._dispatch = dispatch
            self._version += 1
            self._condition.notify_all()

    def wait_for_change(self, version: int, timeout: float) -> int:
        with self._condition:
            if self._version == version:
                self._condition.wait(timeout)
            return self._version

    def snapshot(self, watcher: HandoffWatcher | None = None) -> dict[str, Any]:
        with self._condition:
            data = self._result.to_dict()
            data["version"] = self._version
            data["dispatch"] = self._dispatch.to_dict() if self._dispatch else None
        degraded = bool(watcher and watcher.mode.startswith("degraded"))
        claude_status = (
            "enabled" if self.scheduler.claude.enabled else
            ("available-disabled" if self.scheduler.claude.available else "unavailable")
        )
        codex_status = (
            "enabled" if self.scheduler.codex.enabled else
            ("available-disabled" if self.scheduler.codex.available else "unavailable")
        )
        lifecycle = (
            self.scheduler.lifecycle_snapshot()
            if isinstance(self.scheduler, EventDrivenScheduler)
            else {"enabled": False, "active": None, "last_event": None, "failure_alert": None}
        )
        data["execution_lifecycle"] = lifecycle
        data["system"] = {
            "authority": str(self.source_path),
            "read_only": True,
            "dry_run": self.scheduler.dry_run,
            "watcher_mode": watcher.mode if watcher else "not-started",
            "watcher_degraded": degraded,
            "watcher_message": watcher.degraded_reason if watcher else None,
            "notification_candidate": self.scheduler.notifier.preview(
                "watcher_degraded", "AI 交接事件监听降级", watcher.degraded_reason or "kqueue 不可用"
            ) if degraded else None,
            "claude_trigger": claude_status,
            "claude_trigger_reason": self.scheduler.claude.reason,
            "codex_trigger": codex_status,
            "codex_trigger_reason": self.scheduler.codex.reason,
            # deprecated 只读兼容别名（新前端读 claude_trigger）：
            "fable5_trigger": claude_status,
            "external_processes_enabled": not self.scheduler.dry_run,
            "execution_failure_alert": lifecycle.get("failure_alert"),
        }
        return data


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: StateStore, watcher: HandoffWatcher):
        self.state = state
        self.watcher = watcher
        super().__init__(address, DashboardHandler)

    def handle_error(self, request: object, client_address: object) -> None:
        # SSE 客户端在服务停止/重连时正常断开，不应刷出误导性堆栈。
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlparse(self.path).path
        if route == "/":
            self._send_html()
        elif route == "/api/status":
            self._send_json(self.server.state.snapshot(self.server.watcher))
        elif route == "/api/events":
            self._send_events()
        elif route == "/healthz":
            self._send_json({
                "ok": True,
                "read_only": True,
                "dry_run": self.server.state.scheduler.dry_run,
            })
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        # 仅保留启动/降级说明，避免 SSE 长连接刷屏。
        return

    def _send_html(self) -> None:
        html_path = Path(__file__).with_name("dashboard.html")
        try:
            content = html_path.read_bytes()
        except OSError as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        version = -1
        try:
            while True:
                current = self.server.state.version
                if current != version:
                    version = current
                    payload = json.dumps(
                        self.server.state.snapshot(self.server.watcher),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    self.wfile.write(f"event: status\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                else:
                    observed = self.server.state.wait_for_change(version, 15.0)
                    if observed == version:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    # 有新版本时不提前覆盖 version；下一轮必须发 status。
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class DashboardApplication:
    def __init__(
        self,
        source_path: str | Path,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        fallback_interval: float = 5.0,
        force_fallback: bool = False,
        claude_proxy: str | None = None,
        enable_external_processes: bool = False,
        heartbeat_path: str | Path | None = None,
        heartbeat_interval: float = 2.0,
        heartbeat_stale_after: float = 10.0,
    ):
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("面板 v1 只允许绑定本机回环地址")
        resolved_source = Path(source_path).resolve()
        project_root = (
            resolved_source.parent.parent
            if resolved_source.parent.name == "docs"
            else resolved_source.parent
        )
        if enable_external_processes:
            claude_probe = ClaudeEndpointAdapter(
                project_root=project_root,
                proxy_url=claude_proxy,
            )
            if not claude_probe.probe_authenticated():
                raise RuntimeError(
                    "拒绝启用真实事件执行：Claude Code 登录态核验失败；请先确认代理和 `claude auth status`"
                )
            scheduler: DryRunScheduler | EventDrivenScheduler = EventDrivenScheduler(
                source_path,
                project_root=project_root,
                codex=CodexCommandAdapter(project_root=project_root, enabled=True),
                claude=ClaudeEndpointAdapter(
                    project_root=project_root,
                    proxy_url=claude_proxy,
                    authenticated=True,
                    enabled=True,
                ),
            )
        else:
            scheduler = DryRunScheduler(
                source_path,
                project_root=project_root,
                claude=ClaudeEndpointAdapter(project_root=project_root, proxy_url=claude_proxy),
            )
        self.state = StateStore(source_path, scheduler=scheduler)
        if isinstance(scheduler, EventDrivenScheduler):
            scheduler.set_on_update(self.state.refresh)
        # fallback_interval / force_fallback 仅供测试注入确定性;生产默认值不变。
        self.watcher = HandoffWatcher(
            source_path,
            self.state.refresh,
            fallback_interval=fallback_interval,
            force_fallback=force_fallback,
        )
        self.server = DashboardServer((host, port), self.state, self.watcher)
        self.heartbeat = CoordinatorHeartbeat(
            heartbeat_path or project_root / ".ai-handoff-runtime" / "coordinator_status.json",
            lambda: self.state.snapshot(self.watcher)["system"],
            interval_seconds=heartbeat_interval,
            stale_after_seconds=heartbeat_stale_after,
        )
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    def start(self, *, background: bool = False) -> None:
        self.state.refresh()
        self.watcher.start()
        try:
            self.heartbeat.start()
        except BaseException:
            self.watcher.stop()
            if isinstance(self.state.scheduler, EventDrivenScheduler):
                self.state.scheduler.shutdown()
            self.server.server_close()
            raise
        mode = "原生 kqueue 事件模式" if self.watcher.mode == "native-kqueue" else "降级低频检查模式"
        execution_mode = "事件执行已启用" if not self.state.scheduler.dry_run else "dry-run"
        print(f"AI 交接面板: http://{self.address[0]}:{self.address[1]}  [{mode}; {execution_mode}; 面板只读]")
        if background:
            self._thread = threading.Thread(target=self.server.serve_forever, name="ai-handoff-http", daemon=True)
            self._thread.start()
        else:
            try:
                self.server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                self.watcher.stop()
                if isinstance(self.state.scheduler, EventDrivenScheduler):
                    self.state.scheduler.shutdown()
                self.server.server_close()
                self.heartbeat.stop()

    def stop(self) -> None:
        self.watcher.stop()
        if isinstance(self.state.scheduler, EventDrivenScheduler):
            self.state.scheduler.shutdown()
        self.server.shutdown()
        self.server.server_close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self.heartbeat.stop()


def run(
    source_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    claude_proxy: str | None = None,
    enable_external_processes: bool = False,
) -> None:
    DashboardApplication(
        source_path,
        host,
        port,
        claude_proxy=claude_proxy,
        enable_external_processes=enable_external_processes,
    ).start()
