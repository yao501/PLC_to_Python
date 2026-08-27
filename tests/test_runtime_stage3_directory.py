"""Stage 3 directory-level acceptance for the provisional ST frontend."""
from pathlib import Path
import unittest

import src.runtime as runtime
from src.runtime import st_lexer, st_lowering, st_parser
from src.runtime.st_library_bindings import library_source_aliases


_ST_PUBLIC = (
    "STLexDiagnostic", "STLexError", "STParseDiagnostic", "STParseError",
    "STCompileDiagnostic", "STCompileError", "STCompileResult",
    "STPOUCompileResult", "compile_st_task", "compile_st_function",
    "compile_st_function_block",
)

_PRIMITIVES = (
    "TON", "TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK",
)

_BUSINESS = (
    "APCSTATISTICS", "APCCD", "APCM", "APCHSFOP", "APCHSRATELIM",
    "APCHSACCUM", "APCHXHCL", "APCHSHLLIM", "APCGCQ", "APCSPFINDER",
    "APCPIDZZD", "APCPID", "APCRSFNAUTOPARA", "APCMAUTOPARA",
)


class Stage3DirectoryAcceptanceTests(unittest.TestCase):
    def test_public_compile_surface_is_exact_and_keeps_internals_private(self):
        definitions = {
            "STLexDiagnostic": st_lexer.STLexDiagnostic,
            "STLexError": st_lexer.STLexError,
            "STParseDiagnostic": st_parser.STParseDiagnostic,
            "STParseError": st_parser.STParseError,
            "STCompileDiagnostic": st_lowering.STCompileDiagnostic,
            "STCompileError": st_lowering.STCompileError,
            "STCompileResult": st_lowering.STCompileResult,
            "STPOUCompileResult": st_lowering.STPOUCompileResult,
            "compile_st_task": st_lowering.compile_st_task,
            "compile_st_function": st_lowering.compile_st_function,
            "compile_st_function_block": st_lowering.compile_st_function_block,
        }
        self.assertEqual(tuple(definitions), _ST_PUBLIC)
        for name in _ST_PUBLIC:
            with self.subTest(name=name):
                self.assertIn(name, runtime.__all__)
                self.assertIs(getattr(runtime, name), definitions[name])
        for name in ("lex_st", "parse_st", "library_source_aliases"):
            self.assertNotIn(name, runtime.__all__)
            self.assertFalse(hasattr(runtime, name))

    def test_alias_catalogue_is_exactly_eight_primitives_and_fourteen_blocks(self):
        aliases = library_source_aliases()
        self.assertEqual(tuple(aliases), _PRIMITIVES + _BUSINESS)
        self.assertEqual(len(aliases), 22)
        registry = runtime.build_default_registry()
        self.assertEqual(
            set(aliases), {block_type for block_type, variant in registry.keys()
                           if variant == "engineering"})
        for block_type, mapping in aliases.items():
            with self.subTest(block=block_type):
                schema, _adapter = registry.resolve(block_type, "engineering")
                pins = tuple(schema.inputs) + tuple(schema.inouts) + tuple(schema.outputs)
                self.assertEqual(set(mapping.values()), {pin.name for pin in pins})
                self.assertEqual(len(mapping), len(set(mapping.values())))

    def test_stage3_module_directory_and_dependency_direction_are_fixed(self):
        root = Path(st_lowering.__file__).parent
        self.assertEqual(
            tuple(sorted(path.name for path in root.glob("st_*.py"))),
            ("st_lexer.py", "st_library_bindings.py", "st_lowering.py",
             "st_parser.py"))
        lowering_source = Path(st_lowering.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from src.runtime import", lowering_source)
        self.assertIn("from src.runtime.st_parser import", lowering_source)
        self.assertIn("from src.runtime.st_library_bindings import", lowering_source)

    def test_representative_source_reaches_validated_ir_and_runtime(self):
        source = """
            VAR_GLOBAL Start:BOOL; Delay:TIME; Done:BOOL; Elapsed:TIME; END_VAR
            VAR Timer:TON; END_VAR
            Timer(IN:=Start,PT:=Delay,Q=>Done,ET=>Elapsed);
        """
        compiled = runtime.compile_st_task(source)
        runtime.validate_task(compiled.task, runtime.build_default_registry())
        assembly = runtime.build_runtime(compiled.task, runtime.build_default_registry())
        previous = assembly.store.snapshot()
        trace = []
        for start in (True, True, False):
            assembly.store.write("START", start)
            assembly.store.write("DELAY", 1000)
            assembly.executor.execute_programs(previous)
            previous = assembly.store.snapshot()
            trace.append((previous.as_dict()["DONE"], previous.as_dict()["ELAPSED"]))
        self.assertEqual(trace, [(False, 500), (True, 1000), (False, 0)])


if __name__ == "__main__":
    unittest.main()
