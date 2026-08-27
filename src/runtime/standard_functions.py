"""Deterministic eager standard-function catalogue for typed IR.

Only functions whose arguments may be evaluated eagerly belong here.  In
particular, CODESYS ST ``SEL`` is deliberately absent: its unselected branch is
not evaluated and must therefore lower to control flow instead of ``CallStd``.

The catalogue is internal runtime plumbing, not a second plugin registry.
Unknown ``CallStd`` names remain valid for the existing explicit
``Executor(std_functions=...)`` extension boundary.
"""
from __future__ import annotations

from types import MappingProxyType

from src.runtime.ir import (
    IEC_TYPES, REAL_TYPES, SIGNED_INT_TYPES, UNSIGNED_INT_TYPES, StdSig,
)


_ABS_TYPES = SIGNED_INT_TYPES | UNSIGNED_INT_TYPES | REAL_TYPES


def _minimum(*values):
    return min(values)


def _maximum(*values):
    return max(values)


def _limit(minimum, value, maximum):
    # CODESYS definition: MIN(MAX(IN, Min), Max).
    return min(max(value, minimum), maximum)


_DEFAULT_STANDARD_FUNCTIONS = MappingProxyType({
    "ABS": abs,
    "LIMIT": _limit,
    "MAX": _maximum,
    "MIN": _minimum,
})


def default_standard_functions():
    """Return a fresh mutable mapping backed by immutable catalogue metadata.

    ``Executor`` already copies injected mappings.  Returning another fresh
    dictionary here additionally guarantees that one runtime assembly or test
    cannot add/remove a name from the process-wide defaults.
    """

    return dict(_DEFAULT_STANDARD_FUNCTIONS)


def standard_signature_error(name, sig):
    """Return a stable error for a known builtin signature, else ``None``.

    Unknown names intentionally return ``None`` so the historical explicit
    injection boundary remains available.  Structural ``StdSig`` validation is
    owned by the Loader and precedes this semantic check.
    """

    if name not in _DEFAULT_STANDARD_FUNCTIONS:
        return None
    if not isinstance(sig, StdSig):
        return "standard function %s requires StdSig" % name

    params = sig.param_types
    result = sig.return_type
    if name == "ABS":
        if len(params) != 1:
            return "standard function ABS requires exactly one argument"
        if params[0] != result:
            return "standard function ABS argument and result types must match"
        if result not in _ABS_TYPES:
            return "standard function ABS requires a numeric basic type"
        return None

    if name in {"MIN", "MAX"}:
        if len(params) < 2:
            return "standard function %s requires at least two arguments" % name
        if result not in IEC_TYPES or any(param != result for param in params):
            return ("standard function %s arguments and result must have one "
                    "identical IEC type" % name)
        return None

    # LIMIT(Min, IN, Max), with all four type positions identical.
    if len(params) != 3:
        return "standard function LIMIT requires exactly three arguments"
    if result not in IEC_TYPES or any(param != result for param in params):
        return ("standard function LIMIT arguments and result must have one "
                "identical IEC type")
    return None
