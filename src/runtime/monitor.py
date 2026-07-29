"""软件周期监视器 + 一次性 watchdog 超时事件源（WP-20260729-043；
``docs/ENGINE_SCAN_SPEC.md §4.3/§4.4``、``.cursor/rules/00a-runtime-contract.mdc``、
``src/runtime/scan_runner.py::OuterScanRunner.trigger_watchdog()``）。

职责边界（单一职责：**只测量、锁存并交付事件**）：本模块新增一个可独立测试、可
注入时钟、**无后台线程 / 无 sleep / 无操作系统定时器**的软件周期监视器，把一次性
超时事件交给既有 ``OuterScanRunner.trigger_watchdog()`` 消费。它**不**负责扫描调度、
睡眠、线程抢占或硬件 watchdog——事件何时被消费、如何落安全值由既有安全提交路径决定，
本模块只做**确定性的测量 + 事件生成 + 一次性交付**。

—— 五个公开构件 ——

1. ``SoftwareCycleMonitor``：单一公开监视器。经 ``clock_ns`` 注入整数纳秒时钟
   （生产默认 ``time.monotonic_ns``，测试用手工时钟），每次只允许一个 active
   cycle，内部周期 / 超时 / 抖动计算**全部只用整数纳秒**，绝不累计浮点时间误差。
2. ``CycleToken``（公开 dataclass，只作**身份 capability**）：``begin_cycle()`` 返回的
   周期票据，携带单调 ``sequence`` 与起点 ``start_ns``；错误 / 陈旧 token 交给
   ``finish_cycle`` 一律失败关闭。其字段**不可信**（``frozen`` 仍可被 ``object.__setattr__``
   改写），监视器内部周期 / 超时计算只用不暴露给调用方的可信 ``_seq`` / ``_active_start_ns``
   快照，绝不读取 token 字段（WP-20260729-047）。
3. ``CycleObservation``（不可变）：``finish_cycle()`` 返回的观测值，至少含
   ``sequence`` / ``elapsed_ns`` / 配置 ``cycle_ns`` / ``timeout_ns`` 与确定的
   周期偏差（``deviation_ns``）/ 是否超时（``timed_out``）。
4. ``WatchdogTimeoutEvent``（公开 dataclass）：达到阈值时锁存的**一次性**超时事件；
   重复轮询返回同一事件，绝不重复生成。内部保留的 pending 与交给调用方的是**同一
   实例**，即使 ``frozen=True`` 其字段仍可被 ``object.__setattr__`` 改写，故字段对内部
   **不可信**——准入 / 锁存 / 派发一律不读取公开事件字段，一次性语义由内部 ``_pending`` /
   ``_latched_seq`` 保证（WP-20260729-048）。
5. 分层错误 ``MonitorError`` / ``MonitorConfigError`` / ``MonitorStateError`` /
   ``MonitorClockError``：分别覆盖装配配置、状态机违约、时钟契约违约。

—— 阈值与一次性语义（规范性）——

- 阈值判定：``elapsed_ns >= timeout_ms * 1_000_000``。阈值前无事件；阈值及之后
  **第一次** ``poll_timeout()`` / ``finish_cycle()`` 生成**恰一个**事件；此后重复
  ``poll_timeout()`` 返回**同一** pending 事件（序号 / 内容不变），绝不重复生成。
- 每个 active sequence 至多锁存 / 派发**一次**事件：一旦为某序号生成过事件，即使
  ``dispatch_pending()`` 已把 pending 槽清空、且该 active cycle 尚未 ``finish_cycle()``，
  后续 ``poll_timeout()`` / ``finish_cycle()`` **不得**为同一序号重新生成事件或再次触发
  callback（“已锁存序号”是**独立于 pending 槽**的终态，不因 pending 被消费而复位；只有
  新周期准入才切换到新序号）。
- ``finish_cycle(token)`` 即使此前从未 ``poll_timeout()``，也依据结束时钟发现超时并
  **保留** pending 事件；完成周期**不清除** timeout event。
- pending 事件只由 ``dispatch_pending(callback)`` **一次性**消费：仅当存在 pending
  事件时才调用零参数 ``callback``（预期为 ``runner.trigger_watchdog``），且**在调用
  前**即清除 pending——调用一旦开始该事件不可再次派发，``WatchdogSafeCommit`` 或其它
  异常**原样传播**，绝不自动重试造成二次安全提交。
- pending 事件未消费时 ``begin_cycle()`` 下一周期**失败关闭**（``MonitorStateError``），
  不静默覆盖 / 重置 / 丢失事件。

—— 时钟契约（严格）——

``clock_ns()`` 返回值须为 **exact Python ``int``**（``type(value) is int``）的**非负**值，
且相对本监视器已观察时间**单调不回退**。``bool`` 与**所有 ``int`` 子类**（可能重载
``<`` / ``<=`` / ``-`` / ``*`` 等运算绕过校验，或在诊断阶段经实例侧 / 类型对象侧反噬）、
浮点、字符串、负值或回退一律抛 ``MonitorClockError``，**不强转、不钳位、不静默重置状态**，
也不会伪造 / 覆盖 / 重复 timeout event（exact-int 类型判定在数值比较 / 事件生成 / 状态
推进之前先行执行）。exact-int **拒绝路径实行“零观察”（WP-20260729-047）**：只使用固定、
可信、与被拒对象身份及动态类型无关的错误文本，绝不读取被拒对象的值 / 表示 / 类型名，
也绝不触发其类型对象上的属性访问或数据描述符——因此刚建立的信任边界不会在诊断阶段经
实例侧或类型对象侧再次执行攻击者代码（哪怕描述符抛 ``BaseException`` 或只留下副作用）。

—— 明确不实现（诚实边界，均属后续独立工作包）——

真实实时扫描循环 / 调度线程 / sleep / 优先级 / CPU 亲和 / 连续 deadline miss 升级、
在途扫描卡死的异步抢占、进程 / OS 崩溃恢复、硬件 watchdog、HAL / 真实物理 I/O、
可信驱动回执、执行机构、现场安全与 ``system_ready``。以上均**不在本包**；本模块是纯
确定性的软件事件源，Python 侧行为**不构成**与目标 PLC/CODESYS、硬件 watchdog 或现场
安全回路一致的证据。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

_NS_PER_MS = 1_000_000


# ---------------------------------------------------------------------------
# 分层错误
# ---------------------------------------------------------------------------

class MonitorError(Exception):
    """软件周期监视器错误基类。"""


class MonitorConfigError(MonitorError):
    """装配 / 参数配置错误：``cycle_ms`` / ``timeout_ms`` 非正整数、时钟非可调用、
    ``dispatch_pending`` 的 callback 非可调用等。抛出即表示本次调用**未生效**。"""


class MonitorStateError(MonitorError):
    """状态机违约：重复 begin、错误 / 陈旧 token、重复 finish、pending 事件未消费即
    开始下一周期等。一律**失败关闭**，绝不静默覆盖 / 重置 / 丢失事件。"""


class MonitorClockError(MonitorError):
    """注入时钟契约违约：返回非严格 ``int``（含 ``bool``）、负值或相对已观察时间回退。
    抛出时监视器状态**不被静默修复**，且不会伪造 / 覆盖 / 重复 timeout event。"""


# ---------------------------------------------------------------------------
# 不可变值类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CycleToken:
    """``begin_cycle()`` 返回的**不可变**周期票据。

    ``sequence`` 单调递增（从 1 起，每个 ``begin_cycle`` +1）；``start_ns`` 是该周期
    起点的注入时钟读数（整数纳秒）。token 只作 ``finish_cycle`` 的**身份 capability**
    （``token is monitor._active``）——错误、陈旧（属别的周期 / 别的监视器）或重复使用一律
    失败关闭。``_owner_id`` 记录归属监视器的 ``id()``，仅为可读性保留。

    **安全边界（WP-20260729-047）**：本类是**公开 dataclass**，``frozen=True`` 只挡常规
    赋值，``object.__setattr__`` 仍可改写字段；调用方也可自行构造 exact 类型的伪票据。因此
    监视器把这些字段视为**不可信**——周期 / 超时计算与诊断一律使用监视器**内部、不暴露给
    调用方**的可信 ``_seq`` / ``_active_start_ns`` 快照，绝不读取本对象的字段，避免攻击者
    经字段钩子（``__int__`` / ``__index__`` / ``__sub__`` / ``__repr__`` 等）执行代码或
    污染状态。
    """
    sequence: int
    start_ns: int
    _owner_id: int


@dataclass(frozen=True)
class WatchdogTimeoutEvent:
    """达到超时阈值时锁存的**不可变、一次性** watchdog 超时事件。

    ``sequence`` 为超时周期的序号；``start_ns`` / ``observed_ns`` 为周期起点与检出时钟；
    ``elapsed_ns`` 为检出时已流逝纳秒；``timeout_ns`` 为配置阈值；``overrun_ns`` 为超过
    阈值的纳秒（``elapsed_ns - timeout_ns``，恒 ``>= 0``）。一次性语义由监视器的 pending
    生命周期保证（重复轮询返回同一实例，派发后即消费）。

    **安全边界（WP-20260729-047 / WP-20260729-048）**：本类是**公开 dataclass**，且监视器
    内部保留的 pending 事件与已交给调用方的是**同一实例**——``frozen=True`` 只挡常规赋值，
    ``object.__setattr__`` 仍可强制改写字段。因此**任何已暴露给调用方的事件字段对内部都
    不可信**：监视器的准入拒绝、锁存、防重放与派发一律**不读取本对象的公开字段**，一次性
    语义由内部 ``_pending`` / ``_latched_seq`` 状态保证，而**非**依赖字段不可篡改；避免攻击者
    经字段钩子（``__int__`` / ``__index__`` / ``__sub__`` / ``__repr__`` 等）执行代码或污染
    诊断。
    """
    sequence: int
    start_ns: int
    observed_ns: int
    elapsed_ns: int
    timeout_ns: int
    overrun_ns: int


@dataclass(frozen=True)
class CycleObservation:
    """``finish_cycle()`` 返回的**不可变**周期观测值。

    ``deviation_ns = elapsed_ns - cycle_ns``（正=实际周期长于配置周期，负=短于；确定的
    周期偏差 / 抖动量）；``overran`` 为 ``deviation_ns > 0``；``timed_out`` 为
    ``elapsed_ns >= timeout_ns``（与是否已生成 pending 事件一致）。所有量均为整数纳秒，
    无浮点累计误差。
    """
    sequence: int
    start_ns: int
    finish_ns: int
    elapsed_ns: int
    cycle_ns: int
    timeout_ns: int
    deviation_ns: int
    overran: bool
    timed_out: bool


# ---------------------------------------------------------------------------
# 软件周期监视器
# ---------------------------------------------------------------------------

def _require_positive_int(name: str, value: object) -> int:
    """校验 **exact** 正 Python ``int``：只接受 ``type(value) is int`` 且 ``> 0``。

    只接受 Python ``int``，**拒绝 bool 与所有 ``int`` 子类**——``int`` 子类可重载
    ``<=`` / ``*`` 等运算绕过正值校验与纳秒换算（例如让 ``value <= 0`` 恒假、让
    ``value * _NS_PER_MS`` 返回任意对象），故在数值比较 / 换算**之前**先做 exact-int
    类型判定；同时拒绝浮点 / 字符串 / 零 / 负数。抛出即表示本次调用**未生效**。

    非 exact-int 的拒绝路径实行“零观察”（WP-20260729-047）：只使用固定可信文本，
    绝不读取被拒对象的值 / 表示 / 动态类型名，也绝不触发其类型对象上的属性访问或数据
    描述符——``bool`` 与 ``int`` 子类均由 ``type(value) is not int`` 统一拦截（``bool``
    的 ``type`` 亦非 ``int``），无需单列且不再对任何被拒值格式化。``name`` 是本模块传入
    的可信字段名字面量，可安全参与消息；``> 0`` 校验只在通过 exact-int 闸门后进行，
    ``%d`` 只作用于内建 ``int``。
    """
    if type(value) is not int:
        raise MonitorConfigError(
            "%s 只接受 exact Python int（拒绝 bool 与所有 int 子类及其它类型）；"
            "诊断使用固定可信文本，不读取被拒对象的值 / 表示 / 类型名" % (name,))
    if value <= 0:
        raise MonitorConfigError(
            "%s 须为正整数（拒绝零和负数），实为 %d" % (name, value))
    return value


class SoftwareCycleMonitor:
    """软件周期监视器：测量周期、按阈值锁存一次性超时事件，并一次性交付给既有
    ``OuterScanRunner.trigger_watchdog()``。

    ::

        mon = SoftwareCycleMonitor(cycle_ms=10, timeout_ms=50)   # 生产：monotonic_ns
        tok = mon.begin_cycle()
        # ... 一次扫描 ...
        obs = mon.finish_cycle(tok)         # 正常完成：obs.timed_out=False
        if mon.has_pending_event:           # 若本周期超时
            mon.dispatch_pending(runner.trigger_watchdog)   # 一次性交付，raise 传播

    每次只允许一个 active cycle；内部只用整数纳秒；无线程 / 无 sleep / 无 OS 定时器
    （**本 API 不构成实时调度器**）。详见模块文档串的阈值 / 一次性 / 时钟契约。
    """

    __slots__ = ("_clock_ns", "_cycle_ns", "_timeout_ns", "_seq",
                 "_active", "_active_start_ns", "_pending", "_last_seen_ns",
                 "_latched_seq")

    def __init__(self, *, cycle_ms: int, timeout_ms: int,
                 clock_ns: Callable[[], int] = time.monotonic_ns):
        self._cycle_ns = _require_positive_int("cycle_ms", cycle_ms) * _NS_PER_MS
        self._timeout_ns = _require_positive_int("timeout_ms", timeout_ms) * _NS_PER_MS
        if not callable(clock_ns):
            raise MonitorConfigError(
                "clock_ns 须为返回整数纳秒的可调用对象；诊断使用固定可信文本，"
                "不读取被拒对象的值 / 表示 / 类型名")
        self._clock_ns = clock_ns
        self._seq = 0
        # ``_active`` 只作**身份 capability**（``token is self._active``）：``CycleToken``
        # 是公开 dataclass，虽 ``frozen=True`` 仍可被 ``object.__setattr__`` 改写字段，
        # 故内部计算 / 诊断**绝不读取该对象的字段**，一律使用下面两个不暴露给调用方的
        # 可信内部快照：``_seq`` 是本模块单调递增的 active 序号（active 期间恒等于当前
        # 票据的 sequence）；``_active_start_ns`` 是本模块 ``begin_cycle`` 亲自记录的起点
        # 整数纳秒。两者与 ``_active`` 在 begin / finish 时**同步设置 / 清除**。
        self._active: Optional[CycleToken] = None
        self._active_start_ns: Optional[int] = None
        self._pending: Optional[WatchdogTimeoutEvent] = None
        # 已观察时钟上界（用于回退检测）；初值 0，因合法时钟恒 >= 0，首读不会误判回退。
        self._last_seen_ns = 0
        # “已锁存事件的 sequence” 终态：独立于 pending 槽记录本序号是否已生成过事件，
        # 使同一 active sequence 在 dispatch 清空 pending 后也不会被再次锁存 / 派发。
        # 序号单调递增，新周期序号必不等于旧终态，故无需显式复位（None=尚无锁存）。
        self._latched_seq: Optional[int] = None

    # ---- 只读诊断（不暴露测试时钟或 mutable 内部状态） ----

    @property
    def cycle_ns(self) -> int:
        """配置的目标周期（整数纳秒）。"""
        return self._cycle_ns

    @property
    def timeout_ns(self) -> int:
        """配置的超时阈值（整数纳秒）。"""
        return self._timeout_ns

    @property
    def active(self) -> bool:
        """当前是否存在一个未完成的 active cycle（只读诊断）。"""
        return self._active is not None

    @property
    def active_sequence(self) -> Optional[int]:
        """当前 active cycle 的序号（无 active cycle 时为 ``None``）。

        返回**内部可信** ``_seq``（本模块单调计数），**不**读取已交给调用方、可被
        ``object.__setattr__`` 篡改的 token ``sequence`` 字段。"""
        return None if self._active is None else self._seq

    @property
    def has_pending_event(self) -> bool:
        """是否存在尚未派发的一次性 timeout 事件（只读诊断）。"""
        return self._pending is not None

    # ---- 内部：整数纳秒时钟读取（严格校验，先校验后推进状态） ----

    def _read_clock(self) -> int:
        """读取注入时钟并**严格**校验：**exact** 非负 Python ``int``（``type(value) is int``）、
        相对已观察时间不回退。**先做 exact-int 类型判定**再做数值比较——``bool`` 与所有
        ``int`` 子类可重载 ``<`` / ``-`` 等运算绕过回退检测与 elapsed 计算并伪造超时事件，
        故在任何数值比较之前一律拒绝。任一违约抛 ``MonitorClockError``，且**不**更新任何
        状态（不强转 / 不钳位 / 不静默重置），因此错误时钟绝不会伪造 / 覆盖 / 重复
        timeout event。exact-int 拒绝分支实行“零观察”（WP-20260729-047）：只使用固定
        可信文本，绝不读取被拒返回值的值 / 表示 / 动态类型名，也绝不触发其类型对象上的
        属性访问或数据描述符（哪怕描述符抛 ``BaseException`` 或留下副作用）。"""
        value = self._clock_ns()
        if type(value) is not int:
            raise MonitorClockError(
                "clock_ns() 只接受 exact Python int（拒绝 bool 与所有 int 子类及其它类型）；"
                "诊断使用固定可信文本，不读取被拒返回值的值 / 表示 / 类型名")
        if value < 0:
            raise MonitorClockError("clock_ns() 返回负值 %d，拒绝" % (value,))
        if value < self._last_seen_ns:
            raise MonitorClockError(
                "clock_ns() 相对已观察时间回退：%d < 已观察 %d（拒绝钳位 / 静默重置）"
                % (value, self._last_seen_ns))
        self._last_seen_ns = value
        return value

    def _maybe_latch(self, sequence: int, start_ns: int, now: int) -> None:
        """阈值及之后**首次**检出超时时锁存恰一个 pending 事件；已有 pending 则保留原事件，
        绝不重复生成 / 覆盖。

        终态防重放：``_latched_seq`` 记录本序号是否已生成过事件——即使 pending 已被
        ``dispatch_pending()`` 消费清空、且该 active cycle 尚未 finish，只要 ``sequence``
        已在 ``_latched_seq`` 中，也**不**再生成第二个事件（避免同一超时周期二次派发 /
        二次安全提交）。锁存时同时写入 pending 槽与 ``_latched_seq``，两者同步更新。"""
        if self._pending is not None:
            return
        if self._latched_seq == sequence:
            return
        elapsed = now - start_ns
        if elapsed >= self._timeout_ns:
            self._pending = WatchdogTimeoutEvent(
                sequence=sequence, start_ns=start_ns, observed_ns=now,
                elapsed_ns=elapsed, timeout_ns=self._timeout_ns,
                overrun_ns=elapsed - self._timeout_ns)
            self._latched_seq = sequence

    # ---- 周期生命周期 ----

    def begin_cycle(self) -> CycleToken:
        """开始一个新周期，返回带单调序号与起点的 ``CycleToken``。

        失败关闭：已有 active cycle（重复 begin）→ ``MonitorStateError``；存在未消费的
        pending timeout 事件 → ``MonitorStateError``（必须先 ``dispatch_pending``，绝不
        静默覆盖 / 丢失事件）。时钟违约在创建 token **之前**抛出，序号与状态不前移。
        """
        if self._active is not None:
            # 诊断只用内部可信 ``_seq``（active 期间恒等于当前票据序号），绝不读取已交给
            # 调用方、可被 ``object.__setattr__`` 篡改的 ``self._active.sequence`` 字段。
            raise MonitorStateError(
                "begin_cycle：已有 active cycle（序号 %d）未 finish，拒绝重复开始"
                % (self._seq,))
        if self._pending is not None:
            # WatchdogTimeoutEvent 也是**公开 dataclass**：``frozen=True`` 仍可被
            # ``object.__setattr__`` 改写字段，且该实例已交给调用方（poll / finish 返回同一
            # 实例），故其字段一律**不可信**——绝不读取该事件的任何公开字段（sequence 等）。
            # 诊断只用不暴露给调用方、由 exact-int 内部锁存路径维护的可信 ``_latched_seq``：
            # 生成 pending 时与 ``_pending`` 同步写入、恒为内建 int、与事件序号恒一致
            # （WP-20260729-048），可安全 %d。
            raise MonitorStateError(
                "begin_cycle：存在未派发的 timeout 事件（序号 %d），须先 dispatch_pending "
                "再开始下一周期；诊断使用固定可信文本，不读取 pending 事件的值 / 表示 / 字段"
                % (self._latched_seq,))
        now = self._read_clock()                # 时钟违约在此抛出，序号未推进
        self._seq += 1
        token = CycleToken(sequence=self._seq, start_ns=now, _owner_id=id(self))
        self._active = token
        self._active_start_ns = now             # 内部可信起点，不依赖返回给调用方的 token
        return token

    def poll_timeout(self) -> Optional[WatchdogTimeoutEvent]:
        """在 active cycle 进行中轮询是否已超时（可由独立监视上下文调用）。

        仅锁存并返回**一次性**事件，**不**调用 runner、**不**修改 Store / Executor。
        语义：已有 pending 事件 → 原样返回同一实例（重复轮询不重复生成）；无 active
        cycle 且无 pending → 返回 ``None``（无可测量对象）；active 且未超时 → 返回
        ``None``；active 且达到阈值 → 生成恰一个事件并返回。时钟违约抛
        ``MonitorClockError``，不生成事件。
        """
        if self._pending is not None:
            return self._pending
        if self._active is None:
            return None
        now = self._read_clock()
        # 用内部可信 ``_seq`` / ``_active_start_ns``，绝不读取可被 ``object.__setattr__``
        # 篡改的 token ``sequence`` / ``start_ns`` 字段。
        self._maybe_latch(self._seq, self._active_start_ns, now)
        return self._pending

    def finish_cycle(self, token: CycleToken) -> CycleObservation:
        """完成当前 active cycle，返回不可变 ``CycleObservation``。

        即使此前从未 ``poll_timeout()``，也依据结束时钟发现超时并**保留** pending 事件；
        完成周期**不清除** timeout event。失败关闭：``token`` 非 **exact** ``CycleToken`` /
        属别的周期或监视器（陈旧 token）/ 无 active cycle（重复 finish）→
        ``MonitorStateError``。时钟违约在推进状态**之前**抛出，active cycle 不被清除。

        token 只接受本模块创建的 **exact** ``CycleToken``（``type(token) is CycleToken``），
        拒绝一切子类 / 伪对象；``token`` 仅用作**身份 capability**（``token is self._active``），
        本方法**绝不读取任何 token 字段**。理由（WP-20260729-047 Round 2/3 更正）：``CycleToken``
        是**公开 dataclass**，exact 类型不约束字段，且 ``frozen=True`` 仍可被 ``object.__setattr__``
        改写——外部既可构造 ``CycleToken(sequence=<__int__ 抛 BaseException>, ...)``，也可在
        ``begin_cycle`` 后对**当前 active 票据**执行 ``object.__setattr__(tok, "start_ns", <恶意>)``。
        因此 token 字段（哪怕是 active 票据的）**一律不可信**。所有拒绝路径（非 exact 类型、
        无 active、非当前 active）只用固定可信文本、不读取被拒 token 任何字段；确认
        ``token is self._active`` 后的观测计算与 ``%d`` 诊断只使用**不暴露给调用方的可信内部
        快照** ``_seq`` / ``_active_start_ns``（本模块 ``begin_cycle`` 亲自记录、必为内建
        ``int``），与被 capability 对象承载的字段无关。
        """
        if type(token) is not CycleToken:
            raise MonitorStateError(
                "finish_cycle：token 须为本监视器创建的 exact CycleToken；"
                "诊断使用固定可信文本，不读取被拒对象的值 / 表示 / 类型名")
        if self._active is None:
            raise MonitorStateError(
                "finish_cycle：当前无 active cycle（重复 finish 或未 begin），拒绝")
        if token is not self._active:
            # 身份判断即足够：CycleToken 是公开 dataclass，被拒 token 的字段可能是抛
            # BaseException / 带副作用的恶意对象，故拒绝路径**不读取被拒 token 任何字段**；
            # 诊断只用内部可信 self._seq（本模块创建、必为内建 int），可安全 %d。
            raise MonitorStateError(
                "finish_cycle：陈旧 / 错误 token（与当前 active 周期序号 %d 不是同一票据）；"
                "诊断使用固定可信文本，不读取被拒 token 的值 / 表示 / 类型名 / 字段"
                % (self._seq,))
        # 身份匹配 → 是本模块 begin_cycle 交付的 active capability；但该对象可能已被
        # object.__setattr__ 篡改字段，故观测**只用内部可信快照** _seq / _active_start_ns，
        # 绝不读取 token.sequence / token.start_ns（否则会执行攻击者钩子并部分推进状态）。
        seq = self._seq
        start_ns = self._active_start_ns
        now = self._read_clock()                # 违约在此抛出，active 未清除
        elapsed = now - start_ns
        # 结束时若尚未锁存且已越阈值，则据结束时钟补锁一次（不覆盖既有 pending）。
        self._maybe_latch(seq, start_ns, now)
        observation = CycleObservation(
            sequence=seq, start_ns=start_ns, finish_ns=now,
            elapsed_ns=elapsed, cycle_ns=self._cycle_ns, timeout_ns=self._timeout_ns,
            deviation_ns=elapsed - self._cycle_ns, overran=elapsed > self._cycle_ns,
            timed_out=elapsed >= self._timeout_ns)
        self._active = None                     # 仅清 active，pending 事件保留
        self._active_start_ns = None            # 与 _active 同步清除可信起点
        return observation

    def dispatch_pending(self, callback: Callable[[], object]) -> bool:
        """一次性派发 pending timeout 事件：仅当存在 pending 事件时调用零参数 ``callback``。

        ``callback`` 预期为既有 ``runner.trigger_watchdog``。**在调用前**即清除 pending
        ——调用一旦开始该事件不可再次派发（第二次派发不会造成第二次调用）；``callback``
        抛 ``WatchdogSafeCommit`` 或其它异常一律**原样传播**，绝不吞异常、绝不自动重试
        造成二次安全提交。无 pending 事件时**不**调用 callback，返回 ``False``；成功进入
        派发返回 ``True``（若 callback 抛异常则由异常传播，不返回）。
        """
        if not callable(callback):
            raise MonitorConfigError(
                "dispatch_pending 的 callback 须为零参数可调用对象；诊断使用固定可信文本，"
                "不读取被拒对象的值 / 表示 / 类型名")
        if self._pending is None:
            return False
        # 调用前即消费：即便 callback 抛异常，事件已不可再次派发（禁止二次安全提交）。
        self._pending = None
        callback()
        return True
