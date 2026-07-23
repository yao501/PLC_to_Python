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

—— Shadow mode / write disable（WP-20260722-012，§3/§4.1/§4.3/§4.4）——

第一方 shadow / write-disable 由 ``WriteGate``（共享写出开关）+ ``CommitPort``
（物理写抑制点）+ ``OuterScanRunner``（模式切换编排）三者协作实现：

- ``WriteGate``：运行器与提交端口共享的写出开关，**默认写出禁用（shadow）**——新
  装配的生产扫描栈调用方省略参数即处于 shadow，绝不直接写设备。写出状态存于闭包单元
  （非可写属性），裸写入闭包仅经 ``_claim_control()`` **一次性**交给唯一控制运行器并被
  其守卫事务闭包捕获；此后只有该运行器受支持事务（``set_write_enabled``）能在运行器锁内、
  且 shadow→实写先 ``safe_value`` 边界重建后翻转。持有 gate / port / runner 的任意**普通
  可达引用**都无法取得跳过锁与边界重建的裸翻转（不声称抵御 ``__closure__`` / ``gc`` 反射，
  见 ``WriteGate`` 文档串）。
- ``CommitPort``：持有 ``WriteGate`` 时，shadow 下 ``commit`` **不委托底层**（底层
  驱动与 ``CommitSupervisor.commit()`` 调用次数为 0、``last_physical_committed`` /
  回执 / 故障状态不因伪提交变化），且**不记提交尝试证据**（``attempts`` 只计物理
  尝试）。底层提交端口 ``inner`` **只被端口闭包捕获、不作可读属性暴露**（无 ``inner`` /
  ``_inner``），故经 port / runner 的普通可达引用取不到底层 ``CommitSupervisor`` / 物理驱动
  的裸 ``commit``（Codex WP-20260723-015 Round 2 反证 1；仅 ``__closure__`` 反射可取得，
  属不防御的语言级反射）。**省略 ``WriteGate`` 默认装配 shadow 门**：两者都省略即**自动装配一个默认
  ``WriteGate()``（write-disable）**，得到可运行、首拍及后续拍零物理写的 shadow 端口；
  唯有显式、可审计地 ``legacy_unshadowed=True`` 才保留 WP-007/008 既有无门始终物理写
  （二者互斥，不得同时声明）。
- ``OuterScanRunner.set_write_enabled(bool)``：**唯一**显式模式切换 API，只接受
  **exact ``bool``**（拒绝整数 / 真值对象 / ``bool`` 子类，不做 ``bool()`` 静默转换）；
  与 ``scan_cycle`` / ``trigger_watchdog`` / 另一次切换**共用同一非重入锁互斥**，
  并发/递归切换失败关闭、不留半切换态。**shadow→实写**切换：先原子
  ``mark_boundary_reset_all()`` 为全部输出挂起边界重建、**再**启用写出（任一步失败
  保持 shadow）——实写首拍限速基准一律回到 ``safe_value``，绝不用 shadow 的
  ``last_effective`` 或旧 ``last_physical_committed`` 对齐。

Shadow 正常拍仍完整跑五步、按第 5 步“只算不写”推进 ``prev`` 与逻辑
``last_effective``；成功结果以 ``ShadowScanResult`` 诚实标注 ``physically_committed
= False``，不把逻辑 final 冒充“已写设备”。Shadow 故障拍 / watchdog 仍锁存安全状态、
绕过 request、经 ``stage_safe_image`` 算全通道安全映像并**经
``adopt_safe_image_shadow`` 逻辑采用**（**不**调用冒充物理提交成功的
``confirm_safe_image``），结构化 ``SafeCommitSignal`` 同时标注 shadow 逻辑采用 /
写出被抑制 / 无物理提交成功（``safe_commit_succeeded=False``）。

—— 明确不实现（诚实边界，均属后续独立工作包）——

真实周期计时 / ``sleep`` / 后台线程 / 周期调度 / 抖动统计 / 超时测量 / 硬件
watchdog（何时产生 watchdog 事件由阶段 7 独立 runner/monitor 决定，本包只做
**确定性的信号消费**）、真实可信设备反馈 / HAL（§4.1 阶段 7；本包 shadow→实写首拍
只走无反馈的 ``safe_value`` 分支）、真实驱动 / 协议 I/O、``last_physical_committed``
作反馈基准、``commit_fault`` / ``channel_fault`` 重试与复位公共语义（属
``CommitSupervisor``，本包不改）、shadow 趋势库 / 对拍 UI、自动放开写、现场安全证明、
通知系统、L2 adapter 注册表。以上输出安全 / shadow 制度为**项目工程约定、非
CODESYS 官方语义**，且尚未经真机验证；Python 侧行为**不构成**与目标 PLC/CODESYS、
真实驱动、硬件 watchdog 或现场安全回路一致的证据。
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

    Shadow / write-disable 附加字段（WP-20260722-012；非 shadow 默认全 ``False``）：

    - ``shadow``：本次安全恢复发生在 shadow / write-disable 下（物理写被抑制）；
    - ``write_suppressed_by_shadow``：物理写出被 shadow 抑制（未触碰驱动 /
      ``CommitSupervisor``）；shadow 下与 ``shadow`` 同真；
    - ``shadow_logic_adopted``：安全映像已作为 shadow 的逻辑 ``last_effective``
      被采用（经 ``adopt_safe_image_shadow``，**非**冒充物理提交成功的
      ``confirm_safe_image``）；``failed_stage="shadow_adopt"`` 时为 ``False``。

    **shadow 下 ``safe_commit_succeeded`` 恒为 ``False``**（根本无物理提交），
    “安全映像已算 / 已逻辑采用 / 写出被抑制 / 无物理提交成功”由上述三个附加字段
    与 ``safe_image`` 联合诚实表达，绝不以 ``safe_commit_succeeded=True`` 冒充现场落值。
    """
    cause: str = "unknown"

    def __init__(self, *, safe_image: Optional[Mapping],
                 safe_commit_succeeded: bool,
                 original_exception: Optional[BaseException] = None,
                 fallback_exception: Optional[BaseException] = None,
                 failed_stage: Optional[str] = None,
                 shadow: bool = False,
                 write_suppressed_by_shadow: bool = False,
                 shadow_logic_adopted: bool = False):
        self.safe_image = dict(safe_image) if safe_image is not None else None
        self.safe_commit_succeeded = bool(safe_commit_succeeded)
        self.original_exception = original_exception
        self.fallback_exception = fallback_exception
        self.failed_stage = failed_stage
        self.shadow = bool(shadow)
        self.write_suppressed_by_shadow = bool(write_suppressed_by_shadow)
        self.shadow_logic_adopted = bool(shadow_logic_adopted)
        super().__init__(
            "%s：safe_commit_succeeded=%s（original=%r，failed_stage=%s，"
            "fallback_error=%r，shadow=%s，write_suppressed_by_shadow=%s，"
            "shadow_logic_adopted=%s）"
            % (self.cause, self.safe_commit_succeeded, original_exception,
               failed_stage, fallback_exception, self.shadow,
               self.write_suppressed_by_shadow, self.shadow_logic_adopted))

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
# Shadow / write-disable 共享写出开关与 shadow 观察窗
# ---------------------------------------------------------------------------

class WriteGate:
    """运行器与 ``CommitPort`` **共享的写出开关**（shadow / write-disable，§3/§4.3）。

    **默认写出禁用（shadow）**：``WriteGate()`` 省略参数即 ``writes_enabled=False``
    ——新装配的生产扫描栈不能因调用方省略参数而直接写设备。持有本开关的 ``CommitPort``
    在 shadow 下 ``commit`` **不委托底层驱动 / ``CommitSupervisor``**，从机制上保证
    “底层提交调用次数为 0”。

    **写出状态存于闭包、实例冻结、无可写门属性**：``writes_enabled`` 存于 ``__init__``
    的闭包单元（``enabled`` nonlocal），只有只读闭包 ``_read`` 能观察它，裸写入闭包
    ``_write`` **不挂在实例上**，仅经 ``_claim_control()`` **一次性**交给唯一控制
    ``OuterScanRunner``；此后同一 gate 再次领取失败关闭（杜绝多头翻转）。承载只读闭包与
    一次性领取闭包的 ``__slots__``（``_read_enabled`` / ``_claim_writer``）在构造后即
    **冻结**：本类重写 ``__setattr__`` / ``__delattr__``，对**任何**普通属性赋值 / 删除
    一律抛 ``ScanRunnerConfigError``（构造期改经 ``object.__setattr__`` 写入）。因此持有本
    ``WriteGate`` 引用的任意调用方（含经 ``CommitPort.write_gate`` 取得引用者）：既无可写
    的 ``writes_enabled`` / 门状态属性，也**无法把 ``_read_enabled`` 替换成恒真闭包或删除
    它**，更无 ``_set_writes`` 之类 mutator；装配后 ``_claim_control()`` 又已失效——**只能
    只读观察**。

    诚实边界（Codex WP-20260723-014 Round 1 反证 1 / WP-20260723-015 Round 1 必须返修 1）：
    本封装保证**普通属性赋值 / 删除 / 方法引用**（``gate._read_enabled = ...``、
    ``del gate._read_enabled``、``gate.writes_enabled = True`` 等）都无法替换门状态或绕过
    运行器受支持事务直接开写。**未防御且不声称防御**的是语言级反射——``object.__setattr__`` /
    槽描述符 ``__set__`` 直呼 / ``func.__closure__`` / ``gc``：这些在纯 Python(CPython) 无法
    根除，本项目**不作**“不可伪造 / 不可篡改”的过度声明。运行器领取到的裸写入闭包**只被
    运行器的守卫事务闭包捕获**（不作为可直接调用的裸属性暴露），故经运行器 / gate / port 的
    任何普通可达引用触达的唯一变更入口都必然在运行器锁内、且 shadow→实写时先做
    ``safe_value`` 边界重建。只读 ``writes_enabled`` 的裸布尔读在 GIL 下原子、不撕裂；翻转
    只改该闭包单元，绝不触碰任何提交 / 故障 / LPC 状态。
    """
    __slots__ = ("_read_enabled", "_claim_writer")

    def __init__(self, *, writes_enabled: bool = False):
        if type(writes_enabled) is not bool:
            raise ScanRunnerConfigError(
                "WriteGate.writes_enabled 只接受 exact bool，实为 %r" % (writes_enabled,))
        # 写出状态存于闭包单元（**不是可写实例属性**）：只读经 ``_read`` 观察，写入经
        # ``_write``——``_write`` 不挂实例、仅经一次性 ``_claim_control`` 交给运行器。
        enabled = writes_enabled
        claimed = False

        def _read() -> bool:
            return enabled

        def _write(value: bool) -> None:
            nonlocal enabled
            enabled = value

        def _claim():
            nonlocal claimed
            if claimed:
                raise ScanRunnerConfigError(
                    "WriteGate 写出翻转控制权已被占用：同一 WriteGate 不可被多个运行器控制")
            claimed = True
            return _write

        # 经 ``object.__setattr__`` 绕过本类冻结的 ``__setattr__`` 写入承载槽——构造后这两个
        # 槽即不可经普通属性赋值 / 删除替换（见 ``__setattr__`` / ``__delattr__``）。
        object.__setattr__(self, "_read_enabled", _read)
        object.__setattr__(self, "_claim_writer", _claim)

    def __setattr__(self, name: str, value: Any) -> None:
        # 冻结：门状态存于闭包、承载槽只在构造期经 ``object.__setattr__`` 写入。任何普通
        # 属性赋值（含试图把 ``_read_enabled`` 换成恒真闭包）一律拒绝，杜绝旁路开写。
        raise ScanRunnerConfigError(
            "WriteGate 已冻结：写出状态与控制闭包不可经属性赋值替换（试图写 %r）" % (name,))

    def __delattr__(self, name: str) -> None:
        raise ScanRunnerConfigError(
            "WriteGate 已冻结：写出状态与控制闭包不可经属性删除移除（试图删 %r）" % (name,))

    @property
    def writes_enabled(self) -> bool:
        """当前是否启用物理写出（``True`` 实写 / ``False`` shadow）——只读观察。"""
        return self._read_enabled()

    def _claim_control(self):
        """**一次性**把裸写入闭包交给唯一控制者（其 ``OuterScanRunner``）。

        第二次领取失败关闭（``ScanRunnerConfigError``）——同一 ``WriteGate`` 不允许两个
        运行器都取得翻转权，杜绝“多头切换绕过边界重建”。运行器在装配期全部校验通过后
        才领取（失败构造不消费能力）；装配完成后本方法即失效，持有 gate 的外部调用方
        再调用只会撞到“已占用”而拿不到写入能力。返回的裸写入闭包由运行器封进守卫事务，
        绝不作为可直接调用的裸属性暴露。"""
        return self._claim_writer()


class ShadowScanResult:
    """Shadow 正常拍的只读观察窗：**逻辑已完整执行、``prev`` 已推进，但无物理写出**。

    诚实区分“逻辑 final”与“物理提交”——``physically_committed=False`` /
    ``shadow=True``，且**只暴露** ``logical_outputs()`` 而非 ``outputs()``，绝不把
    shadow 的逻辑 final 描述成“已写设备”。``inputs`` / ``prev`` / ``logical_outputs()``
    均转发内部 ``ScanResult`` 的隔离副本，外部修改不污染运行时。
    """
    __slots__ = ("_inner",)

    def __init__(self, inner: ScanResult):
        self._inner = inner

    @property
    def shadow(self) -> bool:
        return True

    @property
    def physically_committed(self) -> bool:
        return False

    @property
    def inputs(self):
        """本拍第 1 步锁存的输入快照（只读）。"""
        return self._inner.inputs

    @property
    def prev(self):
        """本拍第 5 步“只算不写”后推进的 ``prev`` 快照（只读）。"""
        return self._inner.prev

    def logical_outputs(self) -> dict:
        """本拍门控后的**逻辑 final**（``channel -> value`` 独立副本）——**未写设备**。"""
        return self._inner.outputs()


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

    **Shadow / write-disable（WP-20260722-012）**：注入 ``write_gate``
    （``WriteGate``）即 shadow-capable。持有且 shadow（``writes_enabled=False``）时，
    ``commit`` **不委托底层**（底层驱动与 ``CommitSupervisor.commit()`` 调用次数为 0，
    ``last_physical_committed`` / 回执 / 故障状态不因伪提交变化）、返回 ``None``，且
    **不记提交尝试证据**（``attempts`` 只计**物理**尝试，shadow 下为 0）。

    **省略配置默认 shadow（Codex WP-20260723-014 Round 1 必须返修 2）**：**省略**
    ``write_gate`` **不再**被解释为“授权无门始终物理写”——那会让新装配的生产扫描栈
    因调用方省略参数而直接写设备。两者都省略时**自动装配一个默认 ``WriteGate()``
    （write-disable）**，得到可运行、首拍及后续拍零物理写的 shadow 端口（经
    ``port.write_gate`` 只读暴露，供运行器采用同一门受控切换）。唯有**显式、可审计地**
    ``legacy_unshadowed=True`` 才保留 WP-007/008 既有始终物理写、每次 +1 的行为。
    ``write_gate`` 与 ``legacy_unshadowed`` 互斥（shadow-capable 装配由 ``WriteGate``
    控制写出，不得再声明 legacy 直写）。

    **底层提交端口不暴露 + 端口冻结（Codex WP-20260723-015 Round 1/Round 2 必须返修 1）**：
    底层提交端口 ``inner`` 是**物理写能力的持有者**——若作可读属性暴露（旧 ``inner`` property /
    ``_inner`` 槽），``port.inner.commit(...)`` 乃至 ``port.inner._driver.commit(...)`` 即可**不经门**
    直接物理写（Round 2 反证 1：普通可读属性即取得底层 ``CommitSupervisor`` 及其驱动的可调用
    ``commit``）。故本类**不再把 ``inner`` 作任何可读属性 / property 暴露**：``inner`` 只被构造期闭包
    捕获——``_commit_through``（**受门判定**的委托，等价公开 ``commit``）与 ``_assert_binding``
    （只读身份校验，绝不返回 ``inner``）；经 port / runner 的任意普通可达属性都取不到底层
    ``CommitSupervisor`` / 物理驱动或绕过门的裸 ``commit``。同时写出门引用 ``_write_gate`` 是物理写
    抑制判定依据，也绝不能被替换 / 删除（否则 ``port._write_gate = None`` 即取消抑制）；为此本类
    使用 ``__slots__`` 并重写 ``__setattr__`` / ``__delattr__``：普通属性赋值 / 删除一律抛
    ``ScanRunnerConfigError``，物理提交尝试计数 ``attempts`` 存于闭包单元、只读暴露。诚实边界同
    ``WriteGate``：不防御 ``object.__setattr__`` / 描述符 / ``__closure__`` / ``gc`` 等语言级反射
    （``inner`` 仍可经 ``func.__closure__`` 反射取得，本项目**不作**“不可取得”的过度声明）。
    """
    __slots__ = ("_write_gate", "_commit_through", "_attempts_reader",
                 "_reset_attempts", "_assert_binding")

    def __init__(self, inner: Any, write_gate: Optional[WriteGate] = None, *,
                 legacy_unshadowed: bool = False):
        if not callable(getattr(inner, "commit", None)):
            raise ScanRunnerConfigError(
                "CommitPort 需要带 commit() 的底层提交端口，实为 %r" % (inner,))
        if type(legacy_unshadowed) is not bool:
            raise ScanRunnerConfigError(
                "CommitPort.legacy_unshadowed 只接受 exact bool，实为 %r"
                % (legacy_unshadowed,))
        if write_gate is not None:
            if legacy_unshadowed:
                raise ScanRunnerConfigError(
                    "CommitPort 的 write_gate 与 legacy_unshadowed 互斥："
                    "shadow-capable 装配由 WriteGate 控制写出，不得再声明 legacy 直写")
            if not isinstance(write_gate, WriteGate):
                raise ScanRunnerConfigError(
                    "CommitPort 的 write_gate 须为 WriteGate 或 None，实为 %r"
                    % (write_gate,))
        elif legacy_unshadowed:
            # 显式、可审计 opt-in：保留 WP-007/008 既有无门始终物理写（write_gate 保持
            # None，commit 每次 +1 并透传底层）。
            pass
        else:
            # 两者都省略：**自动装配默认 shadow 门**——绝不把“省略门”解释为授权无门直写
            # （Codex WP-20260723-014 Round 1 必须返修 2）。生成的端口可运行、默认
            # write-disable，首拍及后续拍零物理写；运行器省略 shadow_gate 时采用本门。
            write_gate = WriteGate()

        # 底层提交端口 ``inner`` **只被下面几个闭包捕获**，绝不作为可读实例属性 / property
        # 暴露——否则 ``port.inner`` / ``port._inner`` 即可取得底层 ``CommitSupervisor`` 及其
        # ``_driver`` 的可调用 ``commit`` 而不经门物理写（Codex WP-20260723-015 Round 2 反证 1）。
        # 物理提交尝试计数 ``attempts`` 亦存于闭包单元、只读暴露。
        gate = write_gate
        attempts = 0

        def _commit_through(outputs: Any) -> Any:
            # **唯一**能触达底层 ``inner.commit`` 的入口，且**始终**先做门判定 + 计数：
            # shadow（门禁用）抑制物理写、返回 ``None``、不计尝试；实写才 ``attempts`` +1 并
            # 透传底层。等价于本类公开 ``commit()``，**不构成绕过门的新能力**——门态在拥有同一
            # ``WriteGate`` 的运行器锁内切换，本次 commit 与该切换经运行器同一锁互斥，读到的
            # 门态稳定不撕裂。
            nonlocal attempts
            if gate is not None and not gate.writes_enabled:
                return None
            attempts += 1
            return inner.commit(outputs)

        def _read_attempts() -> int:
            return attempts

        def _reset_attempts() -> None:
            nonlocal attempts
            attempts = 0

        def _assert_binding(bound_policy: Any) -> None:
            # 身份/绑定校验：底层若为 ``CommitSupervisor`` 必须绑定引擎/运行器**同一**
            # ``OutputPolicyService``（否则正常/安全提交走两套逐通道故障状态）。**只**在
            # 不一致时抛错，绝不返回 ``inner``——身份校验不泄露底层提交能力（Codex
            # WP-20260723-015 Round 2 反证 1）。
            if isinstance(inner, CommitSupervisor) and inner.policy is not bound_policy:
                raise ScanRunnerConfigError(
                    "CommitPort 委托的 CommitSupervisor 必须绑定引擎/运行器同一 "
                    "OutputPolicyService（否则正常/安全提交走两套逐通道故障状态）")

        # 经 ``object.__setattr__`` 绕过本类冻结的 ``__setattr__`` 写入承载槽——构造后这些槽
        # 即不可经普通属性赋值 / 删除替换。四个闭包中：``_commit_through`` 受门判定（等价公开
        # commit），``_attempts_reader`` / ``_reset_attempts`` 只读/清零计数，``_assert_binding``
        # 只做身份校验——**没有任何一个返回底层提交器 / 物理驱动或提供绕过门的裸写能力**
        # （``inner`` 仅存于闭包 ``__closure__``，属不防御的语言级反射）。
        object.__setattr__(self, "_write_gate", gate)
        object.__setattr__(self, "_commit_through", _commit_through)
        object.__setattr__(self, "_attempts_reader", _read_attempts)
        object.__setattr__(self, "_reset_attempts", _reset_attempts)
        object.__setattr__(self, "_assert_binding", _assert_binding)

    def __setattr__(self, name: str, value: Any) -> None:
        # 冻结：写出门引用与承载闭包是物理写抑制判定依据，绝不接受外部属性赋值替换
        # （含 ``port._write_gate = None`` 取消抑制）；构造期经 ``object.__setattr__`` 写入。
        raise ScanRunnerConfigError(
            "CommitPort 已冻结：写出门引用与提交闭包不可经属性赋值替换（试图写 %r）" % (name,))

    def __delattr__(self, name: str) -> None:
        raise ScanRunnerConfigError(
            "CommitPort 已冻结：写出门引用与提交闭包不可经属性删除移除（试图删 %r）" % (name,))

    @property
    def write_gate(self) -> Optional[WriteGate]:
        """本端口持有的写出开关（供 runner 只读校验共享同一 ``WriteGate``）。

        经本属性取得的 ``WriteGate`` 引用只能**只读观察** ``writes_enabled``（见 ``WriteGate``；
        不声称抵御反射）。**``CommitPort`` 自身亦已冻结且不再暴露底层端口**：没有 ``inner`` /
        ``_inner`` 属性可取得 ``CommitSupervisor`` 或物理驱动，``_write_gate`` 也不可经普通属性
        赋值换成 ``None`` 或另一门，故无法经端口取消 shadow 抑制或绕过门物理写。
        """
        return self._write_gate

    @property
    def attempts(self) -> int:
        """本拍已发生的**物理**提交尝试次数（shadow 下恒 0）——只读闭包计数。"""
        return self._attempts_reader()

    def reset(self) -> None:
        """本拍开始前清零提交尝试证据（由运行器在自身锁内调用）。"""
        self._reset_attempts()

    def assert_shared_policy(self, policy: Any) -> None:
        """校验底层若为 ``CommitSupervisor`` 则须绑定同一 ``policy``，不一致即抛
        ``ScanRunnerConfigError``。**绝不返回底层提交端口**——供运行器装配期做身份校验而
        不泄露物理提交能力（替代此前会泄露 ``inner`` 的读法，Codex WP-20260723-015
        Round 2 反证 1）。"""
        self._assert_binding(policy)

    def commit(self, outputs: Any) -> Any:
        """经门判定后提交：shadow 抑制物理写（底层零调用、返回 ``None``、不计尝试），实写恰
        透传底层一次并 ``attempts`` +1。**唯一**委托底层的入口，门态由拥有同一 ``WriteGate``
        的运行器在其锁内切换决定，本次 commit 与该切换互斥、门态稳定不撕裂。"""
        return self._commit_through(outputs)


# ---------------------------------------------------------------------------
# 外层安全扫描运行器
# ---------------------------------------------------------------------------

class OuterScanRunner:
    """§4.3/§4.4 外层安全扫描运行器（正常拍复用 ``ScanEngine``，故障拍安全落值）。

    ::

        # 省略配置即默认 shadow（write-disable）：CommitPort 自动装配默认 WriteGate，
        # 运行器采用同一门。首拍及后续拍零物理写，返回 ShadowScanResult。
        port = CommitPort(real_committer)
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)
        shadow = runner.scan_cycle({"DI0": True})   # ShadowScanResult（未写设备）
        runner.set_write_enabled(True)              # 显式退出 shadow，先 safe_value 边界重建
        result = runner.scan_cycle({"DI0": True})   # 实写拍：== engine.scan
        # 扫描/看门狗故障 → raise ScanFaultSafeCommit / WatchdogSafeCommit
        # 若需保留 WP-007/008 既有始终物理写：CommitPort(real_committer, legacy_unshadowed=True)

    装配期强约束：运行器持有的 ``OutputPolicyService`` 与 ``CommitPort`` 必须是
    **引擎注入的同一实例**——否则安全提交走的不是正常路径的同一端口 / 同一策略
    状态，双重状态会漂移。为此只读校验 ``engine`` 的私有注入引用（不修改引擎）。

    **Shadow / write-disable（WP-20260722-012；WP-20260723-014 收口默认 shadow）**：
    ``shadow_gate`` 若显式注入必须与 ``commit_port`` 持有的**同一** ``WriteGate`` 实例
    （只读校验共享，否则运行器切换的门与端口读取的门是两套）；**省略 ``shadow_gate``
    时自动采用 ``commit_port.write_gate``**——因此 ``CommitPort(driver)`` +
    ``OuterScanRunner(engine, policy, port)`` 全省略即得到可运行、默认 shadow（写出禁用）
    的扫描栈，首拍及后续拍零物理写。采用到门后运行器成为 **shadow-capable**：经
    ``set_write_enabled(bool)`` 显式切换（与 scan/watchdog/另一切换共用本锁互斥）。
    仅当端口也无门（显式 ``legacy_unshadowed=True``）时 ``shadow_gate`` 为 ``None``，即
    **既有 WP-007/008 行为**：始终物理写、无模式切换 API。

    **运行器冻结（Codex WP-20260723-015 Round 1 必须返修 1）**：运行器持有的门引用
    ``_shadow_gate`` 与守卫事务闭包 ``_apply_write_mode`` 亦不可被外部普通属性赋值替换。为此
    本类使用 ``__slots__`` 并重写 ``__setattr__`` / ``__delattr__``（构造期经
    ``object.__setattr__`` 写入）：普通属性赋值 / 删除一律抛 ``ScanRunnerConfigError``。诚实
    边界同 ``WriteGate`` / ``CommitPort``：物理写抑制的最终判定在 ``CommitPort.commit`` 读
    ``CommitPort._write_gate.writes_enabled``，二者均已冻结；不防御语言级反射
    （``object.__setattr__`` / 描述符 / ``__closure__`` / ``gc``）。"""

    __slots__ = ("_engine", "_policy", "_port", "_shadow_gate", "_safety",
                 "_lock", "_apply_write_mode")

    def __init__(self, engine: ScanEngine, policy: OutputPolicyService,
                 commit_port: CommitPort, shadow_gate: Optional[WriteGate] = None):
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
        # 非 CommitSupervisor 的底层端口保持既有 WP-007/008 装配不变。**经端口的身份校验
        # 方法核验，不再读取会泄露底层提交能力的 ``inner``**（Codex WP-20260723-015 Round 2
        # 反证 1：``port.inner`` 曾让普通可达引用取得 CommitSupervisor/驱动的裸 commit）。
        commit_port.assert_shared_policy(policy)
        # Shadow write-gate：运行器控制的门必须与端口读取的门是**同一实例**。省略
        # shadow_gate 时**自动采用端口自带门**（默认 shadow 或显式传入）——使“全省略
        # 装配”成为可运行的 write-disable 栈（Codex WP-20260723-014 Round 1 必须返修 2）；
        # 仅当端口显式 legacy 无门时 shadow_gate 才为 None（既有 WP-007/008 行为）。
        port_gate = getattr(commit_port, "write_gate", None)
        if shadow_gate is not None and not isinstance(shadow_gate, WriteGate):
            raise ScanRunnerConfigError(
                "OuterScanRunner 的 shadow_gate 须为 WriteGate 或 None，实为 %r"
                % (shadow_gate,))
        if shadow_gate is None:
            shadow_gate = port_gate                     # 采用端口自带门（可能仍为 None）
        elif shadow_gate is not port_gate:
            raise ScanRunnerConfigError(
                "运行器显式传入的 shadow_gate 必须是 CommitPort 持有的同一 WriteGate 实例"
                "（否则模式切换与物理写抑制读两套门）")
        if shadow_gate is not None and not callable(
                getattr(policy, "mark_boundary_reset_all", None)):
            raise ScanRunnerConfigError(
                "shadow 装配需要 OutputPolicyService.mark_boundary_reset_all()"
                "（shadow→实写首拍全通道原子边界重建）")
        if shadow_gate is not None and not callable(
                getattr(policy, "adopt_safe_image_shadow", None)):
            raise ScanRunnerConfigError(
                "shadow 装配需要 OutputPolicyService.adopt_safe_image_shadow()"
                "（shadow 故障拍逻辑采用安全映像，不冒充物理提交）")
        # 经 ``object.__setattr__`` 绕过本类冻结的 ``__setattr__`` 写入承载槽（构造后即冻结）。
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_policy", policy)
        object.__setattr__(self, "_port", commit_port)
        object.__setattr__(self, "_shadow_gate", shadow_gate)
        # 复用策略注入的同一安全状态服务写入 scan_ok/watchdog_ok 证据。
        object.__setattr__(self, "_safety", policy.safety_state)
        # 运行器级非重入锁：scan_cycle / trigger_watchdog / set_write_enabled 共用，
        # 三者互斥、两拍与模式切换不交错。
        object.__setattr__(self, "_lock", threading.Lock())
        # 装配期**全部校验通过后**领取 WriteGate 的裸写入闭包，并**立即封进守卫事务闭包**
        # `_apply_write_mode`（见 `_build_write_mode_control`）：裸写入闭包不作为可直接
        # 调用的裸属性暴露，任何经 runner / gate / port 的普通可达引用触达的唯一变更入口
        # 都必然在本运行器锁内、且 shadow→实写先 safe_value 边界重建（Codex
        # WP-20260723-014 Round 1 必须返修 1）。同一 WriteGate 已被其他运行器领取则失败
        # 关闭。未配置门（legacy 无门）时无模式可切换。
        object.__setattr__(self, "_apply_write_mode", None)
        if shadow_gate is not None:
            self._build_write_mode_control(shadow_gate)

    def __setattr__(self, name: str, value: Any) -> None:
        # 冻结：门引用与守卫事务闭包不接受外部属性赋值替换（构造期经 object.__setattr__ 写入）。
        raise ScanRunnerConfigError(
            "OuterScanRunner 已冻结：门引用与守卫事务闭包不可经属性赋值替换（试图写 %r）"
            % (name,))

    def __delattr__(self, name: str) -> None:
        raise ScanRunnerConfigError(
            "OuterScanRunner 已冻结：门引用与守卫事务闭包不可经属性删除移除（试图删 %r）"
            % (name,))

    # ---- Shadow / write-disable 模式（WP-20260722-012） ----

    @property
    def writes_enabled(self) -> bool:
        """只读诊断：物理写出是否启用（``True`` 实写 / ``False`` shadow）。

        未配置 shadow write-gate 的既有装配恒为 ``True``（始终物理写）。裸布尔读在
        GIL 下原子，不撕裂。"""
        gate = self._shadow_gate
        return True if gate is None else gate.writes_enabled

    @property
    def shadow(self) -> bool:
        """只读诊断：是否处于 shadow / write-disable（``not writes_enabled``）。"""
        return not self.writes_enabled

    def _build_write_mode_control(self, gate: WriteGate) -> None:
        """装配期一次性领取 ``gate`` 的裸写入闭包，封装为**只经受支持事务行使**的守卫
        闭包 ``_apply_write_mode``。

        领取到的裸写入闭包 ``writer`` 只被下面的 ``_apply`` 捕获（**不**作为可直接调用的
        裸属性挂在运行器上），故持有 runner / port / gate 的任意**普通可达引用**都取不到
        一个“跳过锁与边界重建的裸翻转”；他们能触达的唯一变更入口 ``_apply`` 必然在运行器
        锁内、并在 shadow→实写时先 ``mark_boundary_reset_all()``。这是“普通引用不可绕过”，
        **非**“抵御 ``__closure__`` / ``gc`` 反射”——后者是 Python 语言层面无法根除的，本
        项目不作此过度声明。同一 ``gate`` 已被其他运行器领取则领取即失败关闭。
        """
        writer = gate._claim_control()          # 一次性；同一 gate 二次领取失败关闭
        lock = self._lock
        policy = self._policy

        def _apply(enabled: bool) -> None:
            # exact bool（拒绝整数 / 真值对象 / bool 子类；不做 bool() 静默转换）。
            if type(enabled) is not bool:
                raise ScanRunnerConfigError(
                    "set_write_enabled 只接受 exact bool，拒绝 %r（不做 bool() 静默转换）"
                    % (enabled,))
            # 与 scan_cycle / trigger_watchdog / 另一次切换共用同一非重入锁互斥。
            if not lock.acquire(blocking=False):
                raise ScanRunnerReentryError(
                    "模式切换与 scan_cycle/trigger_watchdog/另一次切换互斥（递归或并发）："
                    "本次拒绝")
            try:
                if enabled == gate.writes_enabled:
                    return                              # 幂等：无状态变化
                if enabled:
                    # shadow→实写：**先**原子挂起全通道边界（失败即保持 shadow、异常
                    # 上抛），**再**翻转写出——实写首拍限速基准一律回到 safe_value。
                    policy.mark_boundary_reset_all()
                    writer(True)
                else:
                    # 实写→shadow：仅翻转门，下一拍起 CommitPort 抑制物理写。
                    writer(False)
            finally:
                lock.release()

        object.__setattr__(self, "_apply_write_mode", _apply)

    def set_write_enabled(self, enabled: bool) -> None:
        """**唯一**显式模式切换：``True`` 退出 shadow（启用实写）/ ``False`` 进入
        shadow（禁用写出）。

        只接受 **exact ``bool``**（``type(enabled) is bool``）——拒绝整数 / 真值对象 /
        ``bool`` 子类等含混输入，**不做 ``bool(value)`` 静默转换**。与 ``scan_cycle`` /
        ``trigger_watchdog`` / 另一次切换共用**同一非重入锁**：并发/递归切换失败关闭
        （``ScanRunnerReentryError``），不留半切换态。幂等：目标态与当前态相同即无操作。

        **shadow→实写**（``True`` 且当前 shadow）：**先**原子
        ``policy.mark_boundary_reset_all()`` 为全部输出挂起边界重建，**再**启用写出；
        ``mark_boundary_reset_all`` 抛错则写出**不**启用、保持 shadow（异常上抛告知
        调用方切换失败）。因此实写首拍限速基准一律回到 ``safe_value``，绝不用 shadow 的
        ``last_effective`` 或旧 ``last_physical_committed`` 对齐；预先存在的
        ``commit_fault`` / ``channel_fault`` 不被本切换触碰（属 ``CommitSupervisor``）。
        **实写→shadow**（``False`` 且当前实写）：仅翻转门，下一拍起 ``CommitPort`` 抑制
        物理写、``CommitSupervisor`` / LPC / 回执 / 故障状态不再变化。

        本方法只是**薄包装**：实际切换由装配期封装的守卫事务闭包 ``_apply_write_mode``
        执行——即使调用方经普通可达引用直接调用该闭包，也一样走锁 + 边界重建的受支持
        路径，无从绕过（Codex WP-20260723-014 Round 1 必须返修 1）。
        """
        apply = self._apply_write_mode
        if apply is None:
            raise ScanRunnerConfigError(
                "本运行器未配置 shadow write-gate，无写出模式可切换")
        apply(enabled)

    def _in_shadow(self) -> bool:
        """当前是否 shadow（在运行器锁内读，门态稳定）。"""
        gate = self._shadow_gate
        return gate is not None and not gate.writes_enabled

    # ---- 正常拍（复用 ScanEngine）+ 扫描异常安全提交（§4.3） ----

    def scan_cycle(self, samples: Mapping) -> ScanResult:
        """执行一个外层扫描拍。

        正常路径：原样复用 ``engine.scan(samples)``（一次业务扫描 + 一次提交），
        实写模式返回其 ``ScanResult``，行为与直接调用引擎完全等价。**Shadow 下**五步
        仍完整执行、第 5 步“只算不写”（``CommitPort`` 抑制物理提交、``prev`` 与逻辑
        ``last_effective`` 照常推进），但返回 ``ShadowScanResult``（``physically_committed
        = False``），诚实区分逻辑 final 与物理落值。

        故障路径（§4.3）：仅当**提交尚未尝试**（``CommitPort.attempts == 0``）时
        捕获到的异常才归类为 ``scan_fault`` → 落全通道安全映像并 raise
        ``ScanFaultSafeCommit``；若异常发生在**提交调用之后**（``attempts >= 1``）
        则属 §4.4 ``commit_fault``，原异常原样上抛、**不**追加第二次提交。
        （shadow 下物理提交被抑制、``attempts`` 恒 0，故不会误判 commit_fault。）
        """
        if not self._lock.acquire(blocking=False):
            raise ScanRunnerReentryError(
                "同一 OuterScanRunner 的 scan_cycle() 不可重入（递归或并发）：本拍拒绝")
        try:
            self._port.reset()
            try:
                result = self._engine.scan(samples)
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
                # 的 request，落全通道安全映像并（shadow 下逻辑采用 / 实写下）提交。
                self._safe_commit_or_raise("scan_fault", scan_exc)  # 必抛，不返回
            else:
                # 正常拍成功：shadow 下以 ShadowScanResult 诚实标注“未写设备”，
                # 实写下返回引擎原始 ScanResult（行为与既有装配等价）。
                if self._in_shadow():
                    return ShadowScanResult(result)
                return result
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
        """锁存故障证据 → 绕过 request 落全通道安全映像 →（实写）同一端口提交一次并
        ``confirm`` /（shadow）逻辑采用。

        实写路径以**两阶段安全事务**保证"策略历史只与真正提交的安全映像一致"：staging
        只准备并写入 ``pending``、签发一次性令牌、**不前移策略历史**；仅在提交成功后才
        用**同一令牌** ``confirm_safe_image`` 前移 ``last_effective``。

        **Shadow 路径（WP-20260722-012）**：``CommitPort`` 抑制物理提交（驱动 /
        ``CommitSupervisor`` 零调用），因此**无物理落值**——``safe_commit_succeeded``
        恒 ``False``；改经 ``adopt_safe_image_shadow`` 让安全映像作为 shadow 的逻辑
        ``last_effective`` 连续模拟（**绝不**调用冒充“已物理提交成功”的
        ``confirm_safe_image``）。信号以 ``shadow`` / ``write_suppressed_by_shadow`` /
        ``shadow_logic_adopted`` 三字段联合诚实标注“安全映像已算 / 已逻辑采用 / 写出被
        抑制 / 无物理提交成功”。shadow 逻辑采用失败（``adopt`` 抛错）同样结构化上报
        （``failed_stage="shadow_adopt"``），不漏普通异常。

        两条路径共享：安全恢复链**任一阶段**失败都以结构化 ``SafeCommitSignal`` 子类
        raise，**同时保留原始扫描异常与具体 fallback 异常**、``failed_stage`` 定位、零
        重试，绝不让普通异常漏出（§4.3）；锁存 / staging / 提交 / 采用阶段失败时安全
        落值未生效、策略历史不前移。实写**确认阶段失败时物理安全提交已成功**
        （``safe_commit_succeeded=True`` 为确凿证据），历史未前移属可审计失配，由
        ``failed_stage="confirm"`` + ``fallback_exception`` 明示，绝不静默。
        """
        signal_cls = _SIGNAL_BY_CAUSE[cause]
        # 门态在运行器锁内读一次，全程一致（切换与本拍经同一锁互斥，不并发翻转）。
        shadow = self._in_shadow()

        def _signal(**kw):
            return signal_cls(shadow=shadow,
                              write_suppressed_by_shadow=shadow, **kw)

        # 准备阶段（锁存 + staging）：任一失败 → 结构化信号、未提交、不前移历史。
        # safe_image 在 staging 成功前为 None。
        safe_image: Optional[dict] = None
        try:
            # 1) 锁存安全状态证据（scan_ok / watchdog_ok=False），不自动清除。
            #    shadow 下同样锁存逻辑安全状态（只是物理写被抑制）。
            self._latch_fault(cause)
        except Exception as latch_exc:
            raise _signal(
                safe_image=None, safe_commit_succeeded=False,
                shadow_logic_adopted=False,
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
            raise _signal(
                safe_image=None, safe_commit_succeeded=False,
                shadow_logic_adopted=False,
                original_exception=original_exception,
                fallback_exception=stage_exc,
                failed_stage="stage_safe_image") from stage_exc

        # 3) 经**同一** CommitPort 提交。shadow 下 CommitPort 抑制物理委托（驱动 /
        #    CommitSupervisor 零调用、返回 None、attempts 不增），实写下恰提交一次。
        try:
            self._port.commit(pending.staged())
        except Exception as commit_exc:
            # 安全提交自身失败（仅实写可能到此；shadow 抑制不抛）：保留原始 + 提交
            # 两个异常；**未前移历史**，绝不冒充已安全提交；不重试。
            raise _signal(
                safe_image=safe_image, safe_commit_succeeded=False,
                shadow_logic_adopted=False,
                original_exception=original_exception,
                fallback_exception=commit_exc,
                failed_stage="commit") from commit_exc

        # 4) 历史前移——两条互不冒充的路径（凭同一次 staging 的一次性令牌）：
        if shadow:
            # shadow：**无物理提交**，经 adopt_safe_image_shadow 逻辑采用安全映像为
            #   shadow 的 last_effective（连续模拟）；绝不调用 confirm_safe_image。
            #   采用失败也结构化上报（failed_stage="shadow_adopt"）。
            try:
                self._policy.adopt_safe_image_shadow(ticket)
            except Exception as adopt_exc:
                raise _signal(
                    safe_image=safe_image, safe_commit_succeeded=False,
                    shadow_logic_adopted=False,
                    original_exception=original_exception,
                    fallback_exception=adopt_exc,
                    failed_stage="shadow_adopt") from adopt_exc
            signal = _signal(safe_image=safe_image, safe_commit_succeeded=False,
                             shadow_logic_adopted=True,
                             original_exception=original_exception)
        else:
            # 实写：安全提交成功——两阶段事务第二阶段：凭一次性令牌 confirm 前移策略
            #   历史到**真正提交**的安全映像（last_effective == safe_image，置边界
            #   基准）；引擎 prev 未前移（本方法不触碰 engine.prev/业务 Store）。物理
            #   提交已成功是确凿证据（safe_commit_succeeded=True），但确认阶段任一失败
            #   也**必须结构化上报**（保留原始 + fallback + 提交成功证据、
            #   failed_stage="confirm"），绝不漏出普通异常或静默留下"物理已提交、历史
            #   未前移"的失配（Codex Round 2 反证 1）。
            try:
                self._policy.confirm_safe_image(ticket)
            except Exception as confirm_exc:
                raise _signal(
                    safe_image=safe_image, safe_commit_succeeded=True,
                    shadow_logic_adopted=False,
                    original_exception=original_exception,
                    fallback_exception=confirm_exc,
                    failed_stage="confirm") from confirm_exc
            signal = _signal(safe_image=safe_image, safe_commit_succeeded=True,
                             shadow_logic_adopted=False,
                             original_exception=original_exception)

        # 5) 全流程成功：结构化上报原始故障。
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
