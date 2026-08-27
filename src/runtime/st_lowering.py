"""Strict Stage 3 ST subset lowering to the existing typed IR.

The current candidate compiles supported declarations, exact-typed expressions
and assignments, IF/CASE/FOR/WHILE, loop EXIT/CONTINUE, eager
ABS/MIN/MAX/LIMIT calls, and lazy SEL.  It does not guess IEC implicit
conversions.  Separate internal entries compile FUNCTION/FUNCTION_BLOCK bodies;
PROGRAM lowering can bind compiled FUNCTIONs and explicitly declared local user
FB instances, plus eight explicitly mapped primitive library FBs and fourteen
business blocks: APCSTATISTICS, APCCD, APCHSFOP, APCHSRATELIM, APCHSACCUM,
APCHXHCL, APCHSHLLIM, APCGCQ, APCSPFINDER, APCPIDZZD, APCPID,
APCRSFNAUTOPARA, APCMAUTOPARA, and APCM.
APCHSACCUM/APCHXHCL/APCSPFINDER/APCPIDZZD/APCPID/APCRSFNAUTOPARA/APCMAUTOPARA support
Schema-declared use_default inputs; APCHSHLLIM/APCGCQ are required-only through
the same generic path.
APCCD/APCM exercise the generic library VAR_IN_OUT read/call/writeback path;
APCM also exposes Schema-declared keep_previous and none_means_no_write inputs.
Project-level POU assembly remains a later package.  Unsupported shapes fail
closed.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
import re

from src.runtime.ir import (
    BINDING_MODES, IEC_TYPES, INT_TYPES, LOGIC_TYPES, NUMERIC_TYPES,
    ORDERED_TYPES, REAL_TYPES,
    SIGNED_INT_TYPES, UNSIGNED_INT_TYPES, Binding, BinOp,
    CallFbInstance,
    CallFb, CallFunc, CallStd, Const, Convert, InstanceDecl, Jmp, JmpIfFalse,
    Label, LoadConst, LoadPrev, LoadVar, POUDefinition, ProgramInstance,
    StackSlot, StdSig, StoreKey, StoreVar, Task, UnOp, VarDecl,
)
from src.runtime.descriptors import (
    BlockSchema, NUMERIC_VARIANTS, Pin, build_default_registry,
    parse_output_access,
)
from src.runtime.descriptors.model import _OutputAccessMap
from src.runtime.loader import (
    IRValidationError, validate_pou_instruction_semantics, validate_task,
)
from src.runtime.st_library_bindings import library_source_aliases
from src.runtime.st_lexer import STLexError, lex_st
from src.runtime.st_parser import (
    STAssignment, STBinary, STCall, STCallStatement, STCase, STContinue,
    STExit, STFor, STIf, STIndex, STLiteral, STMember, STName, STUnary,
    STReturn, STWhile, parse_st,
)


_BINOPS = {
    "+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "MOD": "MOD",
    "AND": "AND", "OR": "OR", "XOR": "XOR",
    "<": "LT", ">": "GT", "<=": "LE", ">=": "GE", "=": "EQ", "<>": "NE",
}
_COMPARE = frozenset({"<", ">", "<=", ">=", "=", "<>"})
_SUPPORTED_SECTIONS = frozenset({"VAR_GLOBAL", "VAR", "VAR_TEMP"})
_TIME_VALUE = re.compile(
    r"(?:(?P<days>\d+)D)?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?(?:(?P<milliseconds>\d+)MS)?"
)
_TIME_FACTORS = {
    "days": 86_400_000,
    "hours": 3_600_000,
    "minutes": 60_000,
    "seconds": 1_000,
    "milliseconds": 1,
}
_TIME_MAX_MS = 0xFFFFFFFF
_STRING_ESCAPES = {
    "$": "$", "'": "'", "L": "\n", "N": "\n", "P": "\f",
    "R": "\r", "T": "\t",
}
_INT_RANGES = {
    "SINT": (-(2 ** 7), 2 ** 7 - 1),
    "USINT": (0, 2 ** 8 - 1), "BYTE": (0, 2 ** 8 - 1),
    "INT": (-(2 ** 15), 2 ** 15 - 1),
    "UINT": (0, 2 ** 16 - 1), "WORD": (0, 2 ** 16 - 1),
    "DINT": (-(2 ** 31), 2 ** 31 - 1),
    "UDINT": (0, 2 ** 32 - 1), "DWORD": (0, 2 ** 32 - 1),
    "LINT": (-(2 ** 63), 2 ** 63 - 1),
    "ULINT": (0, 2 ** 64 - 1), "LWORD": (0, 2 ** 64 - 1),
}
_MAX_STATIC_FOR_ITERATIONS = 100_000
_EAGER_STANDARD_FUNCTIONS = frozenset({"ABS", "MIN", "MAX", "LIMIT"})
_ABS_TYPES = SIGNED_INT_TYPES | UNSIGNED_INT_TYPES | REAL_TYPES
_LIBRARY_PIN_FIELDS = frozenset({
    "name", "iec_type", "kind", "default", "omit_policy"})
_BLOCK_SCHEMA_FIELDS = frozenset(field.name for field in fields(BlockSchema))
_LIBRARY_PIN_KINDS = frozenset({"VAR_INPUT", "VAR_IN_OUT", "VAR_OUTPUT"})
_LIBRARY_OMIT_POLICIES = frozenset({
    "required", "use_default", "keep_previous", "none_means_no_write"})
_PIN_GETATTRIBUTE = Pin.__getattribute__
_BLOCK_SCHEMA_GETATTRIBUTE = BlockSchema.__getattribute__


@dataclass(frozen=True)
class _LibraryPinSnapshot:
    """Trusted, plain-data copy of one catalogue Pin."""

    name: str
    iec_type: str
    kind: str
    default: object
    omit_policy: str


@dataclass(frozen=True)
class _LibrarySchemaSnapshot:
    """Trusted, plain-data copy of one catalogue Schema."""

    block_type: str
    inputs: tuple
    outputs: tuple
    inouts: tuple
    variant: str
    descriptor_version: str
    state_vars: frozenset
    retainable: frozenset
    init_overridable: frozenset
    hmi_writable: frozenset
    output_access: tuple


@dataclass(frozen=True)
class _LibraryBlockSnapshot:
    """One source alias table bound to its trusted engineering Schema."""

    schema: _LibrarySchemaSnapshot
    aliases: tuple


def _library_pin_carrier(pin):
    """Return one disconnected, exact Pin snapshot, else ``None``.

    This runs before a catalogue Pin field is observed.  It intentionally uses
    the instance carrier rather than attribute lookup, so a deleted field cannot
    silently inherit a dataclass class default.  A class-level hook is rejected
    before any instance is consumed: Loader later validates against the original
    Registry, so in the single-threaded compile path this identity gate makes
    that unavoidable downstream use safe as well.
    """
    if type(pin) is not Pin or Pin.__getattribute__ is not _PIN_GETATTRIBUTE:
        return None
    try:
        carrier = object.__getattribute__(pin, "__dict__")
    except BaseException:
        return None
    if type(carrier) is not dict or \
            any(type(field) is not str for field in carrier) or \
            set(carrier) != _LIBRARY_PIN_FIELDS:
        return None
    name = carrier["name"]
    iec_type = carrier["iec_type"]
    kind = carrier["kind"]
    default = carrier["default"]
    omit_policy = carrier["omit_policy"]
    if type(name) is not str or type(iec_type) is not str or \
            type(kind) is not str or type(omit_policy) is not str or \
            iec_type not in IEC_TYPES or kind not in _LIBRARY_PIN_KINDS or \
            omit_policy not in _LIBRARY_OMIT_POLICIES or \
            type(default) not in (type(None), bool, int, float, str):
        return None
    return _LibraryPinSnapshot(name, iec_type, kind, default, omit_policy)


def _library_schema_carrier(schema, *, expected_block_type=None,
                            expected_variant=None):
    """Return one complete, disconnected Schema snapshot, else ``None``.

    The alias catalogue is a trust boundary.  In particular, this must not use
    normal ``schema.field`` access: an exact dataclass object can still have an
    altered class-level ``__getattribute__`` or a privileged-mutated instance
    dict.  The frozen dataclass field set supplies the shell shape rather than
    an independently maintained business-schema list.
    """
    if type(schema) is not BlockSchema or \
            BlockSchema.__getattribute__ is not _BLOCK_SCHEMA_GETATTRIBUTE:
        return None
    try:
        carrier = object.__getattribute__(schema, "__dict__")
    except BaseException:
        return None
    if type(carrier) is not dict or \
            any(type(name) is not str for name in carrier) or \
            frozenset(carrier) != _BLOCK_SCHEMA_FIELDS:
        return None
    block_type = carrier["block_type"]
    variant = carrier["variant"]
    descriptor_version = carrier["descriptor_version"]
    if type(block_type) is not str or not block_type or \
            type(variant) is not str or variant not in NUMERIC_VARIANTS or \
            type(descriptor_version) is not str or not descriptor_version:
        return None
    if expected_block_type is not None and block_type != expected_block_type:
        return None
    if expected_variant is not None and variant != expected_variant:
        return None
    state_vars = carrier["state_vars"]
    retainable = carrier["retainable"]
    init_overridable = carrier["init_overridable"]
    hmi_writable = carrier["hmi_writable"]
    for value in (state_vars, retainable, init_overridable, hmi_writable):
        if type(value) is not frozenset or \
                any(type(name) is not str for name in value):
            return None
    if not retainable <= state_vars or not init_overridable <= state_vars:
        return None
    access = carrier["output_access"]
    if type(access) is not _OutputAccessMap:
        return None
    try:
        pairs = object.__getattribute__(access, "_pairs")
    except BaseException:
        return None
    if type(pairs) is not tuple:
        return None
    access_names = set()
    access_pairs = []
    for pair in pairs:
        if type(pair) is not tuple or len(pair) != 2 or \
                type(pair[0]) is not str or type(pair[1]) is not str or \
                pair[0] in access_names:
            return None
        try:
            parse_output_access(pair[1])
        except BaseException:
            return None
        access_names.add(pair[0])
        access_pairs.append((pair[0], pair[1]))

    snapshots = []
    names = set()
    for expected_kind, pins in (
            ("VAR_INPUT", carrier["inputs"]),
            ("VAR_IN_OUT", carrier["inouts"]),
            ("VAR_OUTPUT", carrier["outputs"])):
        if type(pins) is not tuple:
            return None
        copied = []
        for pin in pins:
            snapshot = _library_pin_carrier(pin)
            if snapshot is None or snapshot.kind != expected_kind or \
                    snapshot.name in names:
                return None
            names.add(snapshot.name)
            copied.append(snapshot)
        snapshots.append(tuple(copied))
    inputs, inouts, outputs = snapshots
    if access_names != {pin.name for pin in outputs}:
        return None
    return _LibrarySchemaSnapshot(
        block_type, inputs, outputs, inouts, variant, descriptor_version,
        state_vars, retainable, init_overridable, hmi_writable,
        tuple(access_pairs))


@dataclass(frozen=True)
class STCompileDiagnostic:
    code: str
    message: str
    start: int
    end: int
    line: int
    column: int


class STCompileError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("; ".join(
            "%s at %d:%d: %s" % (
                error.code, error.line, error.column, error.message)
            for error in self.errors
        ))


@dataclass(frozen=True)
class STCompileResult:
    unit: object
    task: Task
    code: tuple


@dataclass(frozen=True)
class STPOUCompileResult:
    unit: object
    pou: POUDefinition
    code: tuple


class _Lowerer:
    def __init__(self, unit, program_name, cycle_ms, *, pou_kind="PROGRAM",
                 return_type=None, functions=None, function_blocks=None,
                 library_blocks=None, registry=None):
        self.unit = unit
        self.program_name = program_name
        self.cycle_ms = cycle_ms
        self.pou_kind = pou_kind
        self.return_type = return_type
        self.functions = dict(functions or {})
        self.function_blocks = dict(function_blocks or {})
        self.library_blocks = dict(library_blocks or {})
        self.registry = registry
        self.gvl = []
        self.interface = []
        self.locals = []
        self.global_types = {}
        self.local_types = {}
        self.label_index = 0
        # This label is POU-local because every POU executes its own instruction
        # stream.  It deliberately does not consume the numbered control-flow
        # label sequence, preserving deterministic labels for existing constructs.
        self.return_epilogue_label = "__ST_RETURN_EPILOGUE"
        self.active_for_counters = set()
        self.for_iteration_product = 1
        self.loop_contexts = []
        self.instances = []
        self.instance_types = {}
        self.library_instance_types = {}

    def _fail(self, code, message, node):
        span = node.span
        raise STCompileError((STCompileDiagnostic(
            code, message, span.start, span.end, span.line, span.column),))

    def _reject_active_counter_actual(self, name):
        # A writable OUT/INOUT actual is an indirect write channel; binding it
        # to an active FOR counter would break the static iteration count,
        # nested product and termination proof exactly like a direct `I:=...`.
        if name.canonical in self.active_for_counters:
            self._fail(
                "FOR_COUNTER_WRITE",
                "FOR body must not bind its active counter to a writable actual",
                name)

    def _decode_literal(self, literal, expected):
        if literal.kind == "BOOL":
            if expected != "BOOL":
                self._fail("TYPE_MISMATCH", "BOOL literal requires BOOL context", literal)
            return literal.normalized == "TRUE"
        if literal.kind == "INTEGER":
            if expected not in INT_TYPES:
                self._fail(
                    "TYPE_MISMATCH",
                    "decimal INTEGER literal requires an explicit integer context",
                    literal)
            return int(literal.text, 10)
        if literal.kind == "REAL":
            if expected not in REAL_TYPES:
                self._fail(
                    "TYPE_MISMATCH",
                    "REAL literal requires an explicit REAL or LREAL context",
                    literal)
            value = float(literal.text)
            if not math.isfinite(value):
                self._fail("NONFINITE_LITERAL", "REAL literal must be finite", literal)
            return value
        if literal.kind == "TIME_LITERAL":
            if expected != "TIME":
                self._fail("TYPE_MISMATCH", "TIME literal requires TIME context", literal)
            body = literal.normalized.split("#", 1)[1]
            match = _TIME_VALUE.fullmatch(body)
            if match is None or not any(match.groupdict().values()):
                self._fail("INVALID_TIME_LITERAL", "TIME literal is not canonical", literal)
            value = sum(
                int(match.group(name) or "0", 10) * factor
                for name, factor in _TIME_FACTORS.items()
            )
            if value > _TIME_MAX_MS:
                self._fail(
                    "TIME_LITERAL_RANGE",
                    "TIME literal exceeds the supported 32-bit millisecond range",
                    literal)
            return value
        if literal.kind == "STRING":
            if expected != "STRING":
                self._fail("TYPE_MISMATCH", "STRING literal requires STRING context", literal)
            raw = literal.text[1:-1]
            decoded = []
            index = 0
            while index < len(raw):
                char = raw[index]
                if char != "$":
                    if ord(char) > 0xFF:
                        self._fail(
                            "STRING_ENCODING_UNRESOLVED",
                            "STRING literal is outside the frozen Latin-1 subset",
                            literal)
                    decoded.append(char)
                    index += 1
                    continue
                first = raw[index + 1]
                if first.upper() in _STRING_ESCAPES:
                    decoded.append(_STRING_ESCAPES[first.upper()])
                    index += 2
                    continue
                decoded.append(chr(int(raw[index + 1:index + 3], 16)))
                index += 3
            return "".join(decoded)
        self._fail("UNSUPPORTED_LITERAL", "literal kind is not supported", literal)

    def _constant_value(self, expression, expected):
        if isinstance(expression, STLiteral):
            return self._decode_literal(expression, expected)
        if isinstance(expression, STUnary):
            value = self._constant_value(expression.operand, expected)
            if expression.operator == "NOT" and expected in LOGIC_TYPES:
                return not value if expected == "BOOL" else ~value
            if expression.operator == "-" and expected in (SIGNED_INT_TYPES | REAL_TYPES):
                return -value
            self._fail(
                "INVALID_CONSTANT_UNARY",
                "initializer unary operator is not valid for the declared type",
                expression)
        self._fail(
            "NON_CONSTANT_INITIALIZER",
            "initializer must be a supported literal or unary literal",
            expression)

    def _declare(self):
        seen_global = set()
        seen_local = set()
        if self.pou_kind == "FUNCTION":
            # CODESYS exposes the function name as its return variable.  Model
            # it as a per-call VAR cell, then load it once at normal exit to
            # satisfy the existing FUNCTION stack contract.
            seen_local.add(self.program_name)
            self.locals.append(VarDecl(
                self.program_name, self.return_type, section="VAR"))
            self.local_types[self.program_name] = self.return_type
        for declaration in self.unit.declarations:
            declared_type = declaration.type_name.canonical
            if self.pou_kind == "PROGRAM" and declared_type in self.function_blocks:
                if declaration.scope != "VAR":
                    self._fail(
                        "UNSUPPORTED_FB_INSTANCE_SECTION",
                        "user FB instances are supported only in PROGRAM VAR",
                        declaration)
                if declaration.initializer is not None:
                    self._fail(
                        "UNSUPPORTED_FB_INSTANCE_INITIALIZER",
                        "user FB instance initializers are deferred",
                        declaration.initializer)
                for name in declaration.names:
                    canonical = name.canonical
                    if canonical in seen_local:
                        self._fail(
                            "DUPLICATE_DECLARATION",
                            "duplicate declaration in one scope", name)
                    seen_local.add(canonical)
                    self.instances.append(InstanceDecl(
                        canonical, declared_type, kind="user_fb"))
                    self.instance_types[canonical] = declared_type
                continue
            if self.pou_kind == "PROGRAM" and declared_type in self.library_blocks:
                if declaration.scope != "VAR":
                    self._fail(
                        "UNSUPPORTED_LIBRARY_INSTANCE_SECTION",
                        "library FB instances are supported only in PROGRAM VAR",
                        declaration)
                if declaration.initializer is not None:
                    self._fail(
                        "UNSUPPORTED_LIBRARY_INSTANCE_INITIALIZER",
                        "library FB instance initializers are deferred",
                        declaration.initializer)
                for name in declaration.names:
                    canonical = name.canonical
                    if canonical in seen_local:
                        self._fail(
                            "DUPLICATE_DECLARATION",
                            "duplicate declaration in one scope", name)
                    seen_local.add(canonical)
                    self.instances.append(InstanceDecl(
                        canonical, declared_type, kind="library"))
                    self.library_instance_types[canonical] = declared_type
                continue
            if self.pou_kind in {"FUNCTION", "FUNCTION_BLOCK"}:
                supported = {"VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR"}
                if self.pou_kind == "FUNCTION_BLOCK":
                    supported.add("VAR_TEMP")
                if declaration.scope not in supported:
                    self._fail(
                        "UNSUPPORTED_FUNCTION_SECTION"
                        if self.pou_kind == "FUNCTION"
                        else "UNSUPPORTED_FB_SECTION",
                        "%s declaration section is not supported"
                        % self.pou_kind,
                        declaration)
                if declaration.type_name.canonical not in IEC_TYPES:
                    self._fail(
                        "UNSUPPORTED_DECLARATION_TYPE",
                        "%s declaration requires a supported basic IEC type"
                        % self.pou_kind,
                        declaration.type_name)
                if declaration.scope in {
                        "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"} and \
                        declaration.initializer is not None:
                    self._fail(
                        "OPTIONAL_PARAMETER_DEFERRED",
                        "%s interface defaults require an optional-parameter contract"
                        % self.pou_kind,
                        declaration.initializer)
                destination = (
                    self.interface
                    if declaration.scope in {"VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"}
                    else self.locals)
                type_index = self.local_types
                seen = seen_local
            else:
                if declaration.scope not in _SUPPORTED_SECTIONS:
                    self._fail(
                        "UNSUPPORTED_DECLARATION_SECTION",
                        "only VAR_GLOBAL, VAR, and VAR_TEMP lower in this baseline",
                        declaration)
                if declared_type not in IEC_TYPES:
                    self._fail(
                        "UNSUPPORTED_DECLARATION_TYPE",
                        "PROGRAM declaration requires a basic IEC type or a "
                        "catalogued user FB type",
                        declaration.type_name)
                destination = self.gvl if declaration.scope == "VAR_GLOBAL" else self.locals
                type_index = self.global_types if declaration.scope == "VAR_GLOBAL" else self.local_types
                seen = seen_global if declaration.scope == "VAR_GLOBAL" else seen_local
            for name in declaration.names:
                canonical = name.canonical
                if canonical in seen:
                    if self.pou_kind == "FUNCTION" and canonical == self.program_name:
                        self._fail(
                            "FUNCTION_RESULT_NAME_CONFLICT",
                            "FUNCTION result name cannot be redeclared", name)
                    self._fail("DUPLICATE_DECLARATION", "duplicate declaration in one scope", name)
                seen.add(canonical)
                initializer = None
                if declaration.initializer is not None:
                    if declaration.scope == "VAR_TEMP":
                        self._fail(
                            "UNSUPPORTED_TEMP_INITIALIZER",
                            "VAR_TEMP initialization semantics are deferred",
                            declaration.initializer)
                    initializer = self._constant_value(
                        declaration.initializer, declaration.type_name.canonical)
                destination.append(VarDecl(
                    canonical, declaration.type_name.canonical, initial=initializer,
                    section=declaration.scope))
                type_index[canonical] = declaration.type_name.canonical

    def _resolve_type(self, name):
        if name.canonical in self.local_types:
            return self.local_types[name.canonical]
        if name.canonical in self.global_types:
            return self.global_types[name.canonical]
        self._fail("UNDEFINED_NAME", "name is not declared in the ST unit", name)

    def _fixed_type(self, expression):
        if isinstance(expression, STName):
            return self._resolve_type(expression)
        if isinstance(expression, STLiteral):
            return {"BOOL": "BOOL", "TIME_LITERAL": "TIME", "STRING": "STRING"}.get(
                expression.kind)
        if isinstance(expression, STUnary):
            return self._fixed_type(expression.operand)
        if isinstance(expression, STBinary):
            if expression.operator in _COMPARE:
                return "BOOL"
            left = self._fixed_type(expression.left)
            right = self._fixed_type(expression.right)
            if left is not None and right is not None and left != right:
                self._fail("TYPE_MISMATCH", "binary operands have different types", expression)
            return left or right
        if isinstance(expression, STCall) and isinstance(expression.callee, STName):
            function = self.functions.get(expression.callee.canonical)
            if function is not None:
                return function.return_type
            if expression.callee.canonical == "SEL" and len(expression.arguments) == 3:
                branch_hints = [
                    self._fixed_type(argument.value)
                    for argument in expression.arguments[1:]
                    if argument.direction == "POSITIONAL" and argument.value is not None
                ]
                fixed = [hint for hint in branch_hints if hint is not None]
                if fixed and any(hint != fixed[0] for hint in fixed[1:]):
                    self._fail(
                        "TYPE_MISMATCH", "SEL branches have different fixed types",
                        expression)
                return fixed[0] if fixed else None
            if expression.callee.canonical not in _EAGER_STANDARD_FUNCTIONS:
                return None
            hints = [
                self._fixed_type(argument.value)
                for argument in expression.arguments
                if argument.direction == "POSITIONAL" and argument.value is not None
            ]
            fixed = [hint for hint in hints if hint is not None]
            if fixed and any(hint != fixed[0] for hint in fixed[1:]):
                self._fail(
                    "TYPE_MISMATCH",
                    "standard-function arguments have different fixed types",
                    expression)
            return fixed[0] if fixed else None
        return None

    def _lower_function_call(self, expression, expected, function):
        result_type = function.return_type
        if expected is not None and expected != result_type:
            self._fail(
                "TYPE_MISMATCH", "FUNCTION return type differs from required context",
                expression)
        formals = list(function.interface)
        by_name = {formal.name: formal for formal in formals}
        explicit = any(
            argument.name is not None or argument.direction != "POSITIONAL"
            for argument in expression.arguments)

        actuals = {}
        if explicit:
            for argument in expression.arguments:
                if argument.name is None or argument.direction == "POSITIONAL":
                    self._fail(
                        "MIXED_CALL_ARGUMENT_STYLE",
                        "FUNCTION call cannot mix positional and explicit arguments",
                        argument)
                name = argument.name.canonical
                if name in actuals:
                    self._fail(
                        "DUPLICATE_CALL_FORMAL", "FUNCTION formal is bound twice",
                        argument)
                if name not in by_name:
                    self._fail(
                        "UNKNOWN_CALL_FORMAL", "FUNCTION formal name is unknown",
                        argument)
                actuals[name] = argument
        else:
            if any(formal.section != "VAR_INPUT" for formal in formals):
                self._fail(
                    "EXPLICIT_BINDING_REQUIRED",
                    "FUNCTION with OUT/INOUT requires explicit named bindings",
                    expression)
            if len(expression.arguments) != len(formals):
                self._fail(
                    "MISSING_CALL_FORMAL",
                    "positional FUNCTION call must bind every input", expression)
            actuals = {
                formal.name: argument
                for formal, argument in zip(formals, expression.arguments)
            }

        missing = [formal.name for formal in formals if formal.name not in actuals]
        if missing:
            self._fail(
                "MISSING_CALL_FORMAL", "FUNCTION call must bind every formal",
                expression)

        input_records = []
        writable = {}
        for source_index, argument in enumerate(expression.arguments):
            formal_name = argument.name.canonical if explicit else formals[source_index].name
            formal = by_name[formal_name]
            if formal.section == "VAR_INPUT":
                if explicit and argument.direction != "INPUT":
                    self._fail(
                        "CALL_DIRECTION_MISMATCH",
                        "VAR_INPUT requires := binding", argument)
                code, actual_type = self._lower_expression(
                    argument.value, formal.iec_type)
                if actual_type != formal.iec_type:
                    self._fail("TYPE_MISMATCH", "FUNCTION input type mismatch", argument)
                input_records.append((formal.name, code, formal.iec_type))
            else:
                expected_direction = "OUTPUT" if formal.section == "VAR_OUTPUT" else "INPUT"
                if argument.direction != expected_direction:
                    self._fail(
                        "CALL_DIRECTION_MISMATCH",
                        "FUNCTION OUT/INOUT binding direction is invalid", argument)
                if not isinstance(argument.value, STName):
                    self._fail(
                        "CALL_ACTUAL_NOT_WRITABLE",
                        "FUNCTION OUT/INOUT actual must be one writable name", argument)
                self._reject_active_counter_actual(argument.value)
                actual_type = self._resolve_type(argument.value)
                if actual_type != formal.iec_type:
                    self._fail("TYPE_MISMATCH", "FUNCTION writable actual type mismatch", argument)
                writable[formal.name] = argument.value.canonical

        code = []
        input_positions = {}
        for position, (formal_name, argument_code, _iec_type) in enumerate(input_records):
            code.extend(argument_code)
            input_positions[formal_name] = position
        input_count = len(input_records)
        bindings = []
        for formal in formals:
            if formal.section == "VAR_INPUT":
                index = input_count - 1 - input_positions[formal.name]
                bindings.append(Binding(
                    formal.name, "IN", StackSlot(index), formal.iec_type))
            elif formal.section == "VAR_OUTPUT":
                bindings.append(Binding(
                    formal.name, "OUT", StoreKey(writable[formal.name]),
                    formal.iec_type))
            else:
                bindings.append(Binding(
                    formal.name, "INOUT", StoreKey(writable[formal.name]),
                    formal.iec_type))
        code.append(CallFunc(function.name, tuple(bindings), result_type))
        return code, result_type

    def _positional_call_values(self, expression):
        values = []
        for argument in expression.arguments:
            if argument.direction != "POSITIONAL" or argument.name is not None or \
                    argument.value is None:
                self._fail(
                    "UNSUPPORTED_CALL_ARGUMENT",
                    "standard functions require positional input expressions",
                    argument)
            values.append(argument.value)
        return values

    def _lower_sel(self, expression, expected):
        values = self._positional_call_values(expression)
        if len(values) != 3:
            self._fail(
                "STANDARD_FUNCTION_ARITY",
                "SEL requires exactly three arguments", expression)
        guard, in0, in1 = values
        guard_hint = self._fixed_type(guard)
        if guard_hint not in (None, "BOOL"):
            self._fail("NON_BOOL_SEL_GUARD", "SEL G input must be BOOL", guard)

        branch_hints = [self._fixed_type(in0), self._fixed_type(in1)]
        fixed = [hint for hint in branch_hints if hint is not None]
        if fixed and any(hint != fixed[0] for hint in fixed[1:]):
            self._fail("TYPE_MISMATCH", "SEL branches have different fixed types", expression)
        result_type = expected or (fixed[0] if fixed else None)
        if result_type is None:
            self._fail(
                "AMBIGUOUS_STANDARD_FUNCTION_TYPE",
                "SEL branches require a typed context", expression)
        if fixed and fixed[0] != result_type:
            self._fail(
                "TYPE_MISMATCH", "SEL branch type differs from required context",
                expression)

        guard_code, actual_guard = self._lower_expression(guard, "BOOL")
        if actual_guard != "BOOL":
            self._fail("NON_BOOL_SEL_GUARD", "SEL G input must be BOOL", guard)
        in0_label = self._label("SEL_IN0")
        end_label = self._label("SEL_END")
        # Compile both source branches, but emit control flow that executes only
        # the selected one.  G=TRUE selects IN1; G=FALSE selects IN0.
        in0_code, in0_type = self._lower_expression(in0, result_type)
        in1_code, in1_type = self._lower_expression(in1, result_type)
        if in0_type != result_type or in1_type != result_type:
            self._fail(
                "TYPE_MISMATCH", "SEL branches and result must have one type",
                expression)
        code = list(guard_code)
        code.append(JmpIfFalse(in0_label))
        code.extend(in1_code)
        code.extend((Jmp(end_label), Label(in0_label)))
        code.extend(in0_code)
        code.append(Label(end_label))
        return code, result_type

    def _lower_standard_call(self, expression, expected):
        if not isinstance(expression.callee, STName):
            self._fail(
                "UNSUPPORTED_STANDARD_FUNCTION",
                "standard function callee must be one direct name", expression)
        name = expression.callee.canonical
        function = self.functions.get(name)
        if function is not None:
            return self._lower_function_call(expression, expected, function)
        if name == "SEL":
            return self._lower_sel(expression, expected)
        if name not in _EAGER_STANDARD_FUNCTIONS:
            self._fail(
                "UNSUPPORTED_STANDARD_FUNCTION",
                "source call is not a supported eager standard function", expression)

        values = self._positional_call_values(expression)

        count = len(values)
        valid_arity = (
            (name == "ABS" and count == 1)
            or (name in {"MIN", "MAX"} and count >= 2)
            or (name == "LIMIT" and count == 3)
        )
        if not valid_arity:
            self._fail(
                "STANDARD_FUNCTION_ARITY",
                "standard function argument count is invalid", expression)

        hints = [self._fixed_type(value) for value in values]
        fixed = [hint for hint in hints if hint is not None]
        if fixed and any(hint != fixed[0] for hint in fixed[1:]):
            self._fail(
                "TYPE_MISMATCH",
                "standard-function arguments have different fixed types",
                expression)
        actual = expected or (fixed[0] if fixed else None)
        if actual is None:
            self._fail(
                "AMBIGUOUS_STANDARD_FUNCTION_TYPE",
                "numeric standard-function call requires a typed context",
                expression)
        if name == "ABS" and fixed and fixed[0] not in _ABS_TYPES:
            self._fail(
                "INVALID_STANDARD_FUNCTION_TYPE",
                "ABS requires a numeric basic type", expression)
        if fixed and fixed[0] != actual:
            self._fail(
                "TYPE_MISMATCH",
                "standard-function argument type differs from required context",
                expression)
        if name == "ABS" and actual not in _ABS_TYPES:
            self._fail(
                "INVALID_STANDARD_FUNCTION_TYPE",
                "ABS requires a numeric basic type", expression)
        if name != "ABS" and actual not in IEC_TYPES:
            self._fail(
                "INVALID_STANDARD_FUNCTION_TYPE",
                "standard function type is outside the supported IEC set",
                expression)

        code = []
        param_types = []
        for value in values:
            argument_code, argument_type = self._lower_expression(value, actual)
            if argument_type != actual:
                self._fail(
                    "TYPE_MISMATCH",
                    "standard-function argument type differs from result type",
                    value)
            code.extend(argument_code)
            param_types.append(argument_type)
        code.append(CallStd(name, StdSig(tuple(param_types), actual)))
        return code, actual

    def _lower_expression(self, expression, expected=None):
        if isinstance(expression, STName):
            actual = self._resolve_type(expression)
            if expected is not None and actual != expected:
                self._fail("TYPE_MISMATCH", "name type differs from required context", expression)
            return [LoadVar(expression.canonical, actual)], actual
        if isinstance(expression, STLiteral):
            actual = expected or self._fixed_type(expression)
            if actual is None:
                self._fail(
                    "AMBIGUOUS_LITERAL_TYPE",
                    "numeric literal needs an assignment or typed operand context",
                    expression)
            return [LoadConst(self._decode_literal(expression, actual), actual)], actual
        if isinstance(expression, STUnary):
            actual = expected or self._fixed_type(expression)
            if actual is None:
                self._fail("AMBIGUOUS_LITERAL_TYPE", "unary numeric literal type is ambiguous", expression)
            code, operand_type = self._lower_expression(expression.operand, actual)
            if expression.operator == "NOT":
                if operand_type not in LOGIC_TYPES:
                    self._fail("INVALID_OPERATOR_TYPE", "NOT requires BOOL or integer/bit type", expression)
                op = "NOT"
            else:
                if operand_type not in (SIGNED_INT_TYPES | REAL_TYPES | {"TIME"}):
                    self._fail("INVALID_OPERATOR_TYPE", "negation requires signed, real, or TIME", expression)
                op = "NEG"
            return code + [UnOp(op, operand_type)], operand_type
        if isinstance(expression, STBinary):
            op = _BINOPS[expression.operator]
            comparison = expression.operator in _COMPARE
            if comparison and expected not in (None, "BOOL"):
                self._fail("TYPE_MISMATCH", "comparison result requires BOOL context", expression)
            operand_type = self._fixed_type(expression.left) or self._fixed_type(expression.right)
            if not comparison:
                operand_type = expected or operand_type
            if operand_type is None:
                self._fail(
                    "AMBIGUOUS_LITERAL_TYPE",
                    "binary numeric literals require a typed operand or target",
                    expression)
            left_code, left_type = self._lower_expression(expression.left, operand_type)
            right_code, right_type = self._lower_expression(expression.right, operand_type)
            if left_type != right_type:
                self._fail("TYPE_MISMATCH", "binary operands have different types", expression)
            if op in {"ADD", "SUB", "MUL", "DIV", "MOD"}:
                if operand_type not in NUMERIC_TYPES or (
                        op == "MOD" and operand_type not in INT_TYPES):
                    self._fail("INVALID_OPERATOR_TYPE", "arithmetic operator type is unsupported", expression)
            elif op in {"AND", "OR", "XOR"} and operand_type not in LOGIC_TYPES:
                self._fail("INVALID_OPERATOR_TYPE", "logic operator type is unsupported", expression)
            elif op in {"LT", "GT", "LE", "GE"} and operand_type not in ORDERED_TYPES:
                self._fail("INVALID_OPERATOR_TYPE", "ordered comparison type is unsupported", expression)
            result_type = "BOOL" if comparison else operand_type
            return left_code + right_code + [BinOp(op, operand_type)], result_type
        if isinstance(expression, STCall):
            return self._lower_standard_call(expression, expected)
        if isinstance(expression, (STMember, STIndex)):
            self._fail(
                "UNSUPPORTED_EXPRESSION",
                "member, index, and call lowering is deferred",
                expression)
        self._fail("UNSUPPORTED_EXPRESSION", "expression shape is not supported", expression)

    def _label(self, role):
        self.label_index += 1
        return "__ST_%04d_%s" % (self.label_index, role)

    def _case_value(self, expression, selector_type):
        negative = isinstance(expression, STUnary)
        literal = expression.operand if negative else expression
        if not isinstance(literal, STLiteral) or literal.kind != "INTEGER" or (
                negative and expression.operator != "-"):
            self._fail(
                "UNSUPPORTED_CASE_LABEL",
                "CASE labels must be signed decimal integer literals", expression)
        value = int(literal.text, 10)
        if negative:
            value = -value
        lower, upper = _INT_RANGES[selector_type]
        if value < lower or value > upper:
            self._fail(
                "CASE_LABEL_RANGE",
                "CASE label is outside the selector type range", expression)
        return value

    def _lower_case(self, statement):
        if not isinstance(statement.selector, STName):
            self._fail(
                "UNSUPPORTED_CASE_SELECTOR",
                "CASE selector must be one declared integer variable", statement.selector)
        selector_type = self._resolve_type(statement.selector)
        if selector_type not in INT_TYPES:
            self._fail(
                "INVALID_CASE_SELECTOR_TYPE",
                "CASE selector must have an integer or bit-string type", statement.selector)

        decoded = []
        occupied = []
        for branch in statement.branches:
            branch_values = []
            for label in branch.labels:
                lower = self._case_value(label.lower, selector_type)
                upper = lower if label.upper is None else self._case_value(
                    label.upper, selector_type)
                if upper < lower:
                    self._fail(
                        "REVERSED_CASE_RANGE",
                        "CASE range lower bound must not exceed upper bound", label)
                if any(not (upper < used_lower or lower > used_upper)
                       for used_lower, used_upper in occupied):
                    self._fail(
                        "OVERLAPPING_CASE_LABEL",
                        "CASE labels and ranges must not overlap", label)
                occupied.append((lower, upper))
                branch_values.append((lower, upper))
            decoded.append((branch, tuple(branch_values)))

        code = []
        end_label = self._label("CASE_END")
        for branch, labels in decoded:
            body_label = self._label("CASE_BODY")
            next_branch = self._label("CASE_NEXT")
            for lower, upper in labels:
                next_test = self._label("CASE_TEST")
                code.extend((LoadVar(statement.selector.canonical, selector_type),
                             LoadConst(lower, selector_type)))
                if lower == upper:
                    code.append(BinOp("EQ", selector_type))
                else:
                    code.extend((BinOp("GE", selector_type),
                                 LoadVar(statement.selector.canonical, selector_type),
                                 LoadConst(upper, selector_type),
                                 BinOp("LE", selector_type),
                                 BinOp("AND", "BOOL")))
                code.extend((JmpIfFalse(next_test), Jmp(body_label), Label(next_test)))
            code.extend((Jmp(next_branch), Label(body_label)))
            for nested in branch.statements:
                code.extend(self._lower_statement(nested))
            code.extend((Jmp(end_label), Label(next_branch)))
        for nested in statement.else_statements:
            code.extend(self._lower_statement(nested))
        code.append(Label(end_label))
        return code

    def _for_constant(self, expression, counter_type):
        try:
            return self._case_value(expression, counter_type)
        except STCompileError as error:
            if error.errors[0].code == "CASE_LABEL_RANGE":
                self._fail(
                    "FOR_BOUND_RANGE",
                    "FOR start, end, or BY is outside the counter type range",
                    expression)
            raise

    def _lower_for(self, statement):
        counter_type = self._resolve_type(statement.counter)
        if counter_type not in INT_TYPES:
            self._fail(
                "INVALID_FOR_COUNTER_TYPE",
                "FOR counter must have an integer or bit-string type", statement.counter)
        if statement.counter.canonical in self.active_for_counters:
            self._fail(
                "ACTIVE_FOR_COUNTER_REUSE",
                "nested FOR cannot reuse an active counter", statement.counter)
        try:
            start = self._for_constant(statement.start_value, counter_type)
            end = self._for_constant(statement.end_value, counter_type)
            step = 1 if statement.increment is None else self._for_constant(
                statement.increment, counter_type)
        except STCompileError as error:
            if error.errors[0].code == "UNSUPPORTED_CASE_LABEL":
                self._fail(
                    "UNSUPPORTED_FOR_BOUND",
                    "FOR start, end, and BY must be signed decimal integer literals",
                    statement)
            raise
        if step == 0:
            self._fail("ZERO_FOR_INCREMENT", "FOR BY value must not be zero", statement)
        type_min, type_max = _INT_RANGES[counter_type]
        if step > 0:
            iterations = 0 if start > end else (end - start) // step + 1
            compare = "LE"
        else:
            iterations = 0 if start < end else (start - end) // (-step) + 1
            compare = "GE"
        if iterations:
            last = start + (iterations - 1) * step
            after_last = last + step
            if after_last < type_min or after_last > type_max:
                self._fail(
                    "FOR_COUNTER_OVERFLOW",
                    "FOR terminal increment would exceed the counter type range",
                    statement)
        if iterations > _MAX_STATIC_FOR_ITERATIONS:
            self._fail(
                "FOR_ITERATION_LIMIT",
                "FOR static iteration count exceeds the supported scan limit",
                statement)
        nested_product = self.for_iteration_product * iterations
        if nested_product > _MAX_STATIC_FOR_ITERATIONS:
            self._fail(
                "FOR_NESTED_ITERATION_LIMIT",
                "nested FOR iteration product exceeds the supported scan limit",
                statement)

        check_label = self._label("FOR_CHECK")
        continue_label = self._label("FOR_CONTINUE")
        end_label = self._label("FOR_END")
        code = [LoadConst(start, counter_type),
                StoreVar(statement.counter.canonical, counter_type),
                Label(check_label),
                LoadVar(statement.counter.canonical, counter_type),
                LoadConst(end, counter_type), BinOp(compare, counter_type),
                JmpIfFalse(end_label)]
        self.active_for_counters.add(statement.counter.canonical)
        previous_product = self.for_iteration_product
        self.for_iteration_product = nested_product
        self.loop_contexts.append((continue_label, end_label))
        try:
            for nested in statement.statements:
                code.extend(self._lower_statement(nested))
        finally:
            self.loop_contexts.pop()
            self.for_iteration_product = previous_product
            self.active_for_counters.remove(statement.counter.canonical)
        code.extend((Label(continue_label),
                     LoadVar(statement.counter.canonical, counter_type),
                     LoadConst(step, counter_type), BinOp("ADD", counter_type),
                     StoreVar(statement.counter.canonical, counter_type),
                     Jmp(check_label), Label(end_label)))
        return code

    def _lower_while(self, statement):
        check_label = self._label("WHILE_CHECK")
        end_label = self._label("WHILE_END")
        condition_hint = self._fixed_type(statement.condition)
        if condition_hint not in (None, "BOOL") or (
                isinstance(statement.condition, STLiteral)
                and statement.condition.kind != "BOOL"):
            self._fail("NON_BOOL_CONDITION", "WHILE condition must be BOOL", statement)
        condition_code, condition_type = self._lower_expression(
            statement.condition, "BOOL")
        if condition_type != "BOOL":
            self._fail("NON_BOOL_CONDITION", "WHILE condition must be BOOL", statement)
        code = [Label(check_label)]
        code.extend(condition_code)
        code.append(JmpIfFalse(end_label))
        self.loop_contexts.append((check_label, end_label))
        try:
            for nested in statement.statements:
                code.extend(self._lower_statement(nested))
        finally:
            self.loop_contexts.pop()
        code.extend((Jmp(check_label), Label(end_label)))
        return code

    def _lower_fb_instance_call(self, statement):
        call = statement.call
        if not isinstance(call.callee, STName):
            self._fail(
                "UNSUPPORTED_CALL", "FB instance call requires one direct name",
                statement)
        instance_name = call.callee.canonical
        block_type = self.instance_types.get(instance_name)
        if block_type is None:
            self._fail("UNSUPPORTED_CALL", "call target is not a user FB instance", statement)
        target = self.function_blocks[block_type]
        formals = list(target.interface)
        by_name = {formal.name: formal for formal in formals}
        actuals = {}
        for argument in call.arguments:
            if argument.name is None or argument.direction == "POSITIONAL":
                self._fail(
                    "EXPLICIT_BINDING_REQUIRED",
                    "user FB calls require explicit named bindings", argument)
            name = argument.name.canonical
            if name in actuals:
                self._fail(
                    "DUPLICATE_CALL_FORMAL", "FB formal is bound twice", argument)
            if name not in by_name:
                self._fail(
                    "UNKNOWN_CALL_FORMAL", "FB formal name is unknown", argument)
            actuals[name] = argument
        if any(formal.name not in actuals for formal in formals):
            self._fail(
                "MISSING_CALL_FORMAL", "FB call must bind every formal", statement)

        input_records = []
        writable = {}
        for argument in call.arguments:
            formal = by_name[argument.name.canonical]
            if formal.section == "VAR_INPUT":
                if argument.direction != "INPUT":
                    self._fail(
                        "CALL_DIRECTION_MISMATCH",
                        "FB VAR_INPUT requires := binding", argument)
                argument_code, actual_type = self._lower_expression(
                    argument.value, formal.iec_type)
                if actual_type != formal.iec_type:
                    self._fail("TYPE_MISMATCH", "FB input type mismatch", argument)
                input_records.append((formal.name, argument_code))
                continue
            expected_direction = (
                "OUTPUT" if formal.section == "VAR_OUTPUT" else "INPUT")
            if argument.direction != expected_direction:
                self._fail(
                    "CALL_DIRECTION_MISMATCH",
                    "FB OUT/INOUT binding direction is invalid", argument)
            if not isinstance(argument.value, STName):
                self._fail(
                    "CALL_ACTUAL_NOT_WRITABLE",
                    "FB OUT/INOUT actual must be one writable name", argument)
            self._reject_active_counter_actual(argument.value)
            actual_type = self._resolve_type(argument.value)
            if actual_type != formal.iec_type:
                self._fail("TYPE_MISMATCH", "FB writable actual type mismatch", argument)
            writable[formal.name] = argument.value.canonical

        code = []
        input_positions = {}
        for position, (formal_name, argument_code) in enumerate(input_records):
            code.extend(argument_code)
            input_positions[formal_name] = position
        input_count = len(input_records)
        bindings = []
        for formal in formals:
            if formal.section == "VAR_INPUT":
                bindings.append(Binding(
                    formal.name, "IN",
                    StackSlot(input_count - 1 - input_positions[formal.name]),
                    formal.iec_type))
            elif formal.section == "VAR_OUTPUT":
                bindings.append(Binding(
                    formal.name, "OUT", StoreKey(writable[formal.name]),
                    formal.iec_type))
            else:
                bindings.append(Binding(
                    formal.name, "INOUT", StoreKey(writable[formal.name]),
                    formal.iec_type))
        code.append(CallFbInstance(instance_name, tuple(bindings)))
        return code

    def _lower_library_fb_call(self, statement):
        call = statement.call
        if not isinstance(call.callee, STName):
            self._fail(
                "UNSUPPORTED_CALL", "library FB call requires one direct name",
                statement)
        instance_name = call.callee.canonical
        block_type = self.library_instance_types.get(instance_name)
        if block_type is None:
            self._fail("UNSUPPORTED_CALL", "call target is not a library FB instance", statement)
        library = self.library_blocks[block_type]
        schema = library.schema
        aliases = dict(library.aliases)
        engineering_to_pin = {}
        for pins in (schema.inputs, schema.inouts, schema.outputs):
            for pin in pins:
                engineering_to_pin[pin.name] = pin
        source_to_pin = {
            source: engineering_to_pin[engineering]
            for source, engineering in aliases.items()
        }
        actuals = {}
        for argument in call.arguments:
            if argument.name is None or argument.direction == "POSITIONAL":
                self._fail(
                    "EXPLICIT_BINDING_REQUIRED",
                    "library FB calls require explicit named bindings", argument)
            source_name = argument.name.canonical
            if source_name in actuals:
                self._fail(
                    "DUPLICATE_CALL_FORMAL", "library pin is bound twice", argument)
            if source_name not in source_to_pin:
                self._fail(
                    "UNKNOWN_CALL_FORMAL", "library source pin is unknown", argument)
            actuals[source_name] = argument
        pin_kinds = {}
        omittable = set()
        for source_name, pin in source_to_pin.items():
            pin_kind, omit_policy = pin.kind, pin.omit_policy
            pin_kinds[source_name] = pin_kind
            if pin_kind != "VAR_INPUT":
                continue
            if omit_policy in {
                    "use_default", "keep_previous", "none_means_no_write"}:
                omittable.add(source_name)
            elif omit_policy != "required":
                self._fail(
                    "UNSUPPORTED_LIBRARY_OMIT_POLICY",
                    "library input omit policy is not supported by ST source calls",
                    statement)
        if any(source not in actuals and source not in omittable for source in aliases):
            self._fail(
                "MISSING_CALL_FORMAL", "library FB call must bind every source pin",
                statement)

        code = []
        output_actuals = {}
        inout_actuals = {}
        for argument in call.arguments:
            source_name = argument.name.canonical
            engineering_name = aliases[source_name]
            pin = source_to_pin[source_name]
            pin_kind = pin_kinds[source_name]
            if pin_kind == "VAR_INPUT":
                if argument.direction != "INPUT":
                    self._fail(
                        "CALL_DIRECTION_MISMATCH",
                        "library VAR_INPUT requires := binding", argument)
                argument_code, actual_type = self._lower_expression(
                    argument.value, pin.iec_type)
                if actual_type != pin.iec_type:
                    self._fail("TYPE_MISMATCH", "library input type mismatch", argument)
                code.extend(argument_code)
                code.append(StoreVar(
                    "%s.%s" % (instance_name, engineering_name), pin.iec_type))
            elif pin_kind == "VAR_OUTPUT":
                if argument.direction != "OUTPUT":
                    self._fail(
                        "CALL_DIRECTION_MISMATCH",
                        "library VAR_OUTPUT requires => binding", argument)
                if not isinstance(argument.value, STName):
                    self._fail(
                        "CALL_ACTUAL_NOT_WRITABLE",
                        "library output actual must be one writable name", argument)
                self._reject_active_counter_actual(argument.value)
                actual_type = self._resolve_type(argument.value)
                if actual_type != pin.iec_type:
                    self._fail("TYPE_MISMATCH", "library output type mismatch", argument)
                output_actuals[engineering_name] = argument.value.canonical
            else:
                if argument.direction != "INPUT":
                    self._fail(
                        "CALL_DIRECTION_MISMATCH",
                        "library VAR_IN_OUT requires := binding", argument)
                if not isinstance(argument.value, STName):
                    self._fail(
                        "CALL_ACTUAL_NOT_WRITABLE",
                        "library VAR_IN_OUT actual must be one writable name",
                        argument)
                self._reject_active_counter_actual(argument.value)
                actual_type = self._resolve_type(argument.value)
                if actual_type != pin.iec_type:
                    self._fail(
                        "TYPE_MISMATCH",
                        "library VAR_IN_OUT actual type mismatch", argument)
                actual_name = argument.value.canonical
                inout_actuals[engineering_name] = actual_name
                code.extend((
                    LoadVar(actual_name, pin.iec_type),
                    StoreVar(
                        "%s.%s" % (instance_name, engineering_name),
                        pin.iec_type),
                ))
        code.append(CallFb(instance_name))
        for pin in schema.inouts:
            code.extend((
                LoadVar("%s.%s" % (instance_name, pin.name), pin.iec_type),
                StoreVar(inout_actuals[pin.name], pin.iec_type),
            ))
        for pin in schema.outputs:
            code.extend((
                LoadVar("%s.%s" % (instance_name, pin.name), pin.iec_type),
                StoreVar(output_actuals[pin.name], pin.iec_type),
            ))
        return code

    def _lower_statement(self, statement):
        if isinstance(statement, STReturn):
            # RETURN exits the current POU, not the nearest loop.  All POU kinds
            # share one tail label; FUNCTION reaches its result LoadVar only there.
            return [Jmp(self.return_epilogue_label)]
        if isinstance(statement, STCallStatement):
            if isinstance(statement.call.callee, STName) and \
                    statement.call.callee.canonical in self.library_instance_types:
                return self._lower_library_fb_call(statement)
            return self._lower_fb_instance_call(statement)
        if isinstance(statement, STExit):
            if not self.loop_contexts:
                self._fail(
                    "LOOP_CONTROL_OUTSIDE_LOOP", "EXIT requires an enclosing loop",
                    statement)
            return [Jmp(self.loop_contexts[-1][1])]
        if isinstance(statement, STContinue):
            if not self.loop_contexts:
                self._fail(
                    "LOOP_CONTROL_OUTSIDE_LOOP",
                    "CONTINUE requires an enclosing loop", statement)
            return [Jmp(self.loop_contexts[-1][0])]
        if isinstance(statement, STWhile):
            return self._lower_while(statement)
        if isinstance(statement, STIf):
            code = []
            end_label = self._label("IF_END")
            for branch in statement.branches:
                next_label = self._label("IF_NEXT")
                condition_hint = self._fixed_type(branch.condition)
                if condition_hint not in (None, "BOOL") or (
                        isinstance(branch.condition, STLiteral)
                        and branch.condition.kind != "BOOL"):
                    self._fail("NON_BOOL_CONDITION", "IF condition must be BOOL", branch)
                condition_code, condition_type = self._lower_expression(
                    branch.condition, "BOOL")
                if condition_type != "BOOL":
                    self._fail("NON_BOOL_CONDITION", "IF condition must be BOOL", branch)
                code.extend(condition_code)
                code.append(JmpIfFalse(next_label))
                for nested in branch.statements:
                    code.extend(self._lower_statement(nested))
                code.append(Jmp(end_label))
                code.append(Label(next_label))
            for nested in statement.else_statements:
                code.extend(self._lower_statement(nested))
            code.append(Label(end_label))
            return code
        if isinstance(statement, STCase):
            return self._lower_case(statement)
        if isinstance(statement, STFor):
            return self._lower_for(statement)
        if not isinstance(statement, STAssignment) or not isinstance(statement.target, STName):
            self._fail(
                "UNSUPPORTED_ASSIGNMENT_TARGET",
                "only simple-name assignment lowers in this baseline",
                statement)
        if statement.target.canonical in self.active_for_counters:
            self._fail(
                "FOR_COUNTER_WRITE",
                "FOR body must not assign its active counter", statement.target)
        target_type = self._resolve_type(statement.target)
        expression_code, result_type = self._lower_expression(
            statement.value, target_type)
        if result_type != target_type:
            self._fail("TYPE_MISMATCH", "assignment requires an explicit exact type", statement)
        return expression_code + [StoreVar(statement.target.canonical, target_type)]

    def lower(self):
        self._declare()
        code = []
        for statement in self.unit.statements:
            code.extend(self._lower_statement(statement))
        code.append(Label(self.return_epilogue_label))
        if self.pou_kind in {"FUNCTION", "FUNCTION_BLOCK"}:
            if self.pou_kind == "FUNCTION":
                code.append(LoadVar(self.program_name, self.return_type))
            pou = POUDefinition(
                name=self.program_name, pou_kind=self.pou_kind, language="ST",
                interface=self.interface, locals=self.locals,
                return_type=self.return_type, source=self.unit, code=list(code))
            # Validate the POU with the same Loader by placing it beside a
            # minimal reachable PROGRAM; no alternate IR checker is created.
            main = POUDefinition(
                name="__ST_VALIDATION_MAIN", pou_kind="PROGRAM",
                language="ST", code=[])
            validation_task = Task(
                cycle_ms=self.cycle_ms,
                programs=[ProgramInstance("__ST_VALIDATION_MAIN",
                                          "__ST_VALIDATION_MAIN")],
                pou_lib={"__ST_VALIDATION_MAIN": main, self.program_name: pou})
            validate_task(validation_task)
            return STPOUCompileResult(self.unit, pou, tuple(code))
        pou = POUDefinition(
            name=self.program_name, pou_kind="PROGRAM", language="ST",
            locals=self.locals, instances=self.instances,
            source=self.unit, code=list(code))
        pou_lib = {self.program_name: pou}
        pou_lib.update(self.functions)
        pou_lib.update(self.function_blocks)
        task = Task(
            cycle_ms=self.cycle_ms,
            programs=[ProgramInstance(self.program_name, self.program_name)],
            gvl=self.gvl,
            pou_lib=pou_lib)
        validate_task(task, registry=self.registry)
        return STCompileResult(self.unit, task, tuple(code))


def _valid_iec_identifier(value):
    if type(value) is not str:
        return False
    try:
        tokens = lex_st(value)
    except STLexError:
        return False
    return len(tokens) == 2 and tokens[0].kind == "IDENTIFIER"


def _catalogue_error(code, message):
    return STCompileError((STCompileDiagnostic(code, message, 0, 0, 1, 1),))


_STPOU_RESULT_FIELDS = frozenset({"unit", "pou", "code"})
_POU_FIELDS = frozenset({
    "name", "pou_kind", "language", "interface", "locals", "instances",
    "return_type", "source", "code"})
_VARDECL_FIELDS = frozenset({
    "name", "iec_type", "initial", "retain", "persistent", "section"})
_CONST_VALUE_TYPES = (bool, int, float, str)


def _const_value_matches_iec_type(value, iec_type):
    # Basic value/type *category* consistency for a reconstructed catalogue
    # constant: every IEC type belongs to exactly one Python value category
    # (BOOL->bool, integer/bit-string->int, REAL/LREAL->float, TIME->int,
    # STRING->str), which is the only shape the front end can emit through
    # ``_decode_literal``.  ``iec_type`` is verified to be an exact ``str`` by
    # the caller, so the frozenset membership tests below invoke no hostile
    # hook.  Integer range / overflow stays deferred (IR_SPEC §5.3/§5.4): this
    # only rejects a forged constant whose exact-typed value cannot inhabit its
    # declared type at all (e.g. ``LoadConst("x", "INT")`` or
    # ``Const(1, "WP118_NOT_IEC")``), and an ``iec_type`` outside ``IEC_TYPES``
    # matches no category and is therefore rejected.
    if iec_type == "BOOL":
        return type(value) is bool
    if iec_type in INT_TYPES:
        return type(value) is int
    if iec_type in REAL_TYPES:
        return type(value) is float
    if iec_type == "TIME":
        return type(value) is int
    if iec_type == "STRING":
        return type(value) is str
    return False


def _is_exact_type(value, *types):
    # Identity-only membership test.  Unlike ``in`` / ``==`` / ``not in`` on a
    # collection, ``is`` never dispatches to a hostile object's ``__eq__`` /
    # ``__hash__`` / metaclass hooks, so it cannot leak a caller-supplied
    # ``BaseException`` while classifying an untrusted catalogue field.
    actual = type(value)
    for candidate in types:
        if actual is candidate:
            return True
    return False


def _exact_field_view(obj, expected_names, error_code, what):
    # ``obj``'s exact concrete type is verified by the caller (a plain/frozen
    # dataclass without a custom ``__getattribute__``), so reading ``__dict__``
    # dispatches to no hostile hook.  A deleted or added attribute makes the
    # key set diverge and is rejected before any business field is touched.
    fields = vars(obj)
    if set(fields) != expected_names:
        raise _catalogue_error(
            error_code, "%s fields do not match the exact schema" % what)
    return fields


def _clone_var_decl(declaration, error_code="INVALID_FUNCTION_CATALOGUE"):
    if type(declaration) is not VarDecl:
        raise _catalogue_error(
            error_code, "compiled POU declaration has an invalid shell")
    fields = _exact_field_view(
        declaration, _VARDECL_FIELDS, error_code, "compiled POU declaration")
    name = fields["name"]
    iec_type = fields["iec_type"]
    initial = fields["initial"]
    retain = fields["retain"]
    persistent = fields["persistent"]
    section = fields["section"]
    if not _is_exact_type(name, str) or not _is_exact_type(iec_type, str) or \
            not _is_exact_type(retain, bool) or \
            not _is_exact_type(persistent, bool) or \
            not _is_exact_type(section, str) or \
            not _is_exact_type(initial, type(None), bool, int, float, str):
        raise _catalogue_error(
            error_code, "compiled POU declaration fields are invalid")
    if not _valid_iec_identifier(name) or name != name.upper() or \
            iec_type not in IEC_TYPES or \
            (type(initial) is float and not math.isfinite(initial)):
        raise _catalogue_error(
            error_code, "compiled POU declaration fields are invalid")
    # RETAIN / PERSISTENT have no strict-subset source syntax: the front end
    # always emits ``retain=False`` / ``persistent=False`` (st_lowering.py:356),
    # so a forged catalogue setting either flag would silently open an
    # unimplemented persistence contract.  Reject after the exact-``bool`` gate
    # above (identity compare, no hostile hook).
    if retain is not False or persistent is not False:
        raise _catalogue_error(
            error_code,
            "compiled POU declaration must not set retain or persistent")
    # Declaration-level allowed-value invariant the Loader does not police: only
    # a ``VAR`` local may carry a non-``None`` initial value.  The front end
    # rejects interface (VAR_INPUT/VAR_OUTPUT/VAR_IN_OUT) initials as
    # ``OPTIONAL_PARAMETER_DEFERRED`` (st_lowering.py:309-316) and VAR_TEMP
    # initials as ``UNSUPPORTED_TEMP_INITIALIZER`` (st_lowering.py:347-355), so a
    # forged initial in either section would carry semantics the front end can
    # never emit (a defaulted interface parameter, or a VAR_TEMP initial the
    # executor silently discards).  Gate the section policy first, then reuse the
    # same deferred-range-safe value/type *category* consistency the constant
    # gates use for a legal ``VAR`` initial -- both after the exact-shape checks
    # above, so no hostile hook runs.
    if initial is not None:
        if section != "VAR":
            raise _catalogue_error(
                error_code,
                "compiled POU declaration section does not permit an "
                "initial value")
        if not _const_value_matches_iec_type(initial, iec_type):
            raise _catalogue_error(
                error_code,
                "compiled POU declaration initial value is invalid for its "
                "IEC type")
    return VarDecl(
        name, iec_type, initial=initial, retain=retain,
        persistent=persistent, section=section)


def _clone_std_sig(sig, error_code):
    if type(sig) is not StdSig:
        raise _catalogue_error(
            error_code, "compiled POU CALL_STD signature is invalid")
    fields = _exact_field_view(
        sig, frozenset({"param_types", "return_type"}), error_code,
        "compiled POU CALL_STD signature")
    param_types = fields["param_types"]
    return_type = fields["return_type"]
    if type(param_types) is not tuple or \
            any(not _is_exact_type(item, str) for item in param_types) or \
            not _is_exact_type(return_type, str):
        raise _catalogue_error(
            error_code, "compiled POU CALL_STD signature fields are invalid")
    return StdSig(tuple(param_types), return_type)


def _clone_binding_actual(actual, error_code):
    kind = type(actual)
    if kind is StoreKey:
        fields = _exact_field_view(
            actual, frozenset({"key"}), error_code, "compiled POU binding actual")
        if not _is_exact_type(fields["key"], str):
            raise _catalogue_error(
                error_code, "compiled POU binding actual fields are invalid")
        return StoreKey(fields["key"])
    if kind is StackSlot:
        fields = _exact_field_view(
            actual, frozenset({"index", "writable"}), error_code,
            "compiled POU binding actual")
        index = fields["index"]
        writable = fields["writable"]
        if not _is_exact_type(index, int) or not _is_exact_type(writable, bool):
            raise _catalogue_error(
                error_code, "compiled POU binding actual fields are invalid")
        return StackSlot(index, writable)
    if kind is Const:
        fields = _exact_field_view(
            actual, frozenset({"value", "type"}), error_code,
            "compiled POU binding actual")
        value = fields["value"]
        value_type = fields["type"]
        if not _is_exact_type(value, *_CONST_VALUE_TYPES) or \
                not _is_exact_type(value_type, str) or \
                (type(value) is float and not math.isfinite(value)):
            raise _catalogue_error(
                error_code, "compiled POU binding actual fields are invalid")
        if not _const_value_matches_iec_type(value, value_type):
            raise _catalogue_error(
                error_code,
                "compiled POU binding constant value is invalid for its IEC type")
        return Const(value, value_type)
    raise _catalogue_error(
        error_code, "compiled POU binding actual reference is unsupported")


def _clone_binding(binding, error_code):
    if type(binding) is not Binding:
        raise _catalogue_error(error_code, "compiled POU binding is invalid")
    fields = _exact_field_view(
        binding, frozenset({"formal", "mode", "actual", "type"}), error_code,
        "compiled POU binding")
    if not _is_exact_type(fields["formal"], str) or \
            not _is_exact_type(fields["mode"], str) or \
            not _is_exact_type(fields["type"], str):
        raise _catalogue_error(
            error_code, "compiled POU binding fields are invalid")
    # ``mode`` is a declared exact enum; gate its allowed value at the clone
    # boundary (after the exact-``str`` check above, so no hostile hook runs).
    # Unlike section, an illegal mode lives inside a CALL binding whose target
    # cannot resolve in the single-POU isolation task, so relying on
    # ``_validate_catalogue_pou`` would let an FB counterexample fail on the
    # unresolved reference instead of the mode itself; gating here keeps the
    # rejection unambiguously mode-origin for both FUNCTION and FB catalogues.
    if fields["mode"] not in BINDING_MODES:
        raise _catalogue_error(
            error_code, "compiled POU binding mode is unsupported")
    actual = _clone_binding_actual(fields["actual"], error_code)
    return Binding(fields["formal"], fields["mode"], actual, fields["type"])


def _clone_bindings(bindings, error_code):
    if type(bindings) is not tuple:
        raise _catalogue_error(
            error_code, "compiled POU call bindings are invalid")
    return tuple(_clone_binding(binding, error_code) for binding in bindings)


def _clone_instruction(instruction, error_code):
    kind = type(instruction)
    if kind is LoadVar or kind is LoadPrev or kind is StoreVar:
        fields = _exact_field_view(
            instruction, frozenset({"key", "type"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["key"], str) or \
                not _is_exact_type(fields["type"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return kind(fields["key"], fields["type"])
    if kind is LoadConst:
        fields = _exact_field_view(
            instruction, frozenset({"value", "type"}), error_code,
            "compiled POU instruction")
        value = fields["value"]
        value_type = fields["type"]
        if not _is_exact_type(value, *_CONST_VALUE_TYPES) or \
                not _is_exact_type(value_type, str) or \
                (type(value) is float and not math.isfinite(value)):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        if not _const_value_matches_iec_type(value, value_type):
            raise _catalogue_error(
                error_code,
                "compiled POU constant value is invalid for its IEC type")
        return LoadConst(value, value_type)
    if kind is BinOp or kind is UnOp:
        fields = _exact_field_view(
            instruction, frozenset({"op", "type"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["op"], str) or \
                not _is_exact_type(fields["type"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return kind(fields["op"], fields["type"])
    if kind is Convert:
        fields = _exact_field_view(
            instruction, frozenset({"from_type", "to_type"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["from_type"], str) or \
                not _is_exact_type(fields["to_type"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return Convert(fields["from_type"], fields["to_type"])
    if kind is CallStd:
        fields = _exact_field_view(
            instruction, frozenset({"name", "sig"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["name"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        # CALL_STD names are an exact enum too: the front end only emits the
        # eager standard functions, and the isolation Loader runs without a
        # registry, so an unknown exact-``str`` name would otherwise reach the
        # returned Task uncaught.  Membership runs after the exact-``str`` check,
        # so no hostile hook is observed.
        if fields["name"] not in _EAGER_STANDARD_FUNCTIONS:
            raise _catalogue_error(
                error_code,
                "compiled POU CALL_STD names an unsupported standard function")
        return CallStd(fields["name"], _clone_std_sig(fields["sig"], error_code))
    if kind is CallFb:
        fields = _exact_field_view(
            instruction, frozenset({"instance"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["instance"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return CallFb(fields["instance"])
    if kind is CallFunc:
        fields = _exact_field_view(
            instruction, frozenset({"name", "bindings", "ret_type"}),
            error_code, "compiled POU instruction")
        if not _is_exact_type(fields["name"], str) or \
                not _is_exact_type(fields["ret_type"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return CallFunc(
            fields["name"], _clone_bindings(fields["bindings"], error_code),
            fields["ret_type"])
    if kind is CallFbInstance:
        fields = _exact_field_view(
            instruction, frozenset({"instance_path", "bindings"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["instance_path"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return CallFbInstance(
            fields["instance_path"],
            _clone_bindings(fields["bindings"], error_code))
    if kind is Jmp or kind is JmpIfFalse:
        fields = _exact_field_view(
            instruction, frozenset({"label"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["label"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return kind(fields["label"])
    if kind is Label:
        fields = _exact_field_view(
            instruction, frozenset({"id"}), error_code,
            "compiled POU instruction")
        if not _is_exact_type(fields["id"], str):
            raise _catalogue_error(
                error_code, "compiled POU instruction fields are invalid")
        return Label(fields["id"])
    raise _catalogue_error(
        error_code, "compiled POU code contains an unsupported instruction")


def _precheck_catalogue_code(pou, error_code):
    # The whole-stream, reachability-independent combined-semantics check is the
    # Loader's single source of truth, exposed as the supported internal facade
    # ``validate_pou_instruction_semantics``.  ST no longer imports the Loader's
    # private ``_build_scope`` / ``_step`` or ``standard_signature_error`` and keeps
    # no second operator/type/signature rule table: it delegates to the facade and
    # converges the Loader's ``IRValidationError`` into a stable catalogue
    # ``STCompileError`` classified by ``error_code``.
    #
    # Why a whole-stream gate is needed at all: ``_validate_catalogue_pou`` hands the
    # reconstructed POU to the real Loader, whose ``_check_code`` only value-checks
    # control-flow-*reachable* instructions (its worklist never visits a dead
    # instruction sitting after an unconditional ``Jmp`` with no other in-edge).  The
    # clone helpers verified every scalar's exact Python type, but the Loader's
    # allowed-value / reference and *combined*-semantics checks would therefore skip
    # a dead instruction, letting a forged catalogue smuggle an illegal exact-typed
    # scalar, a dangling reference, or a field-legal-but-combination-illegal
    # instruction (e.g. ``LoadVar('A','DINT')`` on a declared ``INT``, ``BinOp('ADD',
    # 'BOOL')``, ``CallStd('ABS', StdSig((), 'INT'))``) into the returned Task through
    # unreachable code.  Loop ``EXIT`` / ``CONTINUE`` legitimately emit dead code
    # (``lower`` lowers every body statement, so an ``EXIT`` before further statements
    # leaves them unreachable), so the facade gate stays reachability-independent by
    # design rather than rejecting unreachable instructions wholesale.
    try:
        validate_pou_instruction_semantics(pou)
    except IRValidationError as error:
        raise _catalogue_error(
            error_code,
            "compiled POU '%s' fails catalogue-boundary instruction semantics: %s"
            % (pou.name, "; ".join(error.errors))) from None


def _validate_catalogue_pou(pou, error_code):
    # The clone helpers above verify only the exact Python *type* of every
    # scalar; the *allowed values* (IEC types, section/mode/op enums, variable
    # and label references, stack/binding well-formedness) are the Loader's
    # contract.  Because the reconstructed POU is now built entirely from
    # identity-verified primitives, it is safe to hand straight to the real
    # Loader.  Running the same isolation gate the compiler applies to its own
    # FUNCTION/FB output turns every value-level Loader rejection into a stable
    # catalogue ``STCompileError`` instead of a leaked ``IRValidationError``.
    # Only this single untrusted POU sits beside a minimal empty PROGRAM, so any
    # failure is unambiguously catalogue-origin (attribution stays exact); the
    # caller's own compiled PROGRAM IR is validated separately in ``lower`` and
    # its internal defects are never swallowed here.
    main = POUDefinition(
        name="__ST_VALIDATION_MAIN", pou_kind="PROGRAM", language="ST", code=[])
    validation_task = Task(
        cycle_ms=500,
        programs=[ProgramInstance("__ST_VALIDATION_MAIN",
                                  "__ST_VALIDATION_MAIN")],
        pou_lib={"__ST_VALIDATION_MAIN": main, pou.name: pou})
    try:
        validate_task(validation_task)
    except IRValidationError as error:
        raise _catalogue_error(
            error_code,
            "compiled POU '%s' fails catalogue-boundary loader validation: %s"
            % (pou.name, "; ".join(error.errors))) from None


def _prepare_compiled_pous(results, program_name, expected_kind, *,
                           invalid_code, collision_code, duplicate_code,
                           reserved_names=()):
    if type(results) is not tuple:
        raise _catalogue_error(
            invalid_code, "compiled POU catalogue must be an exact tuple")
    prepared = {}
    reserved = set(reserved_names) | {program_name}
    for result in results:
        if type(result) is not STPOUCompileResult:
            raise _catalogue_error(
                invalid_code, "catalogue entries must be compiled POU results")
        result_fields = _exact_field_view(
            result, _STPOU_RESULT_FIELDS, invalid_code, "compiled POU result")
        pou = result_fields["pou"]
        code = result_fields["code"]
        if type(pou) is not POUDefinition:
            raise _catalogue_error(
                invalid_code, "catalogue entry POU shell is invalid")
        pou_fields = _exact_field_view(
            pou, _POU_FIELDS, invalid_code, "catalogue entry POU")
        name = pou_fields["name"]
        pou_kind = pou_fields["pou_kind"]
        language = pou_fields["language"]
        interface = pou_fields["interface"]
        locals_ = pou_fields["locals"]
        pou_code = pou_fields["code"]
        return_type = pou_fields["return_type"]
        instances = pou_fields["instances"]
        # Exact-type gate (identity only) before any value comparison, hash or
        # membership test, so a hostile field cannot leak a BaseException.
        if not _is_exact_type(name, str) or \
                not _is_exact_type(pou_kind, str) or \
                not _is_exact_type(language, str) or \
                type(interface) is not list or type(locals_) is not list or \
                type(pou_code) is not list or type(code) is not tuple:
            raise _catalogue_error(
                invalid_code, "catalogue entry fields are invalid")
        # ``instances`` carries no strict-subset FUNCTION/FB payload: the front
        # end never populates it (POUDefinition default ``[]``) and the
        # reconstruction below omits it, so a forged non-empty list would be
        # silently dropped and a hostile container never inspected.  Require an
        # exact empty ``list`` -- an identity type check then a length probe, no
        # element comparison/hash -- so a non-empty or non-list ``instances``
        # fails closed instead of being discarded.
        if type(instances) is not list or len(instances) != 0:
            raise _catalogue_error(
                invalid_code, "catalogue entry must not declare instances")
        if not _valid_iec_identifier(name) or name != name.upper() or \
                pou_kind != expected_kind or language != "ST":
            raise _catalogue_error(
                invalid_code, "catalogue entry kind or fields are invalid")
        if expected_kind == "FUNCTION":
            if not _is_exact_type(return_type, str) or \
                    return_type not in IEC_TYPES:
                raise _catalogue_error(
                    invalid_code, "catalogue entry return type is invalid")
        elif return_type is not None:
            raise _catalogue_error(
                invalid_code, "catalogue entry return type is invalid")
        # Re-materialise the whole instruction stream from verified primitives:
        # no caller-controlled comparison/hash/iteration hook can reach the
        # loader, and the returned Task shares no mutable instruction object.
        cloned_code = [
            _clone_instruction(instruction, invalid_code)
            for instruction in code]
        if name in reserved:
            raise _catalogue_error(
                collision_code, "compiled POU name collides with a reserved name")
        if name in prepared:
            raise _catalogue_error(
                duplicate_code, "compiled POU catalogue contains a duplicate name")
        cloned_interface = [
            _clone_var_decl(item, invalid_code) for item in interface]
        cloned_locals = [
            _clone_var_decl(item, invalid_code) for item in locals_]
        # source is dropped (safe disconnect): a compiled POU no longer needs
        # its front-end model, and retaining the caller's object would keep a
        # caller-mutable alias inside the returned Task.
        prepared_pou = POUDefinition(
            name=name, pou_kind=expected_kind, language="ST",
            interface=cloned_interface, locals=cloned_locals,
            return_type=return_type, source=None, code=cloned_code)
        # Close the value-level trust boundary in two stages.  First pre-check the
        # *whole* reconstructed instruction stream for allowed-value / reference
        # invariants, independent of control-flow reachability, so a forged
        # catalogue cannot smuggle an illegal exact-typed scalar or dangling
        # reference into the Task through dead code the Loader's reachability-driven
        # worklist would skip.  Then hand the POU to the real Loader, turning any
        # remaining value-level rejection (on reachable code) into a stable
        # catalogue error instead of a leaked ``IRValidationError``.
        _precheck_catalogue_code(prepared_pou, invalid_code)
        _validate_catalogue_pou(prepared_pou, invalid_code)
        prepared[name] = prepared_pou
    return prepared


def _prepare_functions(functions, program_name):
    return _prepare_compiled_pous(
        functions, program_name, "FUNCTION",
        invalid_code="INVALID_FUNCTION_CATALOGUE",
        collision_code="FUNCTION_NAME_COLLISION",
        duplicate_code="DUPLICATE_FUNCTION_DEFINITION",
        reserved_names=_EAGER_STANDARD_FUNCTIONS | {"SEL"})


def _prepare_function_blocks(function_blocks, program_name, function_names):
    return _prepare_compiled_pous(
        function_blocks, program_name, "FUNCTION_BLOCK",
        invalid_code="INVALID_FB_CATALOGUE",
        collision_code="FB_NAME_COLLISION",
        duplicate_code="DUPLICATE_FB_DEFINITION",
        reserved_names=_EAGER_STANDARD_FUNCTIONS | {"SEL"} | set(function_names))


def _prepare_library_blocks():
    # ``build_default_registry`` itself registers frozen Schema objects through
    # normal descriptor access.  Gate both classes before invoking it, not just
    # before consuming a resolved entry, so a privileged class hook is always
    # rejected at the catalogue boundary and cannot leak through Registry setup.
    if Pin.__getattribute__ is not _PIN_GETATTRIBUTE or \
            BlockSchema.__getattribute__ is not _BLOCK_SCHEMA_GETATTRIBUTE:
        raise _catalogue_error(
            "INVALID_LIBRARY_ALIAS_CONTRACT",
            "library Schema or Pin class access contract is invalid")
    registry = build_default_registry()
    prepared = {}
    # Gate the outermost alias carrier with an identity-only ``type`` check
    # before touching ``.items()``, so a hostile catalogue whose iteration,
    # comparison or hash hooks raise a custom ``BaseException`` cannot leak past
    # the exact-shell contract.  A genuine ``dict`` yields its stored keys and
    # values without invoking any per-key hook, and each block key is confirmed
    # to be an exact ``str`` before it can reach ``registry.resolve``.
    catalogue = library_source_aliases()
    if type(catalogue) is not dict:
        raise _catalogue_error(
            "INVALID_LIBRARY_ALIAS_CONTRACT",
            "library alias catalogue is not an exact dict")
    for block_type, aliases in catalogue.items():
        if type(block_type) is not str:
            raise _catalogue_error(
                "INVALID_LIBRARY_ALIAS_CONTRACT",
                "library alias block type is not an exact str")
        # An exact-``str`` alias key absent from the frozen engineering Registry
        # must fail closed at the ST alias boundary, not leak the Registry-layer
        # ``UnknownBlockError`` past the ST compile boundary (Codex WP-125 Round
        # 2).  ``has`` is a pure membership test keyed by two exact ``str``
        # values, so it neither raises nor hashes any untrusted object; once it
        # confirms registration, the following ``resolve`` returns the entry
        # without raising.
        if not registry.has(block_type):
            raise _catalogue_error(
                "INVALID_LIBRARY_ALIAS_CONTRACT",
                "library alias block type is not a registered engineering block")
        schema, _adapter = registry.resolve(block_type, "engineering")
        # Gate the resolved Schema shell with an identity-only ``type`` check
        # before reading ``inputs`` / ``inouts`` / ``outputs`` or any other
        # instance field.  A hostile shell whose pin attributes are properties
        # (or whose ``__getattribute__`` observes access and raises a custom
        # ``BaseException``) must fail closed as a catalogue ``STCompileError``
        # without its hook ever firing (Codex WP-141 Round 1 §4).  ``resolve``
        # returns the stored ``_entries`` value verbatim, so this is the first
        # point the shell is touched; a genuine frozen ``BlockSchema`` carries
        # its pin tuples as plain ``__dict__`` slots read without any hook.
        schema_snapshot = _library_schema_carrier(
            schema, expected_block_type=block_type,
            expected_variant="engineering")
        if schema_snapshot is None:
            raise _catalogue_error(
                "INVALID_LIBRARY_ALIAS_CONTRACT",
                "library Schema shell does not match the exact contract")
        schema_pins = (
            schema_snapshot.inputs + schema_snapshot.inouts +
            schema_snapshot.outputs)
        if type(aliases) is not dict or \
                any(type(source) is not str or source != source.upper() or
                    type(engineering) is not str
                    for source, engineering in aliases.items()) or \
                len(set(aliases.values())) != len(aliases) or \
                set(aliases.values()) != {pin.name for pin in schema_pins}:
            raise _catalogue_error(
                "INVALID_LIBRARY_ALIAS_CONTRACT",
                "library source-pin aliases do not match the engineering Schema")
        prepared[block_type] = _LibraryBlockSnapshot(
            schema_snapshot, tuple(aliases.items()))
    return registry, prepared


def compile_st_task(source, program_name="PLC_PRG", cycle_ms=500, *, functions=(),
                    function_blocks=()):
    """Compile the strict supported ST subset into a validated typed-IR Task."""

    if not _valid_iec_identifier(program_name):
        raise STCompileError((STCompileDiagnostic(
            "INVALID_PROGRAM_NAME", "program_name must be one IEC identifier",
            0, 0, 1, 1),))
    if type(cycle_ms) is not int or cycle_ms <= 0:
        raise STCompileError((STCompileDiagnostic(
            "INVALID_CYCLE_MS", "cycle_ms must be a positive exact int",
            0, 0, 1, 1),))
    canonical_name = program_name.upper()
    registry, library_blocks = _prepare_library_blocks()
    prepared = _prepare_functions(functions, canonical_name)
    prepared_fbs = _prepare_function_blocks(
        function_blocks, canonical_name, prepared)
    if set(prepared) & set(library_blocks):
        raise _catalogue_error(
            "FUNCTION_NAME_COLLISION",
            "FUNCTION name collides with a primitive library block")
    if set(prepared_fbs) & set(library_blocks):
        raise _catalogue_error(
            "FB_NAME_COLLISION",
            "user FB name collides with a primitive library block")
    unit = parse_st(source)
    return _Lowerer(
        unit, canonical_name, cycle_ms, functions=prepared,
        function_blocks=prepared_fbs, library_blocks=library_blocks,
        registry=registry).lower()


def compile_st_function(source, function_name, return_type, cycle_ms=500):
    """Compile one strict ST FUNCTION body into a validated POU definition.

    The CODESYS editor stores POU metadata separately from its declaration and
    implementation panes, so name/kind/return type are explicit API metadata
    rather than guessed from a synthetic text header.
    """

    if not _valid_iec_identifier(function_name):
        raise STCompileError((STCompileDiagnostic(
            "INVALID_FUNCTION_NAME", "function_name must be one IEC identifier",
            0, 0, 1, 1),))
    if type(return_type) is not str or return_type.upper() not in IEC_TYPES:
        raise STCompileError((STCompileDiagnostic(
            "INVALID_FUNCTION_RETURN_TYPE",
            "return_type must be one supported basic IEC type",
            0, 0, 1, 1),))
    if type(cycle_ms) is not int or cycle_ms <= 0:
        raise STCompileError((STCompileDiagnostic(
            "INVALID_CYCLE_MS", "cycle_ms must be a positive exact int",
            0, 0, 1, 1),))
    unit = parse_st(source)
    return _Lowerer(
        unit, function_name.upper(), cycle_ms, pou_kind="FUNCTION",
        return_type=return_type.upper()).lower()


def compile_st_function_block(source, fb_name, cycle_ms=500):
    """Compile one strict ST FUNCTION_BLOCK body into a validated POU."""

    if not _valid_iec_identifier(fb_name):
        raise STCompileError((STCompileDiagnostic(
            "INVALID_FB_NAME", "fb_name must be one IEC identifier",
            0, 0, 1, 1),))
    if type(cycle_ms) is not int or cycle_ms <= 0:
        raise STCompileError((STCompileDiagnostic(
            "INVALID_CYCLE_MS", "cycle_ms must be a positive exact int",
            0, 0, 1, 1),))
    unit = parse_st(source)
    return _Lowerer(
        unit, fb_name.upper(), cycle_ms, pou_kind="FUNCTION_BLOCK").lower()
