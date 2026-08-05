"""WP-060 startup/readiness 的确定性与 TOCTOU 反证。"""
from dataclasses import FrozenInstanceError, fields
import unittest

from src.runtime.startup import (
    ReadinessSnapshot, ReadinessError, ReadinessConfigError, ReadinessClockError,
    StartupReadinessController, StartupState, _copy_readiness,
)


class _Clock:
    def __init__(self, now=0, mutate=None):
        self.now = now
        self.mutate = mutate

    def __call__(self):
        if self.mutate is not None:
            self.mutate()
        return self.now


def _ready(**changes):
    values = dict(io_ready=True, bus_ready=True, comm_ready=True,
                  safety_ok=True, interlock_ok=True, output_enable=True)
    values.update(changes)
    return ReadinessSnapshot(**values)


class TestStartupReadinessController(unittest.TestCase):
    def test_snapshot_and_state_keep_fields_and_frozen_contract_without_slots(self):
        self.assertEqual(
            tuple(field.name for field in fields(ReadinessSnapshot)),
            ("io_ready", "bus_ready", "comm_ready", "safety_ok", "interlock_ok",
             "output_enable"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(StartupState)),
            ("system_ready", "preconditions_ok", "output_enable", "in_window",
             "window_elapsed_ns", "inhibit_ns", "observed_ns"),
        )
        snapshot = _ready()
        state = StartupReadinessController(0, clock_ns=_Clock()).observe(snapshot)
        with self.assertRaises(FrozenInstanceError):
            snapshot.io_ready = False
        with self.assertRaises(FrozenInstanceError):
            state.system_ready = False

    def test_untrusted_extra_attributes_and_subclass_do_not_bypass_copy(self):
        snapshot = _ready()
        object.__setattr__(snapshot, "untrusted_extra", object())
        self.assertEqual(_copy_readiness(snapshot), (True,) * 6)

        class SnapshotSubclass(ReadinessSnapshot):
            pass

        subclass = SnapshotSubclass(True, True, True, True, True, True)
        object.__setattr__(subclass, "untrusted_extra", object())
        with self.assertRaises(ReadinessConfigError):
            _copy_readiness(subclass)

    def test_public_state_and_diagnostics_are_complete(self):
        clock = _Clock(7)
        c = StartupReadinessController(3, clock_ns=clock)
        state = c.observe(_ready())
        self.assertIs(type(state), StartupState)
        self.assertEqual(state.system_ready, False)
        self.assertEqual(state.preconditions_ok, True)
        self.assertEqual(state.output_enable, True)
        self.assertEqual(state.in_window, True)
        self.assertEqual(state.window_elapsed_ns, 0)
        self.assertEqual(state.inhibit_ns, 3_000_000)
        self.assertEqual(state.observed_ns, 7)
        self.assertFalse(c.system_ready)
        self.assertFalse(c.released)
        self.assertEqual(c.inhibit_ns, 3_000_000)
        self.assertEqual(c.startup_inhibit_ms, 3)
        self.assertTrue(c.in_stable_window)

    def test_config_and_clock_errors_are_exact_and_do_not_move_state(self):
        for bad in (True, -1, 1.0, object()):
            with self.subTest(inhibit=type(bad)):
                with self.assertRaises(ReadinessConfigError):
                    StartupReadinessController(bad)
        for bad_clock in (None, 1, object()):
            with self.subTest(clock=type(bad_clock)):
                with self.assertRaises(ReadinessConfigError):
                    StartupReadinessController(1, clock_ns=bad_clock)
        clock = _Clock(1)
        c = StartupReadinessController(1, clock_ns=clock)
        c.observe(_ready())
        clock.now = -1
        with self.assertRaises(ReadinessClockError):
            c.observe(_ready())
        self.assertEqual(c.last_seen_ns, 1)

    def test_private_tuple_or_token_bypass_is_absent(self):
        c = StartupReadinessController(0, clock_ns=_Clock(1))
        self.assertFalse(hasattr(c, "_prepare_from_copy"))
        self.assertFalse(hasattr(c, "_commit"))
        self.assertFalse(hasattr(c, "_PreparedReadiness"))
        with self.assertRaises(ReadinessConfigError):
            c._observe_with_commit((True,) * 6, lambda _state: None)
        self.assertEqual(c.last_seen_ns, 0)

    def test_same_timestamp_is_idempotent_and_controllers_are_isolated(self):
        clock = _Clock(10)
        a = StartupReadinessController(1, clock_ns=clock)
        b = StartupReadinessController(0, clock_ns=clock)
        first = a.observe(_ready())
        second = a.observe(_ready())
        self.assertEqual(first, second)
        self.assertFalse(a.system_ready)
        self.assertTrue(b.observe(_ready()).system_ready)
        self.assertFalse(a.system_ready)

    def test_clock_return_type_and_callback_exception_leave_all_state_unchanged(self):
        class _Abort(BaseException):
            pass
        clock = _Clock(10)
        c = StartupReadinessController(1, clock_ns=clock)
        c.observe(_ready())
        before = (c._last_seen_ns, c._window_start_ns, c._released)
        for bad in (True, 1.0, "10", -1):
            with self.subTest(clock_return=type(bad)):
                clock.now = bad
                with self.assertRaises(ReadinessClockError):
                    c.observe(_ready())
                self.assertEqual((c._last_seen_ns, c._window_start_ns, c._released), before)
        clock.now = 20
        with self.assertRaises(_Abort):
            c._observe_with_commit(_ready(), lambda *_args: (_ for _ in ()).throw(_Abort()))
        self.assertEqual((c._last_seen_ns, c._window_start_ns, c._released), before)
    def test_threshold_and_zero_window(self):
        clock = _Clock(0)
        c = StartupReadinessController(10, clock_ns=clock)
        self.assertFalse(c.apply(_ready()).system_ready)
        clock.now = 9_999_999
        self.assertFalse(c.apply(_ready()).system_ready)
        clock.now = 10_000_000
        self.assertTrue(c.apply(_ready()).system_ready)
        self.assertTrue(StartupReadinessController(0, clock_ns=_Clock()).apply(
            _ready()).system_ready)

    def test_release_consumes_window_and_false_requires_a_fresh_full_window(self):
        clock = _Clock(0)
        c = StartupReadinessController(1, clock_ns=clock)
        self.assertFalse(c.apply(_ready()).system_ready)

        clock.now = 1_000_000
        released = c.apply(_ready())
        self.assertTrue(released.system_ready)
        self.assertFalse(released.in_window)
        self.assertEqual(released.window_elapsed_ns, 1_000_000)
        self.assertIsNone(c._window_start_ns)

        clock.now = 2_000_000
        steady = c.apply(_ready())
        self.assertTrue(steady.system_ready)
        self.assertFalse(steady.in_window)
        self.assertEqual(steady.window_elapsed_ns, 0)
        self.assertIsNone(c._window_start_ns)

        clock.now = 2_500_000
        revoked = c.apply(_ready(io_ready=False))
        self.assertFalse(revoked.system_ready)
        self.assertFalse(revoked.in_window)
        self.assertEqual(revoked.window_elapsed_ns, 0)

        clock.now = 3_000_000
        restarted = c.apply(_ready())
        self.assertFalse(restarted.system_ready)
        self.assertTrue(restarted.in_window)
        self.assertEqual(restarted.window_elapsed_ns, 0)
        clock.now = 3_999_999
        self.assertFalse(c.apply(_ready()).system_ready)
        clock.now = 4_000_000
        self.assertTrue(c.apply(_ready()).system_ready)

    def test_jitter_resets_and_release_revokes(self):
        clock = _Clock(0)
        c = StartupReadinessController(1, clock_ns=clock)
        c.apply(_ready())
        clock.now = 500_000
        self.assertFalse(c.apply(_ready(io_ready=False)).system_ready)
        clock.now = 1_500_000
        self.assertFalse(c.apply(_ready()).system_ready)
        clock.now = 2_500_000
        self.assertTrue(c.apply(_ready()).system_ready)
        clock.now = 2_500_001
        self.assertFalse(c.apply(_ready(interlock_ok=False)).system_ready)

    def test_exact_bool_and_deleted_field_are_zero_observation(self):
        clock = _Clock(7)
        c = StartupReadinessController(1, clock_ns=clock)
        bad = _ready()
        object.__setattr__(bad, "io_ready", 1)
        with self.assertRaises(ReadinessConfigError):
            c.apply(bad)
        self.assertEqual(c.last_seen_ns, 0)

    def test_each_readiness_field_rejects_evil_value_without_observation(self):
        class _Evil:
            def __repr__(self):
                raise AssertionError("repr must not run")
            def __bool__(self):
                raise AssertionError("bool must not run")
        for field in ("io_ready", "bus_ready", "comm_ready", "safety_ok",
                      "interlock_ok", "output_enable"):
            with self.subTest(field=field):
                c = StartupReadinessController(0, clock_ns=_Clock())
                value = _ready()
                object.__setattr__(value, field, _Evil())
                with self.assertRaises(ReadinessConfigError):
                    c.apply(value)
                self.assertEqual(c.last_seen_ns, 0)
        deleted = _ready()
        object.__delattr__(deleted, "io_ready")
        with self.assertRaises(ReadinessConfigError):
            c.apply(deleted)
        self.assertEqual(c.last_seen_ns, 0)

    # 三条 WP-055 旧 TOCTOU 红灯：时钟后不得重读公开 readiness。
    def test_clock_mutation_to_int_does_not_prevent_trusted_release(self):
        readiness = _ready()
        clock = _Clock(0, lambda: object.__setattr__(readiness, "io_ready", 1))
        result = StartupReadinessController(0, clock_ns=clock).apply(readiness)
        self.assertTrue(result.system_ready)

    def test_clock_deletes_field_does_not_leak_attribute_error_or_advance_late(self):
        readiness = _ready()
        controller = StartupReadinessController(
            1, clock_ns=_Clock(0, lambda: object.__delattr__(readiness, "io_ready")))
        self.assertFalse(controller.apply(readiness).system_ready)
        self.assertEqual(controller.last_seen_ns, 0)

    def test_clock_mutates_output_enable_but_result_uses_trusted_copy(self):
        readiness = _ready()
        clock = _Clock(0, lambda: object.__setattr__(readiness, "output_enable", 1))
        result = StartupReadinessController(0, clock_ns=clock).apply(readiness)
        self.assertTrue(result.system_ready)
        self.assertTrue(result.output_enable)

    def test_bad_or_rollback_clock_does_not_advance(self):
        clock = _Clock(10)
        c = StartupReadinessController(1, clock_ns=clock)
        c.apply(_ready())
        clock.now = 9
        with self.assertRaises(ReadinessClockError):
            c.apply(_ready())
        self.assertEqual(c.last_seen_ns, 10)

    def test_direct_observe_reentry_is_rejected_and_guard_recovers(self):
        holder = {}

        def reenter():
            holder["controller"].observe(_ready())
            return 0

        controller = StartupReadinessController(0, clock_ns=reenter)
        holder["controller"] = controller
        before = (controller._last_seen_ns,
                  controller._window_start_ns,
                  controller._released)

        with self.assertRaises(ReadinessClockError) as raised:
            controller.observe(_ready())

        self.assertIsInstance(raised.exception.__cause__, ReadinessConfigError)
        self.assertEqual((controller._last_seen_ns,
                          controller._window_start_ns,
                          controller._released), before)

        controller._clock_ns = _Clock(1)
        self.assertTrue(controller.observe(_ready()).system_ready)


if __name__ == "__main__":
    unittest.main()
