"""正式运行时输出安全策略核心 + 原子安全状态快照（WP-20260716-007；
`docs/ENGINE_SCAN_SPEC.md §4/§4.1/§4.2`、`.cursor/rules/00a-runtime-contract.mdc`、
`04-platform-runtime.mdc`）。

本模块产出**直接插入现有 ``ScanEngine`` 第 4 步端口**的生产 ``OutputPolicy``
门控核心：``OutputPolicyService.stage_outputs(pending, store, inputs, prev)``
恰满足引擎注入契约（引擎每拍调用一次，须为每个 OUT 通道显式
``pending.stage(channel, value)``）。**不另造平行扫描器**。

—— 三个公开构件 ——

1. ``OutputPolicy``（frozen 配置值对象）：每个 OUT 映射的分类型门控配置——
   ``var / iec_type / safe_value / rate_limit / commit_fault_retry_n`` 与六类
   ``on_*`` 故障策略。``on_safety_trip / on_scan_fault / on_watchdog`` **固定
   ``"safe"``**（配 ``"hold"`` 在构造期即拒绝）；``safe_value`` 与 request、
   最终值必须严格符合声明 IEC 类型，**不做隐式转换**。整数类型限速必须是
   声明类型内可精确表达的 ``int``（拒绝 float，避免用浮点舍入猜测语义）。

2. ``SafetySnapshot``（frozen 不可变快照）+ ``SafetyStateService``（线程安全、
   **整包替换**的最小安全状态服务）：快照覆盖 ``system_ready / output_enable
   / comm_ok / safety_ok / interlock_ok / scan_ok / watchdog_ok``。策略服务
   每拍只 ``read()`` 一次完整快照，**禁止逐字段读取**形成撕裂状态。

3. ``OutputPolicyService``：装配期对齐 IOMap/Store/策略；每拍对全部 OUT 通道
   **先完整校验和计算、再统一 stage 并提交内部 ``last_effective``**——任一通道
   失败不留部分内部状态；同一服务并发调用**失败关闭**（不交错两拍）。

—— 故障决策（§4 规范性）——

多原因并发按安全优先级取最严者::

    safety_trip ≥ watchdog ≥ scan_fault > comm_loss > startup_not_ready > operator_disable

信号 → 原因映射：``safety_ok``/``interlock_ok`` 任一为假 → ``safety_trip``；
``watchdog_ok`` 假 → ``watchdog``；``scan_ok`` 假 → ``scan_fault``；``comm_ok``
假 → ``comm_loss``；``system_ready`` 假 → ``startup_not_ready``；``output_enable``
假 → ``operator_disable``。任一**强制 safe** 原因命中即一步落 ``safe_value``
（不受限速约束，§4.2）；可配置原因命中 ``"hold"`` 时取该通道 ``last_effective``，
冷启动无历史时退化为 ``safe_value``。

—— Shadow / write-disable 边界支持（WP-20260722-012，仅策略层入口，不含编排）——

自 WP-20260722-012 起本模块**额外**提供两个供 shadow / write-disable 编排消费的
纯策略层入口，**不**在本模块引入 shadow 模式状态、写出开关或物理抑制（那些属
``scan_runner`` 的 ``OuterScanRunner`` / ``WriteGate``）：

- ``mark_boundary_reset_all()``：在 shadow→实写切换时**原子**为全部输出挂起边界
  重建，使实写首拍限速基准回到 ``safe_value``（§4.1“恢复后首拍从安全基准限速”），
  绝不用 shadow 期间照常前移的 ``last_effective`` 或任何 ``last_physical_committed``
  对齐；
- ``adopt_safe_image_shadow(ticket)``：**shadow 下逻辑采用**安全映像——与
  ``confirm_safe_image`` 走**同一令牌校验与历史前移**，但语义上**明确区分**：它
  **不**代表“安全映像已物理提交成功”（shadow 下根本无物理提交），仅让逻辑
  ``last_effective`` 连续模拟。冒充物理提交成功的是 ``confirm_safe_image``，本入口
  绝不与之混用。

—— 明确不实现（诚实边界，均属后续独立工作包，不在本包"顺手完善"）——

watchdog / scan-fault 信号**生成**、扫描异常外层安全提交的**编排**（§4.3 由
scan runner 承担；本模块自 WP-20260720-008 起只提供其消费的两阶段安全映像入口
``stage_safe_image()``（准备/staging）+ ``confirm_safe_image()``（提交成功后才前移
策略历史），不做外层捕获/分类/提交编排）、**shadow 模式状态机 / 写出开关 / 物理
写抑制的编排本体**（WP-20260722-012 由 ``scan_runner`` 承担；本模块只提供上述两个
被其消费的纯策略层入口）、``last_physical_committed``、真实驱动提交、``commit_fault``
/ ``channel_fault`` 锁存与复位（§4.4）、可信设备反馈 / HAL（§4.1 阶段 7）、实时线程、
L2 adapter 注册表、参数装载总闸门。本包只**消费**注入的安全信号，不生成
startup/watchdog 计时，也不实现 outer scan runner 本体。

**基准边界的落地范围（诚实声明）**：§4.1 列出四类需重建物理输出基准的边界
（冷启动、shadow→实写、提交故障恢复、``channel_fault`` 复位）。本包只实现其中
**唯一在范围内**的一类——冷启动首拍以 ``safe_value`` 为基准；以及"安全落值后
恢复正常路径的首拍从 ``safe_value`` 基准限速"。可信设备反馈接口（§4.1 阶段 7
HAL）未实现，故所有基准选择等效于"无可信反馈"分支（``safe_value``）；``inputs``
/ ``prev`` 端口参数在本包不消费（留给后续可信反馈工作包）。以上输出基准与故障
制度为**项目工程约定、非 CODESYS 官方语义**，且尚未经真机验证；Python 侧行为
不构成与目标 PLC 语义一致的证据。
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from src.runtime.ir import IEC_TYPES, IOMap, REAL_TYPES
from src.runtime.numeric import INT_WIDTHS
from src.runtime.store import UnknownStoreKeyError, check_value_type


# ---------------------------------------------------------------------------
# 专用异常
# ---------------------------------------------------------------------------

class OutputPolicyError(Exception):
    """输出策略层错误基类。"""


class OutputPolicyConfigError(OutputPolicyError):
    """装配期配置非法（策略字段非法、IOMap/Store/策略不一致、非生产策略对象等）。

    抛出即表示该策略/服务**未成功装配**，不得进入扫描。
    """


class OutputPolicyReentryError(OutputPolicyError):
    """同一策略服务的并发/递归 ``stage_outputs()``：失败关闭，不交错两拍。

    与 ``ScanEngine`` 同理，锁在原拍返回后即释放——本拍拒绝而非永久锁死。
    """


class SafetyStateError(OutputPolicyError):
    """安全状态快照/服务错误（字段非布尔、传入非 ``SafetySnapshot`` 等）。"""


# ---------------------------------------------------------------------------
# 故障原因与优先级（§4 规范性）
# ---------------------------------------------------------------------------

_ACTIONS: frozenset = frozenset({"safe", "hold"})

#: 强制 ``"safe"``、不可配置为 ``"hold"`` 的故障策略字段（§4 约束 1）。
_FORCED_SAFE_FIELDS: tuple = ("on_safety_trip", "on_scan_fault", "on_watchdog")
#: 可配置 ``safe``/``hold`` 的故障策略字段。
_CONFIGURABLE_FIELDS: tuple = (
    "on_startup_not_ready", "on_operator_disable", "on_comm_loss")

#: 安全优先级从严到宽（§4 约束 2）；`next()` 取首个命中即最严原因。
_PRIORITY: tuple = (
    "safety_trip", "watchdog", "scan_fault",
    "comm_loss", "startup_not_ready", "operator_disable")
#: 强制 safe 的原因（命中即 safe，不看其余配置，§4 约束 2）。
_FORCED_SAFE_CAUSES: frozenset = frozenset({"safety_trip", "watchdog", "scan_fault"})
#: 可配置原因 → 对应 ``OutputPolicy`` 策略字段名。
_CONFIG_FIELD: dict = {
    "comm_loss": "on_comm_loss",
    "startup_not_ready": "on_startup_not_ready",
    "operator_disable": "on_operator_disable",
}

#: 支持 ``rate_limit`` 的模拟量类型（BOOL/STRING 不支持限速）。
_RATE_LIMIT_TYPES: frozenset = (IEC_TYPES - frozenset({"BOOL", "STRING"}))


# ---------------------------------------------------------------------------
# IEC 数值合法性（结构类型 + 数值域；配置期与运行期同一口径）
# ---------------------------------------------------------------------------

def _int_range(iec_type: str) -> tuple:
    """固定宽度整数/位串的声明范围（复用 ``numeric.INT_WIDTHS``，不另立表）。"""
    bits, signed = INT_WIDTHS[iec_type]
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def _iec_value_error(iec_type: str, value: Any) -> Optional[str]:
    """值不满足声明 IEC 类型时返回原因串；合法返回 ``None``。

    在 ``store.check_value_type`` 的**结构类型**映射之上补**数值域**：
    REAL/LREAL 必须有限（拒绝 NaN/Infinity）；固定宽度整数/位串必须落在声明
    范围内（无符号不得为负）。安全值、限速、request 与 final 共用本函数，保证
    配置期与运行期口径一致。**只判定与拒绝，绝不隐式转换、舍入或回绕"修正"**
    非法值（回绕属数值层 F1/engineering 语义，见 ``numeric.wrap_int``）。

    边界（诚实声明）：``TIME`` 的工程位宽尚未冻结，本函数只校验其结构类型为
    int，不施加范围约束；``STRING`` 只校验结构类型。
    """
    if not check_value_type(iec_type, value):
        return "与声明类型 %s 不匹配（不做隐式转换）" % iec_type
    if iec_type in REAL_TYPES:
        if not math.isfinite(value):
            return "须为有限 %s 值（拒绝 NaN/Infinity）" % iec_type
    elif iec_type in INT_WIDTHS:
        lo, hi = _int_range(iec_type)
        if not (lo <= value <= hi):
            return "超出声明类型 %s 的范围 [%d, %d]" % (iec_type, lo, hi)
    return None


# ---------------------------------------------------------------------------
# OutputPolicy 配置值对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutputPolicy:
    """单个 OUT 通道的分类型门控配置（``ENGINE_SCAN_SPEC §4`` 的生产实现）。

    构造即校验（装配期拒绝非法工程）：IEC 类型合法；``safe_value`` 严格匹配
    ``iec_type`` 的**结构类型与数值域**（REAL/LREAL 拒绝 NaN/Infinity；固定宽度
    整数/位串须在声明范围内，见 ``_iec_value_error``）；六类 ``on_*`` ∈
    {safe, hold}，其中 ``on_safety_trip / on_scan_fault / on_watchdog`` 必须为
    ``"safe"``；``rate_limit`` 仅模拟量可设，整数类型须为非负 ``int``（拒绝 float）
    且在声明类型内可精确表达，REAL/LREAL 须为非负有限 ``float``；
    ``commit_fault_retry_n`` 为正整数（其锁存/复位行为属 §4.4，后续工作包）。
    """
    var: str
    iec_type: str
    safe_value: Any
    rate_limit: Optional[Any] = None
    commit_fault_retry_n: int = 3
    on_startup_not_ready: str = "safe"
    on_operator_disable: str = "safe"
    on_comm_loss: str = "safe"
    on_safety_trip: str = "safe"
    on_scan_fault: str = "safe"
    on_watchdog: str = "safe"

    def __post_init__(self):
        if not self.var or not isinstance(self.var, str):
            raise OutputPolicyConfigError("OutputPolicy.var 非法：%r" % (self.var,))
        if self.iec_type not in IEC_TYPES:
            raise OutputPolicyConfigError(
                "OutputPolicy '%s' 的 iec_type 非法：%r" % (self.var, self.iec_type))
        err = _iec_value_error(self.iec_type, self.safe_value)
        if err is not None:
            raise OutputPolicyConfigError(
                "OutputPolicy '%s' 的 safe_value %r %s"
                % (self.var, self.safe_value, err))
        for f in _FORCED_SAFE_FIELDS + _CONFIGURABLE_FIELDS:
            action = getattr(self, f)
            if action not in _ACTIONS:
                raise OutputPolicyConfigError(
                    "OutputPolicy '%s'.%s 非法动作 %r（仅 safe/hold）"
                    % (self.var, f, action))
        for f in _FORCED_SAFE_FIELDS:
            if getattr(self, f) != "safe":
                raise OutputPolicyConfigError(
                    "OutputPolicy '%s'.%s 必须固定为 'safe'（§4 约束 1，"
                    "safety/scan/watchdog 不可配 hold）" % (self.var, f))
        self._validate_rate_limit()
        n = self.commit_fault_retry_n
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise OutputPolicyConfigError(
                "OutputPolicy '%s'.commit_fault_retry_n 须为正整数，得到 %r"
                % (self.var, n))

    def _validate_rate_limit(self) -> None:
        rl = self.rate_limit
        if rl is None:
            return
        if self.iec_type not in _RATE_LIMIT_TYPES:
            raise OutputPolicyConfigError(
                "OutputPolicy '%s'：%s 类型不支持 rate_limit（限速仅约束模拟量，"
                "§4.2）" % (self.var, self.iec_type))
        if self.iec_type in REAL_TYPES:
            if isinstance(rl, bool) or not isinstance(rl, float):
                raise OutputPolicyConfigError(
                    "OutputPolicy '%s'：REAL/LREAL 限速须为 float，得到 %r"
                    % (self.var, rl))
        else:
            # 整数族 / 位串 / TIME：限速须在声明类型内精确表达，只接受 int，
            # 拒绝 float——不靠浮点舍入猜测整数限速语义。
            if isinstance(rl, bool) or not isinstance(rl, int):
                raise OutputPolicyConfigError(
                    "OutputPolicy '%s'：整数类型 %s 的限速须为 int（不得用 float "
                    "猜测舍入语义），得到 %r" % (self.var, self.iec_type, rl))
        # 限速本身必须是声明类型内可**精确表达**的合法值：REAL/LREAL 有限；
        # 整数/位串落在声明范围内（如 USINT 限速不得为 256）。
        err = _iec_value_error(self.iec_type, rl)
        if err is not None:
            raise OutputPolicyConfigError(
                "OutputPolicy '%s'.rate_limit %r %s（限速须在声明类型内精确表达）"
                % (self.var, rl, err))
        if rl < 0:
            raise OutputPolicyConfigError(
                "OutputPolicy '%s'.rate_limit 不得为负：%r" % (self.var, rl))


# ---------------------------------------------------------------------------
# 安全状态：不可变快照 + 整包替换的线程安全服务
# ---------------------------------------------------------------------------

_SAFETY_FIELDS: tuple = (
    "system_ready", "output_enable", "comm_ok", "safety_ok",
    "interlock_ok", "scan_ok", "watchdog_ok")


@dataclass(frozen=True)
class SafetySnapshot:
    """一拍安全信号的**不可变**快照（全部布尔，frozen——外部持有者无法回写）。

    七个信号覆盖 §4 门控与故障原因集合的全部输入。策略服务每拍只取一次完整
    快照使用；本包只**消费**这些信号，不生成 startup/watchdog 计时。
    """
    system_ready: bool
    output_enable: bool
    comm_ok: bool
    safety_ok: bool
    interlock_ok: bool
    scan_ok: bool
    watchdog_ok: bool

    def __post_init__(self):
        for f in _SAFETY_FIELDS:
            value = getattr(self, f)
            if not isinstance(value, bool):
                raise SafetyStateError(
                    "SafetySnapshot.%s 须为 bool，得到 %r" % (f, value))

    @classmethod
    def all_ok(cls) -> "SafetySnapshot":
        """全部信号为真（无故障）的快照——便于测试与"正常拍"装配。"""
        return cls(True, True, True, True, True, True, True)


class SafetyStateService:
    """线程安全、**整包替换**的最小安全状态服务。

    - ``replace(snapshot)``：在锁下**一次性**替换整个不可变快照（不逐字段写）；
    - ``read()``：在锁下返回当前不可变快照——由于快照 frozen 且整包替换，
      读到的一定是某一次完整状态，**不会撕裂**；对外只暴露整包 ``read()``，
      **不提供逐字段 getter**，从机制上杜绝逐字段读形成的撕裂视图。

    本服务只保存/替换/读取安全信号，不实现 startup/watchdog 计时或 outer
    scan runner（后续工作包）。
    """

    def __init__(self, initial: SafetySnapshot):
        if not isinstance(initial, SafetySnapshot):
            raise SafetyStateError(
                "SafetyStateService 需要 SafetySnapshot，实为 %r" % (initial,))
        self._lock = threading.Lock()
        self._snapshot = initial

    def replace(self, snapshot: SafetySnapshot) -> None:
        if not isinstance(snapshot, SafetySnapshot):
            raise SafetyStateError(
                "replace() 需要 SafetySnapshot，实为 %r" % (snapshot,))
        with self._lock:
            self._snapshot = snapshot

    def read(self) -> SafetySnapshot:
        """返回当前完整快照（不可变；外部持有不污染服务）。"""
        with self._lock:
            return self._snapshot


# ---------------------------------------------------------------------------
# 安全映像确认令牌（两阶段安全事务的一次性、不可伪造准备凭证）
# ---------------------------------------------------------------------------

class SafeImageTicket:
    """``stage_safe_image()`` 返回的**一次性、不可伪造**安全映像确认令牌。

    两阶段安全事务的“准备凭证”：只能由**签发它的**同一
    ``OutputPolicyService.confirm_safe_image()`` 消费**恰一次**，携带本次已 staging
    的安全映像独立副本（``channel -> safe_value``）。这样 ``confirm`` 确认的一定是
    同一次 ``stage_safe_image()`` 准备并（由外层 runner）提交的安全映像——任意
    ``Mapping`` 无法冒充“已提交事务”去污染 ``last_effective``
    （Codex WP-20260720-008 Round 2 反证 2）。

    令牌本身不做校验；校验（签发者身份、一次性、逐通道值 == 配置 ``safe_value``）
    全部由 ``confirm_safe_image`` 在锁内完成。
    """
    __slots__ = ("_issuer", "_token", "_image")

    def __init__(self, issuer: "OutputPolicyService", token: object, image: dict):
        self._issuer = issuer
        self._token = token
        self._image = dict(image)

    @property
    def image(self) -> dict:
        """本次安全映像独立副本（``channel -> safe_value``），供审计 / 提交。"""
        return dict(self._image)


# ---------------------------------------------------------------------------
# OutputPolicy 门控服务（ScanEngine 第 4 步注入端口）
# ---------------------------------------------------------------------------

class OutputPolicyService:
    """生产输出门控服务：满足 ``ScanEngine`` 第 4 步 ``stage_outputs`` 注入契约。

    装配期（``__init__``）对齐 IOMap / Store / 策略并拒绝非法工程；运行期
    （``stage_outputs``）对全部 OUT 通道**先完整校验计算、再统一 stage 并提交
    内部状态**，任一通道失败不留部分内部状态；并发调用失败关闭。

    每通道内部状态（引擎级、业务不可见）：``last_effective`` = **上拍逻辑生效值**
    ——每次成功的策略计算（正常 / safe / hold，含冷启动 hold→safe）完成后都前移
    为本拍 final（§4.1 表格"每拍第 4 步策略计算完成后更新"、§4.4 第 5 条），是
    ``hold`` 取值与限速基准的唯一来源；``boundary_reset`` = **独立**的边界状态，
    标记下一次正常路径是否须以 ``safe_value`` 为限速基准（冷启动、安全落值后恢复）。
    """

    def __init__(self, store: Any, io_map: Iterable, safety_state: SafetyStateService):
        if not isinstance(safety_state, SafetyStateService):
            raise OutputPolicyConfigError(
                "OutputPolicyService 需要 SafetyStateService，实为 %r"
                % (safety_state,))
        self._safety = safety_state
        self._lock = threading.Lock()
        self._order: list = []           # 通道装配顺序
        self._policy: dict = {}          # channel -> OutputPolicy
        self._var: dict = {}             # channel -> GVL var
        # channel -> [last_effective, boundary_reset]；冷启动 last_effective 无
        # 历史（None）、boundary_reset=True（首个正常拍以 safe_value 为基准）。
        self._state: dict = {}
        # 未消费的安全映像令牌（两阶段事务：stage 签发，confirm 消费恰一次）。
        self._pending_ticket: object = None

        seen: set = set()
        for io in io_map:
            if not isinstance(io, IOMap):
                raise OutputPolicyConfigError("io_map 含非 IOMap 项：%r" % (io,))
            if io.direction == "IN":
                continue
            if io.direction != "OUT":
                raise OutputPolicyConfigError(
                    "IOMap '%s' 方向非法：%r" % (io.var, io.direction))
            channel = io.channel
            if channel in seen:
                raise OutputPolicyConfigError("输出通道 '%s' 重复映射" % channel)
            seen.add(channel)

            pol = io.policy
            if pol is None:
                raise OutputPolicyConfigError(
                    "输出通道 '%s' 缺少 OutputPolicy（策略缺失）" % channel)
            if not isinstance(pol, OutputPolicy):
                raise OutputPolicyConfigError(
                    "输出通道 '%s' 的 policy 非生产 OutputPolicy 对象：%r"
                    % (channel, pol))
            try:
                declared = store.declared_type(io.var)
            except UnknownStoreKeyError:
                raise OutputPolicyConfigError(
                    "输出通道 '%s' 的 GVL 变量 '%s' 未在 Store 声明"
                    % (channel, io.var)) from None
            if pol.var != io.var:
                raise OutputPolicyConfigError(
                    "输出通道 '%s'：策略 var '%s' 与 IOMap var '%s' 不一致"
                    % (channel, pol.var, io.var))
            if pol.iec_type != declared:
                raise OutputPolicyConfigError(
                    "输出通道 '%s'：策略 iec_type '%s' 与 Store 声明 '%s' 不一致"
                    % (channel, pol.iec_type, declared))

            self._order.append(channel)
            self._policy[channel] = pol
            self._var[channel] = io.var
            self._state[channel] = [None, True]

    # ---- 观察/诊断（返回独立副本，不反向污染服务） ----

    @property
    def safety_state(self) -> SafetyStateService:
        """本服务注入的**同一** ``SafetyStateService`` 实例。

        外层 scan/watchdog runner 通过它写入 ``scan_ok`` / ``watchdog_ok=False``
        的锁存证据——runner 复用本属性而非另建第二套安全状态，保证 runner 写入
        的信号与本策略每拍 ``read()`` 的信号是同一份。
        """
        return self._safety

    def channels(self) -> tuple:
        return tuple(self._order)

    def diagnostic_last_effective(self) -> dict:
        """每通道 ``last_effective`` 的独立诊断副本（值均为不可变标量）。"""
        return {ch: self._state[ch][0] for ch in self._order}

    def commit_specs(self) -> dict:
        """每通道提交监督所需配置的独立副本：``channel -> (iec_type, safe_value,
        commit_fault_retry_n)``（供 ``CommitSupervisor`` 读取，**不复制第二套通道
        策略表**——``safe_value`` / ``commit_fault_retry_n`` 仍以本策略配置为唯一
        源）。返回值为不可变标量元组，外部持有不污染服务内部状态。"""
        return {ch: (self._policy[ch].iec_type, self._policy[ch].safe_value,
                     self._policy[ch].commit_fault_retry_n) for ch in self._order}

    def boundary_pending(self) -> dict:
        """每通道 ``boundary_reset`` 边界基准挂起标志的独立副本
        （``channel -> bool``）：``True`` 表示"下一次正常路径须以 ``safe_value`` 为
        限速基准重建、且**尚未被新一拍 ``stage_outputs`` 消费**"。

        提交监督器在提交时读取本快照，用于判定"显式复位后输出基准是否已在
        ``safe_value`` 上真正重建"：复位经 ``mark_boundary_reset`` 置本标志为
        ``True``，只有当**复位之后**的一拍 ``stage_outputs`` 在 safe 基准上重算并
        把该通道 final 前移（消费边界、标志回落 ``False``）时，监督器才放行业务值；
        否则（标志仍挂起）说明待提交的是**复位前 staging 的陈旧业务值**，须失败关闭
        改写 ``safe_value``（见 ``CommitSupervisor.commit``）。返回值为不可变标量，
        外部持有不污染服务内部状态。与 ``stage_outputs`` 共用同一锁保证读到的一定
        是某一次完整状态、不撕裂。"""
        with self._lock:
            return {ch: self._state[ch][1] for ch in self._order}

    def mark_boundary_reset(self, channel: str) -> None:
        """把某通道的 ``boundary_reset`` 置真：**下一次正常路径**限速基准回到
        ``safe_value``（§4.1“恢复后首拍从安全基准限速”）。

        提交监督器在**瞬时故障恢复**或**锁存 channel_fault 显式复位**后调用本入口，
        使故障期间照常前移的 ``last_effective`` 不再冒充恢复基准——本包无真实
        HAL/可信设备反馈，故恢复首拍一律退化为 ``safe_value`` 基准，**不用
        ``last_physical_committed`` 对齐**。只改独立的边界标记，不动 ``last_effective``
        逻辑值本身。与 ``stage_outputs`` 共用同一锁保证与门控拍串行、不撕裂。"""
        with self._lock:
            if channel not in self._state:
                raise OutputPolicyConfigError(
                    "mark_boundary_reset 未知通道 '%s'" % channel)
            self._state[channel][1] = True

    def mark_boundary_reset_all(self) -> None:
        """**原子**为全部输出通道挂起 ``boundary_reset``（shadow→实写切换专用，
        §4.1）：下一次正常路径每个输出的限速基准都回到 ``safe_value`` 重建，**绝不**
        使用 shadow 期间照常前移的 ``last_effective`` 或任何 ``last_physical_committed``
        对齐（本包无可信 HAL 反馈，边界基准一律退化为 ``safe_value``）。

        与逐通道 ``mark_boundary_reset`` 的区别：本方法在**同一把锁内一次性**置全部
        通道边界标志，实现“全部输出原子挂起边界重建”，不留半挂起的撕裂状态；只改独立
        的边界标记，不动 ``last_effective`` 逻辑值本身。与 ``stage_outputs`` /
        ``stage_safe_image`` / ``confirm_safe_image`` 共用同一锁保证与门控/安全拍串行。
        """
        with self._lock:
            for channel in self._order:
                self._state[channel][1] = True

    # ---- ScanEngine 第 4 步端口 ----

    def stage_outputs(self, pending: Any, store: Any, inputs: Any,
                      prev: Any) -> None:
        """为每个 OUT 通道计算门控值并 stage（引擎每拍调用一次）。

        ``inputs`` / ``prev`` 为引擎契约参数，本包不消费（可信反馈属阶段 7）。
        并发/递归调用失败关闭（``OutputPolicyReentryError``）。
        """
        if not self._lock.acquire(blocking=False):
            raise OutputPolicyReentryError(
                "同一 OutputPolicyService 的 stage_outputs() 不可重入"
                "（递归或并发）：本拍拒绝")
        try:
            # 每拍只取一次完整安全快照——所有通道共用同一视图，杜绝逐字段撕裂。
            snapshot = self._safety.read()

            # 阶段 1：全通道完整校验+计算到局部（任一失败即抛，未 stage、
            # 未改内部状态）。
            results: dict = {}
            new_state: dict = {}
            for channel in self._order:
                pol = self._policy[channel]
                request = store.read(self._var[channel])
                final, last_effective, boundary_reset = self._compute(
                    pol, request, snapshot, self._state[channel])
                err = _iec_value_error(pol.iec_type, final)
                if err is not None:
                    raise OutputPolicyError(
                        "通道 '%s' 计算出的最终值 %r %s" % (channel, final, err))
                results[channel] = final
                new_state[channel] = [last_effective, boundary_reset]

            # 阶段 2a：全通道 stage（类型已在阶段 1 校验，此处不再失败）。
            for channel in self._order:
                pending.stage(channel, results[channel])
            # 阶段 2b：全部 stage 成功后才统一提交内部状态（原子）。
            for channel in self._order:
                self._state[channel] = new_state[channel]
        finally:
            self._lock.release()

    def stage_safe_image(self, pending: Any) -> "SafeImageTicket":
        """外层 scan/watchdog 恢复专用入口（``ENGINE_SCAN_SPEC §4.3/§4.4``）：
        对**全部** OUT 通道一步落 ``safe_value`` 的安全映像，作为**两阶段安全事务
        的第一阶段（仅准备/staging，不前移策略历史）**。

        与 ``stage_outputs`` 的本质区别：**不读取业务 request、不做限速、不读安全
        快照原因**。外层 runner 在扫描/看门狗故障后调用本入口，**绕过**本拍可能已
        损坏或非法的 request，直接按已验证通道配置生成安全映像。复用同一
        ``_order`` / ``_policy`` / ``_iec_value_error`` 口径，**不复制第二套类型表、
        限速表或通道策略**。

        全有或全无（原子）：先对全部通道的 ``safe_value`` 做与运行期同一口径的 IEC
        结构/数值域校验（``safe_value`` 虽在装配期已校验，此处再校验一次以防御通道
        配置漂移），任一不合法即整体失败关闭——不 stage、不前移任何内部状态。全部
        通过后统一 stage 到 ``pending``。

        **不在本方法前移 ``last_effective`` / ``boundary_reset``**：策略历史只有在
        安全映像**真正提交成功后**才由外层 runner 调用 ``confirm_safe_image()`` 前移
        （两阶段事务的第二阶段）。若底层提交失败，runner 不会 confirm，故
        ``diagnostic_last_effective()`` **绝不冒充**未真正生效的安全映像（§4.3
        “策略历史须与真正提交的安全映像一致 / 失败不得声称已安全提交”）。

        与 ``stage_outputs`` 共用同一非重入锁：并发/递归调用**失败关闭**
        （``OutputPolicyReentryError``），不与正常门控拍交错。返回**一次性、不可
        伪造**的 ``SafeImageTicket``（携带本次安全映像独立副本），外层 runner 提交
        成功后须用**同一令牌**调用 ``confirm_safe_image()`` 前移历史——任意 ``Mapping``
        无法冒充已提交事务（Codex Round 2 反证 2）。
        """
        if not self._lock.acquire(blocking=False):
            raise OutputPolicyReentryError(
                "同一 OutputPolicyService 的 stage_safe_image() 不可重入"
                "（递归或并发）：本拍拒绝")
        try:
            # 阶段 1：全通道校验 safe_value（不读 request、不看安全快照）。
            results: dict = {}
            for channel in self._order:
                pol = self._policy[channel]
                safe_value = pol.safe_value
                err = _iec_value_error(pol.iec_type, safe_value)
                if err is not None:
                    raise OutputPolicyError(
                        "通道 '%s' 的 safe_value %r %s（安全映像拒绝落值）"
                        % (channel, safe_value, err))
                results[channel] = safe_value
            # 阶段 2：全通道 stage（全有或全无；类型已在阶段 1 校验）。
            # **不前移 _state**——历史前移留待提交成功后的 confirm_safe_image()。
            for channel in self._order:
                pending.stage(channel, results[channel])
            # 签发一次性令牌：confirm 据此校验“确认的是同一次准备并提交的映像”。
            token = object()
            self._pending_ticket = token
            return SafeImageTicket(self, token, results)
        finally:
            self._lock.release()

    def confirm_safe_image(self, ticket: "SafeImageTicket") -> None:
        """两阶段安全事务的第二阶段：外层 runner 在安全映像**已真正提交成功后**
        调用，才把每通道 ``last_effective`` 前移为已提交的 ``safe_value`` 并置
        ``boundary_reset``（恢复后首个正常拍从 ``safe_value`` 基准限速，§4.1/§4.2；
        与 ``_compute`` 强制 safe 路径的状态前移完全一致）。

        提交失败时 runner 不会调用本方法，故 ``last_effective`` 不会冒充未生效的
        安全映像。``ticket`` **必须**是本服务同一次 ``stage_safe_image()`` 签发、
        尚未消费的 ``SafeImageTicket``（一次性；签发者身份 + 令牌双重校验），且其
        安全映像须恰好覆盖装配通道集合、逐通道值**严格等于当前配置 ``safe_value``**
        并满足 IEC 结构/数值域（防御装配后配置漂移，与 ``stage`` 同口径）——任一不满
        足即整体失败关闭、不前移任何通道、不消费令牌，杜绝任意映像污染
        ``last_effective``（Codex Round 2 反证 2）。与 ``stage_outputs`` /
        ``stage_safe_image`` 共用同一非重入锁。
        """
        if not self._lock.acquire(blocking=False):
            raise OutputPolicyReentryError(
                "同一 OutputPolicyService 的 confirm_safe_image() 不可重入"
                "（递归或并发）：本拍拒绝")
        try:
            self._advance_from_ticket(ticket, "confirm_safe_image")
        finally:
            self._lock.release()

    def adopt_safe_image_shadow(self, ticket: "SafeImageTicket") -> None:
        """**shadow 下逻辑采用**一张已 staging 的安全映像（``ENGINE_SCAN_SPEC §4.3``；
        WP-20260722-012）：把每通道 ``last_effective`` 前移为该 ``safe_value`` 并置
        ``boundary_reset``，使 shadow 期间的逻辑 ``last_effective`` / 限速基准**连续
        模拟**——正如物理路径下 ``confirm_safe_image`` 所做的历史前移。

        与 ``confirm_safe_image`` 的**本质区别（语义，非算法）**：``confirm_safe_image``
        的前置语义是“安全映像**已物理提交成功**”（由外层 runner 在 ``port.commit()``
        成功后调用）；本入口用于 **shadow / write-disable** 下——此时**根本没有物理
        提交**，故绝不可调用 ``confirm_safe_image`` 冒充现场落值。两者共用**同一**
        一次性令牌校验与历史前移逻辑（``_advance_from_ticket``），保证 shadow 逻辑采用
        同样受“签发者身份 + 一次性令牌 + 通道集 + 逐通道值 == 配置 ``safe_value``”四重
        校验约束，任一不满足即整体失败关闭、不前移、不消费令牌。与 ``stage_outputs`` /
        ``stage_safe_image`` / ``confirm_safe_image`` 共用同一非重入锁。
        """
        if not self._lock.acquire(blocking=False):
            raise OutputPolicyReentryError(
                "同一 OutputPolicyService 的 adopt_safe_image_shadow() 不可重入"
                "（递归或并发）：本拍拒绝")
        try:
            self._advance_from_ticket(ticket, "adopt_safe_image_shadow")
        finally:
            self._lock.release()

    def _advance_from_ticket(self, ticket: "SafeImageTicket", entry: str) -> None:
        """令牌四重校验 + 全有或全无历史前移（**调用方须持锁**）。

        ``confirm_safe_image``（物理提交成功后前移）与 ``adopt_safe_image_shadow``
        （shadow 下逻辑采用）共用本核心：两条入口的**校验与前移完全一致**，仅调用语义
        （是否代表物理提交成功）不同——差异体现在外层 runner 选择哪条入口及其结构化
        信号，而非此处的状态变更。``entry`` 仅用于诊断串定位来源。"""
        # 1) 签发者身份 + 一次性令牌校验：拒绝任意映像 / 他服务 / 重复令牌。
        if not isinstance(ticket, SafeImageTicket) or ticket._issuer is not self:
            raise OutputPolicyError(
                "%s 需要本服务 stage_safe_image() 签发的 SafeImageTicket；"
                "拒绝任意映像冒充已 staging 安全事务" % entry)
        if self._pending_ticket is None or ticket._token is not self._pending_ticket:
            raise OutputPolicyError(
                "安全映像令牌已消费或非本次 stage_safe_image() 所签发"
                "（一次性确认，拒绝重复/过期令牌）")
        image = ticket._image
        # 2) 通道集合校验。
        if set(image) != set(self._order):
            raise OutputPolicyError(
                "%s 的安全映像通道 %r 与装配通道 %r 不一致"
                % (entry, sorted(image), sorted(self._order)))
        # 3) 逐通道值校验：== 当前配置 safe_value（严格类型）且 IEC 合法。
        for channel in self._order:
            pol = self._policy[channel]
            value = image[channel]
            err = _iec_value_error(pol.iec_type, value)
            if err is not None:
                raise OutputPolicyError(
                    "通道 '%s' 确认值 %r %s（安全映像拒绝前移）"
                    % (channel, value, err))
            if type(value) is not type(pol.safe_value) or value != pol.safe_value:
                raise OutputPolicyError(
                    "通道 '%s' 确认值 %r 与当前配置 safe_value %r 不一致"
                    "（拒绝污染 last_effective）"
                    % (channel, value, pol.safe_value))
        # 4) 全有或全无：仅在四重校验通过后统一前移；消费令牌（一次性）。
        for channel in self._order:
            self._state[channel] = [image[channel], True]
        self._pending_ticket = None

    def _compute(self, pol: OutputPolicy, request: Any,
                 snapshot: SafetySnapshot, state: list):
        """返回 ``(final, next_last_effective, next_boundary_reset)``。

        故障路径（§4）：命中最严原因；强制 safe 或配 ``"safe"`` → 一步落
        ``safe_value``（不受限速）且置 ``boundary_reset``（恢复从 safe 基准起
        限速）；配 ``"hold"`` → 取 ``last_effective``（冷启动无历史退化为
        ``safe_value``）。正常路径（§4 算法步 2/3）：BOOL 直取 request；模拟量按
        ``rate_limit`` 对"与限速基准之差"限速，基准 = 冷启动/恢复首拍取
        ``safe_value``，否则取 ``last_effective``；成功后清 ``boundary_reset``。

        **三条路径统一**：任何成功计算都把本拍 final 作为新的 ``last_effective``
        返回（§4.1 表格 / §4.4 第 5 条——策略层照常更新以保持逻辑连续），故
        "强制 safe 落 0 → 下一拍 hold"取到的是 0 而非故障前的旧正常值。

        ``request`` 的 IEC 结构/数值域校验在故障决策**之前**执行，正常、强制
        safe、可配置 safe 与 hold 四条路径共用同一口径：非法 request 一律失败
        关闭，不因当前处于故障状态而被静默接受。
        """
        last_effective, boundary_reset = state

        # request 校验是所有路径的公共入口：故障路径同样不接受非法 request
        # （任务书"safe_value、request 与最终值必须严格符合声明 IEC 类型"），
        # 非法即失败关闭，不 stage、不前移 last_effective。
        err = _iec_value_error(pol.iec_type, request)
        if err is not None:
            raise OutputPolicyError(
                "通道 '%s' 的 request 值 %r %s" % (pol.var, request, err))

        cause = self._top_cause(snapshot)

        if cause is not None:
            if cause in _FORCED_SAFE_CAUSES:
                action = "safe"
            else:
                action = getattr(pol, _CONFIG_FIELD[cause])
            if action == "safe":
                # 一步到位落安全值，不受限速；本拍逻辑生效值即 safe_value
                # （§4.1 表：每拍策略计算完成后更新 last_effective），恢复首拍
                # 从 safe 基准限速。
                return pol.safe_value, pol.safe_value, True
            # hold：取上次逻辑生效值；冷启动无历史退化为 safe_value，该退化值
            # 同样是本拍逻辑生效值，须前移 last_effective。
            if last_effective is None:
                return pol.safe_value, pol.safe_value, True
            # 输出保持在 last_effective，恢复正常路径即从该值继续限速。
            return last_effective, last_effective, False

        # ---- 正常路径 ----
        if pol.iec_type == "BOOL" or pol.rate_limit is None:
            final = request
        else:
            # 冷启动 / 安全落值恢复首拍以 safe_value 为限速基准（§4.1 无可信反馈
            # 分支）；此后以 last_effective 为基准。
            if boundary_reset or last_effective is None:
                baseline = pol.safe_value
            else:
                baseline = last_effective
            delta = request - baseline
            if abs(delta) <= pol.rate_limit:
                final = request
            else:
                step = pol.rate_limit if delta > 0 else -pol.rate_limit
                final = baseline + step
        return final, final, False

    @staticmethod
    def _top_cause(snapshot: SafetySnapshot) -> Optional[str]:
        """按安全优先级返回本拍最严故障原因，无故障返回 ``None``。"""
        active: set = set()
        if not (snapshot.safety_ok and snapshot.interlock_ok):
            active.add("safety_trip")
        if not snapshot.watchdog_ok:
            active.add("watchdog")
        if not snapshot.scan_ok:
            active.add("scan_fault")
        if not snapshot.comm_ok:
            active.add("comm_loss")
        if not snapshot.system_ready:
            active.add("startup_not_ready")
        if not snapshot.output_enable:
            active.add("operator_disable")
        if not active:
            return None
        for cause in _PRIORITY:
            if cause in active:
                return cause
        return None  # 不可达（active 非空必命中）
