"""确定性 startup/readiness 控制器（WP-060）。

本模块是 Python 阶段 1 的项目工程约定，不生成真实 readiness、HAL、实时调度或硬件
watchdog 证据。公开 ``ReadinessSnapshot`` 在每拍仍被当作不可信输入：六字段会在任意
不可信时钟调用前逐项验证并复制；时钟之后不再读取它。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

_NS_PER_MS = 1_000_000
_FIELDS = ("io_ready", "bus_ready", "comm_ready", "safety_ok", "interlock_ok",
           "output_enable")


class ReadinessError(Exception):
    """readiness 控制器的稳定错误基类。"""


class ReadinessConfigError(ReadinessError):
    """配置、快照类型/字段或受控提交回调不符合契约；状态不推进。"""


class ReadinessClockError(ReadinessError):
    """注入时钟抛错、非 exact-int、负值或回退；状态不推进。"""


# Round 1 的公开名字保持兼容；新调用方应使用分层 ReadinessError。
StartupReadinessError = ReadinessError


@dataclass(frozen=True)
class ReadinessSnapshot:
    """调用方每拍提供的六个 exact-bool 输入。"""
    io_ready: bool
    bus_ready: bool
    comm_ready: bool
    safety_ok: bool
    interlock_ok: bool
    output_enable: bool

    def __post_init__(self) -> None:
        for field in _FIELDS:
            if type(getattr(self, field)) is not bool:
                raise ReadinessConfigError("ReadinessSnapshot 只接受 exact bool")


@dataclass(frozen=True)
class StartupState:
    """一次 ``observe`` 的完整可信诊断结果。

    ``system_ready`` 只表示五个启动前置条件和稳定窗口；``output_enable`` 保持独立，
    由 OutputPolicy 与它共同决定最终输出。所有数值均为 exact ``int`` 纳秒。
    """
    system_ready: bool
    preconditions_ok: bool
    output_enable: bool
    in_window: bool
    window_elapsed_ns: int
    inhibit_ns: int
    observed_ns: int


# 旧名称兼容：Round 1 只公开的双字段结果现在是完整 StartupState。
StartupReadinessResult = StartupState


def _copy_readiness(value: object) -> tuple[bool, bool, bool, bool, bool, bool]:
    """时钟前 exact 检查并复制；拒绝路径不读取攻击对象表示或真值。"""
    if type(value) is not ReadinessSnapshot:
        raise ReadinessConfigError("readiness 须为 exact ReadinessSnapshot")
    copied = []
    for field in _FIELDS:
        try:
            item = getattr(value, field)
        except (AttributeError, TypeError):
            raise ReadinessConfigError("readiness 字段缺失或不可读取") from None
        if type(item) is not bool:
            raise ReadinessConfigError("readiness 字段须为 exact bool")
        copied.append(item)
    return tuple(copied)  # type: ignore[return-value]


class StartupReadinessController:
    """显式、单线程的稳定窗口控制器；不含线程、sleep 或浮点累计。"""
    __slots__ = ("_inhibit_ns", "_startup_inhibit_ms", "_clock_ns", "_last_seen_ns",
                 "_window_start_ns", "_released", "_observe_active")

    def __init__(self, startup_inhibit_ms: int,
                 *, clock_ns: Callable[[], int] = time.monotonic_ns):
        if type(startup_inhibit_ms) is not int or startup_inhibit_ms < 0:
            raise ReadinessConfigError("startup_inhibit_ms 须为 exact 非负 int")
        if not callable(clock_ns):
            raise ReadinessConfigError("clock_ns 须为可调用对象")
        self._startup_inhibit_ms = startup_inhibit_ms
        self._inhibit_ns = startup_inhibit_ms * _NS_PER_MS
        self._clock_ns = clock_ns
        self._last_seen_ns = 0
        self._window_start_ns: Optional[int] = None
        self._released = False
        self._observe_active = False

    @property
    def system_ready(self) -> bool:
        return self._released

    @property
    def released(self) -> bool:
        return self._released

    @property
    def inhibit_ns(self) -> int:
        return self._inhibit_ns

    @property
    def startup_inhibit_ms(self) -> int:
        return self._startup_inhibit_ms

    @property
    def in_stable_window(self) -> bool:
        return self._window_start_ns is not None and not self._released

    @property
    def last_seen_ns(self) -> int:
        return self._last_seen_ns

    def _read_clock(self) -> int:
        try:
            value = self._clock_ns()
        except BaseException as exc:
            raise ReadinessClockError("clock_ns() 调用失败") from exc
        if type(value) is not int or value < 0:
            raise ReadinessClockError("clock_ns() 须返回 exact 非负 int")
        if value < self._last_seen_ns:
            raise ReadinessClockError("clock_ns() 回退")
        return value

    def _observe_with_commit(self, readiness: ReadinessSnapshot,
                             trusted_commit_callback: Optional[Callable[..., None]] = None
                             ) -> StartupState:
        """内部的完整验证事务；不接收 tuple/token，callback 成功后才写 controller。

        它仍以公开 ``ReadinessSnapshot`` 为唯一输入，故即使被错误地从模块外调用，
        也不能绕过 exact-bool 校验或构造任意 controller 候选。
        """
        # 重入拒绝必须在读取嵌套 readiness / clock 或任何状态写入之前。
        if self._observe_active:
            raise ReadinessConfigError("readiness observe 不允许重入")
        self._observe_active = True
        try:
            copied = _copy_readiness(readiness)
            if (trusted_commit_callback is not None
                    and not callable(trusted_commit_callback)):
                raise ReadinessConfigError("trusted_commit_callback 须为可调用对象")
            now = self._read_clock()
            preconditions_ok = all(copied[:5])
            window_start = self._window_start_ns
            released = self._released
            if not preconditions_ok:
                window_start = None
                released = False
                elapsed = 0
            elif released:
                # 释放后窗口已消费；持续全真只保持 ready，不再计时。
                window_start = None
                elapsed = 0
            else:
                if window_start is None:
                    window_start = now
                elapsed = now - window_start
                released = elapsed >= self._inhibit_ns
                if released:
                    # 本次返回保留触发 elapsed，提交状态则消费起点。
                    window_start = None
            state = StartupState(
                system_ready=released and preconditions_ok,
                preconditions_ok=preconditions_ok,
                output_enable=copied[5],
                in_window=preconditions_ok and not released,
                window_elapsed_ns=elapsed,
                inhibit_ns=self._inhibit_ns,
                observed_ns=now,
            )
            # callback 是双状态域受控提交的唯一扩展点；它抛任何 BaseException
            # 时 controller 尚未更新。回调返回后只有内建字段赋值。
            if trusted_commit_callback is not None:
                trusted_commit_callback(state, copied)
            self._last_seen_ns = now
            self._window_start_ns = window_start
            self._released = released
            return state
        finally:
            # 普通异常与 BaseException 都不得永久占用单线程事务门。
            self._observe_active = False

    def observe(self, readiness: ReadinessSnapshot) -> StartupState:
        """公开单域入口，完整校验并在成功后提交 controller 状态。"""
        return self._observe_with_commit(readiness)

    def apply(self, readiness: ReadinessSnapshot) -> StartupState:
        """兼容别名；与 :meth:`observe` 完全同一路径。"""
        return self.observe(readiness)


__all__ = [
    "ReadinessSnapshot", "StartupState", "StartupReadinessResult",
    "ReadinessError", "ReadinessConfigError", "ReadinessClockError",
    "StartupReadinessError", "StartupReadinessController",
]
