"""WP-20260722-012：阶段 1 Shadow mode / write disable 与安全实写切换测试。

逐条对应任务书"最低新增反证"清单（``git diff --check`` 由 Codex 交接后独立执行）：

1. **默认 shadow 零驱动调用**：``WriteGate()`` 省略参数即 shadow，新装配扫描栈首拍
   不触碰底层驱动 / ``CommitSupervisor``；成功结果为 ``ShadowScanResult``
   （``physically_committed=False``），只暴露 ``logical_outputs()``。
2. **连续两拍 prev/last_effective 推进且 LPC/故障状态冻结**：shadow 正常拍仍跑五步、
   第 5 步“只算不写”推进 ``prev`` 与逻辑 ``last_effective``、模拟量限速连续；底层
   驱动 / 监督器零调用，``last_physical_committed`` / ``commit_fault`` / ``channel_fault``
   不变。
3. **扫描异常与 watchdog 零写出且信号不冒充物理成功**：shadow 故障拍 / watchdog 仍
   锁存安全状态、算全通道安全映像、经 ``adopt_safe_image_shadow`` 逻辑采用；物理提交
   零次，信号 ``shadow`` / ``write_suppressed_by_shadow`` / ``shadow_logic_adopted``
   为真而 ``safe_commit_succeeded=False``，不调用冒充物理落值的 ``confirm_safe_image``。
4. **shadow→实写从非零 ``safe_value`` 限速而非 shadow LE/LPC**：切换先原子挂起全通道
   边界重建，实写首拍限速基准回到 ``safe_value``；恰一次物理提交，后续拍恢复既有语义。
5. **既有 LPC 反证**：实写建立 LPC → shadow → 实写，首拍仍从 ``safe_value`` 而非旧
   ``last_physical_committed`` 或 shadow 逻辑值对齐。
6. **实写→shadow 立即停写**：进入 shadow 后下一拍零物理写、LPC 保持最后真实确认值。
7. **模式 exact-bool、幂等、并发/递归切换失败关闭**：``set_write_enabled`` 只接受 exact
   ``bool``；同态幂等无操作；与 scan/watchdog/另一切换经同一锁互斥。
8. **普通实写与 WP-009~011 回归不变**：实写模式返回 ``ScanResult``、驱动逐拍一次、
   LPC 前移、``commit_fault`` 归类不误报为 scan_fault；预存 ``channel_fault`` 不因切换
   自动清除。

诚实边界：本文件锁定的是当前 Python shadow / write-disable 契约行为——真实 HAL、
可信设备反馈、实时 monitor、硬件 watchdog、真实驱动物理写入、现场对拍与安全证明均
**不在本包**；这些测试**不构成**与 CODESYS PLC 语义一致或真实驱动一致的证据。
"""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from src.runtime import (
    BinOp,
    CommitPort,
    CommitSupervisor,
    Executor,
    IOMap,
    IRExecutionError,
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
    ScanRunnerReentryError,
    ScanResult,
    StoreVar,
    Task,
    VarDecl,
    WatchdogSafeCommit,
    build_runtime_store,
)
# WriteGate / ShadowScanResult 由 __init__ 之外的模块直接导出（本包 scope 未含
# src/runtime/__init__.py，故不改包级 __all__；直接从实现模块导入）。
from src.runtime.scan_runner import ShadowScanResult, WriteGate


# ---------------------------------------------------------------------------
# 测试替身：可计数 / 可控失败驱动 + 计数监督器
# ---------------------------------------------------------------------------

class _CountingDriver:
    """逐批确认回执驱动：默认回显命令（全成功）并**记录每次提交**。

    ``fail_channels`` 中的通道回执给错值（+1）→ 该通道失败关闭；``raise_exc`` 令整批
    抛异常。用于证明 shadow 下驱动**零调用**、实写下逐拍一次、以及故障归类。"""

    def __init__(self):
        self.commands = []
        self.fail_channels = set()
        self.raise_exc = None

    def commit(self, commands):
        self.commands.append(dict(commands))
        if self.raise_exc is not None:
            raise self.raise_exc
        out = {}
        for ch, v in commands.items():
            if ch in self.fail_channels and isinstance(v, int) and not isinstance(v, bool):
                out[ch] = v + 1                        # 错值 → 严格相等失败
            else:
                out[ch] = v
        return out


class _CountingSupervisor(CommitSupervisor):
    """记录 ``commit()`` 调用次数的监督器（证明 shadow 下监督器零调用）。"""

    def __init__(self, driver, policy):
        super().__init__(driver, policy)
        self.commit_calls = 0

    def commit(self, outputs):
        self.commit_calls += 1
        return super().commit(outputs)


class _CallbackDriver:
    """提交期间回调一次（用于递归切换失败关闭测试）。"""

    def __init__(self):
        self.callback = None
        self.reentry_error = None

    def commit(self, commands):
        if self.callback is not None and self.reentry_error is None:
            try:
                self.callback()
            except ScanRunnerReentryError as exc:
                self.reentry_error = exc
        return dict(commands)


class _BlockingDriver:
    """提交时阻塞在事件上（跨线程并发切换失败关闭测试）。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def commit(self, commands):
        self.entered.set()
        self.release.wait(timeout=5)
        return dict(commands)


class _Truthy:
    """真值对象：``bool(x) is True`` 但 ``type(x) is not bool``。"""

    def __bool__(self):
        return True


# ---------------------------------------------------------------------------
# 生产扫描栈装配（驱动 → 计数监督器 → CommitPort(+WriteGate) → 引擎 → 运行器）
# ---------------------------------------------------------------------------

def _stack(*, av_type="INT", av_safe=0, av_rate=None, motor_safe=False,
           code=None, retry_n=3, start_shadow=True, driver=None):
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        VarDecl("AV", av_type, section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=OutputPolicy("Motor", "BOOL", motor_safe)),
        IOMap("AV", "AO0", "OUT",
              policy=OutputPolicy("AV", av_type, av_safe, rate_limit=av_rate,
                                  commit_fault_retry_n=retry_n)),
    ]
    if code is None:
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
    gate = WriteGate() if start_shadow else WriteGate(writes_enabled=True)
    driver = driver or _CountingDriver()
    sup = _CountingSupervisor(driver, policy)
    port = CommitPort(sup, write_gate=gate)
    engine = ScanEngine(task, layout, executor, policy, port)
    runner = OuterScanRunner(engine, policy, port, shadow_gate=gate)
    return SimpleNamespace(task=task, layout=layout, executor=executor,
                           safety=safety, policy=policy, gate=gate,
                           driver=driver, sup=sup, port=port, engine=engine,
                           runner=runner)


# ---------------------------------------------------------------------------
# 1) 默认 write disable / 显式模式与只读诊断
# ---------------------------------------------------------------------------

class TestDefaultShadow(unittest.TestCase):

    def test_write_gate_defaults_to_shadow(self):
        self.assertFalse(WriteGate().writes_enabled)          # 省略参数即 shadow
        self.assertTrue(WriteGate(writes_enabled=True).writes_enabled)

    def test_new_stack_defaults_shadow_zero_driver_calls(self):
        w = _stack(av_rate=5)                                 # 默认 start_shadow=True
        self.assertTrue(w.runner.shadow)
        self.assertFalse(w.runner.writes_enabled)
        r = w.runner.scan_cycle({"DI0": True})
        # 观察窗诚实：ShadowScanResult，未写设备
        self.assertIsInstance(r, ShadowScanResult)
        self.assertTrue(r.shadow)
        self.assertFalse(r.physically_committed)
        self.assertFalse(hasattr(r, "outputs"))               # 不冒充“已提交输出”
        self.assertEqual(r.logical_outputs(), {"DO0": True, "AO0": 5})
        # 底层驱动 / 监督器零调用，无提交尝试证据
        self.assertEqual(w.driver.commands, [])
        self.assertEqual(w.sup.commit_calls, 0)
        self.assertEqual(w.port.attempts, 0)
        # 逻辑 last_effective 已推进；无任何物理提交
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": True, "AO0": 5})
        diag = w.sup.diagnostics()
        self.assertIsNone(diag["AO0"].last_physical_committed)
        self.assertIsNone(diag["DO0"].last_physical_committed)

    def test_shadow_result_outputs_are_isolated_copies(self):
        w = _stack(av_rate=5)
        r = w.runner.scan_cycle({"DI0": True})
        out = r.logical_outputs()
        out["AO0"] = 999                                      # 外部篡改不得回污染
        self.assertEqual(r.logical_outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": True, "AO0": 5})


# ---------------------------------------------------------------------------
# 2) Shadow 正常拍：逻辑继续、物理冻结、限速连续
# ---------------------------------------------------------------------------

class TestShadowNormalCycles(unittest.TestCase):

    def test_two_cycles_advance_prev_and_last_effective_no_writes(self):
        w = _stack(av_safe=0, av_rate=5)
        prev0 = w.engine.prev
        r1 = w.runner.scan_cycle({"DI0": True})
        prev1 = w.engine.prev
        r2 = w.runner.scan_cycle({"DI0": False})
        prev2 = w.engine.prev
        # 限速与非 shadow 完全一致：0→5→10（连续基准）
        self.assertEqual(r1.logical_outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(r2.logical_outputs(), {"DO0": False, "AO0": 10})
        # prev 每拍推进（第 5 步“只算不写”仍前移）
        self.assertIsNot(prev1, prev0)
        self.assertIsNot(prev2, prev1)
        self.assertIs(r2.prev, prev2)
        # 逻辑 last_effective 连续
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": False, "AO0": 10})
        # 底层驱动 / 监督器零调用；LPC / 故障状态全程冻结
        self.assertEqual(w.driver.commands, [])
        self.assertEqual(w.sup.commit_calls, 0)
        for st in w.sup.diagnostics().values():
            self.assertIsNone(st.last_physical_committed)
            self.assertFalse(st.commit_fault)
            self.assertFalse(st.channel_fault)

    def test_shadow_matches_realwrite_logic_values(self):
        # 同一逻辑：shadow 的逻辑 final == 实写的物理命令值（只是不落设备）
        shadow = _stack(av_safe=0, av_rate=5, start_shadow=True)
        real = _stack(av_safe=0, av_rate=5, start_shadow=False)
        rs = shadow.runner.scan_cycle({"DI0": True})
        rr = real.runner.scan_cycle({"DI0": True})
        self.assertEqual(rs.logical_outputs(), rr.outputs())
        self.assertEqual(real.driver.commands, [{"DO0": True, "AO0": 5}])
        self.assertEqual(shadow.driver.commands, [])


# ---------------------------------------------------------------------------
# 3) Shadow 故障拍与 watchdog：零写出、信号不冒充物理成功
# ---------------------------------------------------------------------------

class TestShadowFaultAndWatchdog(unittest.TestCase):

    def _assert_shadow_signal(self, sig, expected_image):
        self.assertTrue(sig.shadow)
        self.assertTrue(sig.write_suppressed_by_shadow)
        self.assertTrue(sig.shadow_logic_adopted)
        self.assertFalse(sig.safe_commit_succeeded)           # 无物理提交
        self.assertIsNone(sig.failed_stage)
        self.assertEqual(sig.safe_image, expected_image)

    def test_scan_exception_in_shadow_zero_writes_honest_signal(self):
        code = [LoadConst(1, "INT"), LoadConst(0, "INT"),
                BinOp("DIV", "INT"), StoreVar("AV", "INT")]
        w = _stack(av_safe=7, code=code)
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})                # 执行期除零
        sig = cm.exception
        self._assert_shadow_signal(sig, {"DO0": False, "AO0": 7})
        # 物理提交零次；提交尝试证据 0；scan_ok 已锁存
        self.assertEqual(w.driver.commands, [])
        self.assertEqual(w.sup.commit_calls, 0)
        self.assertEqual(w.port.attempts, 0)
        self.assertFalse(w.safety.read().scan_ok)
        # 安全映像作为 shadow 逻辑 last_effective 被采用；LPC 仍 None（未物理提交）
        self.assertEqual(w.policy.diagnostic_last_effective(),
                         {"DO0": False, "AO0": 7})
        self.assertIsNone(w.sup.diagnostics()["AO0"].last_physical_committed)

    def test_watchdog_in_shadow_zero_writes_honest_signal(self):
        w = _stack(av_safe=7, av_rate=5)
        av_before = w.layout.store.read("AV")
        prev_before = w.engine.prev
        with self.assertRaises(WatchdogSafeCommit) as cm:
            w.runner.trigger_watchdog()
        sig = cm.exception
        self._assert_shadow_signal(sig, {"DO0": False, "AO0": 7})
        self.assertIsNone(sig.original_exception)
        # 业务 IR 未跑、prev 未前移、物理零写、watchdog_ok 锁存
        self.assertEqual(w.layout.store.read("AV"), av_before)
        self.assertIs(w.engine.prev, prev_before)
        self.assertEqual(w.driver.commands, [])
        self.assertEqual(w.sup.commit_calls, 0)
        self.assertFalse(w.safety.read().watchdog_ok)

    def test_shadow_scan_fault_does_not_call_confirm_safe_image(self):
        # confirm_safe_image 是“已物理提交成功”的确认路径，shadow 绝不调用它
        code = [LoadConst(1, "INT"), LoadConst(0, "INT"),
                BinOp("DIV", "INT"), StoreVar("AV", "INT")]
        w = _stack(av_safe=7, code=code)
        called = []
        orig_confirm = w.policy.confirm_safe_image
        def _spy_confirm(ticket):
            called.append(ticket)
            return orig_confirm(ticket)
        w.policy.confirm_safe_image = _spy_confirm
        with self.assertRaises(ScanFaultSafeCommit):
            w.runner.scan_cycle({"DI0": True})
        self.assertEqual(called, [])                          # confirm 从未被调用

    def test_shadow_adopt_failure_is_structured(self):
        # shadow 逻辑采用失败也须结构化上报，不漏普通异常
        code = [LoadConst(1, "INT"), LoadConst(0, "INT"),
                BinOp("DIV", "INT"), StoreVar("AV", "INT")]
        w = _stack(av_safe=7, code=code)
        def _boom_adopt(ticket):
            raise RuntimeError("adopt boom")
        w.policy.adopt_safe_image_shadow = _boom_adopt
        with self.assertRaises(ScanFaultSafeCommit) as cm:
            w.runner.scan_cycle({"DI0": True})
        sig = cm.exception
        self.assertIsInstance(sig, ScanFaultSafeCommit)       # 非普通 RuntimeError
        self.assertEqual(sig.failed_stage, "shadow_adopt")
        self.assertTrue(sig.shadow)
        self.assertFalse(sig.shadow_logic_adopted)
        self.assertFalse(sig.safe_commit_succeeded)
        self.assertIsInstance(sig.fallback_exception, RuntimeError)
        self.assertIsInstance(sig.original_exception, IRExecutionError)
        self.assertEqual(w.driver.commands, [])               # 仍零物理提交


# ---------------------------------------------------------------------------
# 4) Shadow → 实写边界：safe_value 基准、恰一次物理提交
# ---------------------------------------------------------------------------

class TestShadowToRealWrite(unittest.TestCase):

    def test_first_real_write_uses_safe_value_not_shadow_last_effective(self):
        w = _stack(av_safe=0, av_rate=5)
        # 三拍 shadow 让逻辑 last_effective 爬到 15（0→5→10→15），零物理写
        for _ in range(3):
            w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.policy.diagnostic_last_effective()["AO0"], 15)
        self.assertEqual(w.driver.commands, [])
        # 退出 shadow：显式启用写出
        w.runner.set_write_enabled(True)
        self.assertTrue(w.runner.writes_enabled)
        # 实写首拍：基准回到 safe_value=0 → 0+5=5（若误用 shadow LE=15 则为 20）
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r, ScanResult)
        self.assertNotIsInstance(r, ShadowScanResult)
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 5})
        # 恰一次物理提交，LPC 前移为 5
        self.assertEqual(w.driver.commands, [{"DO0": True, "AO0": 5}])
        self.assertEqual(w.sup.commit_calls, 1)
        self.assertEqual(w.port.attempts, 1)
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 5)
        # 后续拍恢复既有限速：基准 last_effective=5 → 10
        r2 = w.runner.scan_cycle({"DI0": True})
        self.assertEqual(r2.outputs(), {"DO0": True, "AO0": 10})
        self.assertEqual(len(w.driver.commands), 2)

    def test_first_real_write_uses_safe_value_not_stale_lpc(self):
        # 实写建立 LPC → shadow → 实写：首拍仍用 safe_value，而非旧 LPC 对齐
        w = _stack(av_safe=0, av_rate=5, start_shadow=False)
        for _ in range(3):
            w.runner.scan_cycle({"DI0": True})                # LPC 爬到 15
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 15)
        real_commits = len(w.driver.commands)
        # 进入 shadow：逻辑继续到 20，物理冻结、LPC 保持 15
        w.runner.set_write_enabled(False)
        w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.policy.diagnostic_last_effective()["AO0"], 20)
        self.assertEqual(len(w.driver.commands), real_commits)   # 无新物理提交
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 15)
        # 退出 shadow：首拍从 safe_value=0 → 5（非旧 LPC 15→20，非 shadow LE 20→25）
        w.runner.set_write_enabled(True)
        r = w.runner.scan_cycle({"DI0": True})
        self.assertEqual(r.outputs()["AO0"], 5)
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 5)

    def test_preexisting_channel_fault_not_cleared_by_switch(self):
        driver = _CountingDriver()
        driver.fail_channels = {"AO0"}                        # AV 通道回执错值
        w = _stack(av_safe=0, av_rate=5, retry_n=1, start_shadow=False,
                   driver=driver)
        with self.assertRaises(PartialCommitError):
            w.runner.scan_cycle({"DI0": True})                # 一次失败即锁存
        self.assertTrue(w.sup.diagnostics()["AO0"].channel_fault)
        # 切换 shadow ↔ 实写都不得自动清除锁存 channel_fault
        w.runner.set_write_enabled(False)
        self.assertTrue(w.sup.diagnostics()["AO0"].channel_fault)
        w.runner.set_write_enabled(True)
        self.assertTrue(w.sup.diagnostics()["AO0"].channel_fault)


# ---------------------------------------------------------------------------
# 5) 实写 → Shadow：立即停写、LPC 保持
# ---------------------------------------------------------------------------

class TestRealWriteToShadow(unittest.TestCase):

    def test_enter_shadow_stops_physical_write_next_cycle(self):
        w = _stack(av_safe=0, av_rate=5, start_shadow=False)
        r1 = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r1, ScanResult)
        self.assertEqual(w.driver.commands, [{"DO0": True, "AO0": 5}])
        lpc = w.sup.diagnostics()["AO0"].last_physical_committed
        self.assertEqual(lpc, 5)
        # 进入 shadow：下一拍零物理写，逻辑继续，LPC 保持
        w.runner.set_write_enabled(False)
        r2 = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r2, ShadowScanResult)
        self.assertEqual(len(w.driver.commands), 1)           # 无新提交
        self.assertEqual(w.sup.commit_calls, 1)
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 5)
        self.assertEqual(r2.logical_outputs()["AO0"], 10)     # 逻辑继续 5→10


# ---------------------------------------------------------------------------
# 6) 模式切换：exact-bool、幂等、并发/递归失败关闭
# ---------------------------------------------------------------------------

class TestModeSwitch(unittest.TestCase):

    def test_set_write_enabled_requires_exact_bool(self):
        w = _stack(av_rate=5)
        for bad in (1, 0, 1.0, None, "true", _Truthy()):
            with self.assertRaises(ScanRunnerConfigError):
                w.runner.set_write_enabled(bad)
        self.assertFalse(w.runner.writes_enabled)             # 仍 shadow，未被含混切换
        # exact bool 正常
        w.runner.set_write_enabled(True)
        self.assertTrue(w.runner.writes_enabled)
        w.runner.set_write_enabled(False)
        self.assertFalse(w.runner.writes_enabled)

    def test_idempotent_enable_does_not_reset_boundary(self):
        w = _stack(av_safe=0, av_rate=5, start_shadow=False)
        for _ in range(2):
            w.runner.scan_cycle({"DI0": True})                # LPC/LE 到 10，边界已消费
        # 已处实写，再 enable 应幂等无操作——绝不重置边界基准
        w.runner.set_write_enabled(True)
        r = w.runner.scan_cycle({"DI0": True})
        # 若幂等误触 mark_boundary_reset_all 则为 5；正确应从 LE=10 → 15
        self.assertEqual(r.outputs()["AO0"], 15)

    def test_set_write_enabled_without_gate_rejected(self):
        # 既有非 shadow 装配（无 write-gate）无模式可切换
        gvl = [VarDecl("Motor", "BOOL", section="VAR_GLOBAL")]
        io_map = [IOMap("Motor", "DO0", "OUT",
                        policy=OutputPolicy("Motor", "BOOL", False))]
        code = [LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")]
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST", code=code)
        task = Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                    io_map=io_map, pou_lib={"Main": main})
        layout = build_runtime_store(task)
        executor = Executor(task, layout)
        safety = SafetyStateService(SafetySnapshot.all_ok())
        policy = OutputPolicyService(layout.store, task.io_map, safety)
        port = CommitPort(_CountingDriver(), legacy_unshadowed=True)  # 显式 legacy 直写
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)        # 无 shadow_gate
        self.assertTrue(runner.writes_enabled)                # 恒实写
        self.assertFalse(runner.shadow)
        with self.assertRaises(ScanRunnerConfigError):
            runner.set_write_enabled(False)

    def test_exit_shadow_keeps_shadow_if_boundary_reset_fails(self):
        w = _stack(av_safe=0, av_rate=5)
        w.runner.scan_cycle({"DI0": True})                    # shadow 一拍
        def _boom_all():
            raise RuntimeError("boundary boom")
        w.policy.mark_boundary_reset_all = _boom_all
        with self.assertRaises(RuntimeError):
            w.runner.set_write_enabled(True)                  # 挂起边界失败
        # 保持 shadow：写出未启用，下一拍仍零物理写
        self.assertFalse(w.runner.writes_enabled)
        self.assertTrue(w.runner.shadow)
        r = w.runner.scan_cycle({"DI0": False})
        self.assertIsInstance(r, ShadowScanResult)
        self.assertEqual(w.driver.commands, [])

    def test_recursive_switch_during_scan_fails_closed(self):
        drv = _CallbackDriver()
        w = _stack(av_safe=0, av_rate=5, start_shadow=False, driver=drv)
        drv.callback = lambda: w.runner.set_write_enabled(False)
        r = w.runner.scan_cycle({"DI0": True})                # 提交期递归切换
        self.assertIsInstance(drv.reentry_error, ScanRunnerReentryError)
        self.assertIsInstance(r, ScanResult)                  # 外层拍仍完成
        self.assertTrue(w.runner.writes_enabled)              # 未被半切换

    def test_concurrent_switch_during_scan_fails_closed(self):
        drv = _BlockingDriver()
        w = _stack(av_safe=0, av_rate=5, start_shadow=False, driver=drv)
        results = {}

        def worker():
            try:
                results["ok"] = w.runner.scan_cycle({"DI0": True})
            except Exception as exc:                          # pragma: no cover
                results["err"] = exc

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(drv.entered.wait(timeout=5))
        with self.assertRaises(ScanRunnerReentryError):
            w.runner.set_write_enabled(False)                 # 持锁扫描中并发切换
        drv.release.set()
        t.join(timeout=5)
        self.assertNotIn("err", results)
        self.assertTrue(w.runner.writes_enabled)              # 未被半切换


# ---------------------------------------------------------------------------
# 7) 实写模式 = WP-007~011 既有语义回归不变
# ---------------------------------------------------------------------------

class TestRealWriteRegression(unittest.TestCase):

    def test_real_write_advances_lpc_each_cycle(self):
        w = _stack(av_safe=0, av_rate=5, start_shadow=False)
        r1 = w.runner.scan_cycle({"DI0": True})
        r2 = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r1, ScanResult)
        self.assertEqual([c["AO0"] for c in w.driver.commands], [5, 10])
        self.assertEqual(w.sup.diagnostics()["AO0"].last_physical_committed, 10)
        self.assertEqual(w.sup.commit_calls, 2)

    def test_commit_fault_not_misclassified_as_scan_fault(self):
        driver = _CountingDriver()
        driver.raise_exc = RuntimeError("driver boom")
        w = _stack(av_safe=0, av_rate=5, start_shadow=False, driver=driver)
        with self.assertRaises(PartialCommitError):
            w.runner.scan_cycle({"DI0": True})                # 提交调用后失败
        # 提交尝试证据已记（真实物理尝试）→ 归 commit_fault，不误报 scan_fault
        self.assertEqual(w.port.attempts, 1)
        self.assertEqual(w.sup.commit_calls, 1)
        self.assertTrue(w.safety.read().scan_ok)              # 非扫描故障，未锁存


# ---------------------------------------------------------------------------
# 8) 装配期共享 WriteGate 校验
# ---------------------------------------------------------------------------

class TestShadowAssembly(unittest.TestCase):

    def _bare(self):
        gvl = [VarDecl("Motor", "BOOL", section="VAR_GLOBAL")]
        io_map = [IOMap("Motor", "DO0", "OUT",
                        policy=OutputPolicy("Motor", "BOOL", False))]
        code = [LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL")]
        main = POUDefinition(name="Main", pou_kind="PROGRAM", language="ST",
                             code=code)
        task = Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=gvl,
                    io_map=io_map, pou_lib={"Main": main})
        layout = build_runtime_store(task)
        executor = Executor(task, layout)
        safety = SafetyStateService(SafetySnapshot.all_ok())
        policy = OutputPolicyService(layout.store, task.io_map, safety)
        return task, layout, executor, policy

    def test_runner_gate_must_match_port_gate(self):
        task, layout, executor, policy = self._bare()
        gate = WriteGate()
        port = CommitPort(_CountingDriver(), write_gate=gate)
        engine = ScanEngine(task, layout, executor, policy, port)
        other = WriteGate()
        with self.assertRaises(ScanRunnerConfigError):        # 不同 WriteGate 实例
            OuterScanRunner(engine, policy, port, shadow_gate=other)

    def test_runner_adopts_port_gate_when_shadow_gate_omitted(self):
        # WP-014 必须返修 2：省略 shadow_gate 时运行器**自动采用端口自带门**——
        # 端口有门（默认 shadow）+ 运行器省略 shadow_gate 即得到可运行的 write-disable
        # 栈（不再拒绝装配）。
        task, layout, executor, policy = self._bare()
        gate = WriteGate()
        port = CommitPort(_CountingDriver(), write_gate=gate)
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)        # 省略 shadow_gate
        self.assertTrue(runner.shadow)                        # 采用端口默认 shadow 门
        self.assertFalse(runner.writes_enabled)
        # 采用的是**同一**门：切换后端口读到的门态随之变化
        runner.set_write_enabled(True)
        self.assertTrue(runner.writes_enabled)
        self.assertTrue(port.write_gate.writes_enabled)

    def test_commit_port_rejects_non_write_gate(self):
        with self.assertRaises(ScanRunnerConfigError):
            CommitPort(_CountingDriver(), write_gate=object())

    def test_write_gate_rejects_non_bool(self):
        with self.assertRaises(ScanRunnerConfigError):
            WriteGate(writes_enabled=1)

    def test_commit_port_omitting_both_defaults_runnable_shadow(self):
        # Codex WP-014 Round 1 必须返修 2：省略 write_gate 又未显式 opt-in →
        # **自动装配默认 shadow 门**，得到可运行、首拍及后续拍零物理写的 write-disable
        # 栈（不再拒绝装配，也绝不无门直写）；显式 opt-in 才实写。
        driver = _CountingDriver()
        port = CommitPort(driver)                             # 全省略
        self.assertIsInstance(port.write_gate, WriteGate)     # 自动装配了门
        self.assertFalse(port.write_gate.writes_enabled)      # 默认 shadow
        task, layout, executor, policy = self._bare()
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)        # 采用端口门
        self.assertTrue(runner.shadow)
        # 端到端多拍：全程零物理驱动调用
        r1 = runner.scan_cycle({})
        r2 = runner.scan_cycle({})
        self.assertIsInstance(r1, ShadowScanResult)
        self.assertIsInstance(r2, ShadowScanResult)
        self.assertEqual(driver.commands, [])                 # 首拍及后续拍零物理写
        self.assertEqual(port.attempts, 0)
        # 显式、可审计 opt-in（set_write_enabled）后才物理写
        runner.set_write_enabled(True)
        r3 = runner.scan_cycle({})
        self.assertIsInstance(r3, ScanResult)
        self.assertNotIsInstance(r3, ShadowScanResult)
        self.assertEqual(driver.commands, [{"DO0": True}])    # 恰一次物理提交

    def test_commit_port_write_gate_and_legacy_mutually_exclusive(self):
        # shadow-capable 装配由 WriteGate 控制写出，不得再声明 legacy 直写。
        with self.assertRaises(ScanRunnerConfigError):
            CommitPort(_CountingDriver(), write_gate=WriteGate(),
                       legacy_unshadowed=True)

    def test_commit_port_legacy_flag_requires_exact_bool(self):
        with self.assertRaises(ScanRunnerConfigError):
            CommitPort(_CountingDriver(), legacy_unshadowed=1)

    def test_legacy_assembly_requires_explicit_opt_in(self):
        # 既有 WP-007/008 无门始终物理写：须**显式、可审计的** legacy_unshadowed=True
        # 才成立（省略即拒绝，见 test_commit_port_omitting_both_fails_closed）。
        task, layout, executor, policy = self._bare()
        port = CommitPort(_CountingDriver(), legacy_unshadowed=True)
        engine = ScanEngine(task, layout, executor, policy, port)
        runner = OuterScanRunner(engine, policy, port)
        r = runner.scan_cycle({})
        self.assertIsInstance(r, ScanResult)
        self.assertNotIsInstance(r, ShadowScanResult)
        self.assertTrue(runner.writes_enabled)


# ---------------------------------------------------------------------------
# 9) 写出翻转封装：普通可达引用不能绕过运行器事务直接开写（Codex WP-014 Round 1 反证 1
#    / WP-015 Round 1 必须返修 1：对象图冻结，普通属性赋值 / 删除都不能替换门状态或门引用）
# ---------------------------------------------------------------------------

class TestWriteFlipCapability(unittest.TestCase):

    def test_assembled_objects_expose_no_direct_flip(self):
        # 装配完成后交给外部调用方的 gate / port / runner **无任何可达裸翻转**：
        # 写状态存于闭包单元（无可写属性、无 _set_writes/_control_token），运行器不再
        # 保存可直接调用的裸写入令牌（无 _gate_token），装配后 gate._claim_control() 又
        # 已失效——调用方只能只读观察。
        w = _stack(av_safe=0, av_rate=5)                      # 默认 shadow
        gate = w.gate
        self.assertFalse(hasattr(gate, "_set_writes"))
        self.assertFalse(hasattr(gate, "_control_token"))
        self.assertFalse(hasattr(w.runner, "_gate_token"))
        # writes_enabled 无 setter 且实例已冻结：直接赋值一律被 __setattr__ 拒绝。
        with self.assertRaises(ScanRunnerConfigError):
            gate.writes_enabled = True
        # 无法新增写状态属性（__slots__ + 冻结 __setattr__）。
        with self.assertRaises(ScanRunnerConfigError):
            gate._writes_enabled = True
        # WP-015 Round 1 反证：**实际承载槽** _read_enabled / _claim_writer 此前是可写
        # member_descriptor（可换成恒真闭包旁路开写），现经 __setattr__/__delattr__ 冻结——
        # 普通属性赋值 / 删除都拒绝。
        for name in ("_read_enabled", "_claim_writer"):
            self.assertTrue(hasattr(gate, name))              # 槽确实存在（承载只读/领取闭包）
            with self.assertRaises(ScanRunnerConfigError):
                setattr(gate, name, lambda *a, **k: True)
            with self.assertRaises(ScanRunnerConfigError):
                delattr(gate, name)
        # 装配后经端口暴露的同一 gate 再领取控制权失败关闭（运行器已一次性领取）。
        self.assertIs(w.port.write_gate, gate)
        with self.assertRaises(ScanRunnerConfigError):
            w.port.write_gate._claim_control()
        self.assertFalse(gate.writes_enabled)                 # 仍 shadow，未被绕过开写
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r, ShadowScanResult)            # 仍零物理写
        self.assertEqual(w.driver.commands, [])

    def test_frozen_object_graph_rejects_attribute_replacement(self):
        # Codex WP-015 Round 1 必须返修 1：从真实对象图出发，普通属性赋值 / 删除都不能
        # 替换或移除提交端实际读取的门状态 / 门引用。逐一验证 gate / port / runner 三者冻结。
        w = _stack(av_safe=0, av_rate=5)                      # 默认 shadow
        gate, port, runner = w.gate, w.port, w.runner

        # WriteGate：门状态闭包与领取闭包不可替换 / 删除。
        for name in ("_read_enabled", "_claim_writer"):
            with self.assertRaises(ScanRunnerConfigError):
                setattr(gate, name, lambda *a, **k: True)
            with self.assertRaises(ScanRunnerConfigError):
                delattr(gate, name)

        # CommitPort：写出门引用与承载闭包不可替换 / 删除（含 port._write_gate = None 取消
        # 抑制）；attempts 现为只读闭包计数（property），普通赋值同样被冻结拒绝。
        for name, value in (("_write_gate", None),
                            ("_commit_through", lambda o: None),
                            ("_assert_binding", lambda p: None),
                            ("attempts", 999), ("newattr", 1)):
            with self.assertRaises(ScanRunnerConfigError):
                setattr(port, name, value)
        for name in ("_write_gate", "_commit_through", "_assert_binding"):
            with self.assertRaises(ScanRunnerConfigError):
                delattr(port, name)

        # OuterScanRunner：门引用与守卫事务闭包不可替换 / 删除。
        always_on = WriteGate(writes_enabled=True)
        for name, value in (("_shadow_gate", always_on), ("_port", port),
                            ("_apply_write_mode", lambda enabled: None),
                            ("newattr", 1)):
            with self.assertRaises(ScanRunnerConfigError):
                setattr(runner, name, value)
        for name in ("_shadow_gate", "_apply_write_mode"):
            with self.assertRaises(ScanRunnerConfigError):
                delattr(runner, name)

        # 全部替换尝试失败后：门引用未变、仍 shadow、门态仍禁用。
        self.assertIs(port.write_gate, gate)
        self.assertFalse(gate.writes_enabled)
        self.assertTrue(runner.shadow)

    def test_attribute_tampering_cannot_force_physical_write(self):
        # 端到端封堵：三拍 shadow 使逻辑 last_effective=15、零物理写；随后穷举普通属性
        # 篡改（改门状态、换门引用、清门引用）——每一步都被冻结拒绝，物理提交仍为 0。
        # 最后受支持切换首拍仍从 safe_value=0 写 5（非 shadow LE=15→20），证明既有语义未回退。
        w = _stack(av_safe=0, av_rate=5)
        for _ in range(3):
            w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.policy.diagnostic_last_effective()["AO0"], 15)
        self.assertEqual(w.driver.commands, [])

        def _tamper():
            w.gate._read_enabled = lambda: True               # 改门状态为恒真
        def _swap_gate_none():
            w.port._write_gate = None                         # 取消端口 shadow 抑制
        def _swap_runner_gate():
            w.runner._shadow_gate = WriteGate(writes_enabled=True)
        for attempt in (_tamper, _swap_gate_none, _swap_runner_gate):
            with self.assertRaises(ScanRunnerConfigError):
                attempt()

        # 篡改全部失败：再扫描仍是 shadow、零物理写、提交尝试证据 0。
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r, ShadowScanResult)
        self.assertEqual(w.driver.commands, [])
        self.assertEqual(w.sup.commit_calls, 0)
        self.assertEqual(w.port.attempts, 0)

        # 唯一受支持路径仍正确：切换先边界重建，实写首拍从 safe_value=0 写 5。
        w.runner.set_write_enabled(True)
        r2 = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r2, ScanResult)
        self.assertNotIsInstance(r2, ShadowScanResult)
        self.assertEqual(r2.outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(w.driver.commands, [{"DO0": True, "AO0": 5}])

    def test_reachable_capability_cannot_skip_boundary_reset(self):
        # Codex WP-014 Round 1 反证 1 的直接封堵：三拍 shadow 使逻辑 last_effective=15，
        # 此前反证经 `gate._set_writes(True, runner._gate_token)` 跳过锁与边界重建、下一拍
        # 物理写 AO0=20。现在这些裸能力都不存在；**唯一可达变更入口**是运行器守卫闭包
        # `_apply_write_mode`——即便外部直接调用它，也仍先 `mark_boundary_reset_all`，
        # 实写首拍从 safe_value=0 写 5，绝不是 shadow LE=15 → 20。
        w = _stack(av_safe=0, av_rate=5)
        for _ in range(3):
            w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.policy.diagnostic_last_effective()["AO0"], 15)
        self.assertEqual(w.driver.commands, [])
        # 反证用过的裸路径已不存在
        self.assertFalse(hasattr(w.gate, "_set_writes"))
        self.assertFalse(hasattr(w.runner, "_gate_token"))
        # 直接调用可达的守卫能力：仍走边界重建 + 锁，首拍写 5 而非 20
        w.runner._apply_write_mode(True)
        self.assertTrue(w.runner.writes_enabled)
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r, ScanResult)
        self.assertNotIsInstance(r, ShadowScanResult)
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(w.driver.commands, [{"DO0": True, "AO0": 5}])

    def test_claim_control_one_shot_and_dead_after_assembly(self):
        # 独立 gate：首次领取返回可用写入闭包，二次领取失败关闭（杜绝多头翻转）。
        gate = WriteGate()
        self.assertFalse(gate.writes_enabled)
        writer = gate._claim_control()                        # 首次领取成功
        self.assertTrue(callable(writer))
        with self.assertRaises(ScanRunnerConfigError):
            gate._claim_control()                             # 第二次领取拒绝
        # 已装配进运行器的 gate：外部再领取拿不到写入能力。
        w = _stack(av_safe=0, av_rate=5)
        with self.assertRaises(ScanRunnerConfigError):
            w.gate._claim_control()

    def test_second_runner_on_same_gate_rejected(self):
        # 端到端：同一 gate 已被 _stack 的运行器领取控制权，再建运行器装配失败关闭。
        w = _stack(av_safe=0, av_rate=5)
        port2 = CommitPort(w.sup, write_gate=w.gate)
        engine2 = ScanEngine(w.task, w.layout, w.executor, w.policy, port2)
        with self.assertRaises(ScanRunnerConfigError):
            OuterScanRunner(engine2, w.policy, port2, shadow_gate=w.gate)

    def test_direct_capability_flip_during_scan_fails_closed(self):
        # 扫描进行中，直接调用可达的守卫能力 `_apply_write_mode` 也必须与扫描互斥失败
        # 关闭（同一非重入锁）——外部无从借并发绕过锁开写。
        drv = _BlockingDriver()
        w = _stack(av_safe=0, av_rate=5, start_shadow=False, driver=drv)
        results = {}

        def worker():
            try:
                results["ok"] = w.runner.scan_cycle({"DI0": True})
            except Exception as exc:                          # pragma: no cover
                results["err"] = exc

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(drv.entered.wait(timeout=5))
        with self.assertRaises(ScanRunnerReentryError):
            w.runner._apply_write_mode(False)                 # 持锁扫描中并发直接翻转
        drv.release.set()
        t.join(timeout=5)
        self.assertNotIn("err", results)
        self.assertTrue(w.runner.writes_enabled)              # 未被半切换


# ---------------------------------------------------------------------------
# 10) 普通可达对象图不返回底层提交器 / 物理驱动的可调用写能力（Codex WP-20260723-015
#     Round 2 反证 1 / 必须返修：此前 port.inner / port._inner → CommitSupervisor.commit
#     或 ._driver.commit 可**不经门**直接物理写；须证明普通对象遍历取不到绕过门的写能力）
# ---------------------------------------------------------------------------

def _ordinary_reachable(root, *, max_depth=6):
    """从 ``root`` 出发，仅经**普通（非 dunder）属性读取**做有界 BFS，返回可达对象集合
    （``{id(obj): obj}``，按 id 去重）。

    只下潜到 ``src.runtime.*`` 或本测试模块定义的对象，避免深入 stdlib；**绝不读取
    ``__closure__`` / ``__dict__`` 等 dunder**——那属本项目不防御的语言级反射，超出诚实
    边界。用于证明：给定 gate/port/runner 引用，普通属性遍历取不到底层监督器 / 物理驱动。
    """
    seen: dict = {}
    stack = [(root, 0)]
    while stack:
        obj, depth = stack.pop()
        if id(obj) in seen or depth > max_depth:
            continue
        seen[id(obj)] = obj
        for name in dir(obj):
            if name.startswith("__"):
                continue
            try:
                val = getattr(obj, name)
            except Exception:
                continue
            mod = getattr(type(val), "__module__", "") or ""
            if mod.startswith("src.runtime") or mod == __name__:
                stack.append((val, depth + 1))
    return seen


class TestNoReachablePhysicalCommitCapability(unittest.TestCase):
    """Codex WP-20260723-015 Round 2 必须返修 2：从 gate/port/runner 的**普通可达属性图**
    出发，不得取得底层提交器 / 物理驱动的可调用写能力，也不得把泄露固化为既有 API。"""

    def test_port_exposes_no_inner_or_driver(self):
        # 结构性：端口不再有 inner / _inner，普通属性遍历取不到底层监督器 / 物理驱动本身。
        w = _stack(av_safe=0, av_rate=5)
        self.assertFalse(hasattr(w.port, "inner"))
        self.assertFalse(hasattr(w.port, "_inner"))
        for name in dir(w.port):
            if name.startswith("__"):
                continue
            val = getattr(w.port, name)
            self.assertIsNot(val, w.sup)
            self.assertIsNot(val, w.driver)

    def test_ordinary_graph_from_runner_cannot_reach_committer(self):
        # 从真实运行器对象图出发（普通属性 BFS）：底层监督器与物理驱动都不可达；端口自身
        # 可达（合法共享），但它是可达图里唯一提交入口且受门判定。
        w = _stack(av_safe=0, av_rate=5)
        reachable = _ordinary_reachable(w.runner)
        self.assertNotIn(id(w.sup), reachable)
        self.assertNotIn(id(w.driver), reachable)
        self.assertIn(id(w.port), reachable)
        # 引擎注入的提交端口即本端口（装配校验读的也是它，非语言级反射）
        self.assertIs(w.engine._committer, w.port)
        self.assertFalse(hasattr(w.engine._committer, "inner"))

    def test_reachable_commit_is_gate_enforced_end_to_end(self):
        # 端到端 fake-driver：三拍 shadow 使逻辑 last_effective=15、零物理写。普通可达图里
        # 唯一 commit 入口是受门判定的 port.commit——shadow 下返回 None、驱动/监督器零调用；
        # 无任何普通可达对象能提供绕过门的裸 commit。受支持切换后才从 safe_value=0 写 5。
        w = _stack(av_safe=0, av_rate=5)
        for _ in range(3):
            w.runner.scan_cycle({"DI0": True})
        self.assertEqual(w.policy.diagnostic_last_effective()["AO0"], 15)
        self.assertEqual(w.driver.commands, [])
        # 唯一可达提交入口在 shadow 下被门抑制：直接调用 port.commit 也零物理写
        self.assertIsNone(w.port.commit({"DO0": True, "AO0": 20}))
        self.assertEqual(w.driver.commands, [])
        self.assertEqual(w.sup.commit_calls, 0)
        self.assertEqual(w.port.attempts, 0)
        # 底层监督器 / 驱动不在普通可达集合内，无法 sup.commit / driver.commit 旁路
        reachable = _ordinary_reachable(w.runner)
        self.assertNotIn(id(w.sup), reachable)
        self.assertNotIn(id(w.driver), reachable)
        # 受支持路径仍先边界重建，实写首拍从 safe_value=0 写 5（非 shadow LE=15→20）
        w.runner.set_write_enabled(True)
        r = w.runner.scan_cycle({"DI0": True})
        self.assertIsInstance(r, ScanResult)
        self.assertNotIsInstance(r, ShadowScanResult)
        self.assertEqual(r.outputs(), {"DO0": True, "AO0": 5})
        self.assertEqual(w.driver.commands, [{"DO0": True, "AO0": 5}])


if __name__ == "__main__":
    unittest.main()
