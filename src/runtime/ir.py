"""正式运行时 IR 内存模型（L3，`docs/IR_SPEC.md` v2.2.2）。

本模块只定义**语言无关、全类型化**的可执行 IR 数据模型与声明模型；
不含执行器、Store、扫描循环、lowering 或任何数值语义实现。

设计约定（对应 WP-20260713-002 实施范围）：

- 指令、绑定、引用等**值对象**一律 ``@dataclass(frozen=True)``，支持稳定的
  等值比较（``==``），且天然避免可变默认值；其内部集合用 ``tuple`` 编码。
- 声明/容器对象（``VarDecl``/``POUDefinition``/``Task`` 等）的可变集合字段
  一律使用 ``field(default_factory=...)``。
- ``IOMap.policy`` 仅作 OutputPolicy 的类型边界占位（``Any``），其行为属
  `ENGINE_SCAN_SPEC §4`，不在本包实现。
- ``POUDefinition.source`` 仅作前端源模型占位（``Any``）；本包不定义
  ``STBody`` / ``CFCGraph``，也不实现 lowering（D3 载体分支、`.export`
  自动模式顺序重建等均不在本包范围，见 IR_SPEC §4/§6）。

数值语义边界（诚实声明）：REAL/binary32 量化、整数原生位宽/回绕/越界转换
（IR_SPEC §5.3/§5.4）在本包**只保留类型信息**，不实现、不验证；相关行为
与 PLC 语义一致性均属后续阶段与真机对拍范围。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

# ---------------------------------------------------------------------------
# IEC 类型与枚举常量（IR_SPEC §5.1/§8）
# ---------------------------------------------------------------------------

#: 本平台当前建模的 IEC 类型全集（IR_SPEC §8 表 + 位串类型）。
IEC_TYPES: frozenset = frozenset({
    "BOOL",
    # 有符号整数族
    "SINT", "INT", "DINT", "LINT",
    # 无符号整数族
    "USINT", "UINT", "UDINT", "ULINT",
    # 位串
    "BYTE", "WORD", "DWORD", "LWORD",
    # 实数
    "REAL", "LREAL",
    # 其他
    "TIME", "STRING",
})

SIGNED_INT_TYPES: frozenset = frozenset({"SINT", "INT", "DINT", "LINT"})
UNSIGNED_INT_TYPES: frozenset = frozenset({"USINT", "UINT", "UDINT", "ULINT"})
BIT_TYPES: frozenset = frozenset({"BYTE", "WORD", "DWORD", "LWORD"})
INT_TYPES: frozenset = SIGNED_INT_TYPES | UNSIGNED_INT_TYPES | BIT_TYPES
REAL_TYPES: frozenset = frozenset({"REAL", "LREAL"})
#: 算术运算（ADD/SUB/MUL/DIV）允许的求值类型；TIME 参与加减（IEC 常用）。
NUMERIC_TYPES: frozenset = INT_TYPES | REAL_TYPES | frozenset({"TIME"})
#: 逻辑/位运算（AND/OR/XOR）允许的求值类型。
LOGIC_TYPES: frozenset = frozenset({"BOOL"}) | INT_TYPES
#: 有序比较（GT/GE/LT/LE）允许的操作数类型。
ORDERED_TYPES: frozenset = NUMERIC_TYPES | frozenset({"STRING"})

BINOP_ARITH_OPS: frozenset = frozenset({"ADD", "SUB", "MUL", "DIV", "MOD"})
BINOP_LOGIC_OPS: frozenset = frozenset({"AND", "OR", "XOR"})
BINOP_COMPARE_OPS: frozenset = frozenset({"GT", "GE", "LT", "LE", "EQ", "NE"})
BINOP_OPS: frozenset = BINOP_ARITH_OPS | BINOP_LOGIC_OPS | BINOP_COMPARE_OPS
UNOP_OPS: frozenset = frozenset({"NOT", "NEG"})

VAR_SECTIONS: frozenset = frozenset({
    "VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_TEMP", "VAR_GLOBAL",
})
INTERFACE_SECTIONS: frozenset = frozenset({"VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"})

POU_KINDS: frozenset = frozenset({"PROGRAM", "FUNCTION_BLOCK", "FUNCTION"})
POU_LANGUAGES: frozenset = frozenset({"ST", "CFC"})
INSTANCE_KINDS: frozenset = frozenset({"library", "user_fb"})
BINDING_MODES: frozenset = frozenset({"IN", "OUT", "INOUT"})
IO_DIRECTIONS: frozenset = frozenset({"IN", "OUT"})


# ---------------------------------------------------------------------------
# 绑定实参形态（IR_SPEC §5.2：StoreKey | StackSlot | Const）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoreKey:
    """实参指向变量空间中的一个键（命名规则见 IR_SPEC §7）。"""
    key: str


@dataclass(frozen=True)
class StackSlot:
    """实参指向求值栈槽位；``writable=True`` 才可作 OUT 去向（IR_SPEC §5.2）。

    ``index`` 语义（**项目工程约定**，IR_SPEC §5.2 未细化该字段）：距调用点
    栈顶的偏移，``0`` = 栈顶；同一调用内全部 IN×StackSlot 的索引必须互不
    重复且恰好连续覆盖 ``{0..k-1}``（k 为该调用 IN×StackSlot 绑定数），
    调用按 index 消费这 k 个栈值——由 loader 在加载期校验。
    """
    index: int
    writable: bool = False


@dataclass(frozen=True)
class Const:
    """字面常量实参；仅允许 IN 模式（IR_SPEC §5.2）。"""
    value: Any
    type: str


BindingActual = Union[StoreKey, StackSlot, Const]


@dataclass(frozen=True)
class Binding:
    """lowering 期生成、随 CALL_FUNC / CALL_FB_INSTANCE 携带的形实绑定。

    模式约束（加载期校验，见 loader）：IN 可接受 StoreKey/StackSlot/Const；
    OUT 只能绑定可写位置（StoreKey / writable StackSlot，禁止 Const）；
    INOUT 必须是可写 StoreKey（运行期化为 ValueRef，禁止 Const 与值拷贝）。
    """
    formal: str
    mode: str                      # "IN" / "OUT" / "INOUT"
    actual: BindingActual
    type: str                      # IEC 类型（与形参声明核对）


@dataclass(frozen=True)
class ValueRef:
    """INOUT 的运行期形态：指向 Store 键的别名引用（IR_SPEC §5.2）。

    本包只建模该结构；运行期化归执行器（后续工作包）。
    """
    key: str
    type: str


@dataclass(frozen=True)
class StdSig:
    """CALL_STD 的签名：实参类型序列 + 返回类型（泛型标准函数按实参实例化）。"""
    param_types: tuple            # tuple[str, ...]
    return_type: str


# ---------------------------------------------------------------------------
# 可执行 IR 指令集（IR_SPEC §5.2 首版最小集）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LoadVar:
    """压入变量值；``type`` 必须等于该变量声明类型。"""
    key: str
    type: str


@dataclass(frozen=True)
class LoadConst:
    """压入常量。REAL 字面量的 binary32 编码属数值层，本包不实现。"""
    value: Any
    type: str


@dataclass(frozen=True)
class LoadPrev:
    """压入变量的上一拍快照值（``ctx.prev``）；CFC 反馈起点专用。

    注意：反馈边到 LOAD_PREV 的**精确映射算法**是待真机验证假设
    （RISKS::PLATFORM-CFC-FEEDBACK-MAP-1），本包只建模指令本身。
    """
    key: str
    type: str


@dataclass(frozen=True)
class StoreVar:
    """弹出并写入变量；栈顶类型必须严格等于 ``type``（IR_SPEC §5.1）。"""
    key: str
    type: str


@dataclass(frozen=True)
class BinOp:
    """弹 2 压 1。算术/逻辑类结果类型 = ``type``；比较类结果恒为 BOOL，
    ``type`` 为操作数类型（IR_SPEC §5.2）。"""
    op: str
    type: str


@dataclass(frozen=True)
class UnOp:
    """弹 1 压 1（NOT/NEG），结果类型 = ``type``。"""
    op: str
    type: str


@dataclass(frozen=True)
class Convert:
    """显式类型转换；隐式提升/赋值转换的唯一落点（IR_SPEC §5.1）。

    越界转换行为按 ``int_overflow_convert_policy``（TBD，待真机裁决），
    本包不实现转换语义。
    """
    from_type: str
    to_type: str


@dataclass(frozen=True)
class CallStd:
    """标准函数调用（SEL/MIN/MAX/LIMIT/ABS/MUX…）。

    本包只做签名的结构与栈类型校验；标准函数名册/语义属 L2 描述符与
    执行器范围，不在本包接入。
    """
    name: str
    sig: StdSig


@dataclass(frozen=True)
class CallFb:
    """调用**库块**实例的 step（经描述符）。

    本包只校验 ``instance`` 引用的是已声明的 library 实例；管脚元数据在
    COMPONENT_CONTRACT 注册表（L2），本工作包明确不接入。
    """
    instance: str


@dataclass(frozen=True)
class CallFunc:
    """调用用户 FUNCTION：压帧、按 bindings 绑定实参、返回值压栈。"""
    name: str
    bindings: tuple               # tuple[Binding, ...]
    ret_type: str


@dataclass(frozen=True)
class CallFbInstance:
    """调用用户 FUNCTION_BLOCK 实例（实例内存跨周期保持，装载期已展开）。

    ``instance_path`` 为**定义体内**的实例引用路径（如 ``"FB1"`` 或嵌套
    ``"FB1.SUB2"``）；运行期全路径（如 ``"PLC_PRG.FB1"``）由装载展开产生。
    """
    instance_path: str
    bindings: tuple               # tuple[Binding, ...]


@dataclass(frozen=True)
class Jmp:
    label: str


@dataclass(frozen=True)
class JmpIfFalse:
    """条件跳转；要求栈顶为 BOOL（弹出）。"""
    label: str


@dataclass(frozen=True)
class Label:
    id: str


#: 全部指令类型（供 isinstance 校验与文档用）。
Instr = Union[
    LoadVar, LoadConst, LoadPrev, StoreVar,
    BinOp, UnOp, Convert,
    CallStd, CallFb, CallFunc, CallFbInstance,
    Jmp, JmpIfFalse, Label,
]

INSTRUCTION_TYPES: tuple = (
    LoadVar, LoadConst, LoadPrev, StoreVar,
    BinOp, UnOp, Convert,
    CallStd, CallFb, CallFunc, CallFbInstance,
    Jmp, JmpIfFalse, Label,
)


# ---------------------------------------------------------------------------
# 声明部分（IR_SPEC §2）
# ---------------------------------------------------------------------------

@dataclass
class InstanceDecl:
    """FB 实例声明（库块或用户 FB；lowering 据 ``kind`` 选 CALL_FB / CALL_FB_INSTANCE）。"""
    name: str
    block_type: str
    kind: str = "library"          # "library" / "user_fb"
    ctor_args: dict = field(default_factory=dict)
    init_overrides: dict = field(default_factory=dict)
    retain: set = field(default_factory=set)   # RETAIN 状态变量名；{"*"} = 整实例（IR_SPEC §9）


@dataclass
class VarDecl:
    name: str
    iec_type: str
    initial: Any = None
    retain: bool = False
    persistent: bool = False
    section: str = "VAR"


@dataclass
class IOMap:
    """GVL 变量 ↔ 物理点映射。``policy`` 为 OutputPolicy 占位（OUT 方向必填），
    其结构与行为属 ENGINE_SCAN_SPEC §4，本包不实现、不校验其内部。"""
    var: str
    channel: str
    direction: str                 # "IN" / "OUT"
    policy: Any = None


# ---------------------------------------------------------------------------
# POU 模型（IR_SPEC §3：定义与运行实例分离）
# ---------------------------------------------------------------------------

@dataclass
class POUDefinition:
    """源代码级 POU 定义（每名字一份，进 ``Task.pou_lib``）。

    ``source`` 为前端源模型占位（本包不定义 STBody/CFCGraph）；
    ``code`` 为 lower 后的可执行 IR 指令列表（定义级共享、只读），
    允许为 ``None`` 表示尚未 lower——但被 Task 实际引用（可达）的定义
    必须有 code 才能通过装载校验（见 loader）。
    """
    name: str
    pou_kind: str                  # "PROGRAM" / "FUNCTION_BLOCK" / "FUNCTION"
    language: str                  # "ST" / "CFC"
    interface: list = field(default_factory=list)   # list[VarDecl]，仅 INTERFACE_SECTIONS
    locals: list = field(default_factory=list)      # list[VarDecl]，VAR / VAR_TEMP
    instances: list = field(default_factory=list)   # list[InstanceDecl]
    return_type: Optional[str] = None               # 仅 FUNCTION
    source: Any = None
    code: Optional[list] = None                     # list[Instr] | None


@dataclass
class ProgramInstance:
    """运行级 PROGRAM 实例（装载期创建，持久内存）。"""
    definition: str
    store_prefix: str


@dataclass
class FBInstance:
    """运行级用户 FB 实例（装载期按声明路径展开创建，**绝不按调用创建**）。"""
    definition: str
    path: str                      # 实例全路径，如 "PLC_PRG.FB1.SUB2"
    retain: set = field(default_factory=set)


@dataclass
class Task:
    """单任务、固定 500ms（IR_SPEC §3；多任务字段扩展属后续阶段）。"""
    cycle_ms: int = 500
    programs: list = field(default_factory=list)    # list[ProgramInstance]，D2 按列表顺序
    gvl: list = field(default_factory=list)         # list[VarDecl]
    io_map: list = field(default_factory=list)      # list[IOMap]
    pou_lib: dict = field(default_factory=dict)     # dict[str, POUDefinition]
