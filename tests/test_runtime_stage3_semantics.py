"""Stage 3 semantic closure: strict subset decisions, not IEC conformance claims."""
import math
import unittest

from src.runtime import STCompileError, compile_st_task
from src.runtime.descriptors.representative import build_default_registry


class Stage3SemanticClosureTests(unittest.TestCase):
    def assert_code(self, source, code):
        with self.assertRaises(STCompileError) as caught:
            compile_st_task(source)
        self.assertEqual(caught.exception.errors[0].code, code)

    def test_implicit_numeric_conversion_remains_rejected(self):
        self.assert_code(
            "VAR_GLOBAL I:INT; R:REAL; END_VAR R:=I;",
            "TYPE_MISMATCH")
        self.assert_code(
            "VAR_GLOBAL R:REAL; I:INT; END_VAR I:=R;",
            "TYPE_MISMATCH")
        self.assert_code(
            "VAR_GLOBAL R:REAL; I:INT; END_VAR R:=I+I;",
            "TYPE_MISMATCH")

    def test_dynamic_for_bounds_are_stably_rejected(self):
        self.assert_code(
            "VAR_GLOBAL I:INT; N:INT; END_VAR FOR I:=1 TO N DO END_FOR;",
            "UNSUPPORTED_FOR_BOUND")
        self.assert_code(
            "VAR_GLOBAL I:INT; N:INT; END_VAR FOR I:=N TO 1 BY -1 DO END_FOR;",
            "UNSUPPORTED_FOR_BOUND")

    def test_static_for_budget_and_nonfinite_literal_fail_closed(self):
        self.assert_code(
            "VAR_GLOBAL I:DINT; END_VAR FOR I:=1 TO 100001 DO END_FOR;",
            "FOR_ITERATION_LIMIT")
        self.assert_code(
            "VAR_GLOBAL R:REAL; END_VAR R:=1.0E9999;",
            "NONFINITE_LITERAL")

    def test_default_registry_declares_no_nonfinite_float_default(self):
        registry = build_default_registry()
        self.assertEqual(len(registry.keys()), 22)
        for key in registry.keys():
            schema, _adapter = registry.resolve(*key)
            for pin in schema.inputs:
                if type(pin.default) is float:
                    with self.subTest(block=key[0], pin=pin.name):
                        self.assertTrue(math.isfinite(pin.default))

    def test_unsupported_member_and_index_execution_stays_explicit(self):
        self.assert_code(
            "VAR_GLOBAL X:INT; END_VAR X.FIELD:=1;",
            "UNSUPPORTED_ASSIGNMENT_TARGET")
        self.assert_code(
            "VAR_GLOBAL X:INT; I:INT; END_VAR X[I]:=1;",
            "UNSUPPORTED_ASSIGNMENT_TARGET")


if __name__ == "__main__":
    unittest.main()
