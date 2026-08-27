"""WP-104: deterministic eager standard-function catalogue contracts."""
from __future__ import annotations

import unittest
from unittest import mock

from src.runtime.descriptors import build_default_registry
from src.runtime.executor import Executor, MissingStdFunctionError
from src.runtime.ir import (
    CallStd, LoadConst, POUDefinition, ProgramInstance, StdSig, StoreVar,
    Task, VarDecl,
)
from src.runtime.loader import IRValidationError, validate_task
from src.runtime.numeric import NumericMode
from src.runtime.parameters import StartupValidationError, build_runtime
from src.runtime.standard_functions import (
    default_standard_functions, standard_signature_error,
)
from src.runtime.store import build_runtime_store


def _task(code, declarations):
    pou = POUDefinition(
        name="MAIN", pou_kind="PROGRAM", language="ST",
        locals=list(declarations), code=list(code),
    )
    return Task(
        cycle_ms=500,
        programs=[ProgramInstance("MAIN", "PLC_PRG")],
        pou_lib={"MAIN": pou},
    )


def _call(name, param_types, return_type, values, target):
    code = []
    for value, iec_type in zip(values, param_types):
        code.append(LoadConst(value, iec_type))
    code.extend((CallStd(name, StdSig(tuple(param_types), return_type)),
                 StoreVar(target, return_type)))
    return code


class TestStandardFunctionCatalogue(unittest.TestCase):
    def test_catalogue_is_fresh_and_contains_only_eager_builtins(self):
        left = default_standard_functions()
        right = default_standard_functions()
        self.assertEqual(tuple(left), ("ABS", "LIMIT", "MAX", "MIN"))
        self.assertEqual(tuple(right), tuple(left))
        self.assertIsNot(left, right)
        left["BROKEN"] = lambda: None
        self.assertNotIn("BROKEN", right)
        self.assertNotIn("SEL", right)  # SEL requires lazy branch lowering in ST.

        self.assertEqual(right["ABS"](-7), 7)
        self.assertEqual(right["MIN"](9, 3, 5), 3)
        self.assertEqual(right["MAX"]("A", "Z", "M"), "Z")
        self.assertEqual(right["LIMIT"](0.0, 2.5, 1.0), 1.0)

    def test_known_signature_rules_and_unknown_extension_boundary(self):
        self.assertIsNone(standard_signature_error(
            "ABS", StdSig(("INT",), "INT")))
        self.assertIsNone(standard_signature_error(
            "ABS", StdSig(("UINT",), "UINT")))
        self.assertIsNone(standard_signature_error(
            "MIN", StdSig(("STRING", "STRING", "STRING"), "STRING")))
        self.assertIsNone(standard_signature_error(
            "MAX", StdSig(("TIME", "TIME"), "TIME")))
        self.assertIsNone(standard_signature_error(
            "LIMIT", StdSig(("REAL", "REAL", "REAL"), "REAL")))
        self.assertIsNone(standard_signature_error(
            "CUSTOM", StdSig((), "INT")))

        cases = (
            ("ABS", StdSig(("BOOL",), "BOOL")),
            ("ABS", StdSig(("INT", "INT"), "INT")),
            ("ABS", StdSig(("INT",), "DINT")),
            ("MIN", StdSig(("INT",), "INT")),
            ("MIN", StdSig(("INT", "DINT"), "DINT")),
            ("MAX", StdSig(("INT", "INT"), "DINT")),
            ("LIMIT", StdSig(("INT", "INT"), "INT")),
            ("LIMIT", StdSig(("INT", "INT", "DINT"), "DINT")),
        )
        for name, sig in cases:
            with self.subTest(name=name, sig=sig):
                error = standard_signature_error(name, sig)
                self.assertIs(type(error), str)
                self.assertTrue(error)

    def test_build_runtime_executes_all_default_functions(self):
        code = []
        code += _call("ABS", ("INT",), "INT", (-7,), "ABS_OUT")
        code += _call("MIN", ("STRING", "STRING", "STRING"), "STRING",
                      ("Z", "A", "M"), "MIN_OUT")
        code += _call("MAX", ("TIME", "TIME", "TIME"), "TIME",
                      (100, 300, 200), "MAX_OUT")
        code += _call("LIMIT", ("REAL", "REAL", "REAL"), "REAL",
                      (0.0, 2.5, 1.0), "LIMIT_OUT")
        task = _task(code, (
            VarDecl("ABS_OUT", "INT"), VarDecl("MIN_OUT", "STRING"),
            VarDecl("MAX_OUT", "TIME"), VarDecl("LIMIT_OUT", "REAL"),
        ))
        runtime = build_runtime(task, build_default_registry())
        runtime.executor.execute_programs(runtime.store.snapshot())
        self.assertEqual(runtime.store.read("PLC_PRG.ABS_OUT"), 7)
        self.assertEqual(runtime.store.read("PLC_PRG.MIN_OUT"), "A")
        self.assertEqual(runtime.store.read("PLC_PRG.MAX_OUT"), 300)
        self.assertEqual(runtime.store.read("PLC_PRG.LIMIT_OUT"), 1.0)

        other = build_runtime(task, build_default_registry())
        self.assertIsNot(runtime.executor._std, other.executor._std)
        runtime.executor._std.pop("ABS")
        self.assertIn("ABS", other.executor._std)

    def test_known_bad_signature_fails_before_store_or_executor(self):
        task = _task(
            _call("ABS", ("BOOL",), "BOOL", (True,), "OUT"),
            (VarDecl("OUT", "BOOL"),),
        )
        with mock.patch("src.runtime.parameters.build_runtime_store") as store_build, \
                mock.patch("src.runtime.parameters.Executor") as executor_build:
            with self.assertRaises(StartupValidationError) as caught:
                build_runtime(task, build_default_registry())
        self.assertIn("ABS", str(caught.exception))
        store_build.assert_not_called()
        executor_build.assert_not_called()

    def test_loader_aggregates_known_signature_errors(self):
        code = []
        code += _call("ABS", ("BOOL",), "BOOL", (True,), "B")
        code += _call("MIN", ("INT",), "INT", (1,), "I")
        task = _task(code, (VarDecl("B", "BOOL"), VarDecl("I", "INT")))
        with self.assertRaises(IRValidationError) as caught:
            validate_task(task)
        joined = "\n".join(caught.exception.errors)
        self.assertIn("ABS", joined)
        self.assertIn("MIN", joined)

    def test_unknown_callstd_remains_explicit_injection_boundary(self):
        task = _task(
            _call("CUSTOM", (), "INT", (), "OUT"),
            (VarDecl("OUT", "INT"),),
        )
        validate_task(task)
        layout = build_runtime_store(task)
        executor = Executor(task, layout, std_functions={"CUSTOM": lambda: 42})
        executor.execute_programs(layout.store.snapshot())
        self.assertEqual(layout.store.read("PLC_PRG.OUT"), 42)

        missing_layout = build_runtime_store(task)
        missing = Executor(task, missing_layout, numeric_mode=NumericMode())
        with self.assertRaises(MissingStdFunctionError):
            missing.execute_programs(missing_layout.store.snapshot())


if __name__ == "__main__":
    unittest.main()
