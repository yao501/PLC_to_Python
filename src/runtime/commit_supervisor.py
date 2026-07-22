"""提交监督器：驱动确认提交证据 + 逐通道 commit_fault/channel_fault 状态机
（WP-20260721-009；``docs/ENGINE_SCAN_SPEC.md §4.1/§4.4``、
`.cursor/rules/00a-runtime-contract.mdc`、`04-platform-runtime.mdc`）。

职责边界（单一职责：**提交层的确认证据与逐通道故障隔离**）：本模块位于
``OutputPolicy`` 门控之下、底层驱动之上，实现 §4.4 冻结的项目工程约定——把
每次实际发出的命令与**驱动确认回执**精确绑定，只在回执明确成功时才前移该通道
``last_physical_committed``；逐通道维护瞬时 ``commit_fault`` 与锁存
``channel_fault``，并提供三条件显式复位。正常提交、``scan_fault`` 安全提交与
``watchdog`` 安全提交经**同一个** ``CommitSupervisor`` 与同一底层驱动端口，
**不建第二套平行故障状态**。

—— 装配拓扑（与既有 WP-007/008 端口契约衔接，不改写 ``engine.py``）——

::

    driver = RealDriver()                       # 逐批确认回执驱动（见下）
    supervisor = CommitSupervisor(driver, policy)
    port = CommitPort(supervisor)               # 复用既有可审计提交尝试证据
    engine = ScanEngine(task, layout, executor, policy, port)
    runner = OuterScanRunner(engine, policy, port)

``CommitPort`` 每拍先记提交尝试证据（``attempts+=1``）再委托
``supervisor.commit(outputs)``；引擎第 5 步的正常提交与 ``OuterScanRunner`` 的
安全提交都经这条链，故三条路径共享**同一** supervisor 故障状态与驱动端口。

—— 驱动确认回执契约（诚实边界：契约模拟，非真实物理写入）——

``driver.commit(commands: Mapping[str, value]) -> Mapping[str, value]``：接收本拍
每通道命令值，返回**逐通道确认值**映射。判定为该通道可信成功的**充要条件**：

1. 驱动未抛异常（抛异常 → 本拍全部尝试通道判失败，无法逐通道隔离）；
2. 返回值是 ``Mapping``，且其通道集合与本拍**实际尝试提交集合严格一致**
   （缺失或多余通道 → 整批回执不可信、全部失败关闭）；
3. 该通道确认值 ``严格等于`` 本次发出命令值（``type`` 与相等同时成立）；
4. 该通道确认值满足声明 IEC 结构类型与数值域（复用 ``output_policy`` 同一口径，
   **不复制第二套类型/数值表**）。

**exact 内建标量前置门禁（WP-20260722-011）**：判定条件 3/4 之前，先以**纯类型
身份**（``type(x) is`` 内建类型，不触发任何可被子类重载的 dunder）确认发出命令值
与确认值均为当前支持的 exact 内建标量（``bool`` / ``int`` / ``float`` / ``str``，
与 ``store.check_value_type`` 工程映射同集）且 exact 类型相同；任一为**子类**或非
支持类型即**失败关闭**。这保证恶意标量子类重载的 ``__ge__`` / ``__le__`` /
``__eq__`` 等比较/相等运算不能在结构化逐通道失败证据形成前击穿提交路径。

返回 ``None``、仅“未抛异常”、不完整回执、错值、错类型或越界值一律**不得**被
提升为可信成功证据，相应通道 ``last_physical_committed`` **不前移**。

``last_physical_committed`` 只是**驱动确认已写出的最后命令值**与诊断记录，
**不是传感器确认的设备实际位置**，不得当作可信设备反馈或恢复限速基准
（真实可信反馈 HAL 属阶段 7，本包不实现）。

—— 逐通道故障状态机（§4.4）——

- 任一通道回执失败 → 立即置瞬时 ``commit_fault``，保留该通道旧
  ``last_physical_committed``；其他明确成功通道**独立**更新，不被连带标故障。
- 故障通道（``commit_fault`` 或 ``channel_fault``）从下一拍起**忽略策略层本拍
  ``final``**、改写 ``safe_value``；其他通道继续提交自身业务值（逐通道隔离）。
- 连续失败计数在第 ``commit_fault_retry_n`` 次失败时**精确升级并锁存**
  ``channel_fault``；锁存后不存在静默放弃路径，仍每拍尝试写 ``safe_value``。
- 阈值前安全值写成功 → 清瞬时 ``commit_fault`` 与连续失败计数并恢复该通道
  （下一拍重取业务值）；**已升级的 ``channel_fault`` 绝不因安全值写成功而自动
  清除**。
- 提交层故障**不进入** ``OutputPolicy`` 故障原因集合；策略层照常算 ``final`` 并
  维持 ``last_effective`` 逻辑连续（观测层 ``ScanResult`` 反映策略逻辑值，物理
  实际写入以 ``diagnostics()`` 的 ``last_physical_committed`` 为准）。

—— 三条件显式复位与恢复基准（§4.1/§4.4）——

``channel_fault`` 仅经 ``reset_channel_fault(channel, fault_cause_cleared=True)``
解除，且必须**同时**满足：① 调用方明确确认故障原因已消失；② 锁存后已有合法回执
确认 ``safe_value`` 写成功；③ 本次显式复位调用。未知通道、未锁存、原因未确认消失、
锁存后未确认安全值、重复复位均**结构化拒绝**，不静默忽略。瞬时故障恢复或锁存
复位后的首个正常输出经 ``OutputPolicyService.mark_boundary_reset`` 重建边界基准；
本包无真实 HAL/可信设备反馈，故一律退化为 ``safe_value`` 基准，**不用
``last_physical_committed`` 对齐**。为使复位与"策略计算→提交"边界形成**失败关闭的
串行事务**：复位在解锁时置逐通道守卫，提交时读一次
``policy.boundary_pending()``——只要该通道边界仍挂起（复位后尚无一拍
``stage_outputs`` 在 safe 基准上重算并消费边界），提交层就绝不放行复位前 staging 的
陈旧业务值，改写 ``safe_value``，保证**任何被写出的首个正常输出都已按 ``safe_value``
基准重建**；复位与提交本就经同一 supervisor 锁互斥（复位期间提交、提交期间复位均
失败关闭），二者共同消除复位/提交竞态。

—— 明确不实现（诚实边界，均属后续独立工作包）——

shadow mode、真实 HAL/驱动适配、可信设备传感器反馈、真实周期 monitor、抖动/超时
测量、后台线程、硬件 watchdog、L2 adapter 注册表、HMI/通知/操作员身份认证、事件
持久化或现场安全回路。以上提交/故障制度为**项目工程约定、非 CODESYS 官方语义**，
且尚未经真机验证；Python 侧回执**不构成**与目标 PLC/CODESYS、真实驱动、硬件
watchdog 或现场安全回路一致的证据。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional

from src.runtime.output_policy import OutputPolicyService, _iec_value_error


# ---------------------------------------------------------------------------
# 不可信驱动边界的安全表示（失败诊断格式化永不二次抛错）
# ---------------------------------------------------------------------------

def _safe_repr(obj: Any) -> str:
    """对**来自不可信驱动边界**的任意对象生成永不抛错的诊断表示。

    失败关闭路径构造诊断串时（回执通道键、确认值、驱动异常、非 Mapping 回执、
    回执读取异常等），对象自带的 ``__repr__`` 可能抛异常，从而使本应“失败关闭、
    置 ``commit_fault``、保留旧 ``last_physical_committed``”的安全错误路径反被普通
    异常**二次击穿**（Codex Round 3 反证：异常 ``__repr__`` 的多余通道键与错误
    确认值）。**所有**不可信边界对象的诊断表示必须统一经此入口格式化：``repr``
    抛错时退化为稳定占位串，绝不向上传播表示异常。

    fallback 自身也必须绝不触发不可信对象的字符串协议（Codex Round 1 反证：恶意
    元类令 ``type(obj).__name__`` 返回一个 ``__str__`` 抛普通异常的对象，若 fallback
    再对其做 ``%s`` 插值，则 fallback 本身被二次击穿）。因此：类型名仅在其为 **exact
    ``str``**（``type(type_name) is str``，排除任何 ``str`` 子类/伪造对象，其
    ``__str__`` / ``__format__`` 仍可能抛错）时才嵌入；否则一律退到不含任何不可信
    子串的固定占位串。两条 fallback 分支的字符串构造都不会调用不可信字符串协议。"""
    try:
        return repr(obj)
    except Exception:                              # noqa: BLE001 - 表示永不抛错
        pass
    try:
        type_name = type(obj).__name__
    except Exception:                              # noqa: BLE001 - 连类型名也兜底
        type_name = None
    # 仅接受 exact ``str``：``str`` 子类实例可覆写 ``__str__`` / ``__format__`` 再次
    # 抛错，一律拒绝；exact ``str`` 的 ``%s`` 插值不调用任何可被重写的 dunder，永不抛错。
    if type(type_name) is str:
        return "<%s 对象：repr() 抛异常，诊断已退化为安全占位表示>" % type_name
    return "<对象：repr() 与类型名字符串化均抛异常，诊断已退化为安全占位表示>"


# ---------------------------------------------------------------------------
# 专用异常
# ---------------------------------------------------------------------------

class CommitSupervisorError(Exception):
    """提交监督层错误基类。"""


class CommitSupervisorConfigError(CommitSupervisorError):
    """装配期错误：驱动端口无 ``commit()``、策略非生产 ``OutputPolicyService`` 等。

    抛出即表示监督器**未成功装配**，不得进入提交。
    """


class CommitSupervisorReentryError(CommitSupervisorError):
    """同一监督器的 ``commit`` / ``reset`` / ``diagnostics`` 并发或递归重入：
    失败关闭，不交错两拍。锁在原调用返回后即释放——本次拒绝而非永久锁死。
    """


class PartialCommitError(CommitSupervisorError):
    """本拍**至少一个通道**提交失败（含全部失败）的结构化信号。

    携带本拍**逐通道** ``CommitReceipt``（成功与失败都在内）与失败通道列表，供上层
    审计；**绝不吞掉逐通道成功/失败证据**。正常提交路径下由引擎第 5 步向外传播、
    ``prev`` 不前移、``OuterScanRunner`` 据提交尝试证据归为 §4.4 ``commit_fault``
    并原样上抛（不追加第二次安全提交）；安全提交路径下使 ``OuterScanRunner`` 标记
    ``safe_commit_succeeded=False`` 且不 ``confirm_safe_image``（不污染全通道策略
    历史）。
    """

    def __init__(self, receipts: Mapping[str, "CommitReceipt"],
                 failed_channels, driver_exception: Optional[BaseException] = None):
        self.receipts = dict(receipts)
        self.failed_channels = tuple(failed_channels)
        self.driver_exception = driver_exception
        super().__init__(
            "提交部分/全部失败：failed=%s（driver_error=%s）"
            % (list(self.failed_channels), _safe_repr(driver_exception)))


# ---------------------------------------------------------------------------
# 不可变回执与诊断快照
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommitReceipt:
    """单通道一次提交的**不可变、结构化**回执（命令通道 + IEC 值 + 成功/失败）。

    - ``commanded_value``：本次实际发出命令值（故障通道为被改写的 ``safe_value``）；
    - ``overridden_safe``：本次命令是否因该通道处于 ``commit_fault`` / ``channel_fault``
      而被改写为 ``safe_value``（``True`` 表示忽略了策略层 ``final``）；
    - ``ok``：是否为可信成功回执（四项充要条件全部满足）；
    - ``detail``：失败原因串（成功为 ``""``）。
    """
    channel: str
    iec_type: str
    commanded_value: Any
    overridden_safe: bool
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ChannelCommitStatus:
    """单通道提交监督状态的**不可变**诊断快照（值均为不可变标量/回执）。"""
    channel: str
    commit_fault: bool
    channel_fault: bool
    consecutive_failures: int
    safe_confirmed_after_latch: bool
    last_physical_committed: Any
    last_receipt: Optional[CommitReceipt]


# ---------------------------------------------------------------------------
# 内部可变通道状态
# ---------------------------------------------------------------------------

class _ChannelState:
    """单通道提交监督内部可变状态（仅监督器在自身锁内读写）。"""

    __slots__ = ("commit_fault", "channel_fault", "consecutive_failures",
                 "safe_confirmed_after_latch", "last_physical_committed",
                 "last_receipt")

    def __init__(self):
        self.commit_fault = False
        self.channel_fault = False
        self.consecutive_failures = 0
        self.safe_confirmed_after_latch = False
        self.last_physical_committed = None
        self.last_receipt: Optional[CommitReceipt] = None

    def snapshot(self, channel: str) -> ChannelCommitStatus:
        return ChannelCommitStatus(
            channel=channel,
            commit_fault=self.commit_fault,
            channel_fault=self.channel_fault,
            consecutive_failures=self.consecutive_failures,
            safe_confirmed_after_latch=self.safe_confirmed_after_latch,
            last_physical_committed=self.last_physical_committed,
            last_receipt=self.last_receipt)


# ---------------------------------------------------------------------------
# 失败诊断的稳定键表示（永不抛错）
# ---------------------------------------------------------------------------

def _stable_channel_list(channels) -> list:
    """把任意（可能含**混合/不可比较**类型，甚至 ``__repr__`` 抛异常）通道键集合转成
    **永不抛错**的稳定字符串列表，用于不可信回执/结构错误的失败诊断格式化——按每个键
    的**安全表示** ``_safe_repr`` 排序，不依赖键之间的全序关系，也不依赖键自带 ``repr``
    不抛异常。

    修复 Codex Round 2 反证：回执多余通道键为混合类型（如 ``{"CH", 1}``）时，直接
    ``sorted(...)`` 会抛 ``TypeError``。修复 Codex Round 3 反证：多余通道键的 ``__repr__``
    抛异常时，直接 ``repr(k)`` 会漏出普通异常。二者都会使本应失败关闭的安全错误路径反被
    普通异常二次击穿、通道不置故障。诊断格式化本身必须永不抛错，故一律经 ``_safe_repr``。"""
    return sorted(_safe_repr(k) for k in channels)


# ---------------------------------------------------------------------------
# 不可信驱动回执值的 exact 内建标量信任门禁（先于任何可被子类重载的运算）
# ---------------------------------------------------------------------------

def _exact_scalar_reject(commanded: Any, confirmed: Any) -> Optional[str]:
    """在对回执确认值执行任何可被用户子类**重载**的运算（``_iec_value_error`` 内的
    整数值域比较、REAL 有限性、以及严格相等）**之前**，以**纯类型身份**
    （``type(x) is 内建类型``，绝不触发目标对象任何 ``__eq__`` / ``__ne__`` /
    ``__ge__`` / ``__le__`` / ``__float__`` / ``__index__`` 等 dunder）确认发出命令值
    与回执确认值**均为当前支持的 exact 内建标量**（``bool`` / ``int`` / ``float`` /
    ``str``，与 ``store.check_value_type`` 工程映射同集）**且 exact 类型相同**。

    合法（可安全进入既有 IEC 值域/严格相等判定）返回 ``None``；任一为**子类**、非
    支持类型，或二者 exact 类型不同，则返回**失败原因串**（失败关闭）——恶意标量子类
    （如重载 ``__ge__`` / ``__eq__`` 抛普通异常的 ``int`` 子类）由此在触碰任何比较前
    即被结构化拒绝，绝不在逐通道失败证据形成前击穿提交记账（WP-20260722-011）。

    诊断串只对**类型对象**做 ``_safe_repr``（``repr`` 的是类、走元类协议且由安全表示
    兜底），**绝不调用回执实例的比较/字符串 dunder**，因此恶意子类无法借诊断格式化
    二次击穿失败关闭路径。"""
    vt = type(confirmed)
    if not (vt is bool or vt is int or vt is float or vt is str):
        return ("确认值类型 %s 非受支持的 exact 内建标量（bool/int/float/str）：失败"
                "关闭，且不调用其比较/数值 dunder" % (_safe_repr(vt),))
    ct = type(commanded)
    if not (ct is bool or ct is int or ct is float or ct is str):
        return ("发出命令值类型 %s 非受支持的 exact 内建标量：失败关闭"
                % (_safe_repr(ct),))
    if ct is not vt:
        return ("确认值类型 %s 与发出命令类型 %s 非同一 exact 内建标量（错类型）："
                "失败关闭" % (_safe_repr(vt), _safe_repr(ct)))
    return None


# ---------------------------------------------------------------------------
# 提交监督器
# ---------------------------------------------------------------------------

class CommitSupervisor:
    """驱动确认提交证据 + 逐通道 ``commit_fault`` / ``channel_fault`` 状态机。

    满足 ``CommitPort`` 的内层提交端口契约（暴露 ``commit(outputs)``），因此可被
    ``CommitPort`` 包裹后注入既有 ``ScanEngine`` / ``OuterScanRunner``，让正常提交、
    ``scan_fault`` 与 ``watchdog`` 安全提交共享**同一** supervisor 故障状态。

    通道配置（``iec_type`` / ``safe_value`` / ``commit_fault_retry_n``）从注入的
    ``OutputPolicyService`` 读取（``commit_specs()``），**不复制第二套通道策略表**；
    恢复基准重建经同一策略的 ``mark_boundary_reset()``。
    """

    def __init__(self, driver: Any, policy: OutputPolicyService):
        if not isinstance(policy, OutputPolicyService):
            raise CommitSupervisorConfigError(
                "CommitSupervisor 需要 OutputPolicyService，实为 %s"
                % (_safe_repr(policy),))
        if not callable(getattr(driver, "commit", None)):
            raise CommitSupervisorConfigError(
                "CommitSupervisor 需要带 commit() 的底层驱动端口，实为 %s"
                % (_safe_repr(driver),))
        self._driver = driver
        self._policy = policy
        self._order = list(policy.channels())
        specs = policy.commit_specs()
        self._iec_type: dict = {}
        self._safe_value: dict = {}
        self._retry_n: dict = {}
        for channel in self._order:
            iec_type, safe_value, retry_n = specs[channel]
            self._iec_type[channel] = iec_type
            self._safe_value[channel] = safe_value
            self._retry_n[channel] = retry_n
        self._state: dict = {ch: _ChannelState() for ch in self._order}
        # 显式复位后的"待安全基准重建"守卫（逐通道）：显式复位清锁存后置 True，
        # 只有当**复位之后**一拍 ``stage_outputs`` 在 safe 基准上重算并消费了策略
        # ``boundary_reset``（``policy.boundary_pending()`` 回落 False）时才解除；
        # 在此之前提交遇到的业务值一律视为复位前 staging 的陈旧值、失败关闭改写
        # ``safe_value``（见 ``commit``；修复 Codex Round 1 反证 1 的复位/提交竞态）。
        self._await_safe: dict = {ch: False for ch in self._order}
        self._last_receipts: dict = {}
        # 提交/复位/诊断共用的非重入锁：三者互斥、并发或递归即失败关闭。
        self._lock = threading.Lock()

    # ---- 只读观察 ----

    @property
    def policy(self) -> OutputPolicyService:
        """本监督器绑定的**同一** ``OutputPolicyService``（供 runner 校验共享）。"""
        return self._policy

    def channels(self) -> tuple:
        return tuple(self._order)

    def diagnostics(self) -> dict:
        """每通道提交监督状态的**独立**诊断快照（``channel -> ChannelCommitStatus``）。

        并发/递归重入失败关闭（``CommitSupervisorReentryError``）；返回的映射与快照
        对象均不可反向污染监督器内部状态。
        """
        if not self._lock.acquire(blocking=False):
            raise CommitSupervisorReentryError(
                "同一 CommitSupervisor 的 diagnostics() 不可重入（递归或并发）：本次拒绝")
        try:
            return {ch: self._state[ch].snapshot(ch) for ch in self._order}
        finally:
            self._lock.release()

    def last_commit_receipts(self) -> dict:
        """最近一次 ``commit`` 的逐通道回执**独立副本**（无提交则为空）。"""
        if not self._lock.acquire(blocking=False):
            raise CommitSupervisorReentryError(
                "同一 CommitSupervisor 的 last_commit_receipts() 不可重入：本次拒绝")
        try:
            return dict(self._last_receipts)
        finally:
            self._lock.release()

    # ---- CommitPort 内层提交端口 ----

    def commit(self, outputs: Mapping) -> "CommitOutcome":
        """对本拍全部 OUT 通道执行“确定有效命令 → 驱动确认 → 逐通道状态更新”。

        故障通道命令被改写为 ``safe_value``（忽略策略层 ``final``），健康通道用
        ``outputs`` 中的策略值。逐通道独立判定回执并更新状态：任一通道失败即置瞬时
        ``commit_fault`` 且保留旧 ``last_physical_committed``；第 ``commit_fault_retry_n``
        次连续失败精确锁存 ``channel_fault``；阈值前安全值写成功恢复该通道并经策略
        ``mark_boundary_reset`` 重建限速基准。

        全部通道成功 → 返回 ``CommitOutcome``（引擎/runner 忽略返回值）；任一通道
        失败 → 抛 ``PartialCommitError``（保留逐通道回执），**不追加第二次提交**、
        不吞异常。并发/递归重入失败关闭。
        """
        if not self._lock.acquire(blocking=False):
            raise CommitSupervisorReentryError(
                "同一 CommitSupervisor 的 commit() 不可重入（递归或并发）：本拍拒绝")
        try:
            got = set(outputs)
            expected = set(self._order)
            if got != expected:
                # 结构性错误：门控产物通道集与装配集合不符——失败关闭，不发任何命令。
                raise CommitSupervisorError(
                    "commit 收到的通道集 %s 与装配通道集 %s 不一致（拒绝提交）"
                    % (_stable_channel_list(got), _stable_channel_list(expected)))

            # 复位后守卫读取一次策略边界基准挂起标志（supervisor→policy 锁序，与
            # mark_boundary_reset 一致，无环）。显式复位在**提交之外**发生（复位需
            # 同一 supervisor 锁，绝不与本次 commit 交错），故此处读到的是复位已完成
            # 后的边界状态：若 boundary 仍挂起，说明待提交业务值是复位前 staging 的
            # 陈旧值，本拍失败关闭改写 safe_value（修复复位/提交竞态）。
            boundary = self._policy.boundary_pending()

            # 1) 确定每通道有效命令（故障通道 / 待安全基准重建的复位通道改写 safe_value）。
            commands: dict = {}
            faulted: dict = {}
            for channel in self._order:
                st = self._state[channel]
                awaiting = self._await_safe[channel]
                if awaiting and not boundary[channel]:
                    # 复位**之后**已有一拍 stage_outputs 在 safe 基准上重算并消费了
                    # 边界（boundary 回落 False）→ 守卫解除，放行该 safe 基准业务值。
                    self._await_safe[channel] = False
                    awaiting = False
                # 守卫仍在（复位后尚无新一拍在 safe 基准上重建）时改写 safe_value，
                # 绝不放行复位前 staging 的陈旧业务值。
                needs_safe_boundary = awaiting and boundary[channel]
                force_safe = st.commit_fault or st.channel_fault or needs_safe_boundary
                faulted[channel] = force_safe
                commands[channel] = (
                    self._safe_value[channel] if force_safe else outputs[channel])

            # 2) 单次委托底层驱动（批量），异常即整批无确认。
            driver_exc: Optional[BaseException] = None
            confirmations: Any = None
            try:
                confirmations = self._driver.commit(dict(commands))
            except Exception as exc:               # noqa: BLE001 - 逐通道判失败
                driver_exc = exc

            # 回执通道集/逐项取值必须可靠且与尝试提交集严格一致，否则整批不可信。
            # 回执映射的**迭代、通道集读取与逐项取值**任一抛异常都统一转为结构化失败
            # 证据（整批失败关闭），绝不让普通异常漏出（修复 Codex Round 1 反证 2：
            # 满足 Mapping 外形但 __getitem__/迭代抛错的惰性/代理回执）。
            batch_trusted = True
            batch_detail = ""
            confirmed: dict = {}
            if driver_exc is not None:
                batch_trusted = False
                batch_detail = "驱动抛异常：%s" % (_safe_repr(driver_exc),)
            elif not isinstance(confirmations, Mapping):
                batch_trusted = False
                batch_detail = ("驱动回执非 Mapping（含 None）：%s"
                                % (_safe_repr(confirmations),))
            else:
                try:
                    got_keys = set(confirmations)
                    # 通道集一致时才逐项物化取值；一次性把回执读进普通 dict，之后
                    # 判定不再触碰可能抛错的代理映射。
                    if got_keys == expected:
                        confirmed = {ch: confirmations[ch] for ch in self._order}
                except Exception as read_exc:      # noqa: BLE001 - 读回执即失败关闭
                    batch_trusted = False
                    batch_detail = ("回执迭代/取值异常（失败关闭）：%s"
                                    % (_safe_repr(read_exc),))
                else:
                    if got_keys != expected:
                        batch_trusted = False
                        # 诊断格式化按 repr 排序，绝不因回执含混合/不可比较类型键
                        # 抛 TypeError 而使失败关闭路径失效（Codex Round 2 反证）。
                        batch_detail = (
                            "回执通道集 %s 与尝试提交集 %s 不一致（缺失/多余）"
                            % (_stable_channel_list(got_keys),
                               _stable_channel_list(expected)))

            # 3) 逐通道判定回执 + 更新状态（独立，不连带）。
            receipts: dict = {}
            recovered: list = []
            for channel in self._order:
                commanded = commands[channel]
                ok, detail = self._evaluate(channel, commanded, confirmed,
                                            batch_trusted, batch_detail)
                receipt = CommitReceipt(
                    channel=channel, iec_type=self._iec_type[channel],
                    commanded_value=commanded, overridden_safe=faulted[channel],
                    ok=ok, detail=detail)
                receipts[channel] = receipt
                if self._apply_receipt(channel, receipt):
                    recovered.append(channel)

            # 内部最近回执持有**独立**快照，绝不与返回给调用方的 CommitOutcome /
            # PartialCommitError 共享同一可变字典（修复 Codex Round 1 反证 3 的别名污染）。
            self._last_receipts = dict(receipts)

            # 4) 瞬时恢复通道：策略层重建限速基准（首个正常输出退化为 safe_value 基准）。
            for channel in recovered:
                self._policy.mark_boundary_reset(channel)

            failed = [ch for ch in self._order if not receipts[ch].ok]
            if failed:
                raise PartialCommitError(receipts, failed, driver_exc)
            return CommitOutcome(receipts)
        finally:
            self._lock.release()

    def _evaluate(self, channel: str, commanded: Any, confirmed: Mapping,
                  batch_trusted: bool, batch_detail: str):
        """判定单通道回执是否为可信成功；返回 ``(ok, detail)``。

        ``confirmed`` 是已在 ``commit`` 中**一次性物化**为普通 ``dict`` 的回执
        （通道集已校验一致、取值不再触碰可能抛错的代理映射）；``batch_trusted``
        为假时整批已失败关闭，直接返回 ``batch_detail``。"""
        if not batch_trusted:
            return False, batch_detail
        value = confirmed[channel]
        # exact 内建标量门禁（先于任何可被子类重载的运算）：不可信驱动确认值可能是
        # 重载 __ge__ / __le__ / __eq__ 等 dunder 抛普通异常的标量子类；必须在
        # _iec_value_error 的整数值域比较、REAL 有限性与严格相等判定**之前**，以纯
        # 类型身份确认命令值与确认值均为 exact 内建标量且 exact 类型相同，否则失败
        # 关闭——绝不让子类重载运算在结构化逐通道失败证据形成前击穿提交路径。
        gate_detail = _exact_scalar_reject(commanded, value)
        if gate_detail is not None:
            return False, gate_detail
        err = _iec_value_error(self._iec_type[channel], value)
        if err is not None:
            # 确认值经安全表示入口格式化：不可信确认值的 __repr__ 抛异常时不得使本
            # 失败关闭路径被普通异常二次击穿（Codex Round 3 反证：错误确认值）。
            return False, "确认值 %s %s" % (_safe_repr(value), err)
        if type(value) is not type(commanded) or value != commanded:
            return False, ("确认值 %s 与发出命令 %s 不严格相等（错值/错类型）"
                           % (_safe_repr(value), _safe_repr(commanded)))
        return True, ""

    def _apply_receipt(self, channel: str, receipt: CommitReceipt) -> bool:
        """按回执更新单通道状态；返回该通道本拍是否发生**瞬时恢复**（需重建基准）。"""
        st = self._state[channel]
        st.last_receipt = receipt
        if receipt.ok:
            # 可信成功：前移 last_physical_committed（仅驱动确认值 == 发出命令）。
            st.last_physical_committed = receipt.commanded_value
            if receipt.overridden_safe:
                # 本次是故障通道的 safe_value 写入：安全值写成功 → 一律清除瞬时
                # commit_fault 与连续失败计数（§4.4“期间安全值写成功只清瞬时
                # commit_fault，不自动清除 channel_fault”）。
                st.commit_fault = False
                st.consecutive_failures = 0
                if st.channel_fault:
                    # 已锁存：记录“锁存后已有合法安全回执”供三条件复位；但**绝不**
                    # 因安全值写成功自动解除锁存 channel_fault（仍每拍改写 safe_value）。
                    # 通道仍处 forced-safe，故不触发边界基准重建。
                    st.safe_confirmed_after_latch = True
                    return False
                # 仅瞬时 commit_fault：恢复该通道（下一拍重取业务值）→ 重建限速基准。
                return True
            # 健康通道业务值写成功：无额外状态变化。
            return False
        # 失败：置瞬时 commit_fault、保留旧 last_physical_committed、连续计数升级。
        st.commit_fault = True
        st.consecutive_failures += 1
        # 最近一次写入未确认安全 → 复位前置条件失效（更严格更安全）。
        st.safe_confirmed_after_latch = False
        if not st.channel_fault and st.consecutive_failures >= self._retry_n[channel]:
            # 第 commit_fault_retry_n 次连续失败：精确升级并锁存。
            st.channel_fault = True
        return False

    # ---- 三条件显式复位 ----

    def reset_channel_fault(self, channel: str, *,
                            fault_cause_cleared: bool) -> None:
        """显式解除某通道锁存 ``channel_fault``（三条件缺一不可）。

        必须同时满足：① ``fault_cause_cleared=True``（调用方明确确认原因已消失）；
        ② 锁存后已有合法回执确认 ``safe_value`` 写成功
        （``safe_confirmed_after_latch``）；③ 本次显式复位调用。未知通道、未锁存、
        原因未确认消失、锁存后未确认安全值、重复复位均**结构化拒绝**。成功后经策略
        ``mark_boundary_reset`` 重建限速基准（首个正常输出退化为 ``safe_value`` 基准，
        不用 ``last_physical_committed`` 对齐）。并发/递归重入失败关闭。
        """
        if not self._lock.acquire(blocking=False):
            raise CommitSupervisorReentryError(
                "同一 CommitSupervisor 的 reset_channel_fault() 不可重入：本次拒绝")
        try:
            if channel not in self._state:
                raise CommitSupervisorError(
                    "reset_channel_fault 未知通道 '%s'" % channel)
            st = self._state[channel]
            if not st.channel_fault:
                raise CommitSupervisorError(
                    "通道 '%s' 未锁存 channel_fault，拒绝复位（含重复复位）" % channel)
            if not fault_cause_cleared:
                raise CommitSupervisorError(
                    "通道 '%s' 复位缺前置：调用方未确认故障原因已消失" % channel)
            if not st.safe_confirmed_after_latch:
                raise CommitSupervisorError(
                    "通道 '%s' 复位缺前置：锁存后无合法回执确认 safe_value 写成功"
                    % channel)
            # 三条件满足：解除锁存并恢复该通道。置"待安全基准重建"守卫——在
            # **复位之后**的一拍 stage_outputs 于 safe 基准重算并消费策略边界之前，
            # 提交层绝不放行复位前 staging 的陈旧业务值（失败关闭改写 safe_value），
            # 保证任何被写出的首个正常输出都已按 safe_value 基准重建（修复复位/提交
            # 竞态）。守卫由 commit 在观察到 boundary 回落 False 时解除。
            st.channel_fault = False
            st.commit_fault = False
            st.consecutive_failures = 0
            st.safe_confirmed_after_latch = False
            self._await_safe[channel] = True
            self._policy.mark_boundary_reset(channel)
        finally:
            self._lock.release()


@dataclass(frozen=True)
class CommitOutcome:
    """一次**全通道成功**提交的结果（引擎/runner 忽略返回值，供直接调用方/测试）。

    ``receipts`` 在构造时被复制进**独立且不可变**的 ``MappingProxyType`` 快照：
    调用方无法通过它反向污染监督器内部最近回执（``last_commit_receipts()``）——
    修复 Codex Round 1 反证 3 的成功结果别名共享。
    """
    receipts: Mapping[str, CommitReceipt]

    def __post_init__(self):
        object.__setattr__(
            self, "receipts", MappingProxyType(dict(self.receipts)))

    @property
    def all_succeeded(self) -> bool:
        return all(r.ok for r in self.receipts.values())
