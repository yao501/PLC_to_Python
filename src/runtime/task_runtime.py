"""单任务运行栈纵向装配入口（WP-20260730-049；阶段 1 E2E）。

本模块只补一条既有缺口：把已审核的生产组件——正式启动装配
:func:`build_runtime`、安全状态服务、``OutputPolicy`` 门控、``CommitSupervisor``、
默认 shadow ``CommitPort``、五步 ``ScanEngine``、外层安全 ``OuterScanRunner`` 与
确定性 ``SoftwareCycleMonitor``——按固定顺序连成**同一对象图**，提供一个受支持
的**单任务纵向装配入口** :func:`build_task_runtime` 与冻结值对象
:class:`TaskRuntimeAssembly`。

**诚实边界（不实现、不伪称）**：

- 本入口仍是阶段 1 **Python 内部 / 测试装配入口**，不是最终 ST/CFC 用户编程
  入口，也不是工程文件解析器；调用方仍在 Python 内存中构造正式 IR ``Task``。
- 只**复用**既有生产组件、只形成**同一对象图**：不建立第二套 Store、策略、
  提交监督或安全状态；参数校验、Store/Executor 构建、门控算法、提交回执、
  shadow 抑制、watchdog 事件语义**全部**由既有各层承担，本模块不新增语义。
- 默认物理写**继续关闭**：工厂只构造 ``CommitPort(supervisor)`` 的默认 shadow
  路径（自动 ``WriteGate`` / write-disable），唯一放开写路径仍是既有运行器显式、
  可审计的 ``set_write_enabled(True)``。
- 不实现真实调度线程、sleep、后台轮询、startup inhibit 计时 / ``system_ready``
  自动释放、外部参数源、HAL / 真实 I/O、可信反馈、多任务或持久化。
- ``initial_safety is None`` 时用本包冻结的冷启动失败关闭快照 :data:`COLD_START_SAFETY`：
  仅表示“外部 ready / 通信 / 安全 / 联锁尚未建立，内部扫描与 watchdog 尚无已知
  故障”，使全部输出走安全值；它**不**生成真实信号、**不**自动释放。

Python 侧行为**不构成**与目标 CODESYS / PLC 语义、HAL、真实驱动、硬件 watchdog
或现场安全回路一致的证据（阶段 6 对拍 / 阶段 7 HAL）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.runtime.commit_supervisor import CommitSupervisor
from src.runtime.engine import ScanEngine
from src.runtime.monitor import SoftwareCycleMonitor
from src.runtime.numeric import NumericMode
from src.runtime.output_policy import (
    OutputPolicyService,
    SafetySnapshot,
    SafetyStateService,
)
from src.runtime.parameters import RuntimeAssembly, build_runtime
from src.runtime.scan_runner import CommitPort, OuterScanRunner


#: 冷启动失败关闭安全快照（``initial_safety is None`` 时采用）。
#:
#: ``system_ready / output_enable / comm_ok / safety_ok / interlock_ok`` 全假：
#: 外部就绪 / 通信 / 安全 / 联锁尚未建立，故门控命中强制 safe（``safety_trip``），
#: 所有输出一步落 ``safe_value``；``scan_ok / watchdog_ok`` 为真：内部扫描与
#: watchdog 尚无**已知故障**（非“已确认健康”，更非自动释放）。冻结不可变，
#: ``SafetyStateService`` 每拍 ``read()`` 到整包一致视图。
COLD_START_SAFETY: SafetySnapshot = SafetySnapshot(
    system_ready=False,
    output_enable=False,
    comm_ok=False,
    safety_ok=False,
    interlock_ok=False,
    scan_ok=True,
    watchdog_ok=True,
)


@dataclass(frozen=True)
class TaskRuntimeAssembly:
    """单任务运行栈装配结果（冻结值对象；只读暴露同一对象图的各生产组件）。

    只在全部装配阶段成功后由 :func:`build_task_runtime` 一次性构造返回；任一阶段
    失败时调用方**得不到**本对象（不暴露半构造运行时）。``runtime`` 是既有
    :class:`~src.runtime.parameters.RuntimeAssembly`；``task / layout / store /
    executor / startup_inhibit_ms / warnings`` 均为**不复制状态**的便捷转发属性
    （与 ``runtime`` 指向同一对象），不建立第二套状态。
    """

    runtime: RuntimeAssembly
    safety_state: SafetyStateService
    output_policy: OutputPolicyService
    commit_supervisor: CommitSupervisor
    commit_port: CommitPort
    engine: ScanEngine
    runner: OuterScanRunner
    monitor: SoftwareCycleMonitor

    # ---- 不复制状态的便捷转发属性（与 runtime 同一对象，非副本） ----

    @property
    def task(self):
        """本次装配的正式 IR ``Task``（= ``runtime.task``）。"""
        return self.runtime.task

    @property
    def layout(self):
        """运行时布局 ``RuntimeLayout``（= ``runtime.layout``）。"""
        return self.runtime.layout

    @property
    def store(self):
        """运行时 ``Store``（= ``runtime.store`` = ``layout.store``）。"""
        return self.runtime.store

    @property
    def executor(self):
        """执行器 ``Executor``（= ``runtime.executor``）。"""
        return self.runtime.executor

    @property
    def startup_inhibit_ms(self) -> int:
        """已校验的启动稳定窗口下限（= ``runtime.startup_inhibit_ms``；只做配置
        校验，不驱动任何计时 / 释放）。"""
        return self.runtime.startup_inhibit_ms

    @property
    def warnings(self) -> tuple:
        """启动装配收集的结构化告警（= ``runtime.warnings``）。"""
        return self.runtime.warnings


def build_task_runtime(task, registry, *, driver, watchdog_timeout_ms: int,
                       dependencies: Optional[Mapping[str, Any]] = None,
                       numeric_mode: Optional[NumericMode] = None,
                       startup_inhibit_ms: Optional[int] = None,
                       initial_safety: Optional[SafetySnapshot] = None,
                       clock_ns=time.monotonic_ns) -> TaskRuntimeAssembly:
    """把既有生产组件按固定顺序连成同一对象图并返回 :class:`TaskRuntimeAssembly`。

    对象图（每一件都是既有生产组件，不新增语义）::

        build_runtime → SafetyStateService → OutputPolicyService
          → CommitSupervisor → CommitPort（默认 WriteGate / shadow）
          → ScanEngine → OuterScanRunner → SoftwareCycleMonitor

    参数：

    - ``task`` / ``registry``：正式 IR ``Task`` 与 L2 组件 ``Registry``，原样交给
      既有 :func:`build_runtime` 复验并构建 Store/Executor；
    - ``driver``：底层逐批确认回执驱动，只交给 ``CommitSupervisor`` 持有，**装配
      期绝不调用其 ``commit()``**；
    - ``watchdog_timeout_ms``：显式必填，原样交给既有 ``SoftwareCycleMonitor``
      作 ``timeout_ms`` 校验 / 使用（非法由 monitor 稳定拒绝）；
    - ``dependencies`` / ``numeric_mode`` / ``startup_inhibit_ms``：原样透传
      :func:`build_runtime`（各自由既有层校验）；
    - ``initial_safety``：``None`` 时采用冻结冷启动快照 :data:`COLD_START_SAFETY`；
      否则**原样**交给既有 ``SafetyStateService`` 校验并持有（不逐字段真值转换、
      不宽松猜测）；
    - ``clock_ns``：整数纳秒时钟，原样注入既有 ``SoftwareCycleMonitor``。

    ``Task.cycle_ms`` 原样作为 monitor ``cycle_ms``。默认物理写关闭（shadow）：
    唯一放开写路径仍是 ``runner.set_write_enabled(True)``。

    失败关闭：任一装配阶段由既有层按其**稳定异常类型**失败时，异常原样传播，
    **不返回** ``TaskRuntimeAssembly``、**不调用** ``driver.commit()``、**不修改**
    传入的 ``task`` / ``registry`` / ``dependencies``，也不写全局注册表或缓存。
    """
    # 1) 正式启动装配：纯校验 + 构建 Store/Executor（失败即抛既有
    #    StartupValidationError，不构建后续对象图，不触碰 driver）。
    runtime = build_runtime(task, registry, dependencies=dependencies,
                            numeric_mode=numeric_mode,
                            startup_inhibit_ms=startup_inhibit_ms)

    # 2) 安全状态服务：默认冷启动失败关闭快照；显式快照原样交既有服务校验 / 持有。
    initial = COLD_START_SAFETY if initial_safety is None else initial_safety
    safety_state = SafetyStateService(initial)

    # 3) 输出门控服务：绑定同一 Store / io_map / 安全状态（非法策略由既有层拒绝）。
    output_policy = OutputPolicyService(runtime.store, task.io_map, safety_state)

    # 4) 提交监督器：绑定同一策略；只持有 driver，本步不调用其 commit()。
    commit_supervisor = CommitSupervisor(driver, output_policy)

    # 5) 提交端口：默认 shadow（自动 WriteGate / write-disable），禁止 legacy 直写。
    commit_port = CommitPort(commit_supervisor)

    # 6) 五步扫描引擎：同一 task / layout / executor / 策略 / 端口。
    engine = ScanEngine(task, runtime.layout, runtime.executor,
                        output_policy, commit_port)

    # 7) 外层安全运行器：采用端口自带 shadow 门（省略 shadow_gate）。
    runner = OuterScanRunner(engine, output_policy, commit_port)

    # 8) 软件周期监视器：cycle_ms 来自 Task.cycle_ms，timeout / clock 原样注入。
    monitor = SoftwareCycleMonitor(cycle_ms=task.cycle_ms,
                                   timeout_ms=watchdog_timeout_ms,
                                   clock_ns=clock_ns)

    return TaskRuntimeAssembly(
        runtime=runtime,
        safety_state=safety_state,
        output_policy=output_policy,
        commit_supervisor=commit_supervisor,
        commit_port=commit_port,
        engine=engine,
        runner=runner,
        monitor=monitor,
    )


__all__ = [
    "COLD_START_SAFETY",
    "TaskRuntimeAssembly",
    "build_task_runtime",
]
