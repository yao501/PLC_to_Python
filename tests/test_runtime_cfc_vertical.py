"""WP-20260810-090: compiled CFC Task → existing Runtime vertical evidence.

This module intentionally exercises only ``compile_cfc_task(...).task`` followed
by ``build_runtime`` and ``Executor.execute_programs``.  It is not a ScanEngine,
OutputPolicy, or physical-I/O test, and its Python results are not PLC evidence.
"""
from __future__ import annotations

import copy
import itertools
import unittest
from dataclasses import fields, is_dataclass
from unittest.mock import patch

from src.runtime.cfc_lowering import CFCNodeBody, compile_cfc_task
from src.runtime.cfc_order import CFCOrderError
from src.runtime.descriptors import build_default_registry
from src.runtime.executor import Executor, IRExecutionError
from src.runtime.ir import (
    CallFb, InstanceDecl, LoadConst, LoadPrev, LoadVar, POUDefinition,
    ProgramInstance, StoreVar, Task, UnOp, VarDecl,
)
from src.runtime.parameters import StartupValidationError, build_runtime


_EXACT_22_REGISTRY_KEYS = (
    ("APCCD", "engineering"), ("APCGCQ", "engineering"),
    ("APCHSACCUM", "engineering"), ("APCHSFOP", "engineering"),
    ("APCHSHLLIM", "engineering"), ("APCHSRATELIM", "engineering"),
    ("APCHXHCL", "engineering"), ("APCM", "engineering"),
    ("APCMAUTOPARA", "engineering"), ("APCPID", "engineering"),
    ("APCPIDZZD", "engineering"), ("APCRSFNAUTOPARA", "engineering"),
    ("APCSPFINDER", "engineering"), ("APCSTATISTICS", "engineering"),
    ("BLINK", "engineering"), ("F_TRIG", "engineering"),
    ("RS", "engineering"), ("R_TRIG", "engineering"),
    ("SR", "engineering"), ("TOF", "engineering"),
    ("TON", "engineering"), ("TP", "engineering"),
)


def _stable_exact_data(value):
    """Snapshot only the trusted, exact model data constructed in this module."""
    if type(value) in (type(None), bool, int, float, str, bytes):
        return value
    if type(value) is tuple:
        return ("tuple", tuple(_stable_exact_data(item) for item in value))
    if type(value) is list:
        return ("list", tuple(_stable_exact_data(item) for item in value))
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise TypeError("trusted snapshots require string dictionary keys")
        return ("dict", tuple((key, _stable_exact_data(value[key]))
                              for key in sorted(value)))
    if type(value) in (set, frozenset):
        items = tuple(sorted(_stable_exact_data(item) for item in value))
        return (type(value).__name__, items)
    if is_dataclass(value) and type(value).__module__.startswith("src.runtime."):
        return (type(value).__module__, type(value).__qualname__,
                tuple((field.name, _stable_exact_data(getattr(value, field.name)))
                      for field in fields(value)))
    raise TypeError(f"unsupported trusted snapshot value: {type(value)!r}")


def _task_snapshot(task):
    if type(task) is not Task:
        raise TypeError("task snapshot requires the exact runtime Task type")
    return _stable_exact_data(task)


def _registry_fingerprint(registry):
    """Public, complete default-catalog fingerprint; no Registry copying or hooks."""
    keys = registry.keys()
    if keys != _EXACT_22_REGISTRY_KEYS:
        raise AssertionError("default registry catalog drifted")
    entries = []
    for key in keys:
        schema, adapter = registry.resolve(*key)
        entries.append((
            key,
            _stable_exact_data(schema.to_json()),
            id(adapter.cls),
            id(adapter.call_adapter),
            _stable_exact_data(adapter.ctor_args),
            id(adapter.serializer),
        ))
    return tuple(entries)


def _pin(pin_id, formal, direction, value_key, iec="BOOL"):
    return {"pin_id": pin_id, "formal_name": formal, "direction": direction,
            "iec_type": iec, "value_key": value_key}


def _node(node_id, kind, pins, *, order=None):
    return {"node_id": node_id, "kind": kind, "type_name": node_id,
            "instance_name": "", "execution_order_id": order,
            "feedback_marker": None, "pins": pins}


def _conn(source_node_id, source_pin_id, target_node_id, target_pin_id,
          read_mode="current"):
    return {"source_node_id": source_node_id, "source_pin_id": source_pin_id,
            "target_node_id": target_node_id, "target_pin_id": target_pin_id,
            "read_mode": read_mode}


def _cfc_task(payload, *, locals, gvl, instances=()):
    main = POUDefinition("Main", "PROGRAM", "CFC", locals=list(locals),
                         instances=list(instances), source=payload, code=None)
    return Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=list(gvl),
                pou_lib={"Main": main}, cycle_ms=500)


def _manual_task(code, *, locals, gvl, instances=()):
    main = POUDefinition("Main", "PROGRAM", "CFC", locals=list(locals),
                         instances=list(instances), source=None, code=list(code))
    return Task(programs=[ProgramInstance("Main", "PLC_PRG")], gvl=list(gvl),
                pou_lib={"Main": main}, cycle_ms=500)


def _run_cycles(assembly, writes, keys, *, before_execute=None):
    """Executor-only scan helper: prev advances only after a successful whole scan."""
    prev = assembly.store.snapshot()
    trace = []
    for cycle, values in enumerate(writes):
        for key in sorted(values):
            value = values[key]
            assembly.store.write(key, value)
        if before_execute is not None:
            before_execute(cycle, assembly.store)
        assembly.executor.execute_programs(prev)
        trace.append(tuple(assembly.store.read(key) for key in keys))
        prev = assembly.store.snapshot()
    return trace


def _auto_payload():
    return {
        "schema_version": "cfc-model-v1", "carrier": "user_defined",
        "execution_order_mode": "auto", "order_source": "user_defined",
        "nodes": [
            _node("IN", "input", [_pin("out", "OUT", "OUT", "Start")]),
            _node("A", "block", [_pin("in", "IN", "IN", "IA"),
                                    _pin("out", "OUT", "OUT", "X")]),
            _node("B", "block", [_pin("in", "IN", "IN", "IB"),
                                    _pin("out", "OUT", "OUT", "Motor")]),
        ],
        "connections": [_conn("IN", "out", "A", "in"),
                        _conn("A", "out", "B", "in")],
    }


def _auto_bodies():
    return (
        CFCNodeBody("IN", ()),
        CFCNodeBody("A", (LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"))),
        CFCNodeBody("B", (LoadVar("IB", "BOOL"), StoreVar("Motor", "BOOL"))),
    )


def _auto_task(payload):
    return _cfc_task(payload, locals=(VarDecl("IA", "BOOL"), VarDecl("IB", "BOOL")),
                     gvl=(VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
                          VarDecl("X", "BOOL", section="VAR_GLOBAL"),
                          VarDecl("Motor", "BOOL", section="VAR_GLOBAL")))


def _auto_manual_task():
    return _manual_task((
        LoadVar("Start", "BOOL"), StoreVar("IA", "BOOL"),
        LoadVar("IA", "BOOL"), StoreVar("X", "BOOL"),
        LoadVar("X", "BOOL"), StoreVar("IB", "BOOL"),
        LoadVar("IB", "BOOL"), StoreVar("Motor", "BOOL"),
    ), locals=(VarDecl("IA", "BOOL"), VarDecl("IB", "BOOL")),
       gvl=(VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
            VarDecl("X", "BOOL", section="VAR_GLOBAL"),
            VarDecl("Motor", "BOOL", section="VAR_GLOBAL")))


def _feedback_payload():
    return {
        "schema_version": "cfc-model-v1", "carrier": "plcopen_xml",
        "execution_order_mode": "explicit", "order_source": "exported",
        "nodes": [
            _node("A", "block", [_pin("in", "IN", "IN", "IA"),
                                    _pin("out", "OUT", "OUT", "X")], order=1),
            _node("B", "block", [_pin("in", "IN", "IN", "IB"),
                                    _pin("out", "OUT", "OUT", "Y")], order=2),
        ],
        "connections": [_conn("A", "out", "B", "in"),
                        _conn("B", "out", "A", "in", "previous")],
    }


def _feedback_bodies():
    return (
        CFCNodeBody("A", (LoadVar("IA", "BOOL"), UnOp("NOT", "BOOL"),
                            StoreVar("X", "BOOL"))),
        CFCNodeBody("B", (LoadVar("IB", "BOOL"), StoreVar("Y", "BOOL"))),
    )


def _feedback_task(payload):
    return _cfc_task(payload, locals=(VarDecl("IA", "BOOL"), VarDecl("IB", "BOOL")),
                     gvl=(VarDecl("X", "BOOL", section="VAR_GLOBAL"),
                          VarDecl("Y", "BOOL", section="VAR_GLOBAL")))


def _feedback_manual_task(load=LoadPrev):
    return _manual_task((
        load("Y", "BOOL"), StoreVar("IA", "BOOL"),
        LoadVar("IA", "BOOL"), UnOp("NOT", "BOOL"), StoreVar("X", "BOOL"),
        LoadVar("X", "BOOL"), StoreVar("IB", "BOOL"),
        LoadVar("IB", "BOOL"), StoreVar("Y", "BOOL"),
    ), locals=(VarDecl("IA", "BOOL"), VarDecl("IB", "BOOL")),
       gvl=(VarDecl("X", "BOOL", section="VAR_GLOBAL"),
            VarDecl("Y", "BOOL", section="VAR_GLOBAL")))


def _ton_payload():
    return {
        "schema_version": "cfc-model-v1", "carrier": "user_defined",
        "execution_order_mode": "auto", "order_source": "user_defined",
        "nodes": [
            _node("START", "input", [_pin("out", "OUT", "OUT", "Start")]),
            _node("PT", "input", [_pin("out", "OUT", "OUT", "PT_ms", "TIME")]),
            _node("TON", "block", [_pin("in", "IN", "IN", "Timer.IN"),
                                      _pin("pt", "PT_ms", "IN", "Timer.PT_ms", "TIME"),
                                      _pin("q", "Q", "OUT", "Timer.Q")]),
            _node("OUT", "output", [_pin("in", "IN", "IN", "QLink")]),
        ],
        "connections": [_conn("START", "out", "TON", "in"),
                        _conn("PT", "out", "TON", "pt"),
                        _conn("TON", "q", "OUT", "in")],
    }


def _ton_bodies():
    return (CFCNodeBody("START", ()), CFCNodeBody("PT", ()),
            CFCNodeBody("TON", (CallFb("Timer"),)),
            CFCNodeBody("OUT", (LoadVar("QLink", "BOOL"), StoreVar("Motor", "BOOL"))))


def _ton_task(payload):
    return _cfc_task(
        payload, locals=(VarDecl("QLink", "BOOL"),),
        gvl=(VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
             VarDecl("PT_ms", "TIME", section="VAR_GLOBAL", initial=1000),
             VarDecl("Motor", "BOOL", section="VAR_GLOBAL")),
        instances=(InstanceDecl("Timer", "TON", "library"),))


class TestCFCRuntimeVertical(unittest.TestCase):
    def test_acyclic_four_cycles_match_independent_manual_task(self):
        payload = _auto_payload()
        compiled = compile_cfc_task(payload, _auto_bodies(), _auto_task(payload), "Main")
        cfc = build_runtime(compiled.task, build_default_registry())
        manual = build_runtime(_auto_manual_task(), build_default_registry())
        inputs = [{"Start": value} for value in (False, True, True, False)]
        self.assertEqual(_run_cycles(cfc, inputs, ("X", "Motor")),
                         _run_cycles(manual, inputs, ("X", "Motor")))

    def test_72_node_connection_and_body_permutations_are_stable(self):
        payload = _auto_payload()
        baseline = compile_cfc_task(payload, _auto_bodies(), _auto_task(payload), "Main")
        input_values = (False, True, True, False)
        expected_trace = [(False, False), (True, True), (True, True),
                          (False, False)]
        manual = build_runtime(_auto_manual_task(), build_default_registry())
        self.assertEqual(_run_cycles(
            manual, [{"Start": value} for value in input_values], ("X", "Motor")),
            expected_trace)
        cases = 0
        for nodes, connections, bodies in itertools.product(
                itertools.permutations(payload["nodes"]),
                itertools.permutations(payload["connections"]),
                itertools.permutations(_auto_bodies())):
            candidate = copy.deepcopy(payload)
            candidate["nodes"] = list(nodes)
            candidate["connections"] = list(connections)
            result = compile_cfc_task(candidate, tuple(bodies), _auto_task(candidate), "Main")
            self.assertEqual(result.execution_order, baseline.execution_order)
            self.assertEqual(result.code, baseline.code)
            # Every compiled Task gets its own default catalog and four full scans.
            runtime = build_runtime(result.task, build_default_registry())
            self.assertEqual(_run_cycles(
                runtime, [{"Start": value} for value in input_values], ("X", "Motor")),
                expected_trace)
            cases += 1
        self.assertEqual(cases, 72)

    def test_explicit_feedback_six_cycles_matches_independent_load_prev_task(self):
        payload = _feedback_payload()
        compiled = compile_cfc_task(payload, _feedback_bodies(), _feedback_task(payload), "Main")
        cfc = build_runtime(compiled.task, build_default_registry())
        manual = build_runtime(_feedback_manual_task(), build_default_registry())
        writes = ({},) * 6
        expected = [(True, True), (False, False), (True, True),
                    (False, False), (True, True), (False, False)]
        self.assertEqual(_run_cycles(cfc, writes, ("X", "Y")), expected)
        self.assertEqual(_run_cycles(manual, writes, ("X", "Y")), expected)

    def test_load_var_feedback_counterexample_diverges_from_load_prev_trace(self):
        payload = _feedback_payload()
        compiled = compile_cfc_task(payload, _feedback_bodies(), _feedback_task(payload), "Main")
        correct = build_runtime(compiled.task, build_default_registry())
        wrong = build_runtime(_feedback_manual_task(LoadVar), build_default_registry())

        def perturb_current_only(cycle, store):
            if cycle == 0:
                store.write("Y", True)  # after prev capture: current and previous differ

        correct_trace = _run_cycles(correct, ({},) * 3, ("X", "Y"),
                                    before_execute=perturb_current_only)
        wrong_trace = _run_cycles(wrong, ({},) * 3, ("X", "Y"),
                                  before_execute=perturb_current_only)
        self.assertEqual(correct_trace[0], (True, True))
        self.assertEqual(wrong_trace[0], (False, False))
        self.assertNotEqual(correct_trace, wrong_trace)

        # A failed scan may change current values, but must not advance caller prev.
        resumed = build_runtime(_feedback_manual_task(), build_default_registry())
        caller_prev = resumed.store.snapshot()
        caller_prev_values = caller_prev.as_dict()
        main = resumed.executor.task.pou_lib["Main"]
        original_code = list(main.code)
        main.code.append(LoadVar("missing_after_load_prev", "BOOL"))
        resumed.store.write("Y", True)  # current differs from caller-held previous Y=False
        with self.assertRaises(IRExecutionError):
            resumed.executor.execute_programs(caller_prev)
        self.assertEqual(caller_prev.as_dict(), caller_prev_values)
        main.code = original_code
        resumed.store.write("Y", True)  # repair current input and code, reuse the same prev
        resumed.executor.execute_programs(caller_prev)
        self.assertTrue(resumed.store.read("X"))

    def test_ton_l2_exact_22_entry_registry_trace(self):
        payload = _ton_payload()
        registry = build_default_registry()
        self.assertEqual(registry.keys(), _EXACT_22_REGISTRY_KEYS)
        compiled = compile_cfc_task(payload, _ton_bodies(), _ton_task(payload), "Main", registry)
        runtime = build_runtime(compiled.task, registry)
        trace = _run_cycles(runtime,
                            [{"Start": value} for value in (True, True, False, True, True)],
                            ("PLC_PRG.Timer.ET_ms", "PLC_PRG.Timer.Q", "Motor"))
        self.assertEqual(trace, [(500, False, False), (1000, True, True),
                                 (0, False, False), (500, False, False),
                                 (1000, True, True)])

    def test_two_runtimes_from_one_compiled_task_are_state_isolated(self):
        payload = _ton_payload()
        registry = build_default_registry()
        compiled = compile_cfc_task(payload, _ton_bodies(), _ton_task(payload), "Main", registry)
        left = build_runtime(compiled.task, registry)
        middle = build_runtime(compiled.task, registry)
        right = build_runtime(compiled.task, registry)
        self.assertIsNot(left.store, right.store)
        self.assertIsNot(left.store, middle.store)
        self.assertIsNot(middle.store, right.store)
        self.assertIsNot(left.executor, right.executor)
        self.assertIsNot(left.executor, middle.executor)
        self.assertIsNot(middle.executor, right.executor)
        timer_instances = tuple(runtime.executor._adapters["PLC_PRG.Timer"].instance
                                for runtime in (left, middle, right))
        self.assertEqual(len({id(instance) for instance in timer_instances}), 3)
        _run_cycles(left, ({"Start": True},), ("PLC_PRG.Timer.ET_ms",))
        _run_cycles(middle, ({"Start": True},), ("PLC_PRG.Timer.ET_ms",))
        _run_cycles(left, ({"Start": True},), ("PLC_PRG.Timer.ET_ms",))
        _run_cycles(right, ({"Start": True},), ("PLC_PRG.Timer.ET_ms",))
        _run_cycles(middle, ({"Start": True}, {"Start": False}),
                    ("PLC_PRG.Timer.ET_ms",))
        self.assertEqual(left.store.read("PLC_PRG.Timer.ET_ms"), 1000)
        self.assertEqual(middle.store.read("PLC_PRG.Timer.ET_ms"), 0)
        self.assertEqual(right.store.read("PLC_PRG.Timer.ET_ms"), 500)

    def test_success_keeps_payload_bodies_pending_task_and_registry_unchanged(self):
        payload = _auto_payload()
        payload_before = _stable_exact_data(payload)
        bodies = _auto_bodies()
        bodies_before = _stable_exact_data(bodies)
        task = _auto_task(payload)
        task_before = _task_snapshot(task)
        registry = build_default_registry()
        registry_before = _registry_fingerprint(registry)
        result = compile_cfc_task(payload, bodies, task, "Main", registry)
        self.assertEqual(_stable_exact_data(payload), payload_before)
        self.assertEqual(_stable_exact_data(bodies), bodies_before)
        self.assertEqual(_task_snapshot(task), task_before)
        self.assertIs(task.pou_lib["Main"].source, payload)
        self.assertIsNone(task.pou_lib["Main"].code)
        self.assertEqual(_registry_fingerprint(registry), registry_before)
        self.assertIsNot(result.task, task)

    def test_compile_failure_is_atomic_and_never_calls_build_runtime(self):
        payload = _auto_payload()
        payload["carrier"] = "export_native"
        payload["order_source"] = "reconstructed"
        payload_before = _stable_exact_data(payload)
        bodies = _auto_bodies()
        bodies_before = _stable_exact_data(bodies)
        task = _auto_task(payload)
        task_before = _task_snapshot(task)
        registry = build_default_registry()
        registry_before = _registry_fingerprint(registry)
        result_slot = {"result": None}
        with patch("src.runtime.parameters.build_runtime") as runtime_builder:
            try:
                result_slot["result"] = compile_cfc_task(payload, bodies, task, "Main", registry)
            except CFCOrderError:
                pass
            else:
                runtime_builder(result_slot["result"].task, registry)
        runtime_builder.assert_not_called()
        self.assertIsNone(result_slot["result"])
        self.assertEqual(_stable_exact_data(payload), payload_before)
        self.assertEqual(_stable_exact_data(bodies), bodies_before)
        self.assertEqual(_task_snapshot(task), task_before)
        self.assertIs(task.pou_lib["Main"].source, payload)
        self.assertIsNone(task.pou_lib["Main"].code)
        self.assertEqual(_registry_fingerprint(registry), registry_before)

    def test_assembly_validation_failure_is_atomic_before_store_or_executor(self):
        payload = _ton_payload()
        registry = build_default_registry()
        compiled = compile_cfc_task(payload, _ton_bodies(), _ton_task(payload), "Main", registry)
        task_before = _task_snapshot(compiled.task)
        registry_before = _registry_fingerprint(registry)
        assembly_slot = {"assembly": None}
        with patch("src.runtime.parameters.build_runtime_store") as store_builder, \
             patch("src.runtime.parameters.Executor") as executor_builder:
            try:
                assembly_slot["assembly"] = build_runtime(
                    compiled.task, registry, startup_inhibit_ms=True)
            except StartupValidationError:
                pass
        store_builder.assert_not_called()
        executor_builder.assert_not_called()
        self.assertIsNone(assembly_slot["assembly"])
        self.assertEqual(_task_snapshot(compiled.task), task_before)
        self.assertEqual(_registry_fingerprint(registry), registry_before)
        self.assertEqual(compiled.task.pou_lib["Main"].code, list(compiled.code))
