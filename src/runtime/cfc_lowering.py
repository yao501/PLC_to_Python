"""CFC 定序视图到正式 typed IR 的保守 lowering（WP-068/WP-069）。"""
from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields

from src.runtime.cfc_model import CFCModel, load_cfc_model
from src.runtime.cfc_order import CFCOrderEdge, CFCOrderError, CFCOrderGraph, CFCOrderNode, resolve_execution_order
from src.runtime.descriptors.model import BlockSchema, Pin, RuntimeAdapter, _OutputAccessMap
from src.runtime.descriptors.registry import Registry
from src.runtime.ir import (
    BinOp, Binding, CallFb, CallFbInstance, CallFunc, CallStd, Const, Convert,
    IEC_TYPES, IOMap, InstanceDecl, Jmp, JmpIfFalse, Label, LoadConst, LoadPrev, LoadVar,
    POUDefinition, ProgramInstance, StackSlot, StdSig, StoreKey, StoreVar,
    Task, UnOp, VarDecl,
)
from src.runtime.loader import validate_task
from src.runtime.output_policy import _iec_value_error


@dataclass(frozen=True)
class CFCInputBinding:
    source_node_id: str | None
    source_key: str
    target_key: str
    iec_type: str
    feedback: bool = False


@dataclass(frozen=True)
class CFCNodeIR:
    node_id: str
    inputs: tuple = ()
    body: tuple = ()


@dataclass(frozen=True)
class CFCLoweringDiagnostic:
    code: str
    message: str
    node_id: str | None = None

    def sort_key(self) -> tuple[str, str, str]:
        return (self.code, self.node_id or "", self.message)


class CFCLoweringError(ValueError):
    def __init__(self, errors: tuple[CFCLoweringDiagnostic, ...]):
        self.errors = errors
        super().__init__("; ".join(f"{error.code}: {error.message}" for error in errors))


@dataclass(frozen=True)
class CFCLoweringResult:
    task: Task
    pou_name: str
    execution_order: tuple
    code: tuple


def _raise(errors: list[CFCLoweringDiagnostic]) -> None:
    if errors:
        raise CFCLoweringError(tuple(sorted(errors, key=CFCLoweringDiagnostic.sort_key)))


_UNCHANGED = object()
_SAFE_SCALAR_TYPES = (bool, int, float, str)
_CONFIG_LEAF_TYPES = (type(None), bool, int, float, str)
_CONFIG_CONTAINER_TYPES = (list, tuple, dict, set)
_CONFIG_MAX_DEPTH = 32
_CONFIG_MAX_NODES = 4096


def _prove_dataclass_shell(value, expected) -> bool:
    """Prove one exact dataclass instance has precisely its declared fields.

    This is deliberately the only instance-field proof in this module: the
    accepted key set is derived from ``dataclasses.fields`` rather than a
    parallel hand-maintained table.  Callers must make this proof before their
    first read of an untrusted dataclass field.
    """
    if type(value) is not expected:
        return False
    try:
        instance_dict = object.__getattribute__(value, "__dict__")
    except AttributeError:
        return False
    if type(instance_dict) is not dict:
        return False
    keys = tuple(instance_dict)
    if any(type(key) is not str for key in keys):
        return False
    return set(keys) == {field.name for field in dataclass_fields(expected)}


def _validate_cloneable_value(value, code: str, errors: list, *,
                              dict_keys_exact_str: bool = False) -> None:
    """验证用于声明配置的有界、无环、exact 内建值图。

    这只限定 ``initial`` / ``ctor_args`` / ``init_overrides`` 的复制安全，不定义
    IEC 值语义。每个根最多 4096 个 value/container 节点，容器路径最多 32 层；
    memo 允许 DAG 共享而 active path 拒绝循环。
    """
    active: set[int] = set()
    memo: set[int] = set()
    state = {"nodes": 0, "reported": False}

    def reject(message: str) -> None:
        if not state["reported"]:
            errors.append(CFCLoweringDiagnostic("INVALID_CONFIG_VALUE", message))
            state["reported"] = True

    def charge() -> bool:
        state["nodes"] += 1
        if state["nodes"] > _CONFIG_MAX_NODES:
            reject("config value exceeds the per-root node budget")
            return False
        return True

    def visit_leaf(item) -> bool:
        if not charge():
            return False
        if type(item) not in _CONFIG_LEAF_TYPES:
            reject("config value must use exact supported leaves or containers")
            return False
        return True

    def visit_dict_key(item) -> bool:
        if not charge():
            return False
        if dict_keys_exact_str:
            if type(item) is not str:
                reject("config dict keys must be exact str")
                return False
            return True
        if type(item) not in _CONFIG_LEAF_TYPES:
            reject("config value must use exact supported leaves or containers")
            return False
        return True

    def visit(item, depth: int) -> bool:
        # Every occurrence slot is charged before checking cycle/memo/shape.
        if not charge():
            return False
        item_type = type(item)
        if item_type in _CONFIG_LEAF_TYPES:
            return True
        if item_type not in _CONFIG_CONTAINER_TYPES:
            reject("config value must use exact supported leaves or containers")
            return False
        marker = id(item)
        if marker in active:
            reject("config containers must be acyclic")
            return False
        if marker in memo:
            return True
        if depth > _CONFIG_MAX_DEPTH:
            reject("config value exceeds the maximum container depth")
            return False
        active.add(marker)
        if item_type is list or item_type is tuple:
            for child in item:
                if not visit(child, depth + 1):
                    active.remove(marker)
                    return False
        elif item_type is dict:
            for key, child in item.items():
                if not visit_dict_key(key):
                    active.remove(marker)
                    return False
                if not visit(child, depth + 1):
                    active.remove(marker)
                    return False
        else:  # exact set: nested containers cannot be members, but verify leaves.
            for child in item:
                if not visit_leaf(child):
                    active.remove(marker)
                    return False
        active.remove(marker)
        memo.add(marker)
        return True

    visit(value, 1)


def _clone_container_value(value, memo=None):
    """重建已由 :func:`_validate_cloneable_value` 放行的内建容器。

    这不是 ``deepcopy``：调用方配置只会在已验证的 exact 值合同内重建，memo
    保留合法 DAG 的内部共享；source/policy/dependency/callable 等 opaque 边界不经
    本函数，仍保持身份。
    """
    if memo is None:
        memo = {}
    value_type = type(value)
    if value_type in _CONFIG_LEAF_TYPES:
        return value
    marker = id(value)
    if marker in memo:
        return memo[marker]
    if value_type is list:
        cloned = []
        memo[marker] = cloned
        cloned.extend(_clone_container_value(item, memo) for item in value)
        return cloned
    if value_type is tuple:
        cloned = tuple(_clone_container_value(item, memo) for item in value)
        memo[marker] = cloned
        return cloned
    if value_type is dict:
        cloned = {}
        memo[marker] = cloned
        for key, item in value.items():
            cloned[key] = _clone_container_value(item, memo)
        return cloned
    if value_type is set:
        cloned = set(value)
        memo[marker] = cloned
        return cloned
    raise AssertionError("unvalidated config value reached clone")


def _clone_var_decl(decl: VarDecl) -> VarDecl:
    return VarDecl(decl.name, decl.iec_type, _clone_container_value(decl.initial),
                   decl.retain, decl.persistent, decl.section)


def _clone_instance_decl(inst: InstanceDecl) -> InstanceDecl:
    return InstanceDecl(
        inst.name, inst.block_type, inst.kind,
        _clone_container_value(inst.ctor_args),
        _clone_container_value(inst.init_overrides),
        _clone_container_value(inst.retain),
    )


def _clone_pou(pou: POUDefinition, *, code=_UNCHANGED, source=_UNCHANGED) -> POUDefinition:
    if code is _UNCHANGED:
        code = None if pou.code is None else list(pou.code)
    if source is _UNCHANGED:
        source = pou.source
    return POUDefinition(
        pou.name, pou.pou_kind, pou.language,
        [_clone_var_decl(decl) for decl in pou.interface],
        [_clone_var_decl(decl) for decl in pou.locals],
        [_clone_instance_decl(inst) for inst in pou.instances],
        pou.return_type, source, code,
    )


def _clone_task_with_pou(task: Task, pou_name: str, *, code=_UNCHANGED,
                         source=_UNCHANGED) -> Task:
    """逐层重建结果 Task；只保留 source/policy 等明确 opaque leaf 的身份。"""
    pou_lib = {
        name: _clone_pou(
            pou,
            code=code if name == pou_name else _UNCHANGED,
            source=source if name == pou_name else _UNCHANGED,
        )
        for name, pou in task.pou_lib.items()
    }
    return Task(
        task.cycle_ms,
        [ProgramInstance(program.definition, program.store_prefix)
         for program in task.programs],
        [_clone_var_decl(decl) for decl in task.gvl],
        [IOMap(io.var, io.channel, io.direction, io.policy) for io in task.io_map],
        pou_lib,
    )


def _clone_task_with_code(task: Task, pou_name: str, code: tuple) -> Task:
    return _clone_task_with_pou(task, pou_name, code=list(code))


# ---------------------------------------------------------------------------
# 完整、零观察的输入图预检（WP-079 Round 4）
#
# 连续三轮返修的共同根因是把「部分容器/字段预检 + 直接调用冻结 Loader」当成
# 完整的不信任输入边界；但冻结 Loader 的诊断会 ``%r`` 格式化、``==`` / ``!=`` /
# ``in`` 比较或哈希未验证字段，从而观察恶意对象的 ``__repr__`` / ``__eq__`` /
# ``__hash__``，甚至让自定义 ``BaseException`` 逃逸。本节以**单一、完整、先于任何
# clone / ``pou_lib`` 索引或复制 / ``validate_task``** 的递归验证器收口：对整张
# Task 图（``cycle_ms`` 标量、``programs`` / ``gvl`` / ``io_map`` / ``pou_lib`` 容器
# 与其元素、全部 ``pou_lib`` 值、POU 字段/声明/实例、以及 POU code 与 body 指令的
# 字段）只做 ``type(x) is T`` 判定，绝不比较、格式化、哈希或迭代不可信对象。任一
# 非法结构记录固定文本、安全路径的稳定 ``CFCLoweringError`` 诊断。通过后整张图均为
# exact 类型，冻结 Loader 只会对「类型合法但语义非法」的 IR 抛已声明透传的
# ``IRValidationError``，不再观察恶意对象。
# ---------------------------------------------------------------------------

#: Task 顶层容器字段与其 exact 类型（在任何索引/复制/迭代之前逐项校验）。
_TASK_CONTAINERS: tuple = (
    ("programs", list),
    ("gvl", list),
    ("io_map", list),
    ("pou_lib", dict),
)

#: 每种正式 IR 指令必须为 exact ``str`` 的字段；嵌套的 StdSig / Binding 另行递归。
_INSTR_STR_FIELDS: dict = {
    LoadVar: ("key", "type"),
    LoadPrev: ("key", "type"),
    StoreVar: ("key", "type"),
    LoadConst: ("type",),
    BinOp: ("op", "type"),
    UnOp: ("op", "type"),
    Convert: ("from_type", "to_type"),
    CallStd: ("name",),
    CallFb: ("instance",),
    CallFunc: ("name", "ret_type"),
    CallFbInstance: ("instance_path",),
    Jmp: ("label",),
    JmpIfFalse: ("label",),
    Label: ("id",),
}


def _validate_var_decl(decl, code: str, errors: list) -> None:
    if not _prove_dataclass_shell(decl, VarDecl):
        errors.append(CFCLoweringDiagnostic(code, "declaration must be an exact VarDecl"))
        return
    # ``name`` must be a *non-empty* exact str.  The frozen Loader's
    # ``_check_var_decl`` reprs the whole VarDecl on the ``not decl.name`` branch
    # ("无名/非法名变量声明：%r"), which would observe the unchecked Any/bool
    # sub-fields (``initial`` / ``retain`` / ``persistent``) and could leak a
    # malicious ``__repr__`` / ``BaseException``.  Requiring a non-empty name
    # keeps that repr path unreachable so those fields are never observed; the
    # ``type() is str`` guard short-circuits before ``not decl.name`` so a
    # non-str name is never truth-tested either.
    if type(decl.name) is not str or not decl.name:
        errors.append(CFCLoweringDiagnostic(
            code, "VarDecl.name must be a non-empty exact str"))
    for field_name in ("iec_type", "section"):
        if type(getattr(decl, field_name)) is not str:
            errors.append(CFCLoweringDiagnostic(
                code, "VarDecl." + field_name + " must be an exact str"))
    for field_name in ("retain", "persistent"):
        if type(getattr(decl, field_name)) is not bool:
            errors.append(CFCLoweringDiagnostic(
                code, "VarDecl." + field_name + " must be an exact bool"))
    _validate_cloneable_value(decl.initial, code, errors)


def _validate_instance_decl(inst, code: str, errors: list) -> None:
    if not _prove_dataclass_shell(inst, InstanceDecl):
        errors.append(CFCLoweringDiagnostic(code, "instance must be an exact InstanceDecl"))
        return
    for field_name in ("name", "block_type", "kind"):
        if type(getattr(inst, field_name)) is not str:
            errors.append(CFCLoweringDiagnostic(
                code, "InstanceDecl." + field_name + " must be an exact str"))
    for field_name in ("ctor_args", "init_overrides"):
        container = getattr(inst, field_name)
        if type(container) is not dict:
            errors.append(CFCLoweringDiagnostic(
                code, "InstanceDecl." + field_name + " must be an exact dict"))
            continue
        # Each whole constructor mapping is one independently bounded root.
        _validate_cloneable_value(container, code, errors, dict_keys_exact_str=True)
    if type(inst.retain) is not set:
        errors.append(CFCLoweringDiagnostic(code, "InstanceDecl.retain must be an exact set"))
    else:
        for name in inst.retain:
            if type(name) is not str:
                errors.append(CFCLoweringDiagnostic(
                    code, "InstanceDecl.retain members must be exact str"))


def _validate_program(prog, errors: list) -> None:
    if not _prove_dataclass_shell(prog, ProgramInstance):
        errors.append(CFCLoweringDiagnostic(
            "INVALID_PROGRAM", "programs item must be an exact ProgramInstance"))
        return
    for field_name in ("definition", "store_prefix"):
        if type(getattr(prog, field_name)) is not str:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_PROGRAM", "ProgramInstance." + field_name + " must be an exact str"))


def _validate_io_map(io, errors: list) -> None:
    if not _prove_dataclass_shell(io, IOMap):
        errors.append(CFCLoweringDiagnostic("INVALID_IO_MAP", "io_map item must be an exact IOMap"))
        return
    for field_name in ("var", "channel", "direction"):
        if type(getattr(io, field_name)) is not str:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_IO_MAP", "IOMap." + field_name + " must be an exact str"))


def _validate_std_sig(sig, code: str, errors: list, node_id) -> None:
    if not _prove_dataclass_shell(sig, StdSig):
        errors.append(CFCLoweringDiagnostic(code, "CallStd sig must be an exact StdSig", node_id))
        return
    if type(sig.return_type) is not str:
        errors.append(CFCLoweringDiagnostic(code, "StdSig.return_type must be an exact str", node_id))
    if type(sig.param_types) is not tuple:
        errors.append(CFCLoweringDiagnostic(code, "StdSig.param_types must be an exact tuple", node_id))
        return
    for param in sig.param_types:
        if type(param) is not str:
            errors.append(CFCLoweringDiagnostic(
                code, "StdSig.param_types item must be an exact str", node_id))


def _validate_ir_bindings(bindings, code: str, errors: list, node_id) -> None:
    if type(bindings) is not tuple:
        errors.append(CFCLoweringDiagnostic(code, "call bindings must be an exact tuple", node_id))
        return
    for binding in bindings:
        if not _prove_dataclass_shell(binding, Binding):
            errors.append(CFCLoweringDiagnostic(code, "call binding must be an exact Binding", node_id))
            continue
        for field_name in ("formal", "mode", "type"):
            if type(getattr(binding, field_name)) is not str:
                errors.append(CFCLoweringDiagnostic(
                    code, "Binding." + field_name + " must be an exact str", node_id))
        actual = binding.actual
        actual_type = type(actual)
        if actual_type is StoreKey:
            if not _prove_dataclass_shell(actual, StoreKey):
                errors.append(CFCLoweringDiagnostic(code, "Binding.actual must be an exact StoreKey/StackSlot/Const", node_id))
                continue
            if type(actual.key) is not str:
                errors.append(CFCLoweringDiagnostic(code, "StoreKey.key must be an exact str", node_id))
        elif actual_type is StackSlot:
            if not _prove_dataclass_shell(actual, StackSlot):
                errors.append(CFCLoweringDiagnostic(code, "Binding.actual must be an exact StoreKey/StackSlot/Const", node_id))
                continue
            if type(actual.index) is not int or type(actual.index) is bool:
                errors.append(CFCLoweringDiagnostic(code, "StackSlot.index must be an exact int", node_id))
            if type(actual.writable) is not bool:
                errors.append(CFCLoweringDiagnostic(code, "StackSlot.writable must be an exact bool", node_id))
        elif actual_type is Const:
            if not _prove_dataclass_shell(actual, Const):
                errors.append(CFCLoweringDiagnostic(code, "Binding.actual must be an exact StoreKey/StackSlot/Const", node_id))
                continue
            if type(actual.type) is not str:
                errors.append(CFCLoweringDiagnostic(code, "Const.type must be an exact str", node_id))
            _validate_literal_value(actual.value, actual.type, code, errors, node_id)
        else:
            errors.append(CFCLoweringDiagnostic(
                code, "Binding.actual must be an exact StoreKey/StackSlot/Const", node_id))


def _validate_instruction(ins, code: str, errors: list, node_id=None) -> None:
    """对单条正式 IR 指令做 exact-type 字段校验（零观察）。

    ``type(ins)`` 不在指令白名单时报告为非法指令且**不访问任何字段**；否则按声明
    逐字段 ``type() is str`` 校验，并对 CallStd / CallFunc / CallFbInstance 递归校验
    其嵌套 StdSig / Binding。绝不 ``%r`` 或比较指令字段。
    """
    instr_type = type(ins)
    fields = _INSTR_STR_FIELDS.get(instr_type)
    if fields is None:
        errors.append(CFCLoweringDiagnostic(
            code, "body item must be an exact formal IR instruction", node_id))
        return
    if not _prove_dataclass_shell(ins, instr_type):
        errors.append(CFCLoweringDiagnostic(
            code, "body item must be an exact formal IR instruction", node_id))
        return
    for field_name in fields:
        if type(getattr(ins, field_name)) is not str:
            errors.append(CFCLoweringDiagnostic(
                code, "instruction " + field_name + " must be an exact str", node_id))
    if instr_type is LoadConst:
        _validate_literal_value(ins.value, ins.type, code, errors, node_id)
    elif instr_type is CallStd:
        _validate_std_sig(ins.sig, code, errors, node_id)
    elif instr_type is CallFunc or instr_type is CallFbInstance:
        _validate_ir_bindings(ins.bindings, code, errors, node_id)


def _validate_literal_value(value, iec_type, code: str, errors: list, node_id) -> None:
    """统一收紧 ``LoadConst`` 与 ``Binding.actual=Const`` 的值入口。

    先以 ``type() is`` 拒绝子类/任意对象，随后仅把可信 built-in scalar 交给
    OutputPolicy 已有的 IEC 结构、范围及有限性口径。诊断固定，不字符串化 value。
    IEC 类型本身非法时留给冻结 Loader 沿用既有类型诊断。
    """
    if type(value) not in _SAFE_SCALAR_TYPES:
        errors.append(CFCLoweringDiagnostic(
            "INVALID_LITERAL_VALUE",
            "literal value must be an exact built-in scalar valid for its IEC type",
            node_id))
        return
    if (type(iec_type) is str and iec_type in IEC_TYPES and
            _iec_value_error(iec_type, value) is not None):
        errors.append(CFCLoweringDiagnostic(
            "INVALID_LITERAL_VALUE",
            "literal value must be an exact built-in scalar valid for its IEC type",
            node_id))


def _validate_pou(pou, code: str, errors: list) -> None:
    """校验一个 exact ``POUDefinition`` 的全部将被 Loader 观察的字段（零观察）。

    ``pou`` 已由调用方确认为 exact ``POUDefinition``，故 ``getattr`` 不触发自定义
    属性协议。逐项按 exact 类型校验标识字段、声明容器与其元素、以及（若已 lower）
    code 指令字段——``interface`` / ``locals`` / ``instances`` 会被 ``_clone_pou`` 以
    ``list(...)`` 迭代克隆，非 exact list（如 tuple 静默归一化或不可迭代对象泄漏
    TypeError）必须先失败关闭。
    """
    if not _prove_dataclass_shell(pou, POUDefinition):
        errors.append(CFCLoweringDiagnostic(code, "task.pou_lib value must be an exact POUDefinition"))
        return
    for field_name in ("name", "pou_kind", "language"):
        if type(getattr(pou, field_name)) is not str:
            errors.append(CFCLoweringDiagnostic(code, "POU " + field_name + " must be an exact str"))
    if pou.return_type is not None and type(pou.return_type) is not str:
        errors.append(CFCLoweringDiagnostic(code, "POU return_type must be None or an exact str"))
    for field_name in ("interface", "locals"):
        container = getattr(pou, field_name)
        if type(container) is not list:
            errors.append(CFCLoweringDiagnostic(code, "POU " + field_name + " must be an exact list"))
        else:
            for decl in container:
                _validate_var_decl(decl, code, errors)
    if type(pou.instances) is not list:
        errors.append(CFCLoweringDiagnostic(code, "POU instances must be an exact list"))
    else:
        for inst in pou.instances:
            _validate_instance_decl(inst, code, errors)
    if pou.code is not None:
        if type(pou.code) is not list:
            errors.append(CFCLoweringDiagnostic(code, "POU code must be None or an exact list"))
        else:
            for ins in pou.code:
                _validate_instruction(ins, code, errors)


def _validate_task_graph(task, pou_name, errors: list):
    """整张 Task 图的单一、完整、零观察 exact-type 预检。

    在任何 clone、``pou_lib`` 索引 / 复制或 :func:`validate_task` 之前调用；返回
    可安全索引的目标 ``POUDefinition`` 或 ``None``。全程只做 ``type(x) is T`` 判定，
    绝不观察不可信对象的 ``__repr__`` / ``__eq__`` / ``__ne__`` / ``__hash__`` /
    ``__bool__``，也不迭代非 exact 容器；``pou_lib`` 的键先零观察校验为 exact str
    后才允许索引，恶意同 hash 键不会在 ``.get`` 时逃逸。目标 POU 的字段错误报告为
    ``INVALID_TARGET_POU``，其它 ``pou_lib`` 值报告为 ``INVALID_POU_LIB_VALUE``。
    """
    pou_name_ok = type(pou_name) is str and bool(pou_name)
    if not pou_name_ok:
        errors.append(CFCLoweringDiagnostic("INVALID_POU_NAME", "pou_name must be a non-empty exact str"))
    if not _prove_dataclass_shell(task, Task):
        errors.append(CFCLoweringDiagnostic("INVALID_TASK", "task must be an exact Task"))
        return None
    if type(task.cycle_ms) is not int or type(task.cycle_ms) is bool:
        errors.append(CFCLoweringDiagnostic("INVALID_TASK_CYCLE", "task.cycle_ms must be an exact int"))
    pou_lib_ok = True
    for field_name, expected in _TASK_CONTAINERS:
        if type(getattr(task, field_name)) is not expected:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_TASK_CONTAINER",
                "task." + field_name + " must be an exact " + expected.__name__))
            if field_name == "pou_lib":
                pou_lib_ok = False
    if type(task.programs) is list:
        for prog in task.programs:
            _validate_program(prog, errors)
    if type(task.gvl) is list:
        for decl in task.gvl:
            _validate_var_decl(decl, "INVALID_GVL", errors)
    if type(task.io_map) is list:
        for io in task.io_map:
            _validate_io_map(io, errors)
    if pou_lib_ok:
        # 先零观察校验每个键为 exact str（只迭代已存储键，不比较/哈希），任一非 str
        # 键失败关闭后不再索引 pou_lib，避免同 hash 恶意键在 `.get` 时被观察。
        keys_ok = True
        for key in task.pou_lib:
            if type(key) is not str:
                keys_ok = False
        if not keys_ok:
            errors.append(CFCLoweringDiagnostic("INVALID_POU_LIB_KEY", "task.pou_lib keys must be exact str"))
            pou_lib_ok = False
    if not pou_lib_ok:
        return None
    for key, value in task.pou_lib.items():
        entry_code = "INVALID_TARGET_POU" if (pou_name_ok and key == pou_name) else "INVALID_POU_LIB_VALUE"
        if type(value) is not POUDefinition:
            errors.append(CFCLoweringDiagnostic(entry_code, "task.pou_lib value must be an exact POUDefinition"))
        else:
            _validate_pou(value, entry_code, errors)
    if not pou_name_ok:
        return None
    if pou_name not in task.pou_lib:
        errors.append(CFCLoweringDiagnostic("INVALID_TARGET_POU", "target POU name is absent from task.pou_lib"))
        return None
    target = task.pou_lib.get(pou_name)
    if type(target) is not POUDefinition:
        return None  # 已在上面的 pou_lib 值循环记录 INVALID_TARGET_POU
    return target


def _validate_direct_lowering_shells(graph, nodes, errors: list) -> bool:
    """Prove every caller-supplied CFC/order dataclass shell before field reads."""
    valid = True
    if not _prove_dataclass_shell(graph, CFCOrderGraph):
        errors.append(CFCLoweringDiagnostic("INVALID_GRAPH", "graph must be an exact CFCOrderGraph"))
        return False
    for name in ("carrier", "execution_order_mode", "order_source"):
        if type(getattr(graph, name)) is not str:
            errors.append(CFCLoweringDiagnostic("INVALID_GRAPH", "graph " + name + " must be an exact str"))
            valid = False
    for name in ("nodes", "edges"):
        if type(getattr(graph, name)) is not tuple:
            errors.append(CFCLoweringDiagnostic("INVALID_GRAPH_CONTAINER", "graph " + name + " must be an exact tuple"))
            valid = False
    if valid:
        for node in graph.nodes:
            if not _prove_dataclass_shell(node, CFCOrderNode):
                errors.append(CFCLoweringDiagnostic("INVALID_GRAPH_NODE", "graph node must be an exact CFCOrderNode"))
                valid = False
                continue
            if (type(node.node_id) is not str or
                    (node.execution_order_id is not None and
                     (type(node.execution_order_id) is not int or type(node.execution_order_id) is bool)) or
                    type(node.feedback_marker) is not bool):
                errors.append(CFCLoweringDiagnostic("INVALID_GRAPH_NODE", "graph node fields must have exact built-in types"))
                valid = False
        for edge in graph.edges:
            if not _prove_dataclass_shell(edge, CFCOrderEdge):
                errors.append(CFCLoweringDiagnostic("INVALID_GRAPH_EDGE", "graph edge must be an exact CFCOrderEdge"))
                valid = False
                continue
            if type(edge.source) is not str or type(edge.target) is not str:
                errors.append(CFCLoweringDiagnostic("INVALID_GRAPH_EDGE", "graph edge must have exact str endpoints"))
                valid = False
    if type(nodes) is not tuple:
        errors.append(CFCLoweringDiagnostic("INVALID_NODES", "nodes must be an exact tuple"))
        return False
    for fragment in nodes:
        if not _prove_dataclass_shell(fragment, CFCNodeIR):
            errors.append(CFCLoweringDiagnostic("INVALID_FRAGMENT", "fragment must be an exact CFCNodeIR"))
            valid = False
            continue
        if type(fragment.node_id) is not str or type(fragment.inputs) is not tuple or type(fragment.body) is not tuple:
            errors.append(CFCLoweringDiagnostic("INVALID_FRAGMENT_CONTAINER", "fragment fields must use exact built-in containers"))
            valid = False
            continue
        for binding in fragment.inputs:
            if not _prove_dataclass_shell(binding, CFCInputBinding):
                errors.append(CFCLoweringDiagnostic("INVALID_BINDING", "input must be an exact CFCInputBinding", fragment.node_id))
                valid = False
                continue
            if (binding.source_node_id is not None and type(binding.source_node_id) is not str) or \
                    any(type(item) is not str for item in (binding.source_key, binding.target_key, binding.iec_type)) or \
                    type(binding.feedback) is not bool:
                errors.append(CFCLoweringDiagnostic("INVALID_BINDING", "binding fields must have exact built-in types", fragment.node_id))
                valid = False
    return valid


def _copy_registry_pin(pin):
    if not _prove_dataclass_shell(pin, Pin):
        return None
    name = object.__getattribute__(pin, "name")
    iec_type = object.__getattribute__(pin, "iec_type")
    kind = object.__getattribute__(pin, "kind")
    default = object.__getattribute__(pin, "default")
    omit_policy = object.__getattribute__(pin, "omit_policy")
    if (type(name) is not str or type(iec_type) is not str or type(kind) is not str or
            type(omit_policy) is not str or type(default) not in _CONFIG_LEAF_TYPES):
        return None
    return Pin(name, iec_type, kind, default, omit_policy)


def _copy_registry_schema(schema):
    """证明 exact Schema/Pin 纯数据字段后重建，不调用其 hook。"""
    if not _prove_dataclass_shell(schema, BlockSchema):
        return None
    fields = {
        name: object.__getattribute__(schema, name)
        for name in ("block_type", "inputs", "outputs", "inouts", "variant",
                     "descriptor_version", "state_vars", "retainable",
                     "init_overridable", "hmi_writable", "output_access")
    }
    if (type(fields["block_type"]) is not str or type(fields["variant"]) is not str or
            type(fields["descriptor_version"]) is not str):
        return None
    copied_pins = {}
    for name in ("inputs", "outputs", "inouts"):
        collection = fields[name]
        if type(collection) is not tuple:
            return None
        copied = []
        for pin in collection:
            copied_pin = _copy_registry_pin(pin)
            if copied_pin is None:
                return None
            copied.append(copied_pin)
        copied_pins[name] = tuple(copied)
    copied_sets = {}
    for name in ("state_vars", "retainable", "init_overridable", "hmi_writable"):
        collection = fields[name]
        if type(collection) is not frozenset or any(type(item) is not str for item in collection):
            return None
        copied_sets[name] = frozenset(collection)
    access = fields["output_access"]
    if type(access) is not _OutputAccessMap:
        return None
    try:
        object.__getattribute__(access, "__dict__")
    except AttributeError:
        pass
    else:
        return None
    try:
        pairs = object.__getattribute__(access, "_pairs")
    except AttributeError:
        return None
    if type(pairs) is not tuple:
        return None
    copied_access = {}
    seen_access_keys = set()
    for pair in pairs:
        if (type(pair) is not tuple or len(pair) != 2 or
                type(pair[0]) is not str or type(pair[1]) is not str or
                pair[0] in seen_access_keys):
            return None
        seen_access_keys.add(pair[0])
        copied_access[pair[0]] = pair[1]
    try:
        return BlockSchema(
            fields["block_type"], copied_pins["inputs"], copied_pins["outputs"],
            copied_pins["inouts"], fields["variant"], fields["descriptor_version"],
            copied_sets["state_vars"], copied_sets["retainable"],
            copied_sets["init_overridable"], copied_sets["hmi_writable"], copied_access)
    except Exception:  # all constructor inputs above are exact, inert data
        return None


def _copy_registry_adapter(adapter):
    """证明 RuntimeAdapter 的结构，保留 class/callable/dependency 身份。"""
    if not _prove_dataclass_shell(adapter, RuntimeAdapter):
        return None
    cls = object.__getattribute__(adapter, "cls")
    call_adapter = object.__getattribute__(adapter, "call_adapter")
    ctor_args = object.__getattribute__(adapter, "ctor_args")
    serializer = object.__getattribute__(adapter, "serializer")
    if (type(cls) is not type or not callable(call_adapter) or type(ctor_args) is not tuple or
            any(type(name) is not str or not name for name in ctor_args) or
            (serializer is not None and not callable(serializer))):
        return None
    try:
        return RuntimeAdapter(cls, call_adapter, ctor_args, serializer)
    except Exception:  # prechecked values; never invoke adapter/schema business hooks
        return None


def _validate_registry(registry, errors: list):
    """将调用方 Registry 证明并重建为可信 exact Registry。

    下游 Loader 只接收新 Registry；原 Registry 的解析方法、schema 方法和 adapter
    callable 都不在本编译入口执行。空 Registry 仍是合法值并保持既有 Loader 语义。
    """
    if registry is None:
        return None
    copied_entries = []
    invalid = type(registry) is not Registry
    if not invalid:
        try:
            registry_dict = object.__getattribute__(registry, "__dict__")
        except AttributeError:
            invalid = True
            registry_dict = None
        if not invalid:
            if type(registry_dict) is not dict:
                invalid = True
            else:
                registry_keys = tuple(registry_dict)
                if (any(type(key) is not str for key in registry_keys) or
                        set(registry_keys) != {"_entries"}):
                    invalid = True
        entries = registry_dict.get("_entries") if not invalid else None
    else:
        entries = None
    if not invalid and type(entries) is not dict:
        invalid = True
    if not invalid:
        for key, entry in entries.items():
            if (type(key) is not tuple or len(key) != 2 or type(key[0]) is not str or
                    type(key[1]) is not str or type(entry) is not tuple or len(entry) != 2):
                invalid = True
                continue
            schema = _copy_registry_schema(entry[0])
            adapter = _copy_registry_adapter(entry[1])
            if schema is None or adapter is None:
                invalid = True
                continue
            # The reconstructed schema's canonical key must agree with the source key.
            if schema.block_type != key[0] or schema.variant != key[1]:
                invalid = True
                continue
            copied_entries.append((schema, adapter))
    if invalid:
        errors.append(CFCLoweringDiagnostic(
            "INVALID_REGISTRY", "registry must be None or an exact safe Registry"))
        return None
    trusted = Registry()
    for schema, adapter in copied_entries:
        trusted.register(schema, adapter)
    return trusted


def _check_target_preconditions(target, graph, pou_name, errors: list) -> None:
    """校验目标 POU 是绑定该 graph 的 pending CFC POU（well-typed 比较，零观察）。

    ``target`` 已由 :func:`_validate_task_graph` 确认为 exact ``POUDefinition``，其
    ``name`` / ``language`` 也已按 exact str 校验；此处的 ``!=`` / ``is`` 只作用于
    已确认类型的字段（且 ``type() is str`` 前置短路），绝不观察不可信对象。
    """
    if target is None:
        return
    if (type(target.name) is not str or target.name != pou_name or
            type(target.language) is not str or target.language != "CFC" or
            target.source is not graph or target.code is not None):
        errors.append(CFCLoweringDiagnostic(
            "INVALID_TARGET_POU", "target POU must be a pending CFC POU bound to graph"))


def lower_cfc_task(graph, nodes, task, pou_name, registry=None) -> CFCLoweringResult:
    """Lower 一个已定序无环 CFC 的 current-input 绑定，并验证克隆后的 Task。

    ``CFCOrderError`` 与 ``IRValidationError`` 有意向调用方透传；本函数仅将其
    自己的结构门禁报告为 ``CFCLoweringError``。
    """
    errors: list[CFCLoweringDiagnostic] = []
    _validate_direct_lowering_shells(graph, nodes, errors)
    registry = _validate_registry(registry, errors)
    target = _validate_task_graph(task, pou_name, errors)
    _check_target_preconditions(target, graph, pou_name, errors)
    _raise(errors)
    order = resolve_execution_order(graph)
    fragments = nodes

    for graph_node in graph.nodes:
        if graph_node.feedback_marker is not False:
            errors.append(CFCLoweringDiagnostic("FEEDBACK_UNSUPPORTED", "feedback_marker must be exact False", graph_node.node_id))

    by_id: dict[str, CFCNodeIR] = {}
    for fragment in fragments:
        if type(fragment) is not CFCNodeIR:
            errors.append(CFCLoweringDiagnostic("INVALID_FRAGMENT", "fragment must be an exact CFCNodeIR"))
            continue
        if type(fragment.node_id) is not str or not fragment.node_id:
            errors.append(CFCLoweringDiagnostic("INVALID_FRAGMENT_ID", "fragment node_id must be a non-empty exact str"))
            continue
        if fragment.node_id in by_id:
            errors.append(CFCLoweringDiagnostic("DUPLICATE_FRAGMENT", "fragment node_id must be unique", fragment.node_id))
            continue
        by_id[fragment.node_id] = fragment
        if type(fragment.inputs) is not tuple or type(fragment.body) is not tuple:
            errors.append(CFCLoweringDiagnostic("INVALID_FRAGMENT_CONTAINER", "inputs and body must be exact tuples", fragment.node_id))

    graph_ids = {node.node_id for node in graph.nodes}
    for node_id in graph_ids - set(by_id):
        errors.append(CFCLoweringDiagnostic("MISSING_FRAGMENT", "graph node has no fragment", node_id))
    for node_id in set(by_id) - graph_ids:
        errors.append(CFCLoweringDiagnostic("UNKNOWN_FRAGMENT", "fragment node is absent from graph", node_id))

    dependency_pairs: set[tuple[str, str]] = set()
    for node_id, fragment in by_id.items():
        if type(fragment.inputs) is not tuple or type(fragment.body) is not tuple:
            continue
        seen_target_keys: set[str] = set()
        for binding in fragment.inputs:
            if type(binding) is not CFCInputBinding:
                errors.append(CFCLoweringDiagnostic("INVALID_BINDING", "input must be an exact CFCInputBinding", node_id))
                continue
            if (binding.source_node_id is not None and
                    (type(binding.source_node_id) is not str or not binding.source_node_id)):
                errors.append(CFCLoweringDiagnostic("INVALID_BINDING", "source_node_id must be None or a non-empty exact str", node_id))
            if any(type(value) is not str or not value for value in
                   (binding.source_key, binding.target_key, binding.iec_type)):
                errors.append(CFCLoweringDiagnostic("INVALID_BINDING", "binding keys and iec_type must be non-empty exact str", node_id))
            elif binding.target_key in seen_target_keys:
                errors.append(CFCLoweringDiagnostic("DUPLICATE_TARGET_BINDING", "one node input target may be bound only once", node_id))
            else:
                seen_target_keys.add(binding.target_key)
            if binding.feedback is not False:
                errors.append(CFCLoweringDiagnostic("FEEDBACK_UNSUPPORTED", "binding feedback must be exact False", node_id))
            if type(binding.source_node_id) is str and binding.source_node_id:
                dependency_pairs.add((binding.source_node_id, node_id))
        for instruction in fragment.body:
            if type(instruction) is LoadPrev:
                errors.append(CFCLoweringDiagnostic("FEEDBACK_UNSUPPORTED", "LoadPrev is deferred to WP-069", node_id))
            else:
                _validate_instruction(instruction, "INVALID_INSTRUCTION", errors, node_id)

    graph_pairs = {(edge.source, edge.target) for edge in graph.edges}
    if dependency_pairs != graph_pairs:
        errors.append(CFCLoweringDiagnostic("EDGE_BINDING_MISMATCH", "non-external binding dependencies must exactly equal graph edges"))
    # A current (LoadVar) edge only carries this cycle's value if its source
    # executes strictly before its target.  Explicit executionOrderId carriers
    # may reverse that; such an edge must be classified as feedback (LoadPrev)
    # or fail closed, never silently rely on a stale Store value.
    position = {node_id: index for index, node_id in enumerate(order)}
    for source, target in dependency_pairs:
        if source in position and target in position and position[source] >= position[target]:
            errors.append(CFCLoweringDiagnostic(
                "CURRENT_DEPENDENCY_ORDER",
                "current dependency source must execute before its target; "
                "reverse dependencies require explicit feedback lowering", target))
    _raise(errors)

    code: list = []
    for node_id in order:
        fragment = by_id[node_id]
        # Canonicalise by the (unique) target pin so input tuple order cannot
        # change the emitted IR; duplicate target pins were rejected above.
        ordered_inputs = sorted(
            fragment.inputs,
            key=lambda binding: (binding.target_key, binding.source_key, binding.iec_type),
        )
        for binding in ordered_inputs:
            code.extend((LoadVar(binding.source_key, binding.iec_type), StoreVar(binding.target_key, binding.iec_type)))
        code.extend(fragment.body)
    frozen_code = tuple(code)
    cloned = _clone_task_with_code(task, pou_name, frozen_code)
    validate_task(cloned, registry)
    return CFCLoweringResult(cloned, pou_name, tuple(order), frozen_code)


def lower_cfc_feedback_task(graph, nodes, task, pou_name, registry=None) -> CFCLoweringResult:
    """Lower 显式声明的 PLCopen feedback inputs；绝不自动推断反馈边。

    原图中的 feedback dependency 只用于证明环与追溯源模型；定序时先删除
    调用方逐 input 标记的 feedback pairs，再复用 :func:`lower_cfc_task` 的完整
    结构、连接与 Loader 门禁。最终代码只把这些 input 的 ``LoadVar`` 换为
    ``LoadPrev``，并再次验证绑定原图的 Task 副本。
    """
    errors: list[CFCLoweringDiagnostic] = []
    _validate_direct_lowering_shells(graph, nodes, errors)
    registry = _validate_registry(registry, errors)
    _raise(errors)
    if (type(graph.carrier) is not str or
            type(graph.execution_order_mode) is not str or
            type(graph.order_source) is not str or
            (graph.carrier, graph.execution_order_mode, graph.order_source) !=
            ("plcopen_xml", "explicit", "exported")):
        raise CFCLoweringError((CFCLoweringDiagnostic(
            "UNSUPPORTED_FEEDBACK_CARRIER",
            "feedback lowering requires plcopen_xml/explicit/exported"),))
    if type(graph.nodes) is not tuple or type(graph.edges) is not tuple:
        raise CFCLoweringError((CFCLoweringDiagnostic(
            "INVALID_GRAPH_CONTAINER", "graph nodes and edges must be exact tuples"),))
    if type(nodes) is not tuple:
        raise CFCLoweringError((CFCLoweringDiagnostic(
            "INVALID_NODES", "nodes must be an exact tuple"),))

    # Clone / pou_lib 索引 / validate_task 之前，先用单一完整的零观察验证器守住整张
    # Task 图（标量、容器元素、全部 pou_lib 值、POU 声明/实例、body 指令字段），避免
    # 类型混淆泄漏底层异常或让冻结 Loader 观察恶意对象。current 与 feedback 两入口共用。
    target = _validate_task_graph(task, pou_name, errors)
    _check_target_preconditions(target, graph, pou_name, errors)

    graph_ids: set[str] = set()
    order_ids: dict[str, int] = {}
    for node in graph.nodes:
        if type(node) is not CFCOrderNode:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_GRAPH_NODE", "graph node must be an exact CFCOrderNode"))
            continue
        if type(node.node_id) is not str or not node.node_id:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_GRAPH_NODE", "graph node_id must be a non-empty exact str"))
            continue
        if node.node_id in graph_ids:
            errors.append(CFCLoweringDiagnostic(
                "DUPLICATE_NODE", "graph node_id must be unique", node.node_id))
        graph_ids.add(node.node_id)
        order_ids[node.node_id] = node.execution_order_id
        # 真实 PLCopen XML 没有 feedback_marker：节点 marker 必须保持默认 False，
        # 反馈证据只来自调用方逐 input 的显式分类。任何 marker=True（伪造）或非
        # exact False 都失败关闭，绝不把它当作反馈来源，也不与 .export
        # IsFeedbackStart 等价映射。
        if node.feedback_marker is not False:
            errors.append(CFCLoweringDiagnostic(
                "FEEDBACK_MARKER_FORBIDDEN",
                "PLCopen nodes carry no feedback_marker; feedback comes only "
                "from explicit inputs, so feedback_marker must stay default False",
                node.node_id))

    edge_pairs: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if (type(edge) is not CFCOrderEdge or type(edge.source) is not str or
                not edge.source or type(edge.target) is not str or not edge.target):
            errors.append(CFCLoweringDiagnostic(
                "INVALID_GRAPH_EDGE", "graph edge must have exact non-empty str endpoints"))
            continue
        pair = (edge.source, edge.target)
        if pair in edge_pairs:
            errors.append(CFCLoweringDiagnostic(
                "DUPLICATE_EDGE", "graph edge pair must be unique"))
        edge_pairs.add(pair)
        if edge.source not in graph_ids or edge.target not in graph_ids:
            errors.append(CFCLoweringDiagnostic(
                "DANGLING_EDGE", "graph edge endpoint is absent"))

    fragments: dict[str, CFCNodeIR] = {}
    feedback_pairs: set[tuple[str, str]] = set()
    pair_modes: dict[tuple[str, str], set[bool]] = {}
    for fragment in nodes:
        if type(fragment) is not CFCNodeIR:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_FRAGMENT", "fragment must be an exact CFCNodeIR"))
            continue
        node_id = fragment.node_id
        if type(node_id) is not str or not node_id:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_FRAGMENT_ID", "fragment node_id must be a non-empty exact str"))
            continue
        if node_id in fragments:
            errors.append(CFCLoweringDiagnostic(
                "DUPLICATE_FRAGMENT", "fragment node_id must be unique", node_id))
            continue
        fragments[node_id] = fragment
        if type(fragment.inputs) is not tuple or type(fragment.body) is not tuple:
            errors.append(CFCLoweringDiagnostic(
                "INVALID_FRAGMENT_CONTAINER", "inputs and body must be exact tuples",
                node_id))
            continue
        for instruction in fragment.body:
            if type(instruction) is LoadPrev:
                errors.append(CFCLoweringDiagnostic(
                    "FEEDBACK_UNSUPPORTED", "body LoadPrev is forbidden", node_id))
            else:
                _validate_instruction(instruction, "INVALID_INSTRUCTION", errors, node_id)
        seen_target_keys: set[str] = set()
        for binding in fragment.inputs:
            if type(binding) is not CFCInputBinding:
                errors.append(CFCLoweringDiagnostic(
                    "INVALID_BINDING", "input must be an exact CFCInputBinding", node_id))
                continue
            if type(binding.target_key) is str and binding.target_key:
                if binding.target_key in seen_target_keys:
                    errors.append(CFCLoweringDiagnostic(
                        "DUPLICATE_TARGET_BINDING",
                        "one node input target may be bound only once", node_id))
                seen_target_keys.add(binding.target_key)
            if type(binding.feedback) is not bool:
                errors.append(CFCLoweringDiagnostic(
                    "INVALID_BINDING", "binding feedback must be an exact bool", node_id))
                continue
            if binding.feedback and (type(binding.source_node_id) is not str or
                                     not binding.source_node_id):
                errors.append(CFCLoweringDiagnostic(
                    "INVALID_FEEDBACK", "feedback requires a non-empty node source",
                    node_id))
                continue
            if type(binding.source_node_id) is str and binding.source_node_id:
                pair = (binding.source_node_id, node_id)
                pair_modes.setdefault(pair, set()).add(binding.feedback)
                if binding.feedback:
                    feedback_pairs.add(pair)

    if not feedback_pairs:
        errors.append(CFCLoweringDiagnostic(
            "MISSING_FEEDBACK", "at least one explicit feedback input is required"))
    if not feedback_pairs <= edge_pairs:
        errors.append(CFCLoweringDiagnostic(
            "FEEDBACK_EDGE_MISMATCH", "feedback pair must be a graph edge"))
    for pair, modes in pair_modes.items():
        if len(modes) > 1:
            errors.append(CFCLoweringDiagnostic(
                "MIXED_FEEDBACK_PAIR", "one edge cannot mix current and feedback inputs",
                pair[1]))
    _raise(errors)

    projection = CFCOrderGraph(
        tuple(CFCOrderNode(node.node_id, node.execution_order_id, False)
              for node in graph.nodes),
        tuple(edge for edge in graph.edges
              if (edge.source, edge.target) not in feedback_pairs),
        graph.carrier,
        graph.execution_order_mode,
        graph.order_source,
    )
    sanitized_nodes = tuple(
        CFCNodeIR(
            fragment.node_id,
            tuple(CFCInputBinding(
                None if binding.feedback else binding.source_node_id,
                binding.source_key,
                binding.target_key,
                binding.iec_type,
                False,
            ) for binding in fragment.inputs),
            fragment.body,
        )
        for fragment in nodes
    )
    # Order the feedback-free projection directly so the feedback-specific gates
    # below stay precise; ``lower_cfc_task`` then re-validates the sanitized
    # structure and enforces current-dependency ordering on the same projection.
    order = resolve_execution_order(projection)

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in graph_ids}
    for edge in projection.edges:
        adjacency[edge.source].append(edge.target)

    def reaches(start: str, target: str) -> bool:
        seen = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            if current == target:
                return True
            for next_node in adjacency[current]:
                if next_node not in seen:
                    seen.add(next_node)
                    pending.append(next_node)
        return False

    for source, target in feedback_pairs:
        if not reaches(target, source):
            errors.append(CFCLoweringDiagnostic(
                "NON_CYCLIC_FEEDBACK",
                "feedback pair does not close a current-dependency path", target))
        if order_ids[source] < order_ids[target]:
            errors.append(CFCLoweringDiagnostic(
                "FEEDBACK_ORDER",
                "feedback source order must not precede target order", target))
    _raise(errors)

    temporary = _clone_task_with_pou(task, pou_name, source=projection, code=None)
    lower_cfc_task(projection, sanitized_nodes, temporary, pou_name, registry)

    code: list = []
    for node_id in order:
        fragment = fragments[node_id]
        # CFC input connections are keyed by the target pin.  Canonicalising by
        # that key makes source tuple order irrelevant; duplicate target pins
        # were rejected above, so sorting cannot hide last-write-wins semantics.
        ordered_inputs = sorted(
            fragment.inputs,
            key=lambda binding: (
                binding.target_key,
                binding.source_node_id or "",
                binding.source_key,
                binding.iec_type,
                binding.feedback,
            ),
        )
        for binding in ordered_inputs:
            load_type = LoadPrev if binding.feedback else LoadVar
            code.extend((
                load_type(binding.source_key, binding.iec_type),
                StoreVar(binding.target_key, binding.iec_type),
            ))
        code.extend(fragment.body)
    frozen_code = tuple(code)
    cloned = _clone_task_with_code(task, pou_name, frozen_code)
    validate_task(cloned, registry)
    return CFCLoweringResult(cloned, pou_name, tuple(order), frozen_code)


# ---------------------------------------------------------------------------
# 阶段 2 安全内部编译入口（WP-20260809-085）
#
# :func:`compile_cfc_task` 是阶段 2 **唯一**的内部平台编译入口：把 exact
# ``cfc-model-v1`` payload 经冻结 ``load_cfc_model`` 物化成唯一不可变
# ``CFCModel``，再把模型 pins/connections **自动**派生为 ``CFCInputBinding``，
# 投影到现有定序 / lowering 内核，最终返回经 ``validate_task`` 验证的正式 typed IR
# ``Task``。它**不建立第二套模型、定序或 IR 语义**：模型物化复用冻结 Loader，定序
# 复用 :func:`resolve_execution_order`，绑定/发码复用 :func:`lower_cfc_task` /
# :func:`lower_cfc_feedback_task`，本入口只做「不信任入口结构门禁 + 连接自动接线 +
# 载体门禁路由」。调用方只提供每个节点的 typed-IR **body**（:class:`CFCNodeBody`）；
# 连接语义只有模型 pins/connections 一份真相。
#
# 身份边界（WP 精确验收 #1）：调用方 Task 的目标 POU 必须是 pending CFC POU、
# ``code is None``，且以对象身份绑定同一 ``payload``（``source is payload``）。冻结
# lowering 内核要求目标 POU 的 ``source`` **就是**它接收的 ``CFCOrderGraph``，因此
# 成功结果 Task 的目标 POU ``source`` 必然是派生的定序图；本入口据此保留 Loader
# 物化的唯一 ``CFCModel`` 为只读结果字段 :attr:`CFCCompileResult.model`（WP #1 括号
# 明示的「等价且不丢模型 provenance 的只读结果字段」路径），不修改冻结内核。
#
# 本入口全程零观察：只做 ``type(x) is T`` 判定与对象身份比较，绝不 ``%r`` 格式化、
# ``==`` / ``in`` 比较、哈希或真值测试不可信 payload / body / Task 对象。Task 结构复用
# :func:`_validate_task_graph`（已证零观察），payload 物化复用零观察的 Loader，body
# 指令字段复用 :func:`_validate_instruction`（经下沉内核）。任一步失败都在返回前失败
# 关闭，绝不暴露半编译 Task，原 payload / body / Task / POU 均零修改。
# ---------------------------------------------------------------------------

#: 唯一支持显式反馈（``read_mode=previous``）的冻结载体组合。
_FEEDBACK_CARRIER: tuple = ("plcopen_xml", "explicit", "exported")


@dataclass(frozen=True)
class CFCNodeBody:
    """调用方为单个模型节点提供的最小不可变 typed-IR body 描述。

    ``body`` 必须是 exact ``tuple`` 且只含现有正式 typed IR 指令（由下沉内核逐项
    校验，``LoadPrev`` 在 body 中被拒）；``node_id`` 必须与模型节点一一对应。
    """
    node_id: str
    body: tuple = ()


@dataclass(frozen=True)
class CFCCompileResult:
    """成功编译的不可变结果。

    ``model`` 为 Loader 物化的唯一 ``CFCModel``（provenance），``task`` 为经
    ``validate_task`` 验证的正式 typed IR Task，``execution_order`` / ``code`` 为
    冻结定序与发码结果。
    """
    model: CFCModel
    task: Task
    pou_name: str
    execution_order: tuple
    code: tuple


def _validate_compile_target(target, payload, errors: list) -> None:
    """校验目标 POU 是绑定该 payload 的 pending CFC POU（零观察，well-typed 比较）。

    ``target`` 已由 :func:`_validate_task_graph` 确认为 exact ``POUDefinition``；
    ``language`` 的 ``type() is str`` 前置短路使非 str 字段永不被真值/相等测试，
    ``source is payload`` 只做对象身份比较，绝不观察 payload。
    """
    if target is None:
        return
    if (type(target.language) is not str or target.language != "CFC" or
            target.source is not payload or target.code is not None):
        errors.append(CFCLoweringDiagnostic(
            "INVALID_TARGET_POU",
            "target POU must be a pending CFC POU bound to the payload"))


def _validate_bodies(bodies, errors: list) -> dict:
    """零观察校验 ``bodies`` 为 exact tuple、每项 exact ``CFCNodeBody``、``node_id``
    为非空 exact str、``body`` 为 exact tuple、且 ``node_id`` 无重复；返回 id→body 映射。
    """
    by_id: dict = {}
    if type(bodies) is not tuple:
        errors.append(CFCLoweringDiagnostic("INVALID_BODIES", "bodies must be an exact tuple"))
        return by_id
    for item in bodies:
        if not _prove_dataclass_shell(item, CFCNodeBody):
            errors.append(CFCLoweringDiagnostic("INVALID_BODY", "body must be an exact CFCNodeBody"))
            continue
        node_id = item.node_id
        if type(node_id) is not str or not node_id:
            errors.append(CFCLoweringDiagnostic("INVALID_BODY_ID", "body node_id must be a non-empty exact str"))
            continue
        if type(item.body) is not tuple:
            errors.append(CFCLoweringDiagnostic("INVALID_BODY_CONTAINER", "body must be an exact tuple", node_id))
            continue
        if node_id in by_id:
            errors.append(CFCLoweringDiagnostic("DUPLICATE_BODY", "body node_id must be unique", node_id))
            continue
        by_id[node_id] = item
    return by_id


def _bind_internal_task(task, pou_name: str, target, graph) -> Task:
    """构造绑定派生定序图的**新** Task，原 Task / POU 零修改（供冻结内核消费）。

    ``task`` 各容器与 ``target`` 声明容器已由 :func:`_validate_task_graph` 确认为
    exact list/dict，故 ``list(...)`` / ``dict(...)`` 复制安全且断开调用方别名。目标
    POU 以 ``source=graph`` / ``code=None`` 重建，使冻结内核的 ``source is graph``
    前置成立。
    """
    return _clone_task_with_pou(task, pou_name, source=graph, code=None)


def _model_feedback_graph(model: CFCModel) -> CFCOrderGraph:
    """构造反馈 lowering 所需的定序图：节点携载体序号/marker（``None`` 归一 False），
    边为**全部** connection 的节点级去重对（current 与 previous 都在内）。

    :meth:`CFCModel.to_order_graph` 只含 current 边，无法满足
    :func:`lower_cfc_feedback_task` 的 ``feedback_pairs <= edges`` 门禁；本函数只组装
    输入图，定序仍全部交给冻结 :func:`resolve_execution_order`（内部对去除反馈边后的
    投影排序），不引入第二套定序语义。
    """
    nodes = tuple(
        CFCOrderNode(
            node.node_id, node.execution_order_id,
            False if node.feedback_marker is None else node.feedback_marker,
        )
        for node in model.nodes
    )
    seen: set = set()
    edges: list = []
    for conn in model.connections:
        pair = (conn.source_node_id, conn.target_node_id)
        if pair not in seen:
            seen.add(pair)
            edges.append(CFCOrderEdge(conn.source_node_id, conn.target_node_id))
    edges.sort(key=lambda edge: (edge.source, edge.target))
    return CFCOrderGraph(
        nodes, tuple(edges), model.carrier, model.execution_order_mode, model.order_source)


def compile_cfc_task(payload, bodies, task, pou_name, registry=None) -> CFCCompileResult:
    """阶段 2 唯一安全内部编译入口：exact ``cfc-model-v1`` payload → 正式 typed IR Task。

    分层失败关闭：先零观察校验入口自身不信任结构（Task 图、目标 POU 身份绑定、body
    容器），再经冻结 ``load_cfc_model`` 物化模型（透传 ``CFCModelError``），再校验
    body 与模型节点一一对应及反馈载体门禁，最后经冻结 lowering 内核发码并
    ``validate_task``（透传 ``CFCOrderError`` / ``IRValidationError`` 及内核自身
    ``CFCLoweringError``）。入口自有结构错误聚合为稳定 ``CFCLoweringError``。
    """
    errors: list[CFCLoweringDiagnostic] = []

    # 阶段 A：零观察校验入口自身不信任结构（Task 图 + 目标身份 + body 容器）。
    registry = _validate_registry(registry, errors)
    target = _validate_task_graph(task, pou_name, errors)
    _validate_compile_target(target, payload, errors)
    bodies_by_id = _validate_bodies(bodies, errors)
    _raise(errors)

    # 阶段 B：经冻结 Loader 把 payload 物化成唯一可信模型（透传 CFCModelError）。
    model = load_cfc_model(payload)

    # 阶段 C：模型派生结构门禁——body 与节点一一对应 + 反馈载体门禁。
    node_ids = tuple(node.node_id for node in model.nodes)
    node_id_set = set(node_ids)
    body_ids = set(bodies_by_id)
    for node_id in sorted(node_id_set - body_ids):
        errors.append(CFCLoweringDiagnostic("MISSING_BODY", "model node has no body", node_id))
    for node_id in sorted(body_ids - node_id_set):
        errors.append(CFCLoweringDiagnostic("UNKNOWN_BODY", "body node is absent from model", node_id))
    has_feedback = any(conn.read_mode == "previous" for conn in model.connections)
    if has_feedback and (model.carrier, model.execution_order_mode, model.order_source) != _FEEDBACK_CARRIER:
        # 内核未支持 user-defined feedback；export_native/auto/reconstructed 亦不在此
        # 分支——两者都失败关闭，绝不静默改成 current、猜顺序或推断反馈。
        errors.append(CFCLoweringDiagnostic(
            "UNSUPPORTED_FEEDBACK_CARRIER",
            "previous (feedback) connections require plcopen_xml/explicit/exported"))
    _raise(errors)

    # 阶段 D：从模型 pins/connections 自动派生 CFCInputBinding，组装每节点 CFCNodeIR。
    pin_index: dict = {}
    for node in model.nodes:
        for pin in node.pins:
            pin_index[(node.node_id, pin.pin_id)] = pin
    inputs_by_node: dict = {node_id: [] for node_id in node_ids}
    for conn in model.connections:
        target_pin = pin_index[(conn.target_node_id, conn.target_pin_id)]
        source_pin = pin_index[(conn.source_node_id, conn.source_pin_id)]
        inputs_by_node[conn.target_node_id].append(CFCInputBinding(
            conn.source_node_id,
            source_pin.value_key,
            target_pin.value_key,
            target_pin.iec_type,
            conn.read_mode == "previous",
        ))
    fragments = tuple(
        CFCNodeIR(node.node_id, tuple(inputs_by_node[node.node_id]),
                  bodies_by_id[node.node_id].body)
        for node in model.nodes
    )

    # 阶段 E：绑定派生定序图的新内部 Task，委派冻结 lowering 内核（透传其诊断）。
    if has_feedback:
        graph = _model_feedback_graph(model)
        internal = _bind_internal_task(task, pou_name, target, graph)
        result = lower_cfc_feedback_task(graph, fragments, internal, pou_name, registry)
    else:
        graph = model.to_order_graph()
        internal = _bind_internal_task(task, pou_name, target, graph)
        result = lower_cfc_task(graph, fragments, internal, pou_name, registry)

    return CFCCompileResult(model, result.task, pou_name, result.execution_order, result.code)
