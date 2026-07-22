"""WP-20260721-009：提交监督器、通道故障锁存复位与驱动确认提交证据测试。

逐条对应任务书"最低测试"清单（``git diff --check`` 由 Codex 交接后独立执行）：

1. 驱动回执：全成功、单通道失败、多通道部分成功；``None``/仅无异常、缺失/多余
   通道、错值/错类型/越界/非有限回执均失败关闭，不前移相应 ``last_physical_committed``。
2. 逐通道隔离：一通道失败时其他通道仍保留成功证据；故障通道下一拍及后续拍只写
   ``safe_value``，正常通道继续写业务值。
3. 状态机：阈值前恢复、第 N 次精确升级、锁存后继续安全写、安全写成功只清瞬时故障
   不清锁存。
4. 复位：三项前置逐项缺失、未知/未锁存/重复复位均拒绝；全部满足时成功解锁，首拍从
   ``safe_value`` 重建限速基准。
5. 反证 ``last_physical_committed`` 不被 OutputPolicy 当成可信反馈/基准；正常、
   scan-fault、watchdog、安全映像部分失败与 confirm 失败路径都有结构化证据。
6. 递归/并发重入失败关闭；诊断快照不可反向污染；异常后锁与状态仍可恢复使用。
   既有 WP-007/WP-008 语义锁不得改写或放宽（由未改动的既有测试文件回归验证）。

诚实边界：本文件锁定的是当前 Python 提交监督器/驱动回执契约行为——真实 HAL、
真实物理写入、设备位置反馈、硬件 watchdog、shadow、现场安全回路均**不在本包**；
这些测试**不构成**与 CODESYS PLC 语义一致或真实驱动一致的证据。
"""
from __future__ import annotations

import threading
import unittest
from collections.abc import Mapping
from types import SimpleNamespace

from src.runtime import (
    ChannelCommitStatus,
    CommitOutcome,
    CommitPort,
    CommitReceipt,
    CommitSupervisor,
    CommitSupervisorConfigError,
    CommitSupervisorError,
    CommitSupervisorReentryError,
    Executor,
    IOMap,
    LoadConst,
    LoadVar,
    OuterScanRunner,
    OutputPolicy,
    OutputPolicyService,
    PartialCommitError,
    POUDefinition,
    ProgramInstance,
    SafetySnapshot,
    SafetyStateService,
    ScanEngine,
    ScanFaultSafeCommit,
    ScanRunnerConfigError,
    Store,
    StoreVar,
    Task,
    VarDecl,
    WatchdogSafeCommit,
    build_runtime_store,
)
from src.runtime.process_image import OutputPending


# ---------------------------------------------------------------------------
# 驱动测试替身（逐批确认回执契约模拟——非真实物理写入）
# ---------------------------------------------------------------------------

class _Driver:
    """可控确认回执驱动。默认回显命令值（全成功）；可注入失败形态：

    - ``raise_exc``：整批抛异常；
    - ``return_none``：返回 ``None``（仅"未抛异常"不得判成功）；
    - ``omit``：从回执中删掉某通道（缺失 → 通道集不一致 → 整批不可信）；
    - ``extra``：追加未尝试通道（多余 → 通道集不一致 → 整批不可信）；
    - ``wrong``：某通道回执给显式错值/错类型/越界值。
    """

    def __init__(self):
        self.calls = []
        self.raise_exc = None
        self.return_none = False
        self.omit = set()
        self.extra = {}
        self.wrong = {}

    def commit(self, commands):
        self.calls.append(dict(commands))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.return_none:
            return None
        out = {}
        for ch, v in commands.items():
            if ch in self.omit:
                continue
            out[ch] = self.wrong[ch] if ch in self.wrong else v
        out.update(self.extra)
        return out


class _CallbackDriver:
    """提交期间回调一次外部动作（用于重入失败关闭测试）。"""

    def __init__(self):
        self.callback = None
        self.reentry_error = None

    def commit(self, commands):
        if self.callback is not None and self.reentry_error is None:
            try:
                self.callback()
            except CommitSupervisorReentryError as exc:
                self.reentry_error = exc
        return dict(commands)


class _BlockingDriver:
    """提交时阻塞在事件上（跨线程并发重入）。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def commit(self, commands):
        self.entered.set()
        self.release.wait(timeout=5)
        return dict(commands)


class _LazyReceipt(Mapping):
    """满足 ``Mapping`` 外形（声明键集、可迭代），但**逐项取值**抛异常的代理回执。

    复现 Codex Round 1 反证 2：回执通过 ``isinstance(..., Mapping)`` 与键集一致性
    检查，但 ``__getitem__`` 抛普通异常——监督器必须失败关闭而非漏出普通异常。
    """

    def __init__(self, keys, exc):
        self._keys = list(keys)
        self._exc = exc

    def __getitem__(self, key):
        raise self._exc

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


class _ReprBoom:
    """可哈希对象，但 ``__repr__`` 抛普通异常（复现 Codex Round 3 反证）。

    用作**多余回执通道键**或**错误确认值**：验证失败诊断格式化统一经安全表示入口
    （``_safe_repr``），绝不因失控 ``repr`` 二次击穿失败关闭路径。默认对象身份
    ``hash``/``eq``，可直接作为 dict 键。"""

    def __repr__(self):
        raise RuntimeError("repr boom")


class _TypeNameStrBoom:
    """恶意“类型名”对象：``__str__`` / ``__repr__`` 均抛普通异常。

    用作 ``_safe_repr`` fallback 读取到的 ``type(obj).__name__``，复现 Codex Round 1
    反证——fallback 在 ``repr`` 失败后对类型名做 ``%s`` 插值时，若类型名字符串化再次
    抛普通异常，则 fallback 自身被二次击穿。"""

    def __str__(self):
        raise RuntimeError("type-name str boom")

    def __repr__(self):
        raise RuntimeError("type-name repr boom")


class _ReprAndTypeNameBoomMeta(type):
    """元类：令 ``type(obj).__name__`` 返回一个字符串化会抛异常的对象。

    metaclass 上的 data descriptor（``property``）在 MRO 中先于 ``type.__name__``，
    因此 ``cls.__name__`` 读取本属性、返回 ``_TypeNameStrBoom()``（读取本身不抛异常，
    只有随后字符串化才抛）。"""

    @property
    def __name__(cls):                             # noqa: N805 - 元类描述符
        return _TypeNameStrBoom()


class _ReprAndTypeNameBoom(metaclass=_ReprAndTypeNameBoomMeta):
    """可哈希对象：``__repr__`` 抛普通异常，且其**类型名字符串化也抛普通异常**。

    验证 ``_safe_repr`` fallback 的**整个构造过程**（含类型名 ``%s`` 插值）永不二次
    漏出普通异常——`repr` 失败退化后，恶意元类的类型名不得再击穿失败关闭路径。"""

    def __repr__(self):
        raise RuntimeError("repr boom")


class _SwitchableDriver:
    """默认回显命令（全成功）；置 ``lazy_exc`` 后返回逐项取值抛错的惰性回执。"""

    def __init__(self):
        self.calls = []
        self.lazy_exc = None
        self.lazy_keys = None

    def commit(self, commands):
        self.calls.append(dict(commands))
        if self.lazy_exc is not None:
            return _LazyReceipt(self.lazy_keys, self.lazy_exc)
        return dict(commands)


# ---------------------------------------------------------------------------
# 恶意标量子类回执（WP-20260722-011）：重载比较/相等 dunder 抛普通异常
# ---------------------------------------------------------------------------

class _HostileInt(int):
    """恶意 ``int`` 子类：范围/相等比较 dunder 一旦被调用即抛普通异常。

    exact 内建标量门禁必须在触碰任何可重载运算**之前**以纯类型身份失败关闭；若门禁
    缺失，``_iec_value_error`` 的整数值域比较（``lo <= value <= hi``）会调用本类
    ``__ge__`` / ``__le__``、严格相等会调用 ``__eq__`` / ``__ne__``，抛出的普通
    ``RuntimeError`` 将击穿本应失败关闭的提交路径。``dunder_calls`` 记录任何被调用的
    比较 dunder，用于断言门禁从未触碰它们。"""

    def __new__(cls, value):
        obj = super().__new__(cls, value)
        obj.dunder_calls = []
        return obj

    def __ge__(self, other):
        self.dunder_calls.append("__ge__")
        raise RuntimeError("hostile int __ge__")

    def __le__(self, other):
        self.dunder_calls.append("__le__")
        raise RuntimeError("hostile int __le__")

    def __gt__(self, other):
        self.dunder_calls.append("__gt__")
        raise RuntimeError("hostile int __gt__")

    def __lt__(self, other):
        self.dunder_calls.append("__lt__")
        raise RuntimeError("hostile int __lt__")

    def __eq__(self, other):
        self.dunder_calls.append("__eq__")
        raise RuntimeError("hostile int __eq__")

    def __ne__(self, other):
        self.dunder_calls.append("__ne__")
        raise RuntimeError("hostile int __ne__")

    __hash__ = int.__hash__


class _HostileFloat(float):
    """恶意 ``float`` 子类：比较/相等 dunder 一旦被调用即抛普通异常（同根覆盖，证明
    门禁是统一标量信任边界而非只特判整数比较点）。"""

    def __new__(cls, value):
        obj = super().__new__(cls, value)
        obj.dunder_calls = []
        return obj

    def __ge__(self, other):
        self.dunder_calls.append("__ge__")
        raise RuntimeError("hostile float __ge__")

    def __le__(self, other):
        self.dunder_calls.append("__le__")
        raise RuntimeError("hostile float __le__")

    def __eq__(self, other):
        self.dunder_calls.append("__eq__")
        raise RuntimeError("hostile float __eq__")

    def __ne__(self, other):
        self.dunder_calls.append("__ne__")
        raise RuntimeError("hostile float __ne__")

    __hash__ = float.__hash__


class _HostileStr(str):
    """恶意 ``str`` 子类：相等 dunder 一旦被调用即抛普通异常（同根覆盖）。"""

    def __new__(cls, value):
        obj = super().__new__(cls, value)
        obj.dunder_calls = []
        return obj

    def __eq__(self, other):
        self.dunder_calls.append("__eq__")
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        self.dunder_calls.append("__ne__")
        raise RuntimeError("hostile str __ne__")

    __hash__ = str.__hash__


# ---------------------------------------------------------------------------
# 直接装配辅助（Store + IOMap + 策略 + 监督器）
# ---------------------------------------------------------------------------

def _make(specs, driver=None):
    """specs: list of (var, channel, iec_type, safe_value, retry_n, rate)。"""
    store = Store()
    io_map = []
    for var, ch, t, sv, n, rate in specs:
        store.declare(var, t, None)
        io_map.append(IOMap(var, ch, "OUT",
                            policy=OutputPolicy(var, t, sv, rate_limit=rate,
                                                commit_fault_retry_n=n)))
    safety = SafetyStateService(SafetySnapshot.all_ok())
    policy = OutputPolicyService(store, io_map, safety)
    driver = driver or _Driver()
    sup = CommitSupervisor(driver, policy)
    return SimpleNamespace(store=store, io_map=io_map, safety=safety,
                           policy=policy, driver=driver, sup=sup)


def _single(iec_type="INT", safe=0, retry_n=3, rate=None, driver=None):
    return _make([("V", "CH", iec_type, safe, retry_n, rate)], driver=driver)


def _commit(sup, **channel_values):
    """直接调用 supervisor.commit，返回 (outcome_or_None, error_or_None)。"""
    try:
        return sup.commit(dict(channel_values)), None
    except PartialCommitError as exc:
        return None, exc


def _policy_scan(w, var, business):
    """经策略 stage_outputs 生成本拍 staged，再经监督器提交（模拟引擎第 4/5 步）。"""
    w.store.write(var, business)
    pending = OutputPending(w.store, w.io_map)
    w.policy.stage_outputs(pending, w.store, None, None)
    staged = pending.staged()
    err = None
    try:
        w.sup.commit(staged)
    except PartialCommitError as exc:
        err = exc
    return staged, err


# ---------------------------------------------------------------------------
# 1) 驱动确认提交证据
# ---------------------------------------------------------------------------

class TestDriverReceiptEvidence(unittest.TestCase):

    def test_all_success_advances_last_physical_committed(self):
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "BOOL", False, 3, None)])
        outcome, err = _commit(w.sup, CHA=11, CHB=True)
        self.assertIsNone(err)
        self.assertIsInstance(outcome, CommitOutcome)
        self.assertTrue(outcome.all_succeeded)
        diag = w.sup.diagnostics()
        self.assertEqual(diag["CHA"].last_physical_committed, 11)
        self.assertEqual(diag["CHB"].last_physical_committed, True)
        self.assertFalse(diag["CHA"].commit_fault)
        self.assertFalse(diag["CHB"].commit_fault)
        # 回执结构化：命令通道 + 值 + 成功
        r = w.sup.last_commit_receipts()
        self.assertIsInstance(r["CHA"], CommitReceipt)
        self.assertTrue(r["CHA"].ok and r["CHB"].ok)
        self.assertEqual(r["CHA"].commanded_value, 11)
        self.assertFalse(r["CHA"].overridden_safe)

    def test_single_channel_failure_isolated(self):
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None)])
        w.driver.wrong = {"CHB": 999999}          # 错值（越 INT 域也算失败）
        outcome, err = _commit(w.sup, CHA=11, CHB=22)
        self.assertIsInstance(err, PartialCommitError)
        self.assertEqual(err.failed_channels, ("CHB",))
        # 逐通道隔离：A 成功前移 lpc，B 失败保留旧 lpc(None)、置瞬时故障
        diag = w.sup.diagnostics()
        self.assertEqual(diag["CHA"].last_physical_committed, 11)
        self.assertFalse(diag["CHA"].commit_fault)
        self.assertIsNone(diag["CHB"].last_physical_committed)
        self.assertTrue(diag["CHB"].commit_fault)
        self.assertEqual(diag["CHB"].consecutive_failures, 1)
        # 逐通道回执都在（成功与失败）
        self.assertTrue(err.receipts["CHA"].ok)
        self.assertFalse(err.receipts["CHB"].ok)

    def test_multi_channel_partial_success(self):
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None),
                   ("C", "CHC", "INT", 0, 3, None)])
        w.driver.wrong = {"CHA": 7, "CHC": 7}     # A、C 错值，B 成功
        _, err = _commit(w.sup, CHA=1, CHB=2, CHC=3)
        self.assertIsInstance(err, PartialCommitError)
        self.assertEqual(set(err.failed_channels), {"CHA", "CHC"})
        diag = w.sup.diagnostics()
        self.assertEqual(diag["CHB"].last_physical_committed, 2)
        self.assertIsNone(diag["CHA"].last_physical_committed)
        self.assertIsNone(diag["CHC"].last_physical_committed)

    def test_return_none_fails_all_closed(self):
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None)])
        w.driver.return_none = True
        _, err = _commit(w.sup, CHA=11, CHB=22)
        self.assertIsInstance(err, PartialCommitError)
        self.assertEqual(set(err.failed_channels), {"CHA", "CHB"})
        diag = w.sup.diagnostics()
        self.assertIsNone(diag["CHA"].last_physical_committed)
        self.assertIsNone(diag["CHB"].last_physical_committed)

    def test_missing_channel_receipt_fails_all_closed(self):
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None)])
        w.driver.omit = {"CHB"}                    # 缺失通道 → 通道集不一致
        _, err = _commit(w.sup, CHA=11, CHB=22)
        self.assertIsInstance(err, PartialCommitError)
        self.assertEqual(set(err.failed_channels), {"CHA", "CHB"})
        # 缺失回执 → 即便 A 回显正确也不被提升为可信成功
        self.assertIsNone(w.sup.diagnostics()["CHA"].last_physical_committed)

    def test_extra_channel_receipt_fails_all_closed(self):
        w = _single("INT", 0)
        w.driver.extra = {"GHOST": 0}              # 多余通道 → 通道集不一致
        _, err = _commit(w.sup, CH=5)
        self.assertIsInstance(err, PartialCommitError)
        self.assertIsNone(w.sup.diagnostics()["CH"].last_physical_committed)

    def test_extra_channel_uncomparable_key_fails_closed_preserves_lpc(self):
        # Codex Round 2 反证：多余回执通道键与既有通道键**不可比较排序**
        # （混合 str/int，如 {"CH": 7, 1: 0}）时，失败诊断格式化不得漏出普通
        # TypeError——须结构化失败关闭、置提交故障、保留旧 last_physical_committed。
        w = _single("INT", 0)
        outcome, err = _commit(w.sup, CH=5)        # 先正常成功一拍，lpc=5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 5)
        w.driver.extra = {1: 0}                    # 多余通道键 int 1，与 "CH" 不可排序
        with self.assertRaises(PartialCommitError):  # 结构化失败，非漏出 TypeError
            w.sup.commit({"CH": 7})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)              # 已尝试通道置故障
        self.assertEqual(d.consecutive_failures, 1)
        self.assertEqual(d.last_physical_committed, 5)   # 保留旧 lpc，不前移
        self.assertFalse(w.sup.last_commit_receipts()["CH"].ok)

    def test_extra_channel_repr_raising_key_fails_closed_preserves_lpc(self):
        # Codex Round 3 反证：多余回执通道键的 __repr__ 抛异常时，失败诊断格式化
        # 不得漏出普通异常——须结构化失败关闭、置提交故障、连续失败计数前移、保留
        # 旧 last_physical_committed。
        w = _single("INT", 0)
        outcome, err = _commit(w.sup, CH=5)        # 先正常成功一拍，lpc=5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 5)
        w.driver.extra = {_ReprBoom(): 0}          # 多余键，__repr__ 抛 RuntimeError
        with self.assertRaises(PartialCommitError):  # 结构化失败，非漏出 RuntimeError
            w.sup.commit({"CH": 7})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)              # 已尝试通道置故障
        self.assertEqual(d.consecutive_failures, 1)  # 连续失败计数前移
        self.assertEqual(d.last_physical_committed, 5)   # 保留旧 lpc，不前移
        self.assertFalse(w.sup.last_commit_receipts()["CH"].ok)

    def test_extra_channel_repr_and_typename_boom_fails_closed_preserves_lpc(self):
        # Codex Round 1（WP-010）反证：多余回执通道键不仅 __repr__ 抛异常，其恶意元类
        # 令 type(key).__name__ 返回一个 __str__ 也抛异常的对象——_safe_repr fallback
        # 的整个构造过程（含类型名 %s 插值）不得二次漏出普通异常，仍须结构化失败关闭、
        # 置提交故障、连续失败计数前移、保留旧 last_physical_committed。
        w = _single("INT", 0)
        outcome, err = _commit(w.sup, CH=5)        # 先正常成功一拍，lpc=5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 5)
        w.driver.extra = {_ReprAndTypeNameBoom(): 0}  # __repr__ 抛异常且类型名字符串化也抛
        with self.assertRaises(PartialCommitError):    # 结构化失败，非漏出 RuntimeError
            w.sup.commit({"CH": 7})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)              # 已尝试通道置故障
        self.assertEqual(d.consecutive_failures, 1)  # 连续失败计数前移
        self.assertEqual(d.last_physical_committed, 5)   # 保留旧 lpc，不前移
        self.assertFalse(w.sup.last_commit_receipts()["CH"].ok)

    def test_wrong_confirmed_value_repr_raising_fails_closed_preserves_lpc(self):
        # Codex Round 3 反证：错误类型确认值的 __repr__ 抛异常时，构造“确认值不匹配”
        # 诊断不得漏出普通异常——须结构化失败关闭、置提交故障、连续失败计数前移、
        # 保留旧 last_physical_committed。
        w = _single("INT", 0)
        outcome, err = _commit(w.sup, CH=5)        # 先正常成功一拍，lpc=5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 5)
        w.driver.wrong = {"CH": _ReprBoom()}       # 错类型确认值，__repr__ 抛 RuntimeError
        with self.assertRaises(PartialCommitError):  # 结构化失败，非漏出 RuntimeError
            w.sup.commit({"CH": 7})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)              # 已尝试通道置故障
        self.assertEqual(d.consecutive_failures, 1)  # 连续失败计数前移
        self.assertEqual(d.last_physical_committed, 5)   # 保留旧 lpc，不前移
        self.assertFalse(w.sup.last_commit_receipts()["CH"].ok)

    def test_wrong_type_confirmed_fails(self):
        w = _single("INT", 0)
        w.driver.wrong = {"CH": 5.0}               # INT 通道回执给 float
        _, err = _commit(w.sup, CH=5)
        self.assertIsInstance(err, PartialCommitError)
        self.assertIsNone(w.sup.diagnostics()["CH"].last_physical_committed)

    def test_out_of_range_confirmed_fails(self):
        w = _single("USINT", 0)
        w.driver.wrong = {"CH": 999}               # 越 USINT 域
        _, err = _commit(w.sup, CH=10)
        self.assertIsInstance(err, PartialCommitError)

    def test_non_finite_confirmed_fails(self):
        w = _single("REAL", 0.0)
        w.driver.wrong = {"CH": float("inf")}
        _, err = _commit(w.sup, CH=1.5)
        self.assertIsInstance(err, PartialCommitError)

    def test_driver_raises_fails_all_with_exception(self):
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None)])
        boom = RuntimeError("driver boom")
        w.driver.raise_exc = boom
        _, err = _commit(w.sup, CHA=1, CHB=2)
        self.assertIsInstance(err, PartialCommitError)
        self.assertIs(err.driver_exception, boom)
        self.assertEqual(set(err.failed_channels), {"CHA", "CHB"})

    def test_no_exception_alone_is_not_success(self):
        # "仅未抛异常"不得判成功：驱动不抛但回执错值 → 失败关闭
        w = _single("INT", 0)
        w.driver.wrong = {"CH": 6}                 # 命令 5、回执 6，未抛异常
        _, err = _commit(w.sup, CH=5)
        self.assertIsInstance(err, PartialCommitError)
        self.assertIsNone(w.sup.diagnostics()["CH"].last_physical_committed)

    def test_lazy_receipt_getitem_error_fails_closed_preserves_lpc(self):
        # Codex Round 1 反证 2：回执满足 Mapping 外形、键集一致，但逐项取值抛错时，
        # 监督器**不得漏出普通异常**——须结构化失败关闭、置提交故障、保留旧 lpc。
        driver = _SwitchableDriver()
        w = _single("INT", 0, driver=driver)
        outcome, err = _commit(w.sup, CH=5)        # 先正常成功一拍，lpc=5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 5)
        # 下一拍：回执键集一致（{CH}）但 __getitem__ 抛 RuntimeError
        driver.lazy_exc = RuntimeError("receipt read boom")
        driver.lazy_keys = ["CH"]
        with self.assertRaises(PartialCommitError):  # 结构化失败，非漏出 RuntimeError
            w.sup.commit({"CH": 7})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)              # 已尝试通道置故障
        self.assertEqual(d.consecutive_failures, 1)
        self.assertEqual(d.last_physical_committed, 5)   # 保留旧 lpc，不前移
        self.assertFalse(w.sup.last_commit_receipts()["CH"].ok)

    def test_structural_channel_set_mismatch_makes_no_write_state(self):
        # outputs 通道集与装配集不符 → 结构性拒绝，不发命令、不改状态
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None)])
        with self.assertRaises(CommitSupervisorError):
            w.sup.commit({"CHA": 1})               # 缺 CHB
        self.assertEqual(w.driver.calls, [])       # 未发任何命令
        diag = w.sup.diagnostics()
        self.assertFalse(diag["CHA"].commit_fault or diag["CHB"].commit_fault)


# ---------------------------------------------------------------------------
# 1b) 恶意标量子类回执：exact 内建标量门禁在任何可重载运算前失败关闭
#     （WP-20260722-011）
# ---------------------------------------------------------------------------

class TestHostileScalarSubclassGate(unittest.TestCase):
    """不可信驱动回执的恶意标量子类必须在 exact 内建标量门禁处失败关闭，绝不以重载
    比较/相等运算在结构化逐通道失败证据形成前击穿提交路径。"""

    def test_hostile_int_subclass_receipt_fails_closed(self):
        # WP 反证：单通道 USINT，发出 exact int 命令 7，回执为重载 __ge__/__le__/__eq__
        # 抛 RuntimeError 的 int 子类。门禁须在触碰任何比较前失败关闭：产生
        # PartialCommitError（非漏出 RuntimeError），置 commit_fault、连续失败计数精确
        # 前移、保留旧 lpc、存在 ok=False 结构化回执；断言恶意比较 dunder 从未被调用。
        w = _single("USINT", 0)
        outcome, err = _commit(w.sup, CH=5)          # 先正常成功一拍，lpc=5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 5)
        hostile = _HostileInt(7)
        w.driver.wrong = {"CH": hostile}
        with self.assertRaises(PartialCommitError):  # 结构化失败，非漏出 RuntimeError
            w.sup.commit({"CH": 7})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)              # 置提交故障
        self.assertEqual(d.consecutive_failures, 1)  # 连续失败计数精确前移
        self.assertEqual(d.last_physical_committed, 5)   # 保留旧 lpc，不前移
        r = w.sup.last_commit_receipts()["CH"]
        self.assertFalse(r.ok)                        # ok=False 结构化回执
        self.assertIn("exact 内建标量", r.detail)     # 由门禁失败关闭，非落入比较
        self.assertEqual(hostile.dunder_calls, [])    # 恶意比较 dunder 从未被调用

    def test_hostile_float_subclass_receipt_fails_closed(self):
        # 同根 REAL 反证：回执为重载比较/相等 dunder 抛异常的 float 子类（值有限）。
        # 门禁须统一失败关闭、不调用其 dunder，证明是统一标量信任边界。
        w = _single("REAL", 0.0)
        outcome, err = _commit(w.sup, CH=1.5)        # 正常成功一拍，lpc=1.5
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, 1.5)
        hostile = _HostileFloat(1.5)
        w.driver.wrong = {"CH": hostile}
        with self.assertRaises(PartialCommitError):
            w.sup.commit({"CH": 1.5})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)
        self.assertEqual(d.consecutive_failures, 1)
        self.assertEqual(d.last_physical_committed, 1.5)
        r = w.sup.last_commit_receipts()["CH"]
        self.assertFalse(r.ok)
        self.assertIn("exact 内建标量", r.detail)
        self.assertEqual(hostile.dunder_calls, [])

    def test_hostile_str_subclass_receipt_fails_closed(self):
        # 同根 STRING 反证：回执为重载 __eq__/__ne__ 抛异常的 str 子类。门禁须统一
        # 失败关闭、不调用其 dunder（证明非只特判整数比较点）。
        w = _single("STRING", "safe")
        outcome, err = _commit(w.sup, CH="ok")       # 正常成功一拍，lpc="ok"
        self.assertIsNone(err)
        self.assertEqual(w.sup.diagnostics()["CH"].last_physical_committed, "ok")
        hostile = _HostileStr("ok")
        w.driver.wrong = {"CH": hostile}
        with self.assertRaises(PartialCommitError):
            w.sup.commit({"CH": "ok"})
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.commit_fault)
        self.assertEqual(d.consecutive_failures, 1)
        self.assertEqual(d.last_physical_committed, "ok")
        r = w.sup.last_commit_receipts()["CH"]
        self.assertFalse(r.ok)
        self.assertIn("exact 内建标量", r.detail)
        self.assertEqual(hostile.dunder_calls, [])

    def test_multi_channel_hostile_subclass_isolated_from_healthy(self):
        # 多通道反证：健康通道 A 独立成功并前移自身 lpc；恶意 int 子类通道 B 失败关闭，
        # 二者隔离——不因 B 的值子类问题把 A 升级为不可信整批。
        w = _make([("A", "CHA", "INT", 0, 3, None),
                   ("B", "CHB", "INT", 0, 3, None)])
        hostile = _HostileInt(22)
        w.driver.wrong = {"CHB": hostile}
        outcome, err = _commit(w.sup, CHA=11, CHB=22)
        self.assertIsInstance(err, PartialCommitError)
        self.assertEqual(err.failed_channels, ("CHB",))
        diag = w.sup.diagnostics()
        self.assertEqual(diag["CHA"].last_physical_committed, 11)   # A 独立成功
        self.assertFalse(diag["CHA"].commit_fault)
        self.assertIsNone(diag["CHB"].last_physical_committed)      # B 保留旧 lpc(None)
        self.assertTrue(diag["CHB"].commit_fault)
        self.assertEqual(diag["CHB"].consecutive_failures, 1)
        self.assertTrue(err.receipts["CHA"].ok)
        self.assertFalse(err.receipts["CHB"].ok)
        self.assertEqual(hostile.dunder_calls, [])

    def test_hostile_int_subclass_escalates_and_latches(self):
        # 恶意 int 子类连续失败到 commit_fault_retry_n 精确升级并锁存 channel_fault，
        # 与既有语义一致（安全值重试路径不回退）。
        w = _single("INT", 0, retry_n=2)
        # 故障拍改写 safe_value=0；驱动对 safe 值也回执恶意子类 → 持续失败关闭。
        w.driver.wrong = {"CH": _HostileInt(0)}
        with self.assertRaises(PartialCommitError):
            w.sup.commit({"CH": 5})                  # 失败1
        self.assertFalse(w.sup.diagnostics()["CH"].channel_fault)
        with self.assertRaises(PartialCommitError):
            w.sup.commit({"CH": 5})                  # 失败2 → 精确锁存
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.channel_fault)
        self.assertEqual(d.consecutive_failures, 2)


# ---------------------------------------------------------------------------
# 2) 逐通道隔离：故障通道下一拍改写 safe_value，正常通道继续业务值
# ---------------------------------------------------------------------------

class TestPerChannelIsolation(unittest.TestCase):

    def test_faulted_channel_writes_safe_next_scan_others_business(self):
        w = _make([("A", "CHA", "INT", 9, 3, None),   # A safe=9
                   ("B", "CHB", "INT", 0, 3, None)])
        # scan1：A 错值失败、B 成功
        w.driver.wrong = {"CHA": 123}
        _, err = _commit(w.sup, CHA=11, CHB=22)
        self.assertIsInstance(err, PartialCommitError)
        self.assertTrue(w.sup.diagnostics()["CHA"].commit_fault)

        # scan2：驱动恢复；A 被改写 safe_value=9（忽略业务 11），B 仍写业务 22
        w.driver.wrong = {}
        outcome, err2 = _commit(w.sup, CHA=11, CHB=22)
        self.assertIsNone(err2)
        self.assertEqual(w.driver.calls[-1], {"CHA": 9, "CHB": 22})
        r = w.sup.last_commit_receipts()
        self.assertTrue(r["CHA"].overridden_safe)
        self.assertEqual(r["CHA"].commanded_value, 9)
        self.assertFalse(r["CHB"].overridden_safe)
        # A 瞬时恢复：commit_fault 清除
        self.assertFalse(w.sup.diagnostics()["CHA"].commit_fault)
        # B 独立更新 lpc，不被 A 连带
        self.assertEqual(w.sup.diagnostics()["CHB"].last_physical_committed, 22)

    def test_commit_fault_not_in_policy_cause_set(self):
        # 提交层故障不进 OutputPolicy 故障原因集合：故障拍后策略仍算业务 final、
        # 维持 last_effective；监督器独立改写 safe_value。
        w = _single("INT", 9, rate=5)
        # scan1 正常：策略 cold-start 基准 safe9，业务 20 → |20-9|=11>5 → final=14
        staged, err = _policy_scan(w, "V", 20)
        self.assertEqual(staged, {"CH": 14})
        self.assertIsNone(err)
        # scan2：驱动错值使 CH 失败
        w.driver.wrong = {"CH": 111}
        staged, err = _policy_scan(w, "V", 20)
        self.assertEqual(staged, {"CH": 19})       # 策略照常算（14→19），未被强制 safe
        self.assertIsInstance(err, PartialCommitError)
        self.assertTrue(w.sup.diagnostics()["CH"].commit_fault)
        # scan3：策略仍算业务（19→20，last_effective 逻辑连续），监督器改写 safe9
        w.driver.wrong = {}
        staged, err = _policy_scan(w, "V", 20)
        self.assertEqual(staged, {"CH": 20})       # 策略 final 逻辑连续，未见提交故障
        self.assertEqual(w.driver.calls[-1], {"CH": 9})   # 物理写 safe_value
        self.assertEqual(w.policy.diagnostic_last_effective(), {"CH": 20})


# ---------------------------------------------------------------------------
# 3) 状态机：阈值前恢复 / 第 N 次精确升级 / 锁存后继续安全写 / 安全写不清锁存
# ---------------------------------------------------------------------------

class TestFaultStateMachine(unittest.TestCase):

    def test_recovery_before_threshold_clears_transient(self):
        w = _single("INT", 0, retry_n=3)
        w.driver.wrong = {"CH": 5}
        _commit(w.sup, CH=1)                       # 失败1 → commit_fault, count1
        self.assertEqual(w.sup.diagnostics()["CH"].consecutive_failures, 1)
        w.driver.wrong = {}                        # 驱动恢复：下一拍改写 safe0 成功
        _commit(w.sup, CH=1)
        d = w.sup.diagnostics()["CH"]
        self.assertFalse(d.commit_fault)
        self.assertFalse(d.channel_fault)
        self.assertEqual(d.consecutive_failures, 0)

    def test_exact_escalation_at_nth_failure(self):
        w = _single("INT", 0, retry_n=3)
        w.driver.wrong = {"CH": 5}                 # 一直失败
        _commit(w.sup, CH=1)                       # 失败1
        self.assertFalse(w.sup.diagnostics()["CH"].channel_fault)
        _commit(w.sup, CH=1)                       # 失败2（本拍改写 safe0 也失败）
        self.assertFalse(w.sup.diagnostics()["CH"].channel_fault)
        _commit(w.sup, CH=1)                       # 失败3 → 精确锁存
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)
        self.assertEqual(w.sup.diagnostics()["CH"].consecutive_failures, 3)

    def test_latched_keeps_writing_safe_each_scan(self):
        w = _single("INT", 9, retry_n=2)
        w.driver.wrong = {"CH": 5}
        _commit(w.sup, CH=1)                       # 失败1
        _commit(w.sup, CH=1)                       # 失败2 → 锁存
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)
        w.driver.wrong = {}                        # 驱动恢复
        _commit(w.sup, CH=1)                       # 仍改写 safe9
        self.assertEqual(w.driver.calls[-1], {"CH": 9})
        _commit(w.sup, CH=1)                       # 继续改写 safe9（锁存后无静默放弃）
        self.assertEqual(w.driver.calls[-1], {"CH": 9})

    def test_safe_write_success_after_latch_does_not_clear_latch(self):
        w = _single("INT", 9, retry_n=2)
        w.driver.wrong = {"CH": 5}
        _commit(w.sup, CH=1)
        _commit(w.sup, CH=1)                       # 锁存
        w.driver.wrong = {}
        _commit(w.sup, CH=1)                       # 安全写成功
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.channel_fault)           # 锁存绝不因安全写成功而自动清除
        self.assertTrue(d.safe_confirmed_after_latch)

    def test_safe_write_after_latch_clears_transient_keeps_latch(self):
        # Codex Round 2 反证：锁存后 safe_value 写成功必须清瞬时 commit_fault 与
        # 连续失败计数（§4.4“期间安全值写成功只清瞬时 commit_fault”），但**绝不**
        # 自动解除锁存 channel_fault。断言四状态精确取值。
        w = _single("INT", 9, retry_n=2)
        w.driver.wrong = {"CH": 5}
        _commit(w.sup, CH=1)                       # 失败1
        _commit(w.sup, CH=1)                       # 失败2 → 锁存
        d = w.sup.diagnostics()["CH"]
        self.assertTrue(d.channel_fault)
        self.assertTrue(d.commit_fault)            # 锁存拍仍带瞬时故障
        self.assertEqual(d.consecutive_failures, 2)
        w.driver.wrong = {}                        # 驱动恢复：安全值 9 写成功
        _commit(w.sup, CH=1)
        d = w.sup.diagnostics()["CH"]
        self.assertFalse(d.commit_fault)           # 瞬时故障清除
        self.assertEqual(d.consecutive_failures, 0)  # 连续计数清零
        self.assertTrue(d.channel_fault)           # 锁存保持，不自动解除
        self.assertTrue(d.safe_confirmed_after_latch)  # 锁存后已有合法安全回执


# ---------------------------------------------------------------------------
# 4) 三条件显式复位
# ---------------------------------------------------------------------------

class TestExplicitReset(unittest.TestCase):

    def _latched(self, retry_n=2, safe=9, rate=None):
        w = _single("INT", safe, retry_n=retry_n, rate=rate)
        w.driver.wrong = {"CH": 5}
        for _ in range(retry_n):
            _commit(w.sup, CH=1)
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)
        return w

    def test_reset_unknown_channel_rejected(self):
        w = self._latched()
        with self.assertRaises(CommitSupervisorError):
            w.sup.reset_channel_fault("NOPE", fault_cause_cleared=True)

    def test_reset_not_latched_rejected(self):
        w = _single("INT", 0)
        with self.assertRaises(CommitSupervisorError):
            w.sup.reset_channel_fault("CH", fault_cause_cleared=True)

    def test_reset_cause_not_cleared_rejected(self):
        w = self._latched()
        w.driver.wrong = {}
        _commit(w.sup, CH=1)                       # 有合法安全回执
        with self.assertRaises(CommitSupervisorError):
            w.sup.reset_channel_fault("CH", fault_cause_cleared=False)
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)

    def test_reset_without_confirmed_safe_after_latch_rejected(self):
        w = self._latched()                        # 锁存后尚未有成功安全回执
        self.assertFalse(w.sup.diagnostics()["CH"].safe_confirmed_after_latch)
        with self.assertRaises(CommitSupervisorError):
            w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)

    def test_reset_all_conditions_met_unlocks(self):
        w = self._latched()
        w.driver.wrong = {}
        _commit(w.sup, CH=1)                       # 锁存后合法安全回执
        w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        d = w.sup.diagnostics()["CH"]
        self.assertFalse(d.channel_fault)
        self.assertFalse(d.commit_fault)
        self.assertEqual(d.consecutive_failures, 0)

    def test_repeated_reset_rejected(self):
        w = self._latched()
        w.driver.wrong = {}
        _commit(w.sup, CH=1)
        w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        with self.assertRaises(CommitSupervisorError):     # 第二次 → 未锁存
            w.sup.reset_channel_fault("CH", fault_cause_cleared=True)

    def test_first_normal_output_after_reset_from_safe_baseline(self):
        # 复位后首个正常输出从 safe_value 基准限速，而非故障期间漂移的
        # last_effective，也不用 last_physical_committed 对齐。
        w = _single("INT", 0, retry_n=2, rate=5)
        # 三拍正常，让策略 last_effective 漂移到 15（cold 0→5→10→15）
        for _ in range(3):
            staged, err = _policy_scan(w, "V", 100)
            self.assertIsNone(err)
        self.assertEqual(w.policy.diagnostic_last_effective(), {"CH": 15})
        # 制造锁存：驱动错值两拍
        w.driver.wrong = {"CH": 7}
        _policy_scan(w, "V", 100)                  # 失败1
        _policy_scan(w, "V", 100)                  # 失败2 → 锁存
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)
        # 锁存后合法安全回执
        w.driver.wrong = {}
        _policy_scan(w, "V", 100)
        # 复位
        w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        # 首个正常输出：基准回到 safe0 → 100 限到 5（若用漂移 le≈20 则会是 25）
        staged, err = _policy_scan(w, "V", 100)
        self.assertIsNone(err)
        self.assertEqual(staged, {"CH": 5})

    def _latched_with_drift_and_safe_receipt(self):
        """构造：策略 last_effective 漂移 → 锁存 → 锁存后合法安全回执（满足复位前置）。"""
        w = _single("INT", 0, retry_n=2, rate=5)
        for _ in range(3):                         # cold 0→5→10→15
            _policy_scan(w, "V", 100)
        self.assertEqual(w.policy.diagnostic_last_effective(), {"CH": 15})
        w.driver.wrong = {"CH": 7}
        _policy_scan(w, "V", 100)                  # 失败1
        _policy_scan(w, "V", 100)                  # 失败2 → 锁存
        self.assertTrue(w.sup.diagnostics()["CH"].channel_fault)
        w.driver.wrong = {}
        _policy_scan(w, "V", 100)                  # 锁存后合法安全回执
        self.assertTrue(w.sup.diagnostics()["CH"].safe_confirmed_after_latch)
        return w

    def test_reset_between_stage_and_commit_forces_safe_baseline(self):
        # 确定性并发反证（修复 Codex Round 1 反证 1）：显式复位精确插到本拍
        # stage_outputs 与 commit **之间**时，绝不放行复位前 staging 的陈旧业务值——
        # 提交层失败关闭改写 safe_value，直到复位后的新一拍在 safe 基准上重建。
        # 这不是"提交期间调用复位"（那已由 test_reset_during_commit_fails_closed 覆盖）。
        w = self._latched_with_drift_and_safe_receipt()
        # —— 竞态窗口第一步：复位前 stage（基准=漂移的 last_effective，值远大于 safe）——
        w.store.write("V", 100)
        pending = OutputPending(w.store, w.io_map)
        w.policy.stage_outputs(pending, w.store, None, None)
        staged = pending.staged()
        self.assertGreater(staged["CH"], 5)        # 陈旧 staged 明显 > safe 基准限速 5
        # —— 竞态窗口第二步：复位插到 stage 与 commit 之间 ——
        w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        self.assertFalse(w.sup.diagnostics()["CH"].channel_fault)
        # —— 竞态窗口第三步：提交复位前 staging 的陈旧值 → 必须改写 safe0，不放行 ——
        outcome, err = _commit(w.sup, **staged)
        self.assertIsNone(err)
        self.assertEqual(w.driver.calls[-1], {"CH": 0})      # 物理写 safe_value
        self.assertTrue(w.sup.last_commit_receipts()["CH"].overridden_safe)
        # 复位后的下一拍才在 safe 基准上重算：业务 100 从 safe0 限到 5
        staged2, err2 = _policy_scan(w, "V", 100)
        self.assertIsNone(err2)
        self.assertEqual(staged2, {"CH": 5})

    def test_reset_racing_stage_commit_across_threads_forces_safe(self):
        # 线程版并发反证：用事件把复位精确插到工作线程的 stage 与 commit 之间。
        w = self._latched_with_drift_and_safe_receipt()
        staged_done = threading.Event()
        proceed = threading.Event()
        box: dict = {}

        def scan_worker():
            w.store.write("V", 100)
            pending = OutputPending(w.store, w.io_map)
            w.policy.stage_outputs(pending, w.store, None, None)
            box["staged"] = pending.staged()
            staged_done.set()                      # 通知：staging 完成
            proceed.wait(timeout=5)                # 等主线程完成复位后再提交
            try:
                w.sup.commit(dict(box["staged"]))
                box["err"] = None
            except PartialCommitError as exc:      # pragma: no cover
                box["err"] = exc

        t = threading.Thread(target=scan_worker)
        t.start()
        self.assertTrue(staged_done.wait(timeout=5))
        self.assertGreater(box["staged"]["CH"], 5)
        # 复位插入 stage 与 commit 之间
        w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        proceed.set()
        t.join(timeout=5)
        self.assertIsNone(box.get("err"))
        # 陈旧业务值被失败关闭改写 safe0，而非放行
        self.assertEqual(w.driver.calls[-1], {"CH": 0})
        self.assertTrue(w.sup.last_commit_receipts()["CH"].overridden_safe)


# ---------------------------------------------------------------------------
# 5) 集成：正常/scan-fault/watchdog 共享同一监督器；结构化证据；lpc 非可信基准
# ---------------------------------------------------------------------------

def _wire(driver=None, av_safe=0, av_rate=None, retry_n=3):
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("AV", "INT", section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Motor", "DO0", "OUT",
              policy=OutputPolicy("Motor", "BOOL", False, commit_fault_retry_n=retry_n)),
        IOMap("AV", "AO0", "OUT",
              policy=OutputPolicy("AV", "INT", av_safe, rate_limit=av_rate,
                                  commit_fault_retry_n=retry_n)),
    ]
    code = [
        LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL"),
        LoadConst(100, "INT"), StoreVar("AV", "INT"),
    ]
    main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
    task = Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                io_map=io_map, pou_lib={"Main": main})
    layout = build_runtime_store(task)
    executor = Executor(task, layout)
    safety = SafetyStateService(SafetySnapshot.all_ok())
    policy = OutputPolicyService(layout.store, task.io_map, safety)
    driver = driver or _Driver()
    sup = CommitSupervisor(driver, policy)
    port = CommitPort(sup)
    engine = ScanEngine(task, layout, executor, policy, port)
    runner = OuterScanRunner(engine, policy, port)
    return SimpleNamespace(task=task, layout=layout, safety=safety, policy=policy,
                           driver=driver, sup=sup, port=port, engine=engine,
                           runner=runner)


class TestEngineRunnerIntegration(unittest.TestCase):

    def test_shared_supervisor_across_three_paths(self):
        w = _wire(av_safe=7, av_rate=5)
        # 正常拍：全成功（cold-start 基准 = safe_value 7，业务 100 限速 → 7+5=12）
        r = w.runner.scan_cycle({"DI0": True})
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 12})
        self.assertEqual(w.driver.calls[-1], {"DO0": True, "AO0": 12})
        # watchdog 安全提交经同一监督器（全安全值一次提交）
        with self.assertRaises(WatchdogSafeCommit) as cm:
            w.runner.trigger_watchdog()
        self.assertTrue(cm.exception.safe_commit_succeeded)
        self.assertEqual(w.driver.calls[-1], {"DO0": False, "AO0": 7})
        # 监督器诊断可见（同一实例）
        self.assertIn("AO0", w.sup.diagnostics())

    def test_normal_partial_failure_is_commit_fault_not_scan_fault(self):
        w = _wire(av_safe=7, av_rate=5)
        w.driver.wrong = {"AO0": 999}              # 正常提交时 AO0 错值
        prev_before = w.engine.prev
        with self.assertRaises(PartialCommitError):   # 非 SafeCommitSignal
            w.runner.scan_cycle({"DI0": True})
        # 不追加第二次安全提交：驱动只被调用一次
        self.assertEqual(len(w.driver.calls), 1)
        # 任一通道失败拍 prev 不前移
        self.assertIs(w.engine.prev, prev_before)
        # 未锁存 scan_ok（这不是扫描故障）
        self.assertTrue(w.safety.read().scan_ok)
        # 逐通道证据：DO0 成功、AO0 失败
        self.assertTrue(w.sup.diagnostics()["DO0"].last_physical_committed)
        self.assertTrue(w.sup.diagnostics()["AO0"].commit_fault)

    def test_scan_fault_partial_failure_usint(self):
        # 用 USINT AV，预置越界 request 触发 scan_fault；安全提交时驱动使 AO0 失败
        gvl = [VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
               VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
               VarDecl("AV", "USINT", section="VAR_GLOBAL")]
        io_map = [IOMap("Start", "DI0", "IN"),
                  IOMap("Motor", "DO0", "OUT",
                        policy=OutputPolicy("Motor", "BOOL", False)),
                  IOMap("AV", "AO0", "OUT",
                        policy=OutputPolicy("AV", "USINT", 9))]
        code = [LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL")]
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
        task = Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                    io_map=io_map, pou_lib={"Main": main})
        layout = build_runtime_store(task)
        executor = Executor(task, layout)
        safety = SafetyStateService(SafetySnapshot.all_ok())
        policy = OutputPolicyService(layout.store, task.io_map, safety)
        driver = _Driver()
        sup = CommitSupervisor(driver, policy)
        port = CommitPort(sup)
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)
        layout.store.write("AV", 999)              # 越 USINT 域 → 正常策略 staging 失败
        driver.wrong = {"AO0": 200}                # 安全提交时 AO0 回执错值
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            runner.scan_cycle({"DI0": True})
        sig = cm.exception
        self.assertFalse(sig.safe_commit_succeeded)
        self.assertEqual(sig.failed_stage, "commit")
        # 策略历史未前移（未 confirm）——全通道 last_effective 仍冷启动
        self.assertEqual(policy.diagnostic_last_effective(),
                         {"DO0": None, "AO0": None})
        # 逐通道证据：DO0 安全写成功、AO0 失败
        self.assertTrue(sup.diagnostics()["DO0"].last_physical_committed is False)
        self.assertTrue(sup.diagnostics()["AO0"].commit_fault)

    def test_safe_image_all_success_confirms_history(self):
        # 全通道明确成功回执时才 confirm、标记 safe_commit_succeeded=True、前移历史
        w = _wire(av_safe=7)
        with self.assertRaises(WatchdogSafeCommit) as cm:
            w.runner.trigger_watchdog()
        self.assertTrue(cm.exception.safe_commit_succeeded)
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": False, "AO0": 7})

    def test_last_physical_committed_not_policy_baseline(self):
        # lpc 由监督器持有，OutputPolicy 无引用；恢复后限速基准是 safe_value，
        # 而非漂移的 last_effective（结构上也无法是 lpc）。
        w = _wire(av_safe=0, av_rate=5, retry_n=2)
        w.runner.scan_cycle({"DI0": True})         # AO0: 0→5, lpc(AO0)=5
        w.runner.scan_cycle({"DI0": True})         # AO0: 5→10, lpc=10
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 10)
        # 制造 AO0 锁存
        w.driver.wrong = {"AO0": 1}
        with self.assertRaises(PartialCommitError):
            w.runner.scan_cycle({"DI0": True})     # 失败1
        with self.assertRaises(PartialCommitError):
            w.runner.scan_cycle({"DI0": True})     # 失败2 → 锁存
        self.assertTrue(w.sup.diagnostics()["AO0"].channel_fault)
        w.driver.wrong = {}
        w.runner.scan_cycle({"DI0": True})         # 安全写 0 成功
        w.sup.reset_channel_fault("AO0", fault_cause_cleared=True)
        # 复位后首个正常拍：业务 100，从 safe0 基准 → 5（非漂移 le 的 15/20）
        r = w.runner.scan_cycle({"DI0": True})
        self.assertEqual(r.outputs()["AO0"], 5)


# ---------------------------------------------------------------------------
# 6) 并发/递归重入失败关闭；诊断快照独立；异常后可恢复
# ---------------------------------------------------------------------------

class TestReentrancyAndDiagnostics(unittest.TestCase):

    def test_recursive_commit_fails_closed(self):
        cb = _CallbackDriver()
        w = _single("INT", 0, driver=cb)
        cb.callback = lambda: w.sup.commit({"CH": 0})
        w.sup.commit({"CH": 5})
        self.assertIsInstance(cb.reentry_error, CommitSupervisorReentryError)
        # 锁释放后可复用
        outcome, err = _commit(w.sup, CH=5)
        self.assertIsNone(err)

    def test_diagnostics_during_commit_fails_closed(self):
        cb = _CallbackDriver()
        w = _single("INT", 0, driver=cb)
        cb.callback = lambda: w.sup.diagnostics()
        w.sup.commit({"CH": 5})
        self.assertIsInstance(cb.reentry_error, CommitSupervisorReentryError)

    def test_reset_during_commit_fails_closed(self):
        cb = _CallbackDriver()
        w = _single("INT", 0, driver=cb)
        cb.callback = lambda: w.sup.reset_channel_fault("CH", fault_cause_cleared=True)
        w.sup.commit({"CH": 5})
        self.assertIsInstance(cb.reentry_error, CommitSupervisorReentryError)

    def test_concurrent_commit_fails_closed(self):
        blocking = _BlockingDriver()
        w = _single("INT", 0, driver=blocking)
        results = {}

        def worker():
            try:
                results["ok"] = w.sup.commit({"CH": 5})
            except Exception as exc:               # pragma: no cover
                results["err"] = exc

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(blocking.entered.wait(timeout=5))
        with self.assertRaises(CommitSupervisorReentryError):
            w.sup.commit({"CH": 5})
        blocking.release.set()
        t.join(timeout=5)
        self.assertNotIn("err", results)

    def test_diagnostics_snapshot_independent(self):
        w = _single("INT", 0)
        _commit(w.sup, CH=5)
        diag = w.sup.diagnostics()
        diag["CH"] = "tampered"                     # 改返回映射不污染内部
        self.assertIsInstance(w.sup.diagnostics()["CH"], ChannelCommitStatus)
        receipts = w.sup.last_commit_receipts()
        receipts.clear()
        self.assertIn("CH", w.sup.last_commit_receipts())

    def test_outcome_receipts_do_not_alias_internal(self):
        # Codex Round 1 反证 3：成功结果 CommitOutcome.receipts 不得与监督器内部最近
        # 回执共享同一可变字典——修改/清空返回结果不影响 last_commit_receipts()。
        w = _single("INT", 0)
        outcome, err = _commit(w.sup, CH=5)
        self.assertIsNone(err)
        # 返回结果为不可变快照，无法反向污染内部
        with self.assertRaises((AttributeError, TypeError)):
            outcome.receipts.clear()
        self.assertIn("CH", w.sup.last_commit_receipts())
        self.assertTrue(w.sup.last_commit_receipts()["CH"].ok)
        # 内部最近回执与返回结果是相互独立的对象
        self.assertIsNot(outcome.receipts, w.sup.last_commit_receipts())

    def test_state_usable_after_partial_commit_exception(self):
        w = _single("INT", 0)
        w.driver.wrong = {"CH": 9}
        _, err = _commit(w.sup, CH=5)
        self.assertIsInstance(err, PartialCommitError)
        # 异常后锁释放、状态一致：驱动恢复后（改写 safe0）可继续提交
        w.driver.wrong = {}
        outcome, err2 = _commit(w.sup, CH=5)
        self.assertIsNone(err2)


# ---------------------------------------------------------------------------
# 8) 装配校验 + runner 共享校验 + 包边界导出
# ---------------------------------------------------------------------------

class TestConfigAndExports(unittest.TestCase):

    def test_rejects_non_policy(self):
        with self.assertRaises(CommitSupervisorConfigError):
            CommitSupervisor(_Driver(), object())

    def test_rejects_non_commit_driver(self):
        w = _single("INT", 0)
        with self.assertRaises(CommitSupervisorConfigError):
            CommitSupervisor(object(), w.policy)

    def test_runner_rejects_supervisor_bound_to_other_policy(self):
        # 引擎门控用策略 P，但 CommitPort 委托的监督器绑定**另一**策略 Q →
        # runner 装配拒绝（避免正常/安全提交走两套逐通道故障状态）。
        w = _wire()                                # 策略 P = w.policy
        q = _wire()                                # 策略 Q = q.policy（同通道集）
        bad_sup = CommitSupervisor(_Driver(), q.policy)
        bad_port = CommitPort(bad_sup)
        engine2 = ScanEngine(w.task, w.layout, Executor(w.task, w.layout),
                             w.policy, bad_port)   # 门控用 P，提交端口委托绑定 Q 的监督器
        with self.assertRaises(ScanRunnerConfigError):
            OuterScanRunner(engine2, w.policy, bad_port)

    def test_package_exports(self):
        import src.runtime as rt
        for name in ("CommitSupervisor", "CommitReceipt", "ChannelCommitStatus",
                     "CommitOutcome", "CommitSupervisorError",
                     "CommitSupervisorConfigError", "CommitSupervisorReentryError",
                     "PartialCommitError"):
            self.assertIn(name, rt.__all__)
            self.assertTrue(hasattr(rt, name))

    def test_module_does_not_import_prototype(self):
        import src.runtime.commit_supervisor as mod
        with open(mod.__file__, encoding="utf-8") as fh:
            self.assertNotIn("prototype_05", fh.read())


if __name__ == "__main__":
    unittest.main()
