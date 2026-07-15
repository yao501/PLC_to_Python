"""五步扫描编排骨架：确定性单拍执行（WP-20260716-006；
`.cursor/rules/00a-runtime-contract.mdc R6` 与 ENGINE_SCAN_SPEC §3）。

职责边界（单一职责，**只是编排与端口契约**）：本模块把已有的
``latch_inputs()`` / ``Executor.execute_programs()`` / ``OutputPending`` /
``make_prev_snapshot()`` 按 §3 规定的五步顺序串成一个**可重复调用的确定性
单拍入口**；连续调用 ``ScanEngine.scan()`` 即组成扫描循环。本包**不引入**
真实时间调度线程。

五步顺序（ENGINE_SCAN_SPEC §3；R6 第 5 条）::

    1) 输入映像一次性锁存         latch_inputs()
    2)+3) 按 Task.programs 顺序执行可执行 IR（业务只写 request/store）
    4) 输出门控                   注入的输出策略端口 → OutputPending
    5) 一次性提交                 注入的提交端口恰调用一次；成功后才更新 prev

**明确不实现**（诚实边界，均属后续工作包）：不发明 ``OutputPolicy`` 算法
（§4），不实现 ``system_ready`` / ``output_enable`` / ``safety_ok`` /
``interlock_ok`` 门控公式、startup inhibit、watchdog、shadow mode、
``safe_value``、``last_effective`` / ``last_physical_committed`` 两层状态、
``commit_fault`` / ``channel_fault`` 与故障恢复、真实驱动或 HAL。第 4/5 步
的算法与安全语义**全部**由注入的端口承担；本包只保证"谁在什么时候被调用
一次、失败时不越权"。

**扫描异常不落安全值**（ENGINE_SCAN_SPEC §4.3）：任一步异常原样向外传播，
本包**不**伪造安全输出、**不**吞异常、``prev`` **不**前移。捕获异常 → 置
``scan_fault`` → 按 ``on_scan_fault`` 落安全值并提交，是**扫描函数外层**的
engine runner / OutputPolicy 工作包的职责。

``dt_ms`` 纪律（R6 第 2/3 条）：本模块只认 ``Task.cycle_ms``，不读墙钟、
不按 Python 实际耗时推导 dt、不 sleep；给 library adapter 的 ``dt_ms`` 仍由
``Executor`` 从同一 ``Task.cycle_ms`` 传出。

Python 侧行为不构成与目标 PLC 语义一致的证据（一致性属阶段 6 对拍）。
"""
from __future__ import annotations

import threading
from typing import Any, Mapping

from src.runtime.executor import Executor
from src.runtime.process_image import (
    InputSnapshot,
    OutputPending,
    latch_inputs,
    make_prev_snapshot,
)
from src.runtime.store import RuntimeLayout, StoreSnapshot
from src.runtime.ir import Task


class ScanError(Exception):
    """扫描编排层错误基类。

    只用于**编排自身**的契约违约（端口装配错误、输出通道集不符、重入）。
    输入锁存、IR 执行、策略、提交各自的异常**原样传播**，不包进本类型。
    """


class ScanConfigError(ScanError):
    """引擎装配错误：注入件未绑定同一 Task/RuntimeLayout，或端口缺少约定方法。"""


class OutputStagingError(ScanError):
    """第 4 步门控产物与 ``io_map`` 声明的 OUT 通道集不符（缺失或多余）。

    抛出即表示**未进入第 5 步**：提交端口未被调用，``prev`` 未前移。
    """


class ScanReentryError(ScanError):
    """同一引擎对象的并发/递归 ``scan()``：失败关闭，不交错两拍。

    失败关闭是**本拍拒绝**而非永久锁死——锁在原拍返回后即释放。
    """


class ScanResult:
    """一次成功单拍的只读观察窗（生命周期纪律）。

    三个视图均与引擎内部状态隔离：调用方修改取到的副本**不会**污染引擎
    内部 pending、Store 或下一拍的 ``prev``。
    """

    __slots__ = ("_inputs", "_outputs", "_prev")

    def __init__(self, inputs: InputSnapshot, outputs: Mapping,
                 prev: StoreSnapshot):
        self._inputs = inputs
        self._outputs = dict(outputs)
        self._prev = prev

    @property
    def inputs(self) -> InputSnapshot:
        """本拍第 1 步锁存的输入快照（只读）。"""
        return self._inputs

    @property
    def prev(self) -> StoreSnapshot:
        """本拍第 5 步提交成功后生成的 ``prev`` 快照（只读）。"""
        return self._prev

    def outputs(self) -> dict:
        """本拍门控后、已提交的输出（``channel -> value`` 独立副本）。"""
        return dict(self._outputs)


class ScanEngine:
    """确定性单拍扫描编排器（连续调用 ``scan()`` 即扫描循环）。

    ::

        engine = ScanEngine(task, layout, executor, output_policy, committer)
        result = engine.scan({"DI0": True})     # 一拍
        result = engine.scan({"DI0": False})    # 下一拍

    注入端口契约（本包只定义**调用时机与边界**，不定义其算法）：

    - ``output_policy.stage_outputs(pending, store, inputs, prev)``：第 4 步
      被调用**恰一次**，须为每个 OUT 通道显式 ``pending.stage(channel, value)``。
      约定只读 Store（取 request 变量），本包不代其门控、不校验其算法。
    - ``committer.commit(outputs)``：第 5 步被调用**恰一次**，收到门控后
      输出的**独立副本**（修改它不影响本拍结果与下一拍）。
    """

    def __init__(self, task: Task, layout: RuntimeLayout, executor: Executor,
                 output_policy: Any, committer: Any):
        if not isinstance(task, Task):
            raise ScanConfigError("ScanEngine 需要 Task，实为 %r" % (task,))
        if not isinstance(layout, RuntimeLayout):
            raise ScanConfigError(
                "ScanEngine 需要 RuntimeLayout，实为 %r" % (layout,))
        if not isinstance(executor, Executor):
            raise ScanConfigError(
                "ScanEngine 需要 Executor，实为 %r" % (executor,))
        # 注入件必须绑定同一 Task/RuntimeLayout——否则 IR、Store 与 io_map
        # 会分属两套状态，五步顺序即便"看起来"跑通也无意义。
        if executor.task is not task:
            raise ScanConfigError("Executor 绑定的 Task 与引擎不是同一对象")
        if executor.layout is not layout:
            raise ScanConfigError("Executor 绑定的 RuntimeLayout 与引擎不是同一对象")
        if not callable(getattr(output_policy, "stage_outputs", None)):
            raise ScanConfigError("输出策略端口缺少可调用的 stage_outputs()")
        if not callable(getattr(committer, "commit", None)):
            raise ScanConfigError("提交端口缺少可调用的 commit()")

        self.task = task
        self.layout = layout
        self.store = layout.store
        self.executor = executor
        self._policy = output_policy
        self._committer = committer
        # 本拍待提交映像：每拍进入时清空（干净 pending），异常路径亦清空，
        # 不留半拍残留。
        self._pending = OutputPending(self.store, task.io_map)
        self._out_channels = frozenset(self._pending.channels())
        # prev 纪律：创建时取初始只读快照；此后**只有**第 5 步提交成功才替换。
        self._prev = make_prev_snapshot(self.store)
        self._lock = threading.Lock()

    @property
    def cycle_ms(self) -> int:
        """本任务固定扫描周期（R6：dt 的唯一来源，非墙钟）。"""
        return self.task.cycle_ms

    @property
    def prev(self) -> StoreSnapshot:
        """当前 ``prev``：上一拍**成功提交后**的只读快照（冷启动为初始快照）。"""
        return self._prev

    def scan(self, samples: Mapping) -> ScanResult:
        """执行确定性一拍（ENGINE_SCAN_SPEC §3 五步）。

        成功返回 ``ScanResult``；任一步失败时异常原样传播，且保证：提交端口
        未被调用或已明确失败、``prev`` 不前移、pending 不留残留——本拍**不**
        产生伪造的安全输出（§4.3 由外层 runner 负责）。
        """
        # 重入即失败关闭：非重入锁 + 非阻塞获取，递归（同线程）与并发（跨
        # 线程）都拿不到锁，两拍不会交错。
        if not self._lock.acquire(blocking=False):
            raise ScanReentryError(
                "同一 ScanEngine 的 scan() 不可重入（递归或并发）：本拍拒绝")
        try:
            # 1) 输入映像一次性锁存（两阶段原子：失败则 Store 无部分更新，
            #    且后续三段均不执行）
            inputs = latch_inputs(self.store, self.task.io_map, samples)

            # 2)+3) 按 Task.programs 显式列表顺序执行可执行 IR。prev 始终是
            #    上一拍成功提交后的快照，本拍新值绝不冒充上一拍。
            self._pending.clear()
            self.executor.execute_programs(self._prev)

            # 4) 输出门控：只能由注入策略端口显式 stage。引擎**不**把 Store 的
            #    OUT/request 变量直接复制成物理输出——request→物理的通路必须
            #    经过策略端口。
            self._policy.stage_outputs(self._pending, self.store, inputs,
                                       self._prev)
            staged = self._pending.staged()
            self._check_channels(staged)

            # 5) 一次性集中提交：恰调用一次，传独立快照（提交方改它污染不到
            #    内部 pending / 本拍结果 / 下一拍）。
            self._committer.commit(dict(staged))

            # 提交成功后才更新 prev（prev 纪律的唯一前移点）。
            self._prev = make_prev_snapshot(self.store)
            return ScanResult(inputs, staged, self._prev)
        finally:
            # 成功路径：staged 已取副本，清空不影响结果。
            # 异常路径：清掉半拍残留，下一拍从干净 pending 开始。
            self._pending.clear()
            self._lock.release()

    def _check_channels(self, staged: Mapping) -> None:
        """进入提交前拒绝缺失或额外输出通道（零 OUT 通道任务合法：空集）。"""
        got = frozenset(staged.keys())
        missing = self._out_channels - got
        extra = got - self._out_channels
        if missing:
            raise OutputStagingError(
                "输出策略未 stage 全部 OUT 通道，缺失：%s（本拍拒绝提交）"
                % sorted(missing))
        if extra:
            raise OutputStagingError(
                "输出策略 stage 了 io_map 未声明的通道：%s（本拍拒绝提交）"
                % sorted(extra))
