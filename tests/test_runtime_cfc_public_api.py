"""WP-20260810-091: provisional ``src.runtime`` CFC public-contract tests.

These samples are self-contained on purpose: the public surface must not rely
on helpers or fixtures from CFC implementation tests.  They establish a narrow
Python API contract only; they are not PLC, CODESYS, HAL, or field evidence.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import inspect
from pathlib import Path
import subprocess
import sys
import unittest

import src.runtime as runtime
from src.runtime import cfc_lowering, cfc_model, cfc_order
from src.runtime.descriptors import build_default_registry
from src.runtime.ir import LoadConst, LoadVar, POUDefinition, ProgramInstance, StoreVar, Task, VarDecl
from src.runtime.loader import IRValidationError


_PUBLIC_CFC_NAMES = {
    "CFC_MODEL_SCHEMA_VERSION",
    "CFCPin", "CFCNode", "CFCConnection", "CFCModel",
    "load_cfc_model", "dump_cfc_model",
    "CFCModelDiagnostic", "CFCModelError",
    "CFCNodeBody", "CFCCompileResult", "compile_cfc_task",
    "CFCLoweringDiagnostic", "CFCLoweringError",
    "CFCOrderDiagnostic", "CFCOrderError",
}
_PUBLIC_CFC_ORDER = (
    "CFC_MODEL_SCHEMA_VERSION",
    "CFCPin", "CFCNode", "CFCConnection", "CFCModel",
    "load_cfc_model", "dump_cfc_model",
    "CFCModelDiagnostic", "CFCModelError",
    "CFCNodeBody", "CFCCompileResult", "compile_cfc_task",
    "CFCLoweringDiagnostic", "CFCLoweringError",
    "CFCOrderDiagnostic", "CFCOrderError",
)
# This is a compact, stable snapshot of the complete package-level surface:
# exact provisional prefix + full ordered-list count + digest.  It deliberately
# fails if an apparently harmless ``lower_cfc_*`` helper is added to ``__all__``.
_RUNTIME_ALL_COUNT = 232
_RUNTIME_ALL_SHA256 = "44623f23a422f33ef0a0c8178b0c7ef506e373ffaa9f90999d126378e65f9544"
_RUNTIME_PUBLIC_ATTR_COUNT = 255
_RUNTIME_PUBLIC_ATTR_SHA256 = "d4d8c2dc1eee817ca019ca8422bea463da0220dceb9b499be833693744296503"
_TOP_LEVEL_DEFINITIONS = {
    "CFC_MODEL_SCHEMA_VERSION": ("src.runtime.cfc_model", "SCHEMA_VERSION"),
    "CFCPin": ("src.runtime.cfc_model", "CFCPin"),
    "CFCNode": ("src.runtime.cfc_model", "CFCNode"),
    "CFCConnection": ("src.runtime.cfc_model", "CFCConnection"),
    "CFCModel": ("src.runtime.cfc_model", "CFCModel"),
    "load_cfc_model": ("src.runtime.cfc_model", "load_cfc_model"),
    "dump_cfc_model": ("src.runtime.cfc_model", "dump_cfc_model"),
    "CFCModelDiagnostic": ("src.runtime.cfc_model", "CFCModelDiagnostic"),
    "CFCModelError": ("src.runtime.cfc_model", "CFCModelError"),
    "CFCNodeBody": ("src.runtime.cfc_lowering", "CFCNodeBody"),
    "CFCCompileResult": ("src.runtime.cfc_lowering", "CFCCompileResult"),
    "compile_cfc_task": ("src.runtime.cfc_lowering", "compile_cfc_task"),
    "CFCLoweringDiagnostic": ("src.runtime.cfc_lowering", "CFCLoweringDiagnostic"),
    "CFCLoweringError": ("src.runtime.cfc_lowering", "CFCLoweringError"),
    "CFCOrderDiagnostic": ("src.runtime.cfc_order", "CFCOrderDiagnostic"),
    "CFCOrderError": ("src.runtime.cfc_order", "CFCOrderError"),
}
_INTERNAL_DEFINITIONS = {
    "CFCOrderNode": ("src.runtime.cfc_order", "CFCOrderNode"),
    "CFCOrderEdge": ("src.runtime.cfc_order", "CFCOrderEdge"),
    "CFCOrderGraph": ("src.runtime.cfc_order", "CFCOrderGraph"),
    "resolve_execution_order": ("src.runtime.cfc_order", "resolve_execution_order"),
    "CFCInputBinding": ("src.runtime.cfc_lowering", "CFCInputBinding"),
    "CFCNodeIR": ("src.runtime.cfc_lowering", "CFCNodeIR"),
    "CFCLoweringResult": ("src.runtime.cfc_lowering", "CFCLoweringResult"),
    "lower_cfc_task": ("src.runtime.cfc_lowering", "lower_cfc_task"),
    "lower_cfc_feedback_task": ("src.runtime.cfc_lowering", "lower_cfc_feedback_task"),
}
_FORBIDDEN_CFC_NAMES = {
    "SCHEMA_VERSION",
    "CFCOrderNode", "CFCOrderEdge", "CFCOrderGraph", "resolve_execution_order",
    "CFCInputBinding", "CFCNodeIR", "CFCLoweringResult",
    "lower_cfc_task", "lower_cfc_feedback_task",
}


def _pin(pin_id, formal_name, direction, value_key):
    return {
        "pin_id": pin_id,
        "formal_name": formal_name,
        "direction": direction,
        "iec_type": "BOOL",
        "value_key": value_key,
    }


def _payload(*, carrier="user_defined", order_source="user_defined"):
    """A smallest valid current-read CFC graph, built without test imports."""
    return {
        "schema_version": "cfc-model-v1",
        "carrier": carrier,
        "execution_order_mode": "auto",
        "order_source": order_source,
        "nodes": [
            {
                "node_id": "Input",
                "kind": "input",
                "type_name": "Input",
                "instance_name": "",
                "execution_order_id": None,
                "feedback_marker": None,
                "pins": [_pin("out", "OUT", "OUT", "Start")],
            },
            {
                "node_id": "Output",
                "kind": "output",
                "type_name": "Output",
                "instance_name": "",
                "execution_order_id": None,
                "feedback_marker": None,
                "pins": [_pin("in", "IN", "IN", "Signal")],
            },
        ],
        "connections": [{
            "source_node_id": "Input",
            "source_pin_id": "out",
            "target_node_id": "Output",
            "target_pin_id": "in",
            "read_mode": "current",
        }],
    }


def _pending_task(payload):
    program = POUDefinition(
        "Main", "PROGRAM", "CFC",
        locals=[VarDecl("Signal", "BOOL")], source=payload, code=None,
    )
    return Task(
        programs=[ProgramInstance("Main", "PLC_PRG")],
        gvl=[
            VarDecl("Start", "BOOL", section="VAR_GLOBAL"),
            VarDecl("Motor", "BOOL", section="VAR_GLOBAL"),
        ],
        pou_lib={"Main": program},
        cycle_ms=500,
    )


def _bodies(*, invalid_ir=False):
    output_body = (LoadConst(True, "BOOL"),) if invalid_ir else (
        LoadVar("Signal", "BOOL"), StoreVar("Motor", "BOOL"),
    )
    return (
        runtime.CFCNodeBody("Input", ()),
        runtime.CFCNodeBody("Output", output_body),
    )


def _compile(payload, *, bodies=None):
    return runtime.compile_cfc_task(
        payload, _bodies() if bodies is None else bodies, _pending_task(payload),
        "Main", build_default_registry(),
    )


def _public_namespace_digest(module):
    names = tuple(sorted(name for name in vars(module) if not name.startswith("_")))
    return len(names), hashlib.sha256("\n".join(names).encode()).hexdigest()


def _json_tree_container_ids(value, ids):
    """Validate the exact JSON tree contract and collect every dict/list identity."""
    if type(value) in (type(None), bool, int, float, str):
        return
    if type(value) is list:
        ids.append(id(value))
        for item in value:
            _json_tree_container_ids(item, ids)
        return
    if type(value) is dict:
        ids.append(id(value))
        for key, item in value.items():
            if type(key) is not str:
                raise AssertionError("JSON tree key is not an exact str")
            _json_tree_container_ids(item, ids)
        return
    raise AssertionError("JSON tree contains a non-JSON exact value")


def _call_name(expression):
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return _call_name(expression.value) + "." + expression.attr
    return type(expression).__name__


def _definition_time_calls(tree):
    """Return every call reachable while a module/class definition executes.

    Function and method *bodies* are deliberately skipped, but their decorators
    and default expressions are checked because Python evaluates those on import.
    Class bases, decorators, keywords and class-body statements are likewise
    included.  This is an import-side-effect guard, not a general sandbox.
    """
    postponed_annotations = any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__" and
        any(alias.name == "annotations" for alias in node.names)
        for node in tree.body)

    class _Finder(ast.NodeVisitor):
        def __init__(self):
            self.calls = []

        def visit_Call(self, node):
            self.calls.append(_call_name(node.func))
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            for value in (*node.decorator_list, *node.args.defaults,
                          *node.args.kw_defaults):
                if value is not None:
                    self.visit(value)
            if not postponed_annotations:
                for argument in (*node.args.posonlyargs, *node.args.args,
                                 *node.args.kwonlyargs):
                    if argument.annotation is not None:
                        self.visit(argument.annotation)
                if node.args.vararg and node.args.vararg.annotation is not None:
                    self.visit(node.args.vararg.annotation)
                if node.args.kwarg and node.args.kwarg.annotation is not None:
                    self.visit(node.args.kwarg.annotation)
                if node.returns is not None:
                    self.visit(node.returns)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            # Lambda bodies execute later, but their defaults execute while
            # the lambda object is created, just like ``def`` defaults.
            for value in (*node.args.defaults, *node.args.kw_defaults):
                if value is not None:
                    self.visit(value)

        def visit_ClassDef(self, node):
            for value in node.decorator_list:
                self.visit(value)
            for value in node.bases:
                self.visit(value)
            for keyword in node.keywords:
                self.visit(keyword.value)
            for statement in node.body:
                self.visit(statement)

        def visit_AnnAssign(self, node):
            if not postponed_annotations and node.annotation is not None:
                self.visit(node.annotation)
            if node.value is not None:
                self.visit(node.value)

    finder = _Finder()
    for statement in tree.body:
        finder.visit(statement)
    return tuple(finder.calls)


def _cfc_import_graph(runtime_dir):
    """Resolve absolute and relative imports from their complete AST spelling."""
    graph = {}
    for filename in ("cfc_lowering.py", "cfc_model.py", "cfc_order.py"):
        module = filename[:-3]
        tree = ast.parse((runtime_dir / filename).read_text(encoding="utf-8"))
        edges = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                package_parts = ["src", "runtime"]
                if node.level:
                    prefix = package_parts[:len(package_parts) - (node.level - 1)]
                    base = ".".join(prefix)
                    if node.module:
                        names = (base + "." + node.module,)
                    else:
                        names = (base + "." + alias.name for alias in node.names)
                else:
                    # ``from src import runtime`` reaches the package root
                    # through an alias even though ``node.module`` is only
                    # ``src``.  Preserve the base and every qualified alias.
                    aliases = ()
                    if node.module in {"src", "src.runtime"}:
                        aliases = tuple(
                            node.module + "." + alias.name
                            for alias in node.names)
                    names = (node.module,) + aliases
            else:
                continue
            for name in names:
                if name in {"src.runtime", "src.runtime.__init__"}:
                    raise AssertionError("CFC module imports package root")
                if type(name) is str and name.startswith("src.runtime.cfc_"):
                    edges.add(name.rsplit(".", 1)[-1])
        graph[module] = frozenset(edges)
    return graph


class TestCfcPublicSurface(unittest.TestCase):
    def test_exact_surface_and_definition_identity(self):
        self.assertEqual(_PUBLIC_CFC_ORDER, tuple(runtime.__all__[:16]))
        self.assertEqual(_RUNTIME_ALL_COUNT, len(runtime.__all__))
        self.assertEqual(_RUNTIME_ALL_SHA256,
                         hashlib.sha256("\n".join(runtime.__all__).encode()).hexdigest())
        self.assertEqual(_PUBLIC_CFC_NAMES, set(runtime.__all__[:16]))
        self.assertEqual(len(runtime.__all__), len(set(runtime.__all__)))
        self.assertEqual((_RUNTIME_PUBLIC_ATTR_COUNT, _RUNTIME_PUBLIC_ATTR_SHA256),
                         _public_namespace_digest(runtime))
        self.assertEqual(set(_PUBLIC_CFC_ORDER), set(_TOP_LEVEL_DEFINITIONS))
        self.assertEqual(
            _FORBIDDEN_CFC_NAMES - {"SCHEMA_VERSION"},
            set(_INTERNAL_DEFINITIONS),
        )
        self.assertNotIn("SCHEMA_VERSION", runtime.__all__)
        self.assertFalse(hasattr(runtime, "SCHEMA_VERSION"))
        for name, (module_name, source_name) in _TOP_LEVEL_DEFINITIONS.items():
            self.assertIs(getattr(runtime, name),
                          getattr(importlib.import_module(module_name), source_name))
        for name, (module_name, source_name) in _INTERNAL_DEFINITIONS.items():
            self.assertNotIn(name, runtime.__all__)
            self.assertFalse(hasattr(runtime, name), name)
            definition = getattr(importlib.import_module(module_name), source_name)
            self.assertEqual(module_name, definition.__module__)
            self.assertEqual(source_name, definition.__name__)
            if source_name[0].isupper():
                self.assertIs(type(definition), type)
            else:
                self.assertTrue(inspect.isfunction(definition))

        reloaded = importlib.reload(runtime)
        self.assertEqual((_RUNTIME_PUBLIC_ATTR_COUNT, _RUNTIME_PUBLIC_ATTR_SHA256),
                         _public_namespace_digest(reloaded))
        for name, (module_name, source_name) in _TOP_LEVEL_DEFINITIONS.items():
            self.assertIs(getattr(reloaded, name),
                          getattr(importlib.import_module(module_name), source_name))

    def test_top_level_model_round_trip_returns_fresh_containers(self):
        payload = _payload()
        original = copy.deepcopy(payload)
        model = runtime.load_cfc_model(payload)
        dumped = runtime.dump_cfc_model(model)
        other_dump = runtime.dump_cfc_model(model)
        reloaded = runtime.load_cfc_model(dumped)

        self.assertEqual(payload, original)
        self.assertEqual(dumped, runtime.dump_cfc_model(reloaded))
        self.assertEqual(dumped, other_dump)
        self.assertIsNot(dumped, payload)
        self.assertIsNot(dumped["nodes"], payload["nodes"])
        self.assertIsNot(dumped["connections"], payload["connections"])
        self.assertIsNot(dumped, other_dump)
        self.assertIsNot(dumped["nodes"], other_dump["nodes"])
        self.assertIsNot(dumped["connections"], other_dump["connections"])
        for first, second in zip(dumped["nodes"], other_dump["nodes"]):
            self.assertIsNot(first, second)
            self.assertIsNot(first["pins"], second["pins"])
            for first_pin, second_pin in zip(first["pins"], second["pins"]):
                self.assertIsNot(first_pin, second_pin)
        for first, second in zip(dumped["connections"], other_dump["connections"]):
            self.assertIsNot(first, second)
        first_ids, second_ids = [], []
        _json_tree_container_ids(dumped, first_ids)
        _json_tree_container_ids(other_dump, second_ids)
        self.assertEqual(len(first_ids), len(set(first_ids)))
        self.assertEqual(len(second_ids), len(set(second_ids)))
        self.assertFalse(set(first_ids) & set(second_ids))
        for node in dumped["nodes"]:
            for key in ("node_id", "kind", "type_name", "instance_name",
                        "execution_order_id", "feedback_marker"):
                node[key] = "mutated" if type(node[key]) is str else True
            for pin in node["pins"]:
                for key in ("pin_id", "formal_name", "direction", "iec_type", "value_key"):
                    pin[key] = "mutated"
        for connection in dumped["connections"]:
            for key in ("source_node_id", "source_pin_id", "target_node_id",
                        "target_pin_id", "read_mode"):
                connection[key] = "mutated"
        self.assertEqual(other_dump, runtime.dump_cfc_model(model))
        self.assertEqual(payload, original)

    def test_top_level_compile_runtime_executor_path_preserves_inputs(self):
        payload = _payload()
        original = copy.deepcopy(payload)
        bodies = _bodies()
        task = _pending_task(payload)
        original_bodies = copy.deepcopy(bodies)
        original_task = copy.deepcopy(task)
        result = runtime.compile_cfc_task(
            payload, bodies, task, "Main", build_default_registry())
        self.assertIsInstance(result, runtime.CFCCompileResult)
        self.assertEqual(payload, original)
        self.assertEqual(bodies, original_bodies)
        self.assertEqual(task, original_task)

        assembly = runtime.build_runtime(result.task, build_default_registry())
        assembly.store.write("Start", True)
        assembly.executor.execute_programs(assembly.store.snapshot())
        self.assertTrue(assembly.store.read("Motor"))
        self.assertEqual(payload, original)
        self.assertEqual(bodies, original_bodies)
        self.assertEqual(task, original_task)

    def test_public_error_layers_and_diagnostics_are_not_rewrapped(self):
        with self.assertRaises(runtime.CFCModelError) as caught:
            runtime.load_cfc_model({})
        self.assertIs(type(caught.exception), runtime.CFCModelError)
        self.assertEqual(
            (("SCHEMA_ROOT_FIELDS", "record fields must match schema exactly", None, None),),
            tuple((item.code, item.message, item.node_id, item.pin_id)
                  for item in caught.exception.errors))
        self.assertEqual("SCHEMA_ROOT_FIELDS: record fields must match schema exactly",
                         str(caught.exception))

        with self.assertRaises(runtime.CFCLoweringError) as caught:
            _compile(_payload(), bodies=())
        self.assertIs(type(caught.exception), runtime.CFCLoweringError)
        self.assertEqual(
            (("MISSING_BODY", "model node has no body", "Input"),
             ("MISSING_BODY", "model node has no body", "Output")),
            tuple((item.code, item.message, item.node_id) for item in caught.exception.errors))
        self.assertEqual("MISSING_BODY: model node has no body; MISSING_BODY: model node has no body",
                         str(caught.exception))

        with self.assertRaises(runtime.CFCOrderError) as caught:
            _compile(_payload(carrier="export_native", order_source="reconstructed"))
        self.assertIs(type(caught.exception), runtime.CFCOrderError)
        self.assertEqual(
            (("UNSUPPORTED_RECONSTRUCTION",
              "export_native CFC ordering is unsupported until its carrier branch is verified",
              None, None),),
            tuple((item.code, item.message, item.node_id, item.edge)
                  for item in caught.exception.errors))
        self.assertEqual(
            "UNSUPPORTED_RECONSTRUCTION: export_native CFC ordering is unsupported until its carrier branch is verified",
            str(caught.exception))

        with self.assertRaises(IRValidationError) as caught:
            _compile(_payload(), bodies=_bodies(invalid_ir=True))
        self.assertIs(type(caught.exception), IRValidationError)
        self.assertEqual(
            ("POU 'Main'：PROGRAM 正常出口栈应为空，实为 ['BOOL']",),
            tuple(caught.exception.errors))
        self.assertEqual("IR 装载校验失败（1 处）：\n  - POU 'Main'：PROGRAM 正常出口栈应为空，实为 ['BOOL']",
                         str(caught.exception))

        result = _compile(_payload())
        result.task.cycle_ms = 100
        with self.assertRaises(runtime.StartupValidationError) as caught:
            runtime.build_runtime(result.task, build_default_registry())
        expected = (
            "IR 装载校验：Task.cycle_ms 当前冻结范围为单任务、固定 500ms（IR_SPEC §3；ROADMAP 阶段 1），得到 100——多周期/多任务属后续扩展点，本阶段校验器不放行",
        )
        self.assertIs(type(caught.exception), runtime.StartupValidationError)
        self.assertEqual(expected, tuple(caught.exception.errors))
        self.assertEqual("启动参数装载校验失败（1 处）：\n  - " + expected[0], str(caught.exception))

    def test_cfc_directory_is_exact_and_never_imports_package_root(self):
        runtime_dir = Path(runtime.__file__).resolve().parent
        cfc_files = sorted(path.name for path in runtime_dir.glob("cfc_*.py"))
        self.assertEqual(
            ["cfc_lowering.py", "cfc_model.py", "cfc_order.py"], cfc_files)
        graph = _cfc_import_graph(runtime_dir)
        self.assertEqual({
            "cfc_lowering": frozenset({"cfc_model", "cfc_order"}),
            "cfc_model": frozenset({"cfc_order"}),
            "cfc_order": frozenset(),
        }, graph)
        for start in graph:
            seen = set()
            active = set()

            def walk(node):
                self.assertNotIn(node, active, "CFC import cycle")
                if node in seen:
                    return
                active.add(node)
                for child in graph[node]:
                    walk(child)
                active.remove(node)
                seen.add(node)

            walk(start)

    def test_fresh_package_import_is_silent_and_has_no_module_level_calls(self):
        repository = Path(__file__).resolve().parents[1]
        code = (
            "import hashlib, importlib, pathlib, sys; "
            "root = pathlib.Path(sys.argv[1]).resolve(); sys.path.insert(0, str(root)); "
            "runtime = importlib.import_module('src.runtime'); "
            "model = importlib.import_module('src.runtime.cfc_model'); "
            "lowering = importlib.import_module('src.runtime.cfc_lowering'); "
            "order = importlib.import_module('src.runtime.cfc_order'); "
            "assert pathlib.Path(runtime.__file__).resolve() == root / 'src/runtime/__init__.py'; "
            "top = " + repr(_TOP_LEVEL_DEFINITIONS) + "; internal = " + repr(_INTERNAL_DEFINITIONS) + "; "
            "assert len(runtime.__all__) == " + str(_RUNTIME_ALL_COUNT) +
            " and tuple(runtime.__all__[:16]) == " + repr(_PUBLIC_CFC_ORDER) + "; "
            "names = sorted(n for n in vars(runtime) if not n.startswith('_')); "
            "assert len(names) == " + str(_RUNTIME_PUBLIC_ATTR_COUNT) +
            " and hashlib.sha256('\\n'.join(names).encode()).hexdigest() == " +
            repr(_RUNTIME_PUBLIC_ATTR_SHA256) + "; "
            "assert all(getattr(runtime, n) is getattr(importlib.import_module(m), s) for n, (m, s) in top.items()); "
            "assert all(n not in runtime.__all__ and not hasattr(runtime, n) and "
            "getattr(importlib.import_module(m), s).__module__ == m and "
            "getattr(importlib.import_module(m), s).__name__ == s "
            "for n, (m, s) in internal.items())"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(repository)],
            cwd=repository, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stdout)
        self.assertEqual("", completed.stderr)

        runtime_dir = Path(runtime.__file__).resolve().parent
        expected = {
            "__init__.py": (),
            "cfc_model.py": ("frozenset",) * 8 + ("dataclass",) * 5,
            "cfc_order.py": ("dataclass",) * 4,
            "cfc_lowering.py": ("dataclass",) * 4 + ("object", "type") + ("dataclass",) * 2,
        }
        actual = {
            filename: _definition_time_calls(
                ast.parse((runtime_dir / filename).read_text(encoding="utf-8")))
            for filename in expected
        }
        self.assertEqual(expected, actual)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
