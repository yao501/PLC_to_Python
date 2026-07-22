"""外层安全扫描运行器 + 扫描/看门狗故障安全提交（WP-20260720-008；
`docs/ENGINE_SCAN_SPEC.md §4.3/§4.4`、`.cursor/rules/00a-runtime-contract.mdc`、
`04-platform-runtime.mdc`）。

职责边界（单一职责：**只是外层编排与失败分类**）：本模块在既有
``ScanEngine`` **外层**加一层安全恢复入口，落实 ``ENGINE_SCAN_SPEC §4.3``
所要求的"安全值提交不依赖本拍扫描逻辑是否活着"，并为独立监视层将来注入的
软件 watchdog 超时信号提供同一条安全提交路径。

—— 三个公开构件 ——

1. ``CommitPort``（运行器与引擎**共享的同一提交端口**）：仅在委托底层
   ``committer.commit`` 前把 ``attempts`` +1，作为区分 §4.3 ``scan_fault``
   （**提交前**扫描失败）与 §4.4 ``commit_fault``（**提交调用**失败）的
   **可审计阶段证据**。不复制任何策略/类型/限速表，不改写提交语义。

2. ``OuterScanRunner``：``scan_cycle(samples)`` 正常拍**原样复用**注入的
   ``ScanEngine.scan``（一次业务扫描 + 一次提交）；捕获到**提交尝试证据为 0**
   的异常时归类为 ``scan_fault``，绕过可能已损坏的 request，经策略专用
   ``stage_safe_image`` 原子生成全通道安全映像并经**同一** ``CommitPort``
   提交**恰一次**，随后以结构化 ``ScanFaultSafeCommit`` 上报原始扫描异常，
   **不吞异常、不冒充本拍成功**。``trigger_watchdog()`` 是显式、可注入的软件
   watchdog 超时事件入口，跳过业务 IR，复用同一安全 staging + 单次提交路径。

3. ``ScanFaultSafeCommit`` / ``WatchdogSafeCommit``（结构化故障信号）：携带
   ``safe_image`` / ``safe_commit_succeeded`` / ``original_exception`` /
   ``fallback_exception`` / ``failed_stage``（并保留 ``commit_exception`` 兼容
   属性），覆盖安全恢复链任一阶段失败，供上层审计。

—— 失败语义（§4.3/§4.4 规范性）——

- ``scan_fault``（提交尚未尝试即异常）：锁存 ``scan_ok=False`` → 绕过 request →
  全通道 ``safe_value`` 安全映像 → 同一端口提交一次 → **raise**
  ``ScanFaultSafeCommit``（``safe_commit_succeeded=True``，链接原始扫描异常）。
- ``commit_fault``（提交端口已被调用且自身抛错，§4.4）：**不**再发起第二次安全
  提交，原异常**原样上抛**（本包只用提交尝试证据判定，绝不靠异常文本/异常类
  猜测，也不宽泛 ``except`` 后无条件再提交）。
- 外层安全恢复链（锁存 / staging / 提交 / 确认）**任一阶段**失败：结构化异常
  **同时保留**原始扫描异常与该阶段 ``fallback_exception``（``failed_stage`` 标记
  落点），本包内**不自动重试**，绝不让普通异常漏出。锁存 / staging / 提交阶段失败
  时安全提交未生效，``safe_commit_succeeded=False`` 且**未前移策略历史**，不冒充
  安全落值成功；**确认阶段（``"confirm"``）失败时物理安全提交已成功**
  （``safe_commit_succeeded=True``），但策略历史前移失败属可审计失配，由
  ``failed_stage="confirm"`` + ``fallback_exception`` 明示、需上层对账，绝不静默
  （策略历史只在提交成功后经**一次性令牌** ``confirm_safe_image`` 前移）。
- ``watchdog``（显式事件）：跳过业务 IR，锁存 ``watchdog_ok=False`` → 同一安全
  staging + 单次提交 → raise ``WatchdogSafeCommit``。
- 安全故障标志（``scan_ok`` / ``watchdog_ok=False``）**不在下一拍被隐式自动
  清除**：本运行器只写入、绝不自动复位；恢复必须由上层显式替换/确认安全状态
  （避免瞬时故障被悄悄掩盖）。

—— 明确不实现（诚实边界，均属后续独立工作包）——

真实周期计时 / ``sleep`` / 后台线程 / 周期调度 / 抖动统计 / 超时测量 / 硬件
watchdog（何时产生 watchdog 事件由阶段 7 独立 runner/monitor 决定，本包只做
**确定性的信号消费**）、shadow mode、``last_physical_committed``、真实驱动 /
HAL、``commit_fault`` / ``channel_fault`` 重试与复位（§4.4 后续）、可信设备反馈、
通知系统、L2 adapter 注册表。以上输出安全制度为**项目工程约定、非 CODESYS
官方语义**，且尚未经真机验证；Python 侧行为**不构成**与目标 PLC/CODESYS、真实
驱动、硬件 watchdog 或现场安全回路一致的证据。
"""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Mapping, Optional

from src.runtime.commit_supervisor import CommitSupervisor
from src.runtime.engine import ScanEngine, ScanReentryError, ScanResult
from src.runtime.output_policy import OutputPolicyService
from src.runtime.process_image import OutputPending


# ---------------------------------------------------------------------------
# 专用异常 / 结构化故障信号
# ---------------------------------------------------------------------------

class ScanRunnerError(Exception):
    """外层扫描运行器错误基类。"""


class ScanRunnerConfigError(ScanRunnerError):
    """运行器装配错误：注入件类型不符，或未与引擎共享同一策略 / 提交端口。

    抛出即表示运行器**未成功装配**，不得进入扫描。
    """


class ScanRunnerReentryError(ScanRunnerError):
    """同一运行器的并发/递归 ``scan_cycle`` / ``trigger_watchdog``：失败关闭。

    与 ``ScanEngine`` 同理，锁在原拍返回后即释放——本拍拒绝而非永久锁死。
    """


class SafeCommitSignal(ScanRunnerError):
    """外层安全提交的**结构化故障信号**（不吞原始异常、不冒充本拍成功）。

    覆盖安全恢复链（锁存故障证据 → staging 安全映像 → 单次提交）**任一阶段**的
    失败：无论失败发生在锁存、staging 还是提交，都以本结构化信号上报，绝不让普通
    异常漏出而丢失原始扫描异常与"未安全提交"证据（§4.3）。

    属性：

    - ``cause``：``"scan_fault"`` / ``"watchdog"``；
    - ``safe_image``：本次安全映像独立副本（``channel -> safe_value``；staging
      完成前即失败则为 ``None``）；
    - ``safe_commit_succeeded``：安全提交端口是否成功调用一次（``failed_stage`` 为
      ``"confirm"`` 时为 ``True``——物理安全提交已成功，仅后续历史确认失败）；
    - ``original_exception``：``scan_fault`` 为原始扫描异常，``watchdog`` 为
      ``None``；
    - ``fallback_exception``：安全恢复链失败阶段的具体异常（成功则 ``None``）；
    - ``failed_stage``：失败阶段标记 ``"latch_fault"`` / ``"stage_safe_image"`` /
      ``"commit"`` / ``"confirm"``（成功则 ``None``），供审计定位；其中
      ``"confirm"`` 表示**物理安全提交成功、但策略历史前移失败**的可审计失配，
      需上层显式对账（不静默、不漏出普通异常）；
    - ``commit_exception``：**向后兼容**只读属性——仅当失败阶段为 ``"commit"`` 时
      返回 ``fallback_exception``，否则 ``None``。
    """
    cause: str = "unknown"

    def __init__(self, *, safe_image: Optional[Mapping],
                 safe_commit_succeeded: bool,
                 original_exception: Optional[BaseException] = None,
                 fallback_exception: Optional[BaseException] = None,
                 failed_stage: Optional[str] = None):
        self.safe_image = dict(safe_image) if safe_image is not None else None
        self.safe_commit_succeeded = bool(safe_commit_succeeded)
        self.original_exception = original_exception
        self.fallback_exception = fallback_exception
        self.failed_stage = failed_stage
        super().__init__(
            "%s：safe_commit_succeeded=%s（original=%r，failed_stage=%s，"
            "fallback_error=%r）"
            % (self.cause, self.safe_commit_succeeded, original_exception,
               failed_stage, fallback_exception))

    @property
    def commit_exception(self) -> Optional[BaseException]:
        """向后兼容：仅当安全提交（``commit`` 阶段）自身失败时返回其异常。"""
        return self.fallback_exception if self.failed_stage == "commit" else None


class ScanFaultSafeCommit(SafeCommitSignal):
    """§4.3 扫描异常在提交前发生 → 已（尝试）落全通道安全映像。"""
    cause = "scan_fault"


class WatchdogSafeCommit(SafeCommitSignal):
    """§4.3/§4.4 显式软件 watchdog 事件 → 已（尝试）落全通道安全映像。"""
    cause = "watchdog"


# ---------------------------------------------------------------------------
# 共享提交端口（提交尝试证据）
# ---------------------------------------------------------------------------

class CommitPort:
    """运行器与引擎**共享的同一提交端口**：记录本拍是否已尝试调用底层提交。

    ``attempts`` 是区分 §4.3 ``scan_fault``（提交前扫描失败）与 §4.4
    ``commit_fault``（提交调用失败）的**可审计阶段证据**：运行器每拍进入前置 0；
    引擎第 5 步（或运行器安全提交）调用 ``commit`` 即 +1。**先记尝试证据再委托**
    ——即便底层 ``commit`` 抛错，``attempts`` 也已 +1，运行器据此判定"提交已尝试"。

    仅计数后透传给同一底层 ``committer``，异常原样上抛；**不复制任何策略 / 类型 /
    限速表**，不改写提交语义。
    """

    def __init__(self, inner: Any):
        if not callable(getattr(inner, "commit", None)):
            raise ScanRunnerConfigError(
                "CommitPort 需要带 commit() 的底层提交端口，实为 %r" % (inner,))
        self._inner = inner
        self.attempts = 0

    @property
    def inner(self) -> Any:
        """本端口委托的底层提交端口（供 runner 只读校验共享，如 CommitSupervisor）。"""
        return self._inner

    def reset(self) -> None:
        """本拍开始前清零提交尝试证据（由运行器在自身锁内调用）。"""
        self.attempts = 0

    def commit(self, outputs: Any) -> Any:
        self.attempts += 1
        return self._inner.commit(outputs)


# ---------------------------------------------------------------------------
# 外层安全扫描运行器
# ---------------------------------------------------------------------------

class OuterScanRunner:
    """§4.3/§4.4 外层安全扫描运行器（正常拍复用 ``ScanEngine``，故障拍安全落值）。

    ::

        port = CommitPort(real_committer)
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)
        result = runner.scan_cycle({"DI0": True})   # 正常拍：== engine.scan
        # 扫描/看门狗故障 → raise ScanFaultSafeCommit / WatchdogSafeCommit

    装配期强约束：运行器持有的 ``OutputPolicyService`` 与 ``CommitPort`` 必须是
    **引擎注入的同一实例**——否则安全提交走的不是正常路径的同一端口 / 同一策略
    状态，双重状态会漂移。为此只读校验 ``engine`` 的私有注入引用（不修改引擎）。
    """

    def __init__(self, engine: ScanEngine, policy: OutputPolicyService,
                 commit_port: CommitPort):
        if not isinstance(engine, ScanEngine):
            raise ScanRunnerConfigError(
                "OuterScanRunner 需要 ScanEngine，实为 %r" % (engine,))
        if not isinstance(policy, OutputPolicyService):
            raise ScanRunnerConfigError(
                "OuterScanRunner 需要 OutputPolicyService，实为 %r" % (policy,))
        if not isinstance(commit_port, CommitPort):
            raise ScanRunnerConfigError(
                "OuterScanRunner 需要 CommitPort（携带可审计提交尝试证据），"
                "实为 %r" % (commit_port,))
        # 与引擎共享同一策略与提交端口（只读校验引擎注入引用，不改引擎）。
        if getattr(engine, "_policy", None) is not policy:
            raise ScanRunnerConfigError(
                "运行器的 OutputPolicyService 必须是引擎注入的同一实例"
                "（否则安全提交与正常门控走两套策略状态）")
        if getattr(engine, "_committer", None) is not commit_port:
            raise ScanRunnerConfigError(
                "运行器的 CommitPort 必须是引擎注入的同一提交端口"
                "（否则正常提交与安全提交走两条端口）")
        if not callable(getattr(policy, "stage_safe_image", None)):
            raise ScanRunnerConfigError(
                "OutputPolicyService 缺少安全映像入口 stage_safe_image()")
        # 若提交端口委托的是 CommitSupervisor，它必须绑定引擎/运行器**同一**策略——
        # 否则正常提交、scan_fault 与 watchdog 安全提交会分属两套逐通道故障状态
        # （WP-20260721-009 §4“不得出现第二套平行故障状态”）。CommitPort 是可选层，
        # 非 CommitSupervisor 的底层端口保持既有 WP-007/008 装配不变。
        inner = getattr(commit_port, "inner", None)
        if isinstance(inner, CommitSupervisor) and inner.policy is not policy:
            raise ScanRunnerConfigError(
                "CommitPort 委托的 CommitSupervisor 必须绑定引擎/运行器同一 "
                "OutputPolicyService（否则正常/安全提交走两套逐通道故障状态）")
        self._engine = engine
        self._policy = policy
        self._port = commit_port
        # 复用策略注入的同一安全状态服务写入 scan_ok/watchdog_ok 证据。
        self._safety = policy.safety_state
        # 运行器级非重入锁：scan_cycle 与 trigger_watchdog 共用，两拍不交错。
        self._lock = threading.Lock()

    # ---- 正常拍（复用 ScanEngine）+ 扫描异常安全提交（§4.3） ----

    def scan_cycle(self, samples: Mapping) -> ScanResult:
        """执行一个外层扫描拍。

        正常路径：原样复用 ``engine.scan(samples)``（一次业务扫描 + 一次提交），
        返回其 ``ScanResult``，行为与直接调用引擎完全等价。

        故障路径（§4.3）：仅当**提交尚未尝试**（``CommitPort.attempts == 0``）时
        捕获到的异常才归类为 ``scan_fault`` → 落全通道安全映像并 raise
        ``ScanFaultSafeCommit``；若异常发生在**提交调用之后**（``attempts >= 1``）
        则属 §4.4 ``commit_fault``，原异常原样上抛、**不**追加第二次提交。
        """
        if not self._lock.acquire(blocking=False):
            raise ScanRunnerReentryError(
                "同一 OuterScanRunner 的 scan_cycle() 不可重入（递归或并发）：本拍拒绝")
        try:
            self._port.reset()
            try:
                return self._engine.scan(samples)
            except ScanReentryError:
                # 引擎重入是并发契约违约，不是扫描逻辑故障：此刻可能有在途扫描，
                # 安全落值会与之竞争双提交。原样上抛，绝不安全提交。
                raise
            except Exception as scan_exc:
                # 分类只依据可审计的提交尝试证据，绝不看异常文本 / 异常类：
                if self._port.attempts >= 1:
                    # 提交端口已被调用（并抛错）→ §4.4 commit_fault：本包不再追加
                    # 安全提交，原异常原样上抛（不误报为 scan_fault 已安全落值）。
                    raise
                # attempts == 0 → 提交尚未尝试 → §4.3 scan_fault：绕过可能已损坏
                # 的 request，落全通道安全映像并经同一端口提交一次。
                self._safe_commit_or_raise("scan_fault", scan_exc)
        finally:
            self._lock.release()

    # ---- 最小软件 watchdog 信号响应（§4.3/§4.4） ----

    def trigger_watchdog(self) -> None:
        """显式、可注入的软件 watchdog 超时事件入口（确定性信号消费）。

        跳过本拍业务 IR（不调用 ``engine.scan``，故 ``engine.prev`` 与业务 Store
        不因本事件前移），锁存 ``watchdog_ok=False``，复用与 ``scan_fault`` 完全
        相同的全通道安全 staging + 单次提交路径，随后 raise ``WatchdogSafeCommit``。

        本入口**不**测量时间、**不** sleep、**不**起线程——事件何时产生由阶段 7
        独立 monitor 决定。安全标志不在下一拍被隐式清除。
        """
        if not self._lock.acquire(blocking=False):
            raise ScanRunnerReentryError(
                "同一 OuterScanRunner 的 trigger_watchdog() 不可重入（递归或并发）：本拍拒绝")
        try:
            self._port.reset()
            self._safe_commit_or_raise("watchdog", None)
        finally:
            self._lock.release()

    # ---- 内部：全通道安全落值 + 单次提交 ----

    def _safe_commit_or_raise(self, cause: str,
                              original_exception: Optional[BaseException]) -> None:
        """锁存故障证据 → 绕过 request 落全通道安全映像 → 同一端口提交一次。

        以**两阶段安全事务**保证"策略历史只与真正提交的安全映像一致"：staging 只
        准备并写入 ``pending``、签发一次性令牌、**不前移策略历史**；仅在提交成功后才
        用**同一令牌** ``confirm_safe_image`` 前移 ``last_effective``。安全恢复链的
        **任一阶段**（锁存 / staging / 提交 / 确认）失败都以结构化
        ``SafeCommitSignal`` 子类 raise，**同时保留原始扫描异常与具体 fallback
        异常**、``failed_stage`` 定位、零重试，绝不让普通异常漏出（§4.3）。锁存 /
        staging / 提交阶段失败时安全提交未生效，``safe_commit_succeeded=False`` 且不
        前移策略历史，不冒充安全落值成功；**确认阶段失败时物理安全提交已成功**
        （``safe_commit_succeeded=True`` 为确凿证据），但历史未前移属可审计的失配，
        由 ``failed_stage="confirm"`` + ``fallback_exception`` 明示，绝不静默。全流程
        成功时携 ``safe_commit_succeeded=True`` 并链接原始扫描异常。
        """
        signal_cls = _SIGNAL_BY_CAUSE[cause]

        # 准备阶段（锁存 + staging）：任一失败 → 结构化信号、未提交、不前移历史。
        # safe_image 在 staging 成功前为 None。
        safe_image: Optional[dict] = None
        try:
            # 1) 锁存安全状态证据（scan_ok / watchdog_ok=False），不自动清除。
            self._latch_fault(cause)
        except Exception as latch_exc:
            raise signal_cls(
                safe_image=None, safe_commit_succeeded=False,
                original_exception=original_exception,
                fallback_exception=latch_exc,
                failed_stage="latch_fault") from latch_exc

        try:
            # 2) 全通道安全映像：用**新建** pending 绕过引擎本拍可能已损坏的
            #    pending，经策略两阶段入口的第一阶段准备（不读 request、不复制第二
            #    套表、**不前移策略历史**），得到一次性确认令牌 ``ticket``。
            pending = OutputPending(self._engine.store, self._engine.task.io_map)
            ticket = self._policy.stage_safe_image(pending)
            safe_image = ticket.image
        except Exception as stage_exc:
            raise signal_cls(
                safe_image=None, safe_commit_succeeded=False,
                original_exception=original_exception,
                fallback_exception=stage_exc,
                failed_stage="stage_safe_image") from stage_exc

        # 3) 经**同一** CommitPort 提交恰一次。
        try:
            self._port.commit(pending.staged())
        except Exception as commit_exc:
            # 安全提交自身失败：保留原始 + 提交两个异常；**未 confirm**，策略历史
            # 不前移，绝不冒充已安全提交；不重试。
            raise signal_cls(
                safe_image=safe_image, safe_commit_succeeded=False,
                original_exception=original_exception,
                fallback_exception=commit_exc,
                failed_stage="commit") from commit_exc

        # 4) 安全提交成功——两阶段事务第二阶段：凭一次性令牌 confirm 前移策略历史到
        #    **真正提交**的安全映像（last_effective == safe_image，置边界基准）；引擎
        #    prev 未前移（本方法不触碰 engine.prev/业务 Store）。物理提交已成功是确凿
        #    证据（safe_commit_succeeded=True），但确认阶段任一失败也**必须结构化上报**
        #    （保留原始 + fallback + 提交成功证据、failed_stage="confirm"），绝不漏出
        #    普通异常或静默留下"物理已提交、历史未前移"的失配（Codex Round 2 反证 1）。
        try:
            self._policy.confirm_safe_image(ticket)
        except Exception as confirm_exc:
            raise signal_cls(
                safe_image=safe_image, safe_commit_succeeded=True,
                original_exception=original_exception,
                fallback_exception=confirm_exc,
                failed_stage="confirm") from confirm_exc

        # 5) 全流程成功：结构化上报原始故障（safe_commit_succeeded=True）。
        signal = signal_cls(safe_image=safe_image, safe_commit_succeeded=True,
                            original_exception=original_exception)
        if original_exception is not None:
            raise signal from original_exception
        raise signal

    def _latch_fault(self, cause: str) -> None:
        """整包替换安全快照，锁存 ``scan_ok`` 或 ``watchdog_ok=False``。"""
        snapshot = self._safety.read()
        if cause == "scan_fault":
            self._safety.replace(replace(snapshot, scan_ok=False))
        else:
            self._safety.replace(replace(snapshot, watchdog_ok=False))


_SIGNAL_BY_CAUSE = {
    "scan_fault": ScanFaultSafeCommit,
    "watchdog": WatchdogSafeCommit,
}
