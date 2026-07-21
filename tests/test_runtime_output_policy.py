"""WP-20260716-007：生产 OutputPolicy 门控核心与原子安全状态快照测试。

逐条对应任务书"最低测试"清单：BOOL/REAL/整数正常路径；非零安全值；六类原因
及并发优先级；三类强制 safe 配置拒绝；每一种可配置 hold/safe；冷启动 hold；
安全落值绕过限速、恢复后从 safe 基准限速；两拍 last_effective；多通道原子失败；
安全状态整包快照/并发读取；IOMap/Store/类型/数值非法配置；直接注入真实
``ScanEngine`` 至少连续两拍且策略没有绕过 pending。

诚实边界：本文件锁定的是当前 Python 门控/安全状态行为——``commit_fault`` /
``channel_fault`` / shadow / 可信设备反馈 / watchdog 计时均**不在本包**，属后续
工作包；这些测试不构成与 CODESYS PLC 语义一致的证据。
"""
from __future__ import annotations

import threading
import unittest

from src.runtime import (
    Executor,
    IOMap,
    LoadConst,
    LoadVar,
    OutputImageError,
    OutputPolicy,
    OutputPolicyConfigError,
    OutputPolicyError,
    OutputPolicyReentryError,
    OutputPolicyService,
    POUDefinition,
    ProgramInstance,
    SafetySnapshot,
    SafetyStateError,
    SafetyStateService,
    ScanEngine,
    Store,
    StoreVar,
    Task,
    VarDecl,
    build_runtime_store,
)
from src.runtime.output_policy import SafeImageTicket
from src.runtime.process_image import OutputPending


# ---------------------------------------------------------------------------
# 单通道装配辅助（Store + IOMap + 安全服务 + 策略服务 + pending）
# ---------------------------------------------------------------------------

def _single(iec_type, safe_value, rate_limit=None, initial=None, **onflags):
    store = Store()
    store.declare("V", iec_type, initial)
    pol = OutputPolicy("V", iec_type, safe_value, rate_limit=rate_limit, **onflags)
    io_map = [IOMap("V", "CH", "OUT", policy=pol)]
    safety = SafetyStateService(SafetySnapshot.all_ok())
    svc = OutputPolicyService(store, io_map, safety)
    return store, io_map, safety, svc


def _stage(svc, io_map, store):
    pending = OutputPending(store, io_map)
    svc.stage_outputs(pending, store, None, None)
    return pending.staged()


def _cause_snapshot(**overrides):
    base = dict(system_ready=True, output_enable=True, comm_ok=True,
                safety_ok=True, interlock_ok=True, scan_ok=True, watchdog_ok=True)
    base.update(overrides)
    return SafetySnapshot(**base)


# ---------------------------------------------------------------------------
# 正常路径：BOOL / REAL / 整数
# ---------------------------------------------------------------------------

class TestNormalPath(unittest.TestCase):

    def test_bool_normal_path_takes_request(self):
        store, io_map, _, svc = _single("BOOL", False)
        store.write("V", True)
        self.assertEqual(_stage(svc, io_map, store), {"CH": True})
        store.write("V", False)
        self.assertEqual(_stage(svc, io_map, store), {"CH": False})

    def test_int_normal_path_rate_limited_from_cold_start(self):
        # 冷启动基准 = safe_value(0)；rate=5：request 100 → 5，再 →10（两拍 le）
        store, io_map, _, svc = _single("INT", 0, rate_limit=5)
        store.write("V", 100)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 5})
        self.assertEqual(_stage(svc, io_map, store), {"CH": 10})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 10})

    def test_int_within_rate_limit_takes_request(self):
        store, io_map, _, svc = _single("INT", 0, rate_limit=50)
        store.write("V", 30)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 30})

    def test_int_no_rate_limit_takes_request(self):
        store, io_map, _, svc = _single("INT", 0)
        store.write("V", 12345)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 12345})

    def test_real_normal_path_rate_limited(self):
        store, io_map, _, svc = _single("REAL", 0.0, rate_limit=1.5)
        store.write("V", 10.0)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 1.5})
        self.assertEqual(_stage(svc, io_map, store), {"CH": 3.0})

    def test_negative_direction_rate_limit(self):
        store, io_map, _, svc = _single("INT", 0, rate_limit=5)
        store.write("V", -100)
        self.assertEqual(_stage(svc, io_map, store), {"CH": -5})
        self.assertEqual(_stage(svc, io_map, store), {"CH": -10})

    def test_two_scans_last_effective_baseline(self):
        # 第二拍以第一拍 last_effective 为基准，而非 safe_value
        store, io_map, _, svc = _single("INT", 0, rate_limit=10)
        store.write("V", 100)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 10})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 10})
        self.assertEqual(_stage(svc, io_map, store), {"CH": 20})


# ---------------------------------------------------------------------------
# 六类原因、优先级、可配置 hold/safe、非零安全值、冷启动 hold
# ---------------------------------------------------------------------------

class TestFaultCausesAndPriority(unittest.TestCase):

    def _seed(self, svc, io_map, store, value):
        """跑一拍正常路径给通道播种 last_effective=value。"""
        store.write("V", value)
        _stage(svc, io_map, store)
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": value})

    def test_nonzero_safe_value_on_forced_safe_cause(self):
        store, io_map, safety, svc = _single("INT", 7, rate_limit=100)
        self._seed(svc, io_map, store, 50)          # le=50
        safety.replace(_cause_snapshot(safety_ok=False))
        store.write("V", 50)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 7})   # 非零安全值

    def test_each_forced_safe_cause_drops_to_safe(self):
        for sig in ("safety_ok", "interlock_ok", "scan_ok", "watchdog_ok"):
            store, io_map, safety, svc = _single("INT", 9)
            safety.replace(_cause_snapshot(**{sig: False}))
            store.write("V", 42)
            self.assertEqual(_stage(svc, io_map, store), {"CH": 9},
                             "%s 应强制落安全值" % sig)

    def test_configurable_cause_safe(self):
        for sig, field in (("system_ready", "on_startup_not_ready"),
                           ("output_enable", "on_operator_disable"),
                           ("comm_ok", "on_comm_loss")):
            store, io_map, safety, svc = _single("INT", 3, **{field: "safe"})
            safety.replace(_cause_snapshot(**{sig: False}))
            store.write("V", 88)
            self.assertEqual(_stage(svc, io_map, store), {"CH": 3},
                             "%s=safe 应落安全值" % field)

    def test_configurable_cause_hold_takes_last_effective(self):
        for sig, field in (("system_ready", "on_startup_not_ready"),
                           ("output_enable", "on_operator_disable"),
                           ("comm_ok", "on_comm_loss")):
            store, io_map, safety, svc = _single("INT", 0, **{field: "hold"})
            self._seed(svc, io_map, store, 55)      # le=55
            safety.replace(_cause_snapshot(**{sig: False}))
            store.write("V", 88)
            self.assertEqual(_stage(svc, io_map, store), {"CH": 55},
                             "%s=hold 应保持 last_effective" % field)

    def test_cold_start_hold_degrades_to_safe(self):
        # 无历史（冷启动）时 hold 退化为 safe_value；该退化值即本拍逻辑生效值，
        # 须前移 last_effective（§4.1 表：每拍策略计算完成后更新），不留 None。
        store, io_map, safety, svc = _single("INT", 4, on_comm_loss="hold")
        safety.replace(_cause_snapshot(comm_ok=False))
        store.write("V", 88)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 4})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 4})

    def test_cold_start_hold_then_hold_keeps_degraded_value(self):
        # 冷启动 hold→safe 后再 hold：取已前移的 last_effective(4)，不回到 None
        # 分支，也不错误跳到 request。
        store, io_map, safety, svc = _single("INT", 4, on_comm_loss="hold")
        safety.replace(_cause_snapshot(comm_ok=False))
        store.write("V", 88)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 4})
        self.assertEqual(_stage(svc, io_map, store), {"CH": 4})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 4})

    def test_forced_safe_then_configurable_hold_holds_safe_value(self):
        # Codex Round 1 必须返修 1 的最小反证：正常拍 50 → safety_trip 强制 0
        # → 下一拍仅 comm_loss(hold)。hold = 上拍**逻辑生效值** = 0（§4 约束 3、
        # §4.1 表），不得回跳到故障前的旧正常值 50。
        store, io_map, safety, svc = _single(
            "INT", 0, rate_limit=100, on_comm_loss="hold")
        self._seed(svc, io_map, store, 50)                  # 正常拍：le=50
        safety.replace(_cause_snapshot(safety_ok=False))
        self.assertEqual(_stage(svc, io_map, store), {"CH": 0})    # 强制落安全值
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 0},
                         "safe 落值后 last_effective 必须前移为本拍 final=0")
        safety.replace(_cause_snapshot(comm_ok=False))
        store.write("V", 50)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 0},
                         "hold 应取上拍逻辑生效值 0，而非故障前的 50")
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 0})

    def test_configurable_safe_then_hold_holds_safe_value(self):
        # 同一反证的可配置 safe 变体：comm_loss(safe) 落安全值 → operator_disable
        # (hold) 取到的仍是 safe_value，而非故障前正常值。
        store, io_map, safety, svc = _single(
            "INT", 7, rate_limit=100, on_comm_loss="safe",
            on_operator_disable="hold")
        self._seed(svc, io_map, store, 50)
        safety.replace(_cause_snapshot(comm_ok=False))
        self.assertEqual(_stage(svc, io_map, store), {"CH": 7})
        safety.replace(_cause_snapshot(output_enable=False))
        self.assertEqual(_stage(svc, io_map, store), {"CH": 7})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 7})

    def test_hold_with_history_keeps_last_effective_stable(self):
        # hold 命中且有历史：final = last_effective 不变，连续多拍稳定（前移语义
        # 不得把 hold 值漂移）。
        store, io_map, safety, svc = _single("INT", 0, on_comm_loss="hold")
        self._seed(svc, io_map, store, 55)
        safety.replace(_cause_snapshot(comm_ok=False))
        store.write("V", 88)
        for _ in range(3):
            self.assertEqual(_stage(svc, io_map, store), {"CH": 55})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 55})

    def test_priority_picks_stricter_configurable_cause(self):
        # comm_loss(hold) 与 operator_disable(safe) 并发：comm_loss 优先 → hold
        store, io_map, safety, svc = _single(
            "INT", 0, on_comm_loss="hold", on_operator_disable="safe")
        self._seed(svc, io_map, store, 33)
        safety.replace(_cause_snapshot(comm_ok=False, output_enable=False))
        store.write("V", 88)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 33})

    def test_forced_safe_wins_over_configurable_hold(self):
        # safety_trip 与 comm_loss(hold) 并发：强制 safe 胜出
        store, io_map, safety, svc = _single("INT", 1, on_comm_loss="hold")
        self._seed(svc, io_map, store, 33)
        safety.replace(_cause_snapshot(safety_ok=False, comm_ok=False))
        store.write("V", 88)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 1})


# ---------------------------------------------------------------------------
# 安全落值绕过限速 + 恢复后从 safe 基准限速
# ---------------------------------------------------------------------------

class TestSafeDropAndRecovery(unittest.TestCase):

    def test_safe_drop_bypasses_rate_limit(self):
        store, io_map, safety, svc = _single("INT", 0, rate_limit=5)
        store.write("V", 100)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 5})   # le=5
        # 故障：一步落 safe_value=0，绕过 rate=5（否则只能到 0，此处正好 0；
        # 用非零 safe 更能证明绕过）
        safety.replace(_cause_snapshot(scan_ok=False))
        self.assertEqual(_stage(svc, io_map, store), {"CH": 0})

    def test_safe_drop_to_nonzero_bypasses_rate_limit(self):
        # 冷启动基准 = safe_value=80；request=20 逐拍 -5 走到 le=20
        store, io_map, safety, svc = _single("INT", 80, rate_limit=5)
        store.write("V", 20)
        out = None
        for _ in range(12):
            out = _stage(svc, io_map, store)      # 80→75→…→20
        self.assertEqual(out, {"CH": 20})
        safety.replace(_cause_snapshot(watchdog_ok=False))
        # 一步跳到 safe_value=80（|80-20|=60 >> rate 5，证明绕过限速）
        self.assertEqual(_stage(svc, io_map, store), {"CH": 80})

    def test_recovery_rate_limits_from_safe_baseline(self):
        # 落安全值后恢复正常路径首拍，从 safe_value 基准限速（非故障前 le）
        store, io_map, safety, svc = _single("INT", 0, rate_limit=5)
        store.write("V", 100)
        _stage(svc, io_map, store)                # le=5
        _stage(svc, io_map, store)                # le=10
        safety.replace(_cause_snapshot(safety_ok=False))
        self.assertEqual(_stage(svc, io_map, store), {"CH": 0})   # 落安全值
        # 恢复：基准回到 safe_value(0)，request 100 → 5（而非从 10 → 15）
        safety.replace(SafetySnapshot.all_ok())
        self.assertEqual(_stage(svc, io_map, store), {"CH": 5})


# ---------------------------------------------------------------------------
# 运行期 IEC 数值域：非有限 / 越界 request 必须拒绝，不得 stage
# （Codex Round 1 必须返修 2；配置期与运行期同一口径）
# ---------------------------------------------------------------------------

class TestRuntimeNumericValidity(unittest.TestCase):

    def _assert_rejected(self, iec_type, safe_value, bad_request):
        store, io_map, _, svc = _single(iec_type, safe_value)
        store.write("V", bad_request)
        pending = OutputPending(store, io_map)
        with self.assertRaises(OutputPolicyError,
                               msg="%s request=%r 应被拒绝" % (iec_type, bad_request)):
            svc.stage_outputs(pending, store, None, None)
        # 失败关闭：既未 stage 非法值，也未前移内部状态
        self.assertEqual(pending.staged(), {})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": None})

    def test_non_finite_real_request_rejected_not_staged(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            self._assert_rejected("REAL", 0.0, bad)

    def test_out_of_range_int_request_rejected_not_staged(self):
        for iec_type, bad in (("USINT", 999), ("USINT", -1), ("SINT", 128),
                              ("INT", 40000)):
            self._assert_rejected(iec_type, 0, bad)

    def test_in_range_request_still_accepted(self):
        # 运行期收紧不得误杀合法边界值
        store, io_map, _, svc = _single("USINT", 0)
        store.write("V", 255)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 255})

    def test_rate_limited_final_stays_in_declared_range(self):
        # 限速路径的 final 也走同一数值域校验；正常收敛不受影响
        store, io_map, _, svc = _single("USINT", 0, rate_limit=100)
        store.write("V", 255)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 100})
        self.assertEqual(_stage(svc, io_map, store), {"CH": 200})
        self.assertEqual(_stage(svc, io_map, store), {"CH": 255})

    def _assert_rejected_under_cause(self, iec_type, safe_value, bad_request,
                                     snapshot, expected_last_effective=None,
                                     **onflags):
        """故障状态下非法 request 必须与正常路径同口径失败关闭。"""
        store, io_map, safety, svc = _single(iec_type, safe_value, **onflags)
        if expected_last_effective is not None:
            # 先跑一拍正常路径建立历史，验证失败时不前移该历史
            store.write("V", expected_last_effective)
            _stage(svc, io_map, store)
        safety.replace(snapshot)
        store.write("V", bad_request)
        pending = OutputPending(store, io_map)
        with self.assertRaises(
                OutputPolicyError,
                msg="%s 故障态 request=%r 应被拒绝" % (iec_type, bad_request)):
            svc.stage_outputs(pending, store, None, None)
        self.assertEqual(pending.staged(), {})
        self.assertEqual(svc.diagnostic_last_effective(),
                         {"CH": expected_last_effective})

    def test_forced_safe_cause_still_rejects_illegal_request(self):
        # Codex Round 2 反证：safety_trip 下 REAL NaN / USINT 999 此前被静默
        # 接受并 stage safe_value，internal state 同时前移。
        for bad in (float("nan"), float("inf")):
            self._assert_rejected_under_cause(
                "REAL", 0.0, bad, _cause_snapshot(safety_ok=False))
        for iec_type, bad in (("USINT", 999), ("USINT", -1), ("SINT", 128)):
            self._assert_rejected_under_cause(
                iec_type, 0, bad, _cause_snapshot(safety_ok=False))

    def test_forced_safe_cause_illegal_request_does_not_advance_history(self):
        self._assert_rejected_under_cause(
            "USINT", 0, 999, _cause_snapshot(watchdog_ok=False),
            expected_last_effective=50)
        self._assert_rejected_under_cause(
            "REAL", 0.0, float("nan"), _cause_snapshot(scan_ok=False),
            expected_last_effective=50.0)

    def test_configurable_safe_cause_still_rejects_illegal_request(self):
        self._assert_rejected_under_cause(
            "USINT", 0, 999, _cause_snapshot(comm_ok=False),
            expected_last_effective=50, on_comm_loss="safe")

    def test_configurable_hold_cause_still_rejects_illegal_request(self):
        # hold 分支同样不得因"输出不取 request"而跳过 request 校验
        self._assert_rejected_under_cause(
            "USINT", 0, 999, _cause_snapshot(comm_ok=False),
            expected_last_effective=50, on_comm_loss="hold")
        self._assert_rejected_under_cause(
            "REAL", 0.0, float("-inf"), _cause_snapshot(comm_ok=False),
            expected_last_effective=50.0, on_comm_loss="hold")

    def test_cold_start_hold_cause_still_rejects_illegal_request(self):
        # 冷启动 hold（无历史）退化为 safe_value，仍不接受非法 request
        self._assert_rejected_under_cause(
            "USINT", 0, 999, _cause_snapshot(comm_ok=False),
            on_comm_loss="hold")

    def test_legal_request_under_cause_still_gated(self):
        # 收紧不得误杀：故障态下合法 request 仍按原策略落安全值/保持
        store, io_map, safety, svc = _single("USINT", 7, on_comm_loss="hold")
        safety.replace(_cause_snapshot(safety_ok=False))
        store.write("V", 200)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 7})


# ---------------------------------------------------------------------------
# 原子性：多通道失败不留部分内部状态 / 部分 stage
# ---------------------------------------------------------------------------

class _FaultyStore:
    """委托真实 Store，但读某变量时抛异常（构造多通道计算期失败）。"""

    def __init__(self, real, boom_var):
        self._real = real
        self._boom = boom_var

    def read(self, key):
        if key == self._boom:
            raise RuntimeError("read boom: %s" % key)
        return self._real.read(key)

    def declared_type(self, key):
        return self._real.declared_type(key)


class TestAtomicMultiChannel(unittest.TestCase):

    def _two_channel(self):
        store = Store()
        store.declare("A", "INT", 0)
        store.declare("B", "INT", 0)
        io_map = [
            IOMap("A", "CHA", "OUT", policy=OutputPolicy("A", "INT", 0)),
            IOMap("B", "CHB", "OUT", policy=OutputPolicy("B", "INT", 0)),
        ]
        safety = SafetyStateService(SafetySnapshot.all_ok())
        return store, io_map, safety, OutputPolicyService(store, io_map, safety)

    def test_channel_failure_leaves_no_partial_state_or_pending(self):
        store, io_map, _, svc = self._two_channel()
        store.write("A", 11)
        store.write("B", 22)
        pending = OutputPending(store, io_map)
        faulty = _FaultyStore(store, "B")      # 第二通道计算期抛错

        with self.assertRaises(RuntimeError):
            svc.stage_outputs(pending, faulty, None, None)

        # 无部分 stage：pending 为空
        self.assertEqual(pending.staged(), {})
        # 无部分内部状态：两通道 last_effective 均未前移
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

        # 恢复：用完好 store 正常提交，证明失败拍未污染后续
        result = svc.stage_outputs(OutputPending(store, io_map), store, None, None)
        self.assertIsNone(result)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": 11, "CHB": 22})


# ---------------------------------------------------------------------------
# 并发/重入失败关闭
# ---------------------------------------------------------------------------

class _ReentrantPending:
    """stage() 期间递归回调服务，触发失败关闭。"""

    def __init__(self, svc, store):
        self._svc = svc
        self._store = store
        self.inner_error = None
        self.staged = {}

    def stage(self, channel, value):
        if self.inner_error is None:
            try:
                self._svc.stage_outputs(self, self._store, None, None)
            except OutputPolicyReentryError as exc:
                self.inner_error = exc
        self.staged[channel] = value


class TestReentrancy(unittest.TestCase):

    def test_reentrant_call_fails_closed(self):
        store, io_map, _, svc = _single("INT", 0)
        store.write("V", 5)
        pending = _ReentrantPending(svc, store)
        svc.stage_outputs(pending, store, None, None)
        self.assertIsInstance(pending.inner_error, OutputPolicyReentryError)
        self.assertEqual(pending.staged, {"CH": 5})
        # 外层拍正常完成、锁释放：下一拍仍可执行
        self.assertEqual(_stage(svc, io_map, store), {"CH": 5})


# ---------------------------------------------------------------------------
# 安全状态：整包快照、不可变、线程安全整包替换/并发读取
# ---------------------------------------------------------------------------

class TestSafetyState(unittest.TestCase):

    _FIELDS = ("system_ready", "output_enable", "comm_ok", "safety_ok",
               "interlock_ok", "scan_ok", "watchdog_ok")

    def test_snapshot_is_immutable(self):
        snap = SafetySnapshot.all_ok()
        with self.assertRaises(Exception):
            snap.system_ready = False           # frozen dataclass

    def test_snapshot_rejects_non_bool(self):
        with self.assertRaises(SafetyStateError):
            SafetySnapshot(1, True, True, True, True, True, True)

    def test_service_replace_is_whole_package(self):
        svc = SafetyStateService(SafetySnapshot.all_ok())
        self.assertTrue(svc.read().system_ready)
        tripped = _cause_snapshot(safety_ok=False)
        svc.replace(tripped)
        self.assertIs(svc.read(), tripped)      # 整包替换，读到完整新快照

    def test_service_rejects_non_snapshot(self):
        with self.assertRaises(SafetyStateError):
            SafetyStateService(object())
        svc = SafetyStateService(SafetySnapshot.all_ok())
        with self.assertRaises(SafetyStateError):
            svc.replace({"system_ready": True})

    def test_concurrent_read_never_torn(self):
        svc = SafetyStateService(SafetySnapshot.all_ok())
        all_true = SafetySnapshot.all_ok()
        all_false = SafetySnapshot(*([False] * 7))
        stop = threading.Event()
        distinct_counts = []

        def writer():
            for _ in range(3000):
                svc.replace(all_true)
                svc.replace(all_false)
            stop.set()

        def reader():
            while not stop.is_set():
                snap = svc.read()
                vals = {getattr(snap, f) for f in self._FIELDS}
                distinct_counts.append(len(vals))

        w = threading.Thread(target=writer)
        readers = [threading.Thread(target=reader) for _ in range(3)]
        for t in readers:
            t.start()
        w.start()
        w.join(timeout=10)
        for t in readers:
            t.join(timeout=10)

        # 每次读到的快照必是同质（全 True 或全 False）——撕裂会出现 2 种值
        self.assertTrue(distinct_counts)
        self.assertEqual(max(distinct_counts), 1)


# ---------------------------------------------------------------------------
# 配置非法：策略字段 / IOMap / Store / 类型 / 数值
# ---------------------------------------------------------------------------

class TestConfigRejection(unittest.TestCase):

    def test_forced_safe_cause_hold_rejected(self):
        for field in ("on_safety_trip", "on_scan_fault", "on_watchdog"):
            with self.assertRaises(OutputPolicyConfigError, msg=field):
                OutputPolicy("V", "BOOL", False, **{field: "hold"})

    def test_unknown_action_rejected(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "BOOL", False, on_comm_loss="freeze")

    def test_safe_value_type_mismatch_rejected(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "INT", 1.5)         # INT 收到 float
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "BOOL", 0)          # BOOL 收到 int
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "REAL", 1)          # REAL 收到 int（不隐式放宽）

    def test_illegal_iec_type_rejected(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "FLOAT", 0.0)

    def test_int_rate_limit_must_be_int_not_float(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "INT", 0, rate_limit=1.5)
        # bool 亦拒绝（bool 是 int 子类，但语义非法）
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "INT", 0, rate_limit=True)

    def test_real_rate_limit_must_be_float(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "REAL", 0.0, rate_limit=2)

    def test_bool_and_string_reject_rate_limit(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "BOOL", False, rate_limit=1)
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "STRING", "", rate_limit=1)

    # ---- Codex Round 1 必须返修 2：IEC 数值域（非有限 / 越界）配置期反证 ----

    def test_real_safe_value_must_be_finite(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(OutputPolicyConfigError,
                                   msg="REAL safe_value=%r 应拒绝" % bad):
                OutputPolicy("V", "REAL", bad)

    def test_real_rate_limit_must_be_finite(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(OutputPolicyConfigError,
                                   msg="REAL rate_limit=%r 应拒绝" % bad):
                OutputPolicy("V", "REAL", 0.0, rate_limit=bad)

    def test_safe_value_out_of_declared_int_range_rejected(self):
        # 无符号越上界 / 无符号取负 / 有符号越界，均须在装配期拒绝
        for iec_type, bad in (("USINT", 999), ("USINT", 256), ("USINT", -1),
                              ("SINT", 128), ("SINT", -129), ("BYTE", 256),
                              ("INT", 32768), ("INT", -32769)):
            with self.assertRaises(OutputPolicyConfigError,
                                   msg="%s safe_value=%r 应拒绝" % (iec_type, bad)):
                OutputPolicy("V", iec_type, bad)

    def test_safe_value_at_declared_int_bounds_accepted(self):
        # 边界内合法值不得被误杀（拒绝规则须精确到声明范围端点）
        for iec_type, ok in (("USINT", 0), ("USINT", 255), ("SINT", -128),
                             ("SINT", 127), ("INT", 32767), ("BYTE", 255)):
            self.assertEqual(OutputPolicy("V", iec_type, ok).safe_value, ok)

    def test_rate_limit_not_exactly_representable_in_declared_type_rejected(self):
        # USINT 限速 256 无法在声明类型内精确表达 → 拒绝（不得回绕成 0）
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "USINT", 0, rate_limit=256)
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "SINT", 0, rate_limit=128)

    def test_negative_rate_limit_rejected(self):
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicy("V", "INT", 0, rate_limit=-1)

    def test_commit_fault_retry_n_must_be_positive_int(self):
        for bad in (0, -3, 1.0, True):
            with self.assertRaises(OutputPolicyConfigError, msg=repr(bad)):
                OutputPolicy("V", "INT", 0, commit_fault_retry_n=bad)

    # ---- 服务装配期 ----

    def _store_with(self, name="V", iec_type="INT"):
        store = Store()
        store.declare(name, iec_type, None)
        return store

    def test_service_rejects_missing_policy(self):
        store = self._store_with()
        io_map = [IOMap("V", "CH", "OUT", policy=None)]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))

    def test_service_rejects_non_production_policy_object(self):
        store = self._store_with()
        io_map = [IOMap("V", "CH", "OUT", policy=object())]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))

    def test_service_rejects_var_mismatch(self):
        store = self._store_with()
        io_map = [IOMap("V", "CH", "OUT", policy=OutputPolicy("OTHER", "INT", 0))]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))

    def test_service_rejects_type_mismatch_with_store(self):
        store = self._store_with(iec_type="INT")
        io_map = [IOMap("V", "CH", "OUT", policy=OutputPolicy("V", "REAL", 0.0))]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))

    def test_service_rejects_unknown_store_var(self):
        store = Store()                            # V 未声明
        io_map = [IOMap("V", "CH", "OUT", policy=OutputPolicy("V", "INT", 0))]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))

    def test_service_rejects_duplicate_channel(self):
        store = self._store_with()
        io_map = [
            IOMap("V", "CH", "OUT", policy=OutputPolicy("V", "INT", 0)),
            IOMap("V", "CH", "OUT", policy=OutputPolicy("V", "INT", 0)),
        ]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))

    def test_service_rejects_non_safety_service(self):
        store = self._store_with()
        io_map = [IOMap("V", "CH", "OUT", policy=OutputPolicy("V", "INT", 0))]
        with self.assertRaises(OutputPolicyConfigError):
            OutputPolicyService(store, io_map, object())

    def test_service_ignores_in_direction(self):
        store = Store()
        store.declare("Vout", "INT", 0)
        io_map = [
            IOMap("Din", "DI0", "IN"),             # 只处理 OUT，IN 跳过
            IOMap("Vout", "CH", "OUT", policy=OutputPolicy("Vout", "INT", 0)),
        ]
        svc = OutputPolicyService(store, io_map, SafetyStateService(SafetySnapshot.all_ok()))
        self.assertEqual(svc.channels(), ("CH",))


# ---------------------------------------------------------------------------
# 端口契约：直接注入真实 ScanEngine（连续两拍，策略不绕过 pending）
# ---------------------------------------------------------------------------

class _RecordingCommitter:
    def __init__(self):
        self.received = []

    def commit(self, outputs):
        self.received.append(dict(outputs))


def _engine_task():
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("AV", "INT", section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=OutputPolicy("Motor", "BOOL", False)),
        IOMap("AV", "AO0", "OUT",
              policy=OutputPolicy("AV", "INT", 0, rate_limit=5)),
    ]
    code = [
        LoadVar("Start", "BOOL"), StoreVar("Motor", "BOOL"),
        LoadConst(100, "INT"), StoreVar("AV", "INT"),
    ]
    main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
    return Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                io_map=io_map, pou_lib={"Main": main})


class TestRealEngineIntegration(unittest.TestCase):

    def _build(self, safety=None):
        task = _engine_task()
        layout = build_runtime_store(task)
        executor = Executor(task, layout)
        safety = safety or SafetyStateService(SafetySnapshot.all_ok())
        policy = OutputPolicyService(layout.store, task.io_map, safety)
        committer = _RecordingCommitter()
        engine = ScanEngine(task, layout, executor, policy, committer)
        return engine, layout, safety, policy, committer

    def test_two_scans_policy_applied_and_not_bypassed(self):
        engine, layout, _, policy, committer = self._build()

        r1 = engine.scan({"DI0": True})
        # Motor 直取 request；AV 被限速（业务写了 100，但输出只 5）——证明
        # request 未绕过策略直达 pending
        self.assertEqual(r1.outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(layout.store.read("AV"), 100)   # 业务确实写了 100

        r2 = engine.scan({"DI0": False})
        self.assertEqual(r2.outputs(), {"DO0": False, "AO0": 10})
        self.assertEqual(committer.received, [{"DO0": True, "AO0": 5},
                                              {"DO0": False, "AO0": 10}])
        self.assertEqual(policy.diagnostic_last_effective(), {"DO0": False, "AO0": 10})

    def test_safety_trip_drives_all_outputs_to_safe(self):
        safety = SafetyStateService(_cause_snapshot(safety_ok=False))
        engine, _, _, _, committer = self._build(safety=safety)
        result = engine.scan({"DI0": True})
        # 强制安全：Motor→False、AV→0（safe_value，一步到位）
        self.assertEqual(result.outputs(), {"DO0": False, "AO0": 0})

    def test_service_channels_match_engine_out_channels(self):
        engine, _, _, policy, _ = self._build()
        # 策略服务的通道集恰为引擎 OUT 通道集——完整 stage，不缺不多
        self.assertEqual(set(policy.channels()), {"DO0", "AO0"})
        engine.scan({"DI0": True})   # 不触发 OutputStagingError 即证明通道对齐


# ---------------------------------------------------------------------------
# WP-20260720-008 新公共 API：stage_safe_image / safety_state
# （外层 scan/watchdog runner 消费的专用安全映像入口；反证测试，不放宽既有语义）
# ---------------------------------------------------------------------------

class TestSafeImageEntry(unittest.TestCase):

    def _two_channel(self, a_type="INT", a_safe=7, b_type="REAL", b_safe=3.5):
        store = Store()
        store.declare("A", a_type, None)
        store.declare("B", b_type, None)
        io_map = [
            IOMap("A", "CHA", "OUT", policy=OutputPolicy("A", a_type, a_safe)),
            IOMap("B", "CHB", "OUT", policy=OutputPolicy("B", b_type, b_safe)),
        ]
        safety = SafetyStateService(SafetySnapshot.all_ok())
        return store, io_map, safety, OutputPolicyService(store, io_map, safety)

    def test_safety_state_property_returns_injected_service(self):
        store, io_map, safety, svc = _single("INT", 0)
        self.assertIs(svc.safety_state, safety)

    def test_stage_safe_image_returns_ticket_without_advancing_history(self):
        # 两阶段事务第一阶段：stage 只 staging + 签发一次性令牌，**绝不**前移历史。
        store, io_map, _, svc = self._two_channel()
        pending = OutputPending(store, io_map)
        ticket = svc.stage_safe_image(pending)
        self.assertIsInstance(ticket, SafeImageTicket)
        self.assertEqual(ticket.image, {"CHA": 7, "CHB": 3.5})
        self.assertEqual(pending.staged(), {"CHA": 7, "CHB": 3.5})
        # staging 后 last_effective 仍为冷启动 None（未 confirm 不前移）
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_ticket_image_is_independent_copy(self):
        # 令牌暴露的 image 是独立副本，外部改写不污染服务内部准备状态。
        store, io_map, _, svc = self._two_channel()
        ticket = svc.stage_safe_image(OutputPending(store, io_map))
        got = ticket.image
        got["CHA"] = 999
        self.assertEqual(ticket.image, {"CHA": 7, "CHB": 3.5})

    def test_confirm_safe_image_advances_history_after_commit(self):
        # 第二阶段：confirm 凭同一令牌才把 last_effective 前移为真正提交的安全映像。
        store, io_map, _, svc = self._two_channel()
        pending = OutputPending(store, io_map)
        ticket = svc.stage_safe_image(pending)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})
        svc.confirm_safe_image(ticket)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": 7, "CHB": 3.5})

    def test_confirm_safe_image_rejects_arbitrary_mapping(self):
        # Codex Round 2 反证 2：任意 Mapping（含同键错误值）**不能**冒充已提交事务
        # 污染 last_effective——confirm 只接受本服务签发的 SafeImageTicket。
        store, io_map, _, svc = self._two_channel(b_type="USINT", b_safe=9)
        svc.stage_safe_image(OutputPending(store, io_map))   # 先备一个真令牌
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image({"CHA": True, "CHB": 999})  # 同键错误值的裸字典
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_confirm_safe_image_rejects_foreign_ticket(self):
        # 他服务签发的令牌不得跨服务确认（签发者身份校验）。
        store, io_map, _, svc = self._two_channel()
        _, other_io, _, other_svc = self._two_channel()
        foreign = other_svc.stage_safe_image(OutputPending(store, other_io))
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image(foreign)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_confirm_safe_image_rejects_reused_ticket(self):
        # 一次性：同一令牌不得重复确认（第二次令牌已消费 → 拒绝）。
        store, io_map, _, svc = self._two_channel()
        ticket = svc.stage_safe_image(OutputPending(store, io_map))
        svc.confirm_safe_image(ticket)
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image(ticket)

    def test_confirm_safe_image_rejects_superseded_ticket(self):
        # 令牌一次性：再次 stage 会作废上一枚未消费令牌，旧令牌确认被拒。
        store, io_map, _, svc = self._two_channel()
        stale = svc.stage_safe_image(OutputPending(store, io_map))
        svc.stage_safe_image(OutputPending(store, io_map))   # 签发新令牌
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image(stale)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_confirm_safe_image_rejects_tampered_value(self):
        # 逐通道值校验：令牌映像被篡改为**同键错误值**（≠ 配置 safe_value）→ 拒绝。
        store, io_map, _, svc = self._two_channel(a_type="USINT", a_safe=9)
        ticket = svc.stage_safe_image(OutputPending(store, io_map))
        object.__setattr__(ticket, "_image", {"CHA": 5, "CHB": 3.5})   # 5 ≠ 9
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image(ticket)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_confirm_safe_image_rejects_out_of_range_value(self):
        # 逐通道值校验：令牌映像被篡改为**越界值**（IEC 数值域非法）→ 拒绝。
        store, io_map, _, svc = self._two_channel(a_type="USINT", a_safe=9)
        ticket = svc.stage_safe_image(OutputPending(store, io_map))
        # 同时漂移配置与令牌映像到 999：绕过“值 == safe_value”，命中 IEC 域校验。
        object.__setattr__(io_map[0].policy, "safe_value", 999)
        object.__setattr__(ticket, "_image", {"CHA": 999, "CHB": 3.5})
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image(ticket)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_confirm_safe_image_rejects_non_finite_value(self):
        # 逐通道值校验：REAL 通道令牌映像被篡改为非有限值 → 拒绝。
        store, io_map, _, svc = self._two_channel()   # CHB 为 REAL/3.5
        ticket = svc.stage_safe_image(OutputPending(store, io_map))
        object.__setattr__(io_map[1].policy, "safe_value", float("inf"))
        object.__setattr__(ticket, "_image", {"CHA": 7, "CHB": float("inf")})
        with self.assertRaises(OutputPolicyError):
            svc.confirm_safe_image(ticket)
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_stage_safe_image_ignores_business_request(self):
        # 不读 request：即便 Store 中 request 非法/非有限，安全映像仍按 safe_value 落值
        store, io_map, _, svc = _single("USINT", 9)
        store.write("V", 999)                      # 非法 request
        pending = OutputPending(store, io_map)
        self.assertEqual(svc.stage_safe_image(pending).image, {"CH": 9})
        self.assertEqual(pending.staged(), {"CH": 9})

    def test_stage_safe_image_all_or_nothing_on_drifted_safe_value(self):
        # 防御通道配置漂移：篡改第二通道 safe_value 为越界值（绕过 frozen 校验），
        # 运行期同口径再校验须整体失败关闭——无部分 stage、无历史前移。
        store, io_map, _, svc = self._two_channel(b_type="USINT", b_safe=9)
        drifted = io_map[1].policy
        object.__setattr__(drifted, "safe_value", 999)
        pending = OutputPending(store, io_map)
        with self.assertRaises(OutputPolicyError):
            svc.stage_safe_image(pending)
        self.assertEqual(pending.staged(), {})
        self.assertEqual(svc.diagnostic_last_effective(), {"CHA": None, "CHB": None})

    def test_confirm_safe_image_sets_boundary_for_recovery(self):
        # 安全映像 confirm 后恢复正常拍：限速基准回到 safe_value（非故障前
        # last_effective）——须经两阶段 stage + confirm 才生效。
        store, io_map, _, svc = _single("INT", 80, rate_limit=5)
        store.write("V", 20)
        out = None
        for _ in range(12):
            out = _stage(svc, io_map, store)       # 冷启动 80 → 逐步降到 20
        self.assertEqual(out, {"CH": 20})
        pending = OutputPending(store, io_map)
        ticket = svc.stage_safe_image(pending)     # 一步落 80（staging，不前移）
        self.assertEqual(ticket.image, {"CH": 80})
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 20})   # 未 confirm
        svc.confirm_safe_image(ticket)             # 提交成功后 confirm
        self.assertEqual(svc.diagnostic_last_effective(), {"CH": 80})
        store.write("V", 20)
        self.assertEqual(_stage(svc, io_map, store), {"CH": 75})       # 从 80 基准限速

    def test_stage_safe_image_reentry_fails_closed(self):
        store, io_map, _, svc = _single("INT", 0)

        class _ReentrantSafePending:
            def __init__(self):
                self.inner_error = None
                self.staged_vals = {}

            def stage(self, channel, value):
                if self.inner_error is None:
                    try:
                        svc.stage_safe_image(self)
                    except OutputPolicyReentryError as exc:
                        self.inner_error = exc
                self.staged_vals[channel] = value

        pending = _ReentrantSafePending()
        svc.stage_safe_image(pending)
        self.assertIsInstance(pending.inner_error, OutputPolicyReentryError)
        # 外层落值成功、锁释放：下一次仍可执行
        self.assertEqual(_stage(svc, io_map, store), {"CH": 0})


# ---------------------------------------------------------------------------
# 包边界：稳定导出
# ---------------------------------------------------------------------------

class TestPackageExports(unittest.TestCase):

    def test_new_public_api_exported(self):
        import src.runtime as rt
        for name in ("OutputPolicy", "SafetySnapshot", "SafetyStateService",
                     "OutputPolicyService", "OutputPolicyError",
                     "OutputPolicyConfigError", "OutputPolicyReentryError",
                     "SafetyStateError"):
            self.assertIn(name, rt.__all__)
            self.assertTrue(hasattr(rt, name))

    def test_existing_exports_not_regressed(self):
        import src.runtime as rt
        for name in ("ScanEngine", "Executor", "Store", "Task", "validate_task"):
            self.assertIn(name, rt.__all__)

    def test_module_does_not_import_prototype(self):
        import src.runtime.output_policy as mod
        with open(mod.__file__, encoding="utf-8") as fh:
            self.assertNotIn("prototype_05", fh.read())


if __name__ == "__main__":
    unittest.main()
