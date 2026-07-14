"""过程映像基础：输入一次性锁存、输出待提交容器、prev 快照 API
（WP-20260714-003；对应 ENGINE_SCAN_SPEC §2/§3 的数据结构底座）。

本模块只建立**数据结构与基础操作**，不实现完整扫描：

- ``latch_inputs()``：按 ``IOMap.direction == "IN"`` 把物理通道采样映射到
  GVL。**两阶段**：先对一拍输入完整校验并形成独立快照，全部通过后才
  一次性更新 Store——任何一处非法都不会造成部分输入已写入。
- ``InputSnapshot``：锁存产物，只读；外部输入字典在锁存后的变化不影响
  本拍快照。
- ``OutputPending``：输出待提交映像的最小容器。业务 Store 写入**不会**
  自动进入本容器，本容器也**不**连接任何驱动——业务写入与物理 I/O 的
  边界由此显式隔开。
- ``make_prev_snapshot()``：为调用方在**正确提交点**生成 ``prev`` 快照
  提供基础 API；提交时机由后续扫描引擎决定（ENGINE_SCAN_SPEC §3 第 5
  步），本包不实现 ``scan()``、不自动决定提交时机。

诚实边界：本包不执行 ``OutputPolicy``、不提交驱动、不实现 shadow /
watchdog / 故障恢复 / 安全默认值（ENGINE_SCAN_SPEC §4 属后续工作包）；
Python 侧行为不构成与目标 PLC 语义一致的证据。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.runtime.ir import IOMap
from src.runtime.store import (
    Store,
    StoreSnapshot,
    UnknownStoreKeyError,
    check_value_type,
)


class ProcessImageError(Exception):
    """过程映像层错误基类。"""


class InputImageError(ProcessImageError):
    """输入锁存错误（未知/缺失通道、重复或非法映射、类型不匹配等）。
    抛出即表示本拍 Store **未发生任何写入**。"""


class OutputImageError(ProcessImageError):
    """输出待提交映像错误（未知通道、类型不匹配等）。"""


# ---------------------------------------------------------------------------
# 输入映像
# ---------------------------------------------------------------------------

class InputSnapshot:
    """一拍输入的只读快照（``var -> value``）。

    由 ``latch_inputs()`` 在校验通过后生成；值为不可变标量的独立副本，
    外部采样字典之后的修改不影响本对象。
    """

    __slots__ = ("_values",)

    def __init__(self, values: dict):
        self._values = dict(values)

    def read(self, var: str) -> Any:
        try:
            return self._values[var]
        except KeyError:
            raise InputImageError("输入快照中不存在变量 '%s'" % var) from None

    def __contains__(self, var: str) -> bool:
        return var in self._values

    def keys(self):
        return tuple(self._values.keys())

    def as_dict(self) -> dict:
        return dict(self._values)


def latch_inputs(store: Store, io_map: Iterable, samples: Mapping) -> InputSnapshot:
    """输入映像一次性锁存（两阶段，保证无部分更新）。

    阶段 1（纯校验，不写 Store）：
    - 收集 ``direction == "IN"`` 的映射；通道或变量在 IN 映射中重复、
      方向非法 → ``InputImageError``；
    - ``samples`` 中出现未映射的未知通道 → 错误；
    - IN 映射要求的通道在 ``samples`` 缺失 → 错误；
    - 目标变量未在 Store 声明，或采样值与其声明类型不匹配 → 错误。

    阶段 2（提交）：按映射把快照值一次性写入 Store，并返回只读
    ``InputSnapshot``。阶段 1 任何错误都发生在第一次写入之前。
    """
    # ---- 阶段 1：校验 + 形成快照 ----
    in_maps: dict = {}          # channel -> var
    seen_vars: set = set()
    for io in io_map:
        if not isinstance(io, IOMap):
            raise InputImageError("io_map 含非 IOMap 项：%r" % (io,))
        if io.direction == "OUT":
            continue
        if io.direction != "IN":
            raise InputImageError(
                "IOMap '%s' 方向非法：%r" % (io.var, io.direction))
        if io.channel in in_maps:
            raise InputImageError("输入通道 '%s' 重复映射" % io.channel)
        if io.var in seen_vars:
            raise InputImageError("GVL 变量 '%s' 被多个输入通道映射" % io.var)
        in_maps[io.channel] = io.var
        seen_vars.add(io.var)

    unknown = set(samples.keys()) - set(in_maps.keys())
    if unknown:
        raise InputImageError("采样包含未知/未映射通道：%s" % sorted(unknown))
    missing = set(in_maps.keys()) - set(samples.keys())
    if missing:
        raise InputImageError("采样缺失必要输入通道：%s" % sorted(missing))

    staged: dict = {}
    for channel, var in in_maps.items():
        value = samples[channel]
        try:
            declared = store.declared_type(var)
        except UnknownStoreKeyError:
            raise InputImageError(
                "输入通道 '%s' 的目标变量 '%s' 未在 Store 声明" % (channel, var)
            ) from None
        if not check_value_type(declared, value):
            raise InputImageError(
                "输入通道 '%s' 采样值 %r 与变量 '%s' 声明类型 %s 不匹配"
                "（不做隐式转换）" % (channel, value, var, declared))
        staged[var] = value

    # ---- 阶段 2：一次性提交 ----
    snapshot = InputSnapshot(staged)
    for var, value in staged.items():
        store.write(var, value)
    return snapshot


# ---------------------------------------------------------------------------
# 输出待提交映像
# ---------------------------------------------------------------------------

class OutputPending:
    """输出待提交映像的最小容器（``channel -> value``）。

    - 由 ``io_map`` 中 ``direction == "OUT"`` 的条目定义合法通道集与
      各通道目标类型（取自对应 GVL 变量的声明类型）；
    - ``stage()`` 只是暂存，**不**产生物理 I/O、不触发驱动；
    - 业务 Store 的写入**不会**自动出现在本容器——从 request 到物理
      提交必须经过后续工作包的 OutputPolicy 与提交层（ENGINE_SCAN_SPEC
      §3 第 4/5 步），本包不实现。
    """

    def __init__(self, store: Store, io_map: Iterable):
        self._types: dict = {}      # channel -> declared iec_type
        self._vars: dict = {}       # channel -> gvl var
        self._staged: dict = {}     # channel -> value
        for io in io_map:
            if not isinstance(io, IOMap) or io.direction != "OUT":
                continue
            if io.channel in self._types:
                raise OutputImageError("输出通道 '%s' 重复映射" % io.channel)
            try:
                declared = store.declared_type(io.var)
            except UnknownStoreKeyError:
                raise OutputImageError(
                    "输出通道 '%s' 的源变量 '%s' 未在 Store 声明"
                    % (io.channel, io.var)) from None
            self._types[io.channel] = declared
            self._vars[io.channel] = io.var

    def channels(self):
        return tuple(self._types.keys())

    def var_for(self, channel: str) -> str:
        try:
            return self._vars[channel]
        except KeyError:
            raise OutputImageError("未知输出通道 '%s'" % channel) from None

    def stage(self, channel: str, value: Any) -> None:
        declared = self._types.get(channel)
        if declared is None:
            raise OutputImageError("向未知输出通道 '%s' 暂存输出" % channel)
        if not check_value_type(declared, value):
            raise OutputImageError(
                "输出通道 '%s' 暂存值 %r 与目标类型 %s 不匹配"
                % (channel, value, declared))
        self._staged[channel] = value

    def staged(self) -> dict:
        """返回当前暂存内容的独立副本（修改副本不影响容器）。"""
        return dict(self._staged)

    def clear(self) -> None:
        self._staged.clear()


# ---------------------------------------------------------------------------
# prev 快照基础 API
# ---------------------------------------------------------------------------

def make_prev_snapshot(store: Store) -> StoreSnapshot:
    """在调用方选定的提交点生成 ``prev`` 快照（``LOAD_PREV`` 的数据基础）。

    ENGINE_SCAN_SPEC §3 规定 ``ctx.prev`` 在第 5 步提交后取快照——
    **何时调用本函数由后续扫描引擎决定**，本包不实现扫描、不自动决定
    提交时机。快照与 Store 隔离：之后的 Store 写入不会污染既有快照。
    """
    return store.snapshot()
