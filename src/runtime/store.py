"""正式运行时 Store 与实例状态布局（WP-20260714-003）。

本模块提供后续 IR 执行器可依赖的**最小、确定性**运行时内存底座：

- ``Store``：扁平变量空间（命名边界遵守 `IR_SPEC §7`），声明制——未声明
  键不得被静默创建；每键保存声明 IEC 类型与值；读写未知键、重复声明、
  类型不匹配抛专用异常。
- ``StoreSnapshot``：与当前 Store 隔离的只读快照，为后续 ``LOAD_PREV`` /
  ``ctx.prev`` 提供基础；快照生成后，当前 Store 的写入不影响快照。
- ``build_runtime_store(task)``：根据**已通过** ``validate_task()`` 的 Task
  建立运行时布局——PROGRAM 实例只创建一次、用户 FB 实例按声明路径递归
  展开且各自持久状态完全隔离、运行期调用不创建实例（本包不含执行器，
  该规则由"布局建立后 Store 拒绝新键"从机制上保证）。

诚实边界（不实现、不伪称）：

- 只维护类型元数据与 Python 侧结构性类型检查（值的 Python 类型须与声明
  IEC 类型的工程映射一致），**不实现** REAL32 量化、整数回绕、隐式提升
  或越界转换（IR_SPEC §5.3/§5.4，属后续数值层与真机裁决范围）。
- RETAIN/PERSISTENT 仅保留声明元数据（``retain_flags()`` 可查询），
  **不实现**持久化、恢复或下载语义（阶段 8，`IR_SPEC §9`）。
- 库块（kind="library"）内部状态与管脚分配依赖尚未建立的 L2 描述符
  注册表（COMPONENT_CONTRACT），本包**不猜测其管脚**：只在布局中登记
  库块实例路径与声明（``RuntimeLayout.library_instances``），不为其分配
  任何 Store 键，接入边界留待 L2 工作包。
- FUNCTION 无持久实例；VAR_TEMP 与 FUNCTION 调用局部变量不进持久
  Store（调用帧属下一个执行器工作包）。

**持久键格式（集中于单一 helper ``persistent_key()``，项目工程约定）**：
`IR_SPEC §7` 给出 GVL=``<var>``、实例引脚=``<instance>.<pin>``、POU 局部/
temp=``<pou>#<frame>.<var>``（面向调用帧），未显式给出 PROGRAM/FB **持久**
状态的扁平键格式。本包约定：持久状态键 = ``<实例全路径>.<变量名>``，即
``persistent_key(path, var)``，其中 PROGRAM 路径 = ``store_prefix``、FB 路径 =
父路径 + "." + 实例名（与 §3 ``FBInstance.path`` 和 §7 ``<instance>.<pin>``
的点分形态一致组合）。该约定不改变 §7 已定义的三类键，仅补足其未裁决处；
若后续评审另定格式，只需改此一处 helper。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from src.runtime.ir import (
    BIT_TYPES,
    IEC_TYPES,
    REAL_TYPES,
    SIGNED_INT_TYPES,
    UNSIGNED_INT_TYPES,
    FBInstance,
    InstanceDecl,
    POUDefinition,
    ProgramInstance,
    Task,
    VarDecl,
)
from src.runtime.loader import validate_task

# ---------------------------------------------------------------------------
# 专用异常
# ---------------------------------------------------------------------------


class StoreError(Exception):
    """Store/布局层错误基类。"""


class UnknownStoreKeyError(StoreError):
    """读/写未声明的键（未声明键不得被静默创建）。"""


class DuplicateStoreKeyError(StoreError):
    """重复声明同一键。"""


class StoreTypeError(StoreError):
    """声明类型非法，或值的 Python 类型与声明 IEC 类型不匹配。"""


class InstanceLayoutError(StoreError):
    """运行实例布局错误（如 init_overrides 指向不存在的变量）。"""


# ---------------------------------------------------------------------------
# IEC 类型 → Python 结构类型映射（工程映射，engineering 模式口径，IR_SPEC §8）
# ---------------------------------------------------------------------------

_INT_LIKE = SIGNED_INT_TYPES | UNSIGNED_INT_TYPES | BIT_TYPES | frozenset({"TIME"})

#: 各 IEC 类型的默认初值（未给 initial 时使用；工程口径，非数值语义实现）。
_DEFAULTS = {"BOOL": False, "REAL": 0.0, "LREAL": 0.0, "STRING": ""}


def _default_for(iec_type: str) -> Any:
    if iec_type in _INT_LIKE:
        return 0
    return _DEFAULTS[iec_type]


def check_value_type(iec_type: str, value: Any) -> bool:
    """值的 Python 类型是否与声明 IEC 类型的工程映射一致（不做任何转换）。

    映射（IR_SPEC §8 engineering 列）：BOOL→bool；整数族/位串/TIME→int
    （bool 除外）；REAL/LREAL→float（int 不自动放宽——赋值转换须经
    ``CONVERT`` 显式化，IR_SPEC §5.1）；STRING→str。
    """
    if iec_type == "BOOL":
        return isinstance(value, bool)
    if iec_type in _INT_LIKE:
        return isinstance(value, int) and not isinstance(value, bool)
    if iec_type in REAL_TYPES:
        return isinstance(value, float)
    if iec_type == "STRING":
        return isinstance(value, str)
    return False


# ---------------------------------------------------------------------------
# 键生成（单一 helper——持久键格式的唯一定义点）
# ---------------------------------------------------------------------------

def persistent_key(instance_path: str, var_name: str) -> str:
    """PROGRAM/FB 持久状态的扁平键 = ``<实例全路径>.<变量名>``。

    见模块 docstring"持久键格式"——这是项目工程约定的唯一落点，
    改格式只改这里。GVL 键不经本函数（GVL = 裸变量名，IR_SPEC §7）。
    """
    return "%s.%s" % (instance_path, var_name)


# ---------------------------------------------------------------------------
# Store 与快照
# ---------------------------------------------------------------------------

@dataclass
class _Cell:
    iec_type: str
    value: Any
    retain: bool = False
    persistent: bool = False


class StoreSnapshot:
    """只读快照。与源 Store 完全隔离：生成后源 Store 的写入不影响本对象。

    当前 IEC 类型集的值均映射为不可变 Python 标量（bool/int/float/str），
    故浅拷贝即完全隔离；若未来引入可变值类型，须在此升级拷贝策略。
    """

    __slots__ = ("_values", "_types")

    def __init__(self, values: dict, types: dict):
        self._values = values
        self._types = types

    def read(self, key: str) -> Any:
        try:
            return self._values[key]
        except KeyError:
            raise UnknownStoreKeyError("快照中不存在键 '%s'" % key) from None

    def declared_type(self, key: str) -> str:
        try:
            return self._types[key]
        except KeyError:
            raise UnknownStoreKeyError("快照中不存在键 '%s'" % key) from None

    def __contains__(self, key: str) -> bool:
        return key in self._values

    def keys(self):
        return tuple(self._values.keys())

    def as_dict(self) -> dict:
        """导出值的独立副本（修改返回值不影响快照）。"""
        return dict(self._values)


class Store:
    """扁平变量空间：声明制、带类型元数据、专用异常。"""

    def __init__(self):
        self._cells: dict = {}          # key -> _Cell

    # ---- 声明 ----
    def declare(self, key: str, iec_type: str, initial: Any = None,
                retain: bool = False, persistent: bool = False) -> None:
        if not key or not isinstance(key, str):
            raise StoreTypeError("非法键：%r" % (key,))
        if iec_type not in IEC_TYPES:
            raise StoreTypeError("键 '%s' 的 IEC 类型非法：%r" % (key, iec_type))
        if key in self._cells:
            raise DuplicateStoreKeyError("键 '%s' 重复声明" % key)
        value = _default_for(iec_type) if initial is None else initial
        if not check_value_type(iec_type, value):
            raise StoreTypeError(
                "键 '%s' 初值 %r 与声明类型 %s 不匹配（不做隐式转换，"
                "IR_SPEC §5.1）" % (key, value, iec_type)
            )
        self._cells[key] = _Cell(iec_type, value, retain, persistent)

    # ---- 读写 ----
    def read(self, key: str) -> Any:
        cell = self._cells.get(key)
        if cell is None:
            raise UnknownStoreKeyError("读未声明键 '%s'" % key)
        return cell.value

    def write(self, key: str, value: Any) -> None:
        cell = self._cells.get(key)
        if cell is None:
            raise UnknownStoreKeyError(
                "写未声明键 '%s'（未声明键不得被静默创建）" % key)
        if not check_value_type(cell.iec_type, value):
            raise StoreTypeError(
                "键 '%s' 写入值 %r 与声明类型 %s 不匹配（不做隐式转换）"
                % (key, value, cell.iec_type)
            )
        cell.value = value

    # ---- 元数据 ----
    def declared_type(self, key: str) -> str:
        cell = self._cells.get(key)
        if cell is None:
            raise UnknownStoreKeyError("查询未声明键 '%s'" % key)
        return cell.iec_type

    def retain_flags(self, key: str) -> tuple:
        """返回 (retain, persistent) 声明元数据；本包不实现任何恢复行为。"""
        cell = self._cells.get(key)
        if cell is None:
            raise UnknownStoreKeyError("查询未声明键 '%s'" % key)
        return (cell.retain, cell.persistent)

    def __contains__(self, key: str) -> bool:
        return key in self._cells

    def keys(self):
        return tuple(self._cells.keys())

    # ---- 快照 ----
    def snapshot(self) -> StoreSnapshot:
        """生成只读快照（供 ``ctx.prev`` / ``LOAD_PREV`` 基础使用）。

        提交时机由调用方（后续扫描引擎，ENGINE_SCAN_SPEC §3 第 5 步）
        决定，本包不实现 ``scan()``、不自动决定提交点。
        """
        return StoreSnapshot(
            {k: c.value for k, c in self._cells.items()},
            {k: c.iec_type for k, c in self._cells.items()},
        )


# ---------------------------------------------------------------------------
# 运行实例布局
# ---------------------------------------------------------------------------

#: 进入持久 Store 的变量区段（IR_SPEC §3）：VAR_IN_OUT 是引用别名、无自身
#: 存储；VAR_TEMP 每次进入 POU 清零、不跨周期，均不分配持久键。
_PERSISTENT_SECTIONS = ("VAR_INPUT", "VAR_OUTPUT", "VAR")


@dataclass
class RuntimeLayout:
    """装载期实例展开结果：Store + 实例登记表（不含任何执行能力）。"""
    store: Store
    programs: list = field(default_factory=list)            # list[ProgramInstance]
    fb_instances: list = field(default_factory=list)        # list[ir.FBInstance]
    library_instances: list = field(default_factory=list)   # list[(path, InstanceDecl)]

    def fb_paths(self):
        return tuple(fb.path for fb in self.fb_instances)


def build_runtime_store(task: Task) -> RuntimeLayout:
    """根据已通过静态校验的 Task 建立运行时内存布局。

    规则（IR_SPEC §3 实例化规则）：
    - 先执行 ``validate_task(task)``（防御性复验；失败则不建任何状态）；
    - GVL 变量以裸名声明（含 retain/persistent 元数据）；
    - 每个 ``ProgramInstance`` 只创建一次：其 VAR_INPUT/VAR_OUTPUT/VAR
      在 ``store_prefix`` 路径下分配持久键；
    - 定义体内的 user_fb 实例按路径**递归展开**，每实例一份独立持久
      状态；``init_overrides`` 覆盖对应变量的 ``initial``，键不存在即抛
      ``InstanceLayoutError``（不得静默丢失）；
    - library 实例只登记路径与声明，不分配键（L2 描述符边界）；
    - FUNCTION 不创建持久实例；VAR_TEMP / VAR_IN_OUT 不分配持久键。

    布局建立后，Store 拒绝一切未声明键的读写——"运行期调用不得创建
    实例内存"由此从机制上保证（执行器属后续工作包）。
    """
    validate_task(task)
    store = Store()
    layout = RuntimeLayout(store=store)

    # 1) GVL
    for decl in task.gvl:
        store.declare(decl.name, decl.iec_type, decl.initial,
                      retain=decl.retain, persistent=decl.persistent)

    # 2) PROGRAM 实例（只创建一次；store_prefix 唯一性已由 validate_task 保证）
    for prog in task.programs:
        definition = task.pou_lib[prog.definition]
        _allocate_pou_state(store, definition, prog.store_prefix, overrides=None)
        layout.programs.append(prog)
        _expand_instances(task, layout, definition, prog.store_prefix)

    return layout


def _allocate_pou_state(store: Store, definition: POUDefinition,
                        path: str, overrides: Optional[Mapping]) -> None:
    """为一个 PROGRAM/FB 实例分配持久变量；FUNCTION 不得到达此处。"""
    if definition.pou_kind == "FUNCTION":
        raise InstanceLayoutError(
            "FUNCTION '%s' 无实例内存，不能分配持久状态（IR_SPEC §3）"
            % definition.name)
    overrides = dict(overrides) if overrides else {}
    persistent_vars = [
        d for d in list(definition.interface) + list(definition.locals)
        if isinstance(d, VarDecl) and d.section in _PERSISTENT_SECTIONS
    ]
    persistent_names = {d.name for d in persistent_vars}
    unknown = set(overrides) - persistent_names
    if unknown:
        raise InstanceLayoutError(
            "实例 '%s'（定义 %s）的 init_overrides 指向不存在的持久变量：%s"
            "（不得静默丢失）" % (path, definition.name, sorted(unknown)))
    for d in persistent_vars:
        initial = overrides[d.name] if d.name in overrides else d.initial
        store.declare(persistent_key(path, d.name), d.iec_type, initial,
                      retain=d.retain, persistent=d.persistent)


def _expand_instances(task: Task, layout: RuntimeLayout,
                      definition: POUDefinition, parent_path: str) -> None:
    """递归展开定义体内声明的 FB 实例（validate_task 已排除循环）。"""
    for inst in definition.instances:
        if not isinstance(inst, InstanceDecl):
            continue
        path = "%s.%s" % (parent_path, inst.name)
        if inst.kind == "library":
            # L2 描述符注册表未建立：不猜测管脚，不分配键，仅登记边界。
            layout.library_instances.append((path, inst))
            continue
        sub_def = task.pou_lib[inst.block_type]
        _allocate_pou_state(store=layout.store, definition=sub_def,
                            path=path, overrides=inst.init_overrides)
        layout.fb_instances.append(
            FBInstance(definition=sub_def.name, path=path,
                       retain=set(inst.retain)))
        _expand_instances(task, layout, sub_def, path)
