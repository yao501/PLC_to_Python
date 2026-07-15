"""监听交接文件所在目录，优先 macOS kqueue，支持原子替换。"""

from __future__ import annotations

import os
from pathlib import Path
import select
import threading
import time
from typing import Callable


class HandoffWatcher:
    def __init__(
        self,
        path: str | Path,
        callback: Callable[[], None],
        *,
        debounce_seconds: float = 0.25,
        fallback_interval: float = 5.0,
        force_fallback: bool = False,
    ):
        self.path = Path(path)
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        # 下限仅防止 0/负值造成忙轮询；生产默认 5.0 不变，测试可注入更短间隔以获得确定性。
        self.fallback_interval = max(0.05, fallback_interval)
        self._native_supported = hasattr(select, "kqueue") and not force_fallback
        self.mode = "native-kqueue" if self._native_supported else "degraded-low-frequency-check"
        self.degraded_reason = None if self._native_supported else "当前平台不可用 kqueue，每 5 秒低频检查交接文件"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        target = self._run_kqueue if self._native_supported else self._run_fallback
        self._thread = threading.Thread(target=target, name="ai-handoff-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run_kqueue(self) -> None:
        directory_fd: int | None = None
        queue = None
        try:
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            queue = select.kqueue()
            flags = (
                select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB
                | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE
            )
            event = select.kevent(
                directory_fd,
                filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                fflags=flags,
            )
            queue.control([event], 0, 0)
            pending_at: float | None = None
            while not self._stop.is_set():
                events = queue.control(None, 8, 0.25)
                if events:
                    pending_at = time.monotonic() + self.debounce_seconds
                if pending_at is not None and time.monotonic() >= pending_at:
                    pending_at = None
                    self._safe_callback()
        except (OSError, AttributeError):
            # 运行期原生监听失效时明确切换低频降级。
            self.mode = "degraded-low-frequency-check"
            self.degraded_reason = "kqueue 监听运行期失效，已切换低频检查"
            self._run_fallback()
        finally:
            if queue is not None:
                queue.close()
            if directory_fd is not None:
                os.close(directory_fd)

    def _run_fallback(self) -> None:
        previous = self._signature()
        while not self._stop.wait(self.fallback_interval):
            current = self._signature()
            if current != previous:
                previous = current
                self._safe_callback()

    def _signature(self) -> tuple[int, int, int] | None:
        try:
            stat = self.path.stat()
            return stat.st_ino, stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _safe_callback(self) -> None:
        try:
            self.callback()
        except Exception:
            # 不让一次解析失败杀死监听线程；状态层会呈现读取错误。
            return
