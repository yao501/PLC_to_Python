"""可执行 IR 数据模型（IR_SPEC v2.2.1 §2/§3/§5 的原型子集）。

原型指令集（按 PLATFORM_ROADMAP 阶段 0.5 定义 + 5 案例所需最小扩展）：
LOAD_CONST / LOAD_VAR / STORE_VAR / BINOP / UNOP / CONVERT / CALL_FB /
CALL_FB_INSTANCE / JMP / JMP_IF_FALSE / LABEL。
不含（0.5 原型范围外，阶段 1 起实现）：LOAD_PREV、CALL_FUNC、CALL_STD、
VAR_TEMP、RETAIN、嵌套 FB 实例展开。

所有指令全类型化（§5.1）：无类型指令列表不是合法 IR，加载器拒绝（loader.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

# ---------------------------------------------------------------- 指令

BINOPS_ARITH = {"ADD", "SUB", "MUL", "DIV", "MOD"}
BINOPS_LOGIC = {"AND", "OR", "XOR"}
BINOPS_CMP = {"GT", "GE", "LT", "LE", "EQ", "NE"}
UNOPS = {"NOT", "NEG"}


@dataclass(frozen=True)
class Binding:
    """调用绑定（IR_SPEC §5.2）：lowering 期生成，随 CALL_FB_INSTANCE 携带。"""

    formal: str
    mode: str            # "IN" / "OUT" / "INOUT"
    actual_kind: str     # "var"（StoreKey）/ "const"（原型不支持 StackSlot）
    actual: Any          # var: store 键；const: 常量值
    type: str            # IEC 类型（加载期与形参声明核对）


@dataclass(frozen=True)
class Instr:
    """单条可执行指令。frozen + tuple bindings，支持结构相等比较（双前端合流验收用）。"""

    op: str
    key: Optional[str] = None        # 变量键 / 库实例名 / FB 实例路径 / LABEL id / JMP 目标
    type: Optional[str] = None       # IEC 类型；CONVERT 时为 from 类型
    value: Any = None                # LOAD_CONST 常量
    subop: Optional[str] = None      # BINOP/UNOP 运算名
    to_type: Optional[str] = None    # CONVERT 目标类型
    bindings: Optional[Tuple[Binding, ...]] = None  # CALL_FB_INSTANCE


# 便捷构造器（保持测试/前端代码可读）
def LOAD_VAR(key, type):        return Instr("LOAD_VAR", key=key, type=type)
def LOAD_CONST(value, type):    return Instr("LOAD_CONST", value=value, type=type)
def STORE_VAR(key, type):       return Instr("STORE_VAR", key=key, type=type)
def BINOP(subop, type):         return Instr("BINOP", subop=subop, type=type)
def UNOP(subop, type):          return Instr("UNOP", subop=subop, type=type)
def CONVERT(from_t, to_t):      return Instr("CONVERT", type=from_t, to_type=to_t)
def CALL_FB(inst):              return Instr("CALL_FB", key=inst)
def CALL_FB_INSTANCE(path, bindings):
    return Instr("CALL_FB_INSTANCE", key=path, bindings=tuple(bindings))
def JMP(label):                 return Instr("JMP", key=label)
def JMP_IF_FALSE(label):        return Instr("JMP_IF_FALSE", key=label)
def LABEL(label):               return Instr("LABEL", key=label)


# ---------------------------------------------------------------- 声明（IR_SPEC §2/§3）

@dataclass
class VarDecl:
    name: str
    iec_type: str
    initial: Any = None
    section: str = "VAR"       # VAR / VAR_INPUT / VAR_OUTPUT / VAR_IN_OUT / VAR_GLOBAL


@dataclass
class InstanceDecl:
    name: str
    block_type: str            # 库块查 REGISTRY；用户 FB 查 pou_lib
    kind: str = "library"      # "library" -> CALL_FB / "user_fb" -> CALL_FB_INSTANCE


@dataclass
class POUDefinition:
    """用户 POU 定义（源代码级，每名字一份）。原型只用 FUNCTION_BLOCK。"""

    name: str
    pou_kind: str                       # "FUNCTION_BLOCK"（原型子集）
    interface: list = field(default_factory=list)   # list[VarDecl]，section 标 IN/OUT/IN_OUT
    locals: list = field(default_factory=list)      # list[VarDecl]
    code: list = field(default_factory=list)        # lower 后 IR；变量键用 "self.<名>"，定义级共享只读


@dataclass
class ProgramInstance:
    name: str
    code: list = field(default_factory=list)        # lower 后的可执行 IR


# ---------------------------------------------------------------- 输出策略（ENGINE_SCAN_SPEC §4）

FORCED_SAFE_FIELDS = ("on_safety_trip", "on_scan_fault", "on_watchdog")


@dataclass
class OutputPolicy:
    var: str                   # 业务侧 request 变量（GVL）
    iec_type: str
    safe_value: Any
    rate_limit: Optional[float] = None
    commit_fault_retry_n: int = 3
    on_startup_not_ready: str = "safe"
    on_operator_disable: str = "safe"
    on_comm_loss: str = "safe"
    on_safety_trip: str = "safe"       # 强制 safe，配 "hold" = 非法工程（加载器拒绝）
    on_scan_fault: str = "safe"        # 强制 safe
    on_watchdog: str = "safe"          # 强制 safe


@dataclass
class IOMap:
    var: str
    channel: str
    direction: str             # "IN" / "OUT"
    policy: Optional[OutputPolicy] = None   # OUT 必填


# ---------------------------------------------------------------- 任务

@dataclass
class Task:
    cycle_ms: int = 500
    programs: list = field(default_factory=list)    # list[ProgramInstance]，D2 按列表顺序
    gvl: list = field(default_factory=list)         # list[VarDecl]
    io_map: list = field(default_factory=list)      # list[IOMap]
    instances: list = field(default_factory=list)   # list[InstanceDecl]（原型：任务级平铺声明，不嵌套）
    pou_lib: dict = field(default_factory=dict)     # name -> POUDefinition
