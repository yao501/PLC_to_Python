"""WP-20260801-062：阶段 1 Python 内部跨组件公开 API 总验收。

本文件自行构造正式 IR ``Task``、默认 ``Registry``、手动 exact-int
时钟与内存驱动；不从其它测试模块导入私有 helper，不复制产品算法。
证据只适用于当前 Python 内部单任务运行栈，不推导 PLC/CODESYS、
HAL、实时调度、硬件 watchdog 或现场安全验证。
"""
from __future__ import annotations

import unittest

from src.primitives.latches import SR
from src.primitives.timers import TON
from src.runtime import (
    BinOp,
    CallFb,
    InstanceDecl,
    IOMap,
    IRExecutionError,
    LoadConst,
    LoadVar,
    MissingVariantError,
    OutputPolicy,
    PartialCommitError,
    POUDefinition,
    ProgramInstance,
    ReadinessSnapshot,
    SafetySnapshot,
    ScanFaultSafeCommit,
    StartupValidationError,
    StoreVar,
    Task,
    TaskRuntimeAssembly,
    VarDecl,
    WatchdogSafeCommit,
    build_default_registry,
    build_task_runtime,
    persistent_key,
    validate_task,
)


CAPABILITY_TEST_MATRIX = {
    "catalog_22_and_f2_fail_closed": (
        "TestStage1PublicAcceptance.test_catalog_22_and_f2_fail_closed",),
    "registry_loader_runtime_object_graph": (
        "TestStage1PublicAcceptance.test_registry_loader_runtime_object_graph",),
    "cold_start_default_write_disable": (
        "TestStage1PublicAcceptance.test_cold_start_default_write_disable",),
    "readiness_window_withdraw_operator_gate": (
        "TestStage1PublicAcceptance.test_readiness_window_withdraw_operator_gate",),
    "explicit_real_write_policy_supervisor_receipt": (
        "TestStage1PublicAcceptance.test_explicit_real_write_policy_supervisor_receipt",),
    "ton_state_block_five_step_prev": (
        "TestStage1PublicAcceptance.test_ton_state_block_five_step_prev",),
    "scan_commit_channel_fault_layering": (
        "TestStage1PublicAcceptance.test_scan_commit_channel_fault_layering",),
    "monitor_timeout_once_next_cycle": (
        "TestStage1PublicAcceptance.test_monitor_timeout_once_next_cycle",),
    "dual_assembly_full_isolation": (
        "TestStage1PublicAcceptance.test_dual_assembly_full_isolation",),
    "assembly_fail_closed_registry_reusable": (
        "TestStage1PublicAcceptance.test_assembly_fail_closed_registry_reusable",),
}


_CATALOG = (
    "TON", "TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK",
    "APCHSHLLIM", "APCM", "APCHSACCUM", "APCHSFOP", "APCHSRATELIM",
    "APCHXHCL", "APCSTATISTICS", "APCCD", "APCGCQ", "APCMAUTOPARA",
    "APCPID", "APCPIDZZD", "APCRSFNAUTOPARA", "APCSPFINDER",
)


class _ManualClock:
    def __init__(self):
        self.now_ns = 0

    def __call__(self):
        return self.now_ns

    def advance_ms(self, value):
        self.now_ns += value * 1_000_000


class _ConfirmingDriver:
    def __init__(self):
        self.commands = []

    def commit(self, commands):
        copied = dict(commands)
        self.commands.append(copied)
        return dict(copied)


class _FailingDriver:
    def __init__(self):
        self.commands = []

    def commit(self, commands):
        self.commands.append(dict(commands))
        raise RuntimeError("memory driver failure")


class _SwitchableDriver:
    def __init__(self):
        self.commands = []
        self.fail = False

    def commit(self, commands):
        copied = dict(commands)
        self.commands.append(copied)
        if self.fail:
            raise RuntimeError("switched memory driver failure")
        return dict(copied)


def _readiness(**changes):
    fields = dict(io_ready=True, bus_ready=True, comm_ready=True,
                  safety_ok=True, interlock_ok=True, output_enable=True)
    fields.update(changes)
    return ReadinessSnapshot(**fields)


def _stage1_task(*, commit_fault_retry_n=3):
    """TON + SR 的最小正式 Task：Motor = T1.Q AND L1.Q1。"""
    gvl = [
        VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Reset", "BOOL", section="VAR_GLOBAL"),
        VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
    ]
    io_map = [
        IOMap("Start", "DI0", "IN"),
        IOMap("Reset", "DI1", "IN"),
        IOMap("Motor", "DO0", "OUT", policy=OutputPolicy(
            "Motor", "BOOL", False,
            commit_fault_retry_n=commit_fault_retry_n)),
    ]
    code = [
        LoadVar("Start", "BOOL"), StoreVar("T1.IN", "BOOL"),
        LoadConst(1000, "TIME"), StoreVar("T1.PT_ms", "TIME"),
        CallFb("T1"),
        LoadVar("Start", "BOOL"), StoreVar("L1.SET1", "BOOL"),
        LoadVar("Reset", "BOOL"), StoreVar("L1.RESET", "BOOL"),
        CallFb("L1"),
        LoadVar("T1.Q", "BOOL"), LoadVar("L1.Q1", "BOOL"),
        BinOp("AND", "BOOL"), StoreVar("Motor", "BOOL"),
    ]
    main = POUDefinition(
        name="Main", pou_kind="PROGRAM", language="ST", code=code,
        instances=[InstanceDecl("T1", "TON", kind="library"),
                   InstanceDecl("L1", "SR", kind="library")],
    )
    return Task(cycle_ms=500,
                programs=[ProgramInstance("Main", "PLC_PRG")],
                gvl=gvl, io_map=io_map, pou_lib={"Main": main})


def _fault_task():
    main = POUDefinition(
        name="Main", pou_kind="PROGRAM", language="ST",
        code=[LoadConst(True, "BOOL"), StoreVar("Motor", "BOOL"),
              LoadConst(1, "INT"), LoadConst(0, "INT"),
              BinOp("DIV", "INT"), StoreVar("N", "INT")],
    )
    return Task(
        cycle_ms=500,
        programs=[ProgramInstance("Main", "PLC_PRG")],
        gvl=[VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
             VarDecl("N", "INT", section="VAR_GLOBAL")],
        io_map=[IOMap("Motor", "DO0", "OUT",
                      policy=OutputPolicy("Motor", "BOOL", False))],
        pou_lib={"Main": main},
    )


def _rate_limit_task():
    """REAL 请求恒为 10.0，safe=0.0，每拍最多变化 2.0。"""
    main = POUDefinition(
        name="Main", pou_kind="PROGRAM", language="ST",
        code=[LoadConst(10.0, "REAL"), StoreVar("Valve", "REAL")],
    )
    return Task(
        cycle_ms=500,
        programs=[ProgramInstance("Main", "PLC_PRG")],
        gvl=[VarDecl("Valve", "REAL", section="VAR_GLOBAL")],
        io_map=[IOMap("Valve", "AO0", "OUT", policy=OutputPolicy(
            "Valve", "REAL", 0.0, rate_limit=2.0))],
        pou_lib={"Main": main},
    )


def _invalid_ctor_task():
    main = POUDefinition(
        name="Main", pou_kind="PROGRAM", language="ST", code=[],
        instances=[InstanceDecl("A1", "APCHSACCUM", kind="library",
                                ctor_args={"IV": float("nan")})],
    )
    return Task(cycle_ms=500,
                programs=[ProgramInstance("Main", "PLC_PRG")],
                pou_lib={"Main": main})


def _build(*, task=None, registry=None, driver=None, clock=None,
           startup_inhibit_ms=0, initial_safety=None):
    task = _stage1_task() if task is None else task
    registry = build_default_registry() if registry is None else registry
    driver = _ConfirmingDriver() if driver is None else driver
    clock = _ManualClock() if clock is None else clock
    assembly = build_task_runtime(
        task, registry, driver=driver, watchdog_timeout_ms=1000,
        startup_inhibit_ms=startup_inhibit_ms,
        initial_safety=initial_safety, clock_ns=clock)
    return assembly, driver, clock


class TestStage1PublicAcceptance(unittest.TestCase):
    def test_capability_matrix_is_exact_and_machine_resolvable(self):
        expected = {
            "catalog_22_and_f2_fail_closed",
            "registry_loader_runtime_object_graph",
            "cold_start_default_write_disable",
            "readiness_window_withdraw_operator_gate",
            "explicit_real_write_policy_supervisor_receipt",
            "ton_state_block_five_step_prev",
            "scan_commit_channel_fault_layering",
            "monitor_timeout_once_next_cycle",
            "dual_assembly_full_isolation",
            "assembly_fail_closed_registry_reusable",
        }
        expected_mapping = {
            capability: (
                "TestStage1PublicAcceptance.test_%s" % capability,)
            for capability in expected
        }
        self.assertEqual(CAPABILITY_TEST_MATRIX, expected_mapping)
        all_targets = [
            target
            for targets in CAPABILITY_TEST_MATRIX.values()
            for target in targets
        ]
        self.assertEqual(len(all_targets), len(expected_mapping))
        self.assertEqual(len(all_targets), len(set(all_targets)))
        for capability, targets in CAPABILITY_TEST_MATRIX.items():
            with self.subTest(capability=capability):
                self.assertTrue(targets)
                for target in targets:
                    class_name, separator, method_name = target.partition(".")
                    self.assertEqual(separator, ".")
                    cls = globals().get(class_name)
                    self.assertTrue(isinstance(cls, type), target)
                    self.assertTrue(issubclass(cls, unittest.TestCase), target)
                    self.assertTrue(callable(getattr(cls, method_name, None)), target)

    def test_catalog_22_and_f2_fail_closed(self):
        registry = build_default_registry()
        expected_keys = {(name, "engineering") for name in _CATALOG}
        self.assertEqual(len(_CATALOG), 22)
        self.assertEqual(set(registry.keys()), expected_keys)
        self.assertEqual(registry.block_types(), tuple(sorted(_CATALOG)))
        for block_type in _CATALOG:
            with self.subTest(block_type=block_type):
                engineering = registry.resolve(block_type, "engineering")
                self.assertIs(engineering,
                              registry.resolve(block_type, "fidelity_f1"))
                with self.assertRaises(MissingVariantError):
                    registry.resolve(block_type, "fidelity_f2")

    def test_registry_loader_runtime_object_graph(self):
        task = _stage1_task()
        registry = build_default_registry()
        validate_task(task, registry)
        assembly, driver, _ = _build(task=task, registry=registry,
                                     startup_inhibit_ms=250)
        self.assertIsInstance(assembly, TaskRuntimeAssembly)
        self.assertIs(assembly.task, task)
        self.assertIs(assembly.runtime.task, task)
        self.assertIs(assembly.layout, assembly.runtime.layout)
        self.assertIs(assembly.store, assembly.layout.store)
        self.assertIs(assembly.executor, assembly.runtime.executor)
        self.assertIs(assembly.engine.task, task)
        self.assertIs(assembly.engine.layout, assembly.layout)
        self.assertIs(assembly.engine.executor, assembly.executor)
        self.assertIs(assembly.output_policy.safety_state,
                      assembly.safety_state)
        self.assertIs(assembly.commit_supervisor.policy,
                      assembly.output_policy)
        self.assertEqual(assembly.commit_supervisor.channels(), ("DO0",))
        self.assertIsNotNone(assembly.commit_port.write_gate)
        self.assertEqual(assembly.monitor.cycle_ns, 500_000_000)
        self.assertEqual(assembly.startup_inhibit_ms, 250)
        self.assertTrue(assembly.runner.shadow)
        self.assertEqual(driver.commands, [])

    def test_cold_start_default_write_disable(self):
        assembly, driver, _ = _build(startup_inhibit_ms=100)
        cold = assembly.safety_state.read()
        self.assertFalse(cold.system_ready)
        self.assertFalse(cold.output_enable)
        self.assertTrue(cold.scan_ok)
        self.assertTrue(cold.watchdog_ok)
        first = assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        second = assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(first.logical_outputs(), {"DO0": False})
        self.assertEqual(second.logical_outputs(), {"DO0": False})
        self.assertTrue(assembly.runner.shadow)
        self.assertFalse(assembly.runner.writes_enabled)
        self.assertFalse(assembly.startup_controller.system_ready)
        self.assertEqual(assembly.commit_port.attempts, 0)
        self.assertEqual(driver.commands, [])

    def test_readiness_window_withdraw_operator_gate(self):
        assembly, driver, clock = _build(startup_inhibit_ms=100)
        self.assertIs(assembly.output_policy.safety_state,
                      assembly.safety_state)
        self.assertFalse(assembly.apply_readiness(_readiness()).system_ready)
        clock.advance_ms(99)
        self.assertFalse(assembly.apply_readiness(_readiness()).system_ready)
        self.assertFalse(assembly.apply_readiness(
            _readiness(io_ready=False)).system_ready)
        withdrawn = assembly.safety_state.read()
        self.assertFalse(withdrawn.system_ready)
        self.assertTrue(withdrawn.output_enable)
        self.assertTrue(withdrawn.comm_ok)
        self.assertTrue(withdrawn.safety_ok)
        self.assertTrue(withdrawn.interlock_ok)
        clock.advance_ms(100)
        self.assertFalse(assembly.apply_readiness(
            _readiness(output_enable=False)).system_ready)
        clock.advance_ms(100)
        released = assembly.apply_readiness(
            _readiness(output_enable=False))
        self.assertTrue(released.system_ready)
        self.assertFalse(released.output_enable)
        released_state = assembly.safety_state.read()
        self.assertTrue(released_state.system_ready)
        self.assertFalse(released_state.output_enable)
        self.assertTrue(released_state.comm_ok)
        self.assertTrue(released_state.safety_ok)
        self.assertTrue(released_state.interlock_ok)
        assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        result = assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(result.logical_outputs(), {"DO0": False})

        enabled = assembly.apply_readiness(_readiness(output_enable=True))
        self.assertTrue(enabled.system_ready)
        self.assertTrue(enabled.output_enable)
        enabled_state = assembly.safety_state.read()
        self.assertTrue(enabled_state.system_ready)
        self.assertTrue(enabled_state.output_enable)
        self.assertTrue(enabled_state.comm_ok)
        self.assertTrue(enabled_state.safety_ok)
        self.assertTrue(enabled_state.interlock_ok)
        enabled_first = assembly.runner.scan_cycle(
            {"DI0": True, "DI1": False})
        enabled_second = assembly.runner.scan_cycle(
            {"DI0": True, "DI1": False})
        self.assertEqual(enabled_first.logical_outputs(), {"DO0": True})
        self.assertEqual(enabled_second.logical_outputs(), {"DO0": True})

        disabled = assembly.apply_readiness(_readiness(output_enable=False))
        self.assertTrue(disabled.system_ready)
        self.assertFalse(disabled.output_enable)
        disabled_state = assembly.safety_state.read()
        self.assertTrue(disabled_state.system_ready)
        self.assertFalse(disabled_state.output_enable)
        self.assertTrue(disabled_state.comm_ok)
        self.assertTrue(disabled_state.safety_ok)
        self.assertTrue(disabled_state.interlock_ok)
        disabled_scan = assembly.runner.scan_cycle(
            {"DI0": True, "DI1": False})
        self.assertEqual(disabled_scan.logical_outputs(), {"DO0": False})
        self.assertTrue(assembly.runner.shadow)
        self.assertEqual(assembly.commit_port.attempts, 0)
        self.assertEqual(driver.commands, [])

    def test_explicit_real_write_policy_supervisor_receipt(self):
        assembly, driver, _ = _build(
            task=_rate_limit_task(), initial_safety=SafetySnapshot.all_ok())
        self.assertTrue(assembly.runner.shadow)
        shadow_values = []
        for _ in range(4):
            result = assembly.runner.scan_cycle({})
            shadow_values.append(result.logical_outputs()["AO0"])
        self.assertEqual(shadow_values, [2.0, 4.0, 6.0, 8.0])
        self.assertEqual(driver.commands, [])

        # shadow 历史已远离 safe_value；切到实写后首拍必须重建
        # safe 边界，因而是 0.0 -> 2.0，不得沿用 8.0 -> 10.0。
        assembly.runner.set_write_enabled(True)
        first = assembly.runner.scan_cycle({})
        self.assertEqual(first.outputs(), {"AO0": 2.0})
        self.assertEqual(driver.commands, [{"AO0": 2.0}])
        self.assertEqual(assembly.commit_port.attempts, 1)
        receipt = assembly.commit_supervisor.last_commit_receipts()["AO0"]
        diagnostic = assembly.commit_supervisor.diagnostics()["AO0"]
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.commanded_value, 2.0)
        self.assertEqual(diagnostic.last_physical_committed, 2.0)
        self.assertFalse(diagnostic.commit_fault)
        self.assertFalse(diagnostic.channel_fault)

    def test_ton_state_block_five_step_prev(self):
        assembly, driver, _ = _build(initial_safety=SafetySnapshot.all_ok())
        ton = TON()
        latch = SR()
        plan = ((True, False), (True, False), (False, False),
                (False, True), (True, True))
        previous = assembly.engine.prev
        for start, reset in plan:
            result = assembly.runner.scan_cycle({"DI0": start, "DI1": reset})
            expected_q, expected_et = ton.step(
                500, IN=start, PT_ms=1000)
            expected_latch = latch.step(SET1=start, RESET=reset)
            self.assertEqual(result.inputs.read("Start"), start)
            self.assertEqual(result.inputs.read("Reset"), reset)
            self.assertEqual(assembly.store.read(
                persistent_key("PLC_PRG.T1", "Q")), expected_q)
            self.assertEqual(assembly.store.read(
                persistent_key("PLC_PRG.T1", "ET_ms")), expected_et)
            self.assertEqual(assembly.store.read(
                persistent_key("PLC_PRG.L1", "Q1")), expected_latch)
            self.assertEqual(result.logical_outputs(),
                             {"DO0": expected_q and expected_latch})
            self.assertIs(result.prev, assembly.engine.prev)
            self.assertEqual(
                result.prev.as_dict(), assembly.store.snapshot().as_dict())
            self.assertIsNot(assembly.engine.prev, previous)
            previous = assembly.engine.prev
        self.assertEqual(driver.commands, [])

    def test_scan_commit_channel_fault_layering(self):
        scan_assembly, scan_driver, _ = _build(
            task=_fault_task(), initial_safety=SafetySnapshot.all_ok())
        prev_before_fault = scan_assembly.engine.prev
        self.assertEqual(
            scan_assembly.output_policy.diagnostic_last_effective(),
            {"DO0": None})
        with self.assertRaises(ScanFaultSafeCommit) as scan_raised:
            scan_assembly.runner.scan_cycle({})
        scan_signal = scan_raised.exception
        self.assertIsInstance(scan_signal.original_exception,
                              IRExecutionError)
        self.assertEqual(scan_signal.safe_image, {"DO0": False})
        self.assertTrue(scan_signal.shadow)
        self.assertTrue(scan_signal.write_suppressed_by_shadow)
        self.assertTrue(scan_signal.shadow_logic_adopted)
        self.assertFalse(scan_signal.safe_commit_succeeded)
        self.assertIsNone(scan_signal.failed_stage)
        self.assertIsNone(scan_signal.fallback_exception)
        self.assertIsNone(scan_signal.commit_exception)
        self.assertIs(scan_assembly.engine.prev, prev_before_fault)
        self.assertFalse(scan_assembly.safety_state.read().scan_ok)
        self.assertEqual(
            scan_assembly.output_policy.diagnostic_last_effective(),
            {"DO0": False})
        self.assertEqual(scan_assembly.commit_port.attempts, 0)
        self.assertEqual(scan_driver.commands, [])

        commit_driver = _FailingDriver()
        commit_assembly, _, _ = _build(
            task=_stage1_task(commit_fault_retry_n=2),
            driver=commit_driver, initial_safety=SafetySnapshot.all_ok())
        commit_assembly.runner.set_write_enabled(True)
        prev_before_first_commit_fault = commit_assembly.engine.prev
        prev_content_before_first_commit_fault = (
            prev_before_first_commit_fault.as_dict())
        with self.assertRaises(PartialCommitError):
            commit_assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertIs(commit_assembly.engine.prev,
                      prev_before_first_commit_fault)
        self.assertEqual(commit_assembly.engine.prev.as_dict(),
                         prev_content_before_first_commit_fault)
        first_status = commit_assembly.commit_supervisor.diagnostics()["DO0"]
        self.assertTrue(first_status.commit_fault)
        self.assertFalse(first_status.channel_fault)
        self.assertEqual(first_status.consecutive_failures, 1)
        self.assertFalse(first_status.last_receipt.overridden_safe)
        self.assertEqual(commit_driver.commands, [{"DO0": False}])

        prev_before_second_commit_fault = commit_assembly.engine.prev
        prev_content_before_second_commit_fault = (
            prev_before_second_commit_fault.as_dict())
        with self.assertRaises(PartialCommitError):
            commit_assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertIs(commit_assembly.engine.prev,
                      prev_before_second_commit_fault)
        self.assertEqual(commit_assembly.engine.prev.as_dict(),
                         prev_content_before_second_commit_fault)
        second_status = commit_assembly.commit_supervisor.diagnostics()["DO0"]
        self.assertTrue(second_status.commit_fault)
        self.assertTrue(second_status.channel_fault)
        self.assertEqual(second_status.consecutive_failures, 2)
        self.assertTrue(second_status.last_receipt.overridden_safe)
        self.assertIs(commit_assembly.store.read("Motor"), True)
        self.assertEqual(commit_driver.commands,
                         [{"DO0": False}, {"DO0": False}])
        self.assertTrue(commit_assembly.safety_state.read().scan_ok)
        self.assertEqual(commit_assembly.commit_port.attempts, 1)

    def test_monitor_timeout_once_next_cycle(self):
        assembly, driver, clock = _build(
            initial_safety=SafetySnapshot.all_ok())
        normal = assembly.monitor.begin_cycle()
        assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        clock.advance_ms(500)
        normal_observation = assembly.monitor.finish_cycle(normal)
        self.assertFalse(normal_observation.timed_out)
        self.assertFalse(assembly.monitor.has_pending_event)

        second_normal = assembly.monitor.begin_cycle()
        second_scan = assembly.runner.scan_cycle(
            {"DI0": True, "DI1": False})
        clock.advance_ms(500)
        second_observation = assembly.monitor.finish_cycle(second_normal)
        self.assertFalse(second_observation.timed_out)
        self.assertEqual(second_scan.logical_outputs(), {"DO0": True})
        self.assertIs(assembly.store.read(
            persistent_key("PLC_PRG.T1", "Q")), True)
        self.assertEqual(assembly.output_policy.diagnostic_last_effective(),
                         {"DO0": True})

        timed = assembly.monitor.begin_cycle()
        store_before_watchdog = assembly.store.snapshot().as_dict()
        prev_before_watchdog = assembly.engine.prev
        clock.advance_ms(1000)
        event = assembly.monitor.poll_timeout()
        self.assertIsNotNone(event)
        with self.assertRaises(WatchdogSafeCommit) as raised:
            assembly.monitor.dispatch_pending(assembly.runner.trigger_watchdog)
        signal = raised.exception
        self.assertEqual(signal.safe_image, {"DO0": False})
        self.assertTrue(signal.shadow)
        self.assertTrue(signal.write_suppressed_by_shadow)
        self.assertTrue(signal.shadow_logic_adopted)
        self.assertFalse(signal.safe_commit_succeeded)
        self.assertIsNone(signal.original_exception)
        self.assertIsNone(signal.fallback_exception)
        self.assertIsNone(signal.failed_stage)
        self.assertFalse(assembly.safety_state.read().watchdog_ok)
        self.assertEqual(assembly.store.snapshot().as_dict(),
                         store_before_watchdog)
        self.assertIs(assembly.engine.prev, prev_before_watchdog)
        self.assertEqual(assembly.output_policy.diagnostic_last_effective(),
                         {"DO0": False})
        self.assertEqual(assembly.commit_port.attempts, 0)
        self.assertFalse(assembly.monitor.has_pending_event)
        self.assertFalse(assembly.monitor.dispatch_pending(
            assembly.runner.trigger_watchdog))
        timed_observation = assembly.monitor.finish_cycle(timed)
        self.assertTrue(timed_observation.timed_out)

        next_token = assembly.monitor.begin_cycle()
        self.assertEqual(next_token.sequence, timed.sequence + 1)
        next_scan = assembly.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(next_scan.logical_outputs(), {"DO0": False})
        clock.advance_ms(500)
        next_observation = assembly.monitor.finish_cycle(next_token)
        self.assertFalse(next_observation.timed_out)
        self.assertFalse(assembly.monitor.has_pending_event)
        self.assertEqual(driver.commands, [])

    def test_dual_assembly_full_isolation(self):
        task = _stage1_task()
        registry = build_default_registry()
        first_driver = _SwitchableDriver()
        first, first_driver, first_clock = _build(
            task=task, registry=registry, driver=first_driver,
            startup_inhibit_ms=0)
        second, second_driver, _ = _build(
            task=task, registry=registry, startup_inhibit_ms=0)
        for left, right in (
            (first.layout, second.layout),
            (first.store, second.store),
            (first.executor, second.executor),
            (first.engine, second.engine),
            (first.startup_controller, second.startup_controller),
            (first.monitor, second.monitor),
            (first.safety_state, second.safety_state),
            (first.output_policy, second.output_policy),
            (first.commit_supervisor, second.commit_supervisor),
            (first.commit_port, second.commit_port),
            (first.commit_port.write_gate, second.commit_port.write_gate),
            (first.runner, second.runner),
            (first_driver, second_driver),
        ):
            self.assertIsNot(left, right)

        # 先让 second 独立建立一拍块状态，再冻结其全域基线；
        # 后续只操作 first，second 必须逐域精确不变。
        second.runner.scan_cycle({"DI0": True, "DI1": False})
        second_store = second.store.snapshot().as_dict()
        second_prev = second.engine.prev
        second_safety = second.safety_state.read()
        second_diagnostics = second.commit_supervisor.diagnostics()
        second_receipts = second.commit_supervisor.last_commit_receipts()
        self.assertTrue(first.apply_readiness(_readiness()).system_ready)
        self.assertFalse(second.startup_controller.system_ready)
        first.runner.scan_cycle({"DI0": True, "DI1": False})
        first.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(first.store.read(
            persistent_key("PLC_PRG.T1", "ET_ms")), 1000)
        self.assertEqual(second.store.read(
            persistent_key("PLC_PRG.T1", "ET_ms")), 500)
        first_token = first.monitor.begin_cycle()
        self.assertTrue(first.monitor.active)
        self.assertFalse(second.monitor.active)
        first_clock.advance_ms(500)
        first.monitor.finish_cycle(first_token)
        with self.assertRaises(WatchdogSafeCommit):
            first.runner.trigger_watchdog()
        self.assertFalse(first.safety_state.read().watchdog_ok)
        first.runner.set_write_enabled(True)
        first_driver.fail = True
        with self.assertRaises(PartialCommitError):
            first.runner.scan_cycle({"DI0": False, "DI1": True})
        self.assertTrue(
            first.commit_supervisor.diagnostics()["DO0"].commit_fault)
        self.assertTrue(first.runner.writes_enabled)

        self.assertEqual(second.store.snapshot().as_dict(), second_store)
        self.assertIs(second.engine.prev, second_prev)
        self.assertEqual(second.safety_state.read(), second_safety)
        self.assertEqual(second.commit_supervisor.diagnostics(),
                         second_diagnostics)
        self.assertEqual(second.commit_supervisor.last_commit_receipts(),
                         second_receipts)
        self.assertFalse(second.startup_controller.system_ready)
        self.assertFalse(second.monitor.active)
        self.assertTrue(second.runner.shadow)
        self.assertFalse(second.runner.writes_enabled)
        self.assertEqual(second_driver.commands, [])

        # first 已把自己的 TON/SR 复位并进入 watchdog/commit fault；
        # second 继续一拍必须仍从自己 ET=500/Q1=True 的历史前进。
        reference_ton = TON()
        reference_latch = SR()
        reference_ton.step(500, IN=True, PT_ms=1000)
        reference_latch.step(SET1=True, RESET=False)
        expected_q, expected_et = reference_ton.step(
            500, IN=True, PT_ms=1000)
        expected_latch = reference_latch.step(SET1=True, RESET=False)
        resumed = second.runner.scan_cycle({"DI0": True, "DI1": False})
        self.assertEqual(second.store.read(
            persistent_key("PLC_PRG.T1", "ET_ms")), expected_et)
        self.assertEqual(second.store.read(
            persistent_key("PLC_PRG.T1", "Q")), expected_q)
        self.assertEqual(second.store.read(
            persistent_key("PLC_PRG.L1", "Q1")), expected_latch)
        self.assertIs(second.store.read("Motor"),
                      expected_q and expected_latch)
        self.assertEqual(resumed.logical_outputs(), {"DO0": False})
        self.assertEqual(second_driver.commands, [])

    def test_assembly_fail_closed_registry_reusable(self):
        registry = build_default_registry()
        original_keys = registry.keys()
        cases = (
            (_stage1_task(), {"startup_inhibit_ms": -1}),
            (_invalid_ctor_task(), {}),
            (_stage1_task(), {"numeric_mode": "engineering"}),
        )
        for task, options in cases:
            with self.subTest(options=options):
                driver = _ConfirmingDriver()
                with self.assertRaises(StartupValidationError):
                    build_task_runtime(
                        task, registry, driver=driver,
                        watchdog_timeout_ms=1000, **options)
                self.assertEqual(driver.commands, [])
                self.assertEqual(registry.keys(), original_keys)

        assembly, driver, _ = _build(registry=registry)
        self.assertIsInstance(assembly, TaskRuntimeAssembly)
        self.assertEqual(driver.commands, [])


if __name__ == "__main__":
    unittest.main()
