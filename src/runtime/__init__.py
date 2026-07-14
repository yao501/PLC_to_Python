"""正式运行时包（阶段 1 起步）：L3 IR 内存模型 + 装载期静态校验。

只导出本包稳定的 IR 模型与校验入口。**不是最终用户编程入口**——当前仅
允许 Python 代码在引擎内部/测试中构造 IR（PLATFORM_ROADMAP 阶段 1）。
不导出 prototype_05 任何原型模块;原型代码一次性、不复用。
"""
from src.runtime.ir import (
    # 类型与枚举常量
    IEC_TYPES, INT_TYPES, SIGNED_INT_TYPES, UNSIGNED_INT_TYPES, BIT_TYPES,
    REAL_TYPES, NUMERIC_TYPES, LOGIC_TYPES, ORDERED_TYPES,
    BINOP_OPS, BINOP_ARITH_OPS, BINOP_LOGIC_OPS, BINOP_COMPARE_OPS, UNOP_OPS,
    VAR_SECTIONS, INTERFACE_SECTIONS, POU_KINDS, POU_LANGUAGES,
    INSTANCE_KINDS, BINDING_MODES, IO_DIRECTIONS,
    # 绑定与引用
    StoreKey, StackSlot, Const, Binding, ValueRef, StdSig,
    # 指令集
    LoadVar, LoadConst, LoadPrev, StoreVar, BinOp, UnOp, Convert,
    CallStd, CallFb, CallFunc, CallFbInstance, Jmp, JmpIfFalse, Label,
    Instr, INSTRUCTION_TYPES,
    # 声明与容器
    InstanceDecl, VarDecl, IOMap,
    POUDefinition, ProgramInstance, FBInstance, Task,
)
from src.runtime.loader import IRValidationError, validate_task

__all__ = [
    "IEC_TYPES", "INT_TYPES", "SIGNED_INT_TYPES", "UNSIGNED_INT_TYPES",
    "BIT_TYPES", "REAL_TYPES", "NUMERIC_TYPES", "LOGIC_TYPES", "ORDERED_TYPES",
    "BINOP_OPS", "BINOP_ARITH_OPS", "BINOP_LOGIC_OPS", "BINOP_COMPARE_OPS",
    "UNOP_OPS", "VAR_SECTIONS", "INTERFACE_SECTIONS", "POU_KINDS",
    "POU_LANGUAGES", "INSTANCE_KINDS", "BINDING_MODES", "IO_DIRECTIONS",
    "StoreKey", "StackSlot", "Const", "Binding", "ValueRef", "StdSig",
    "LoadVar", "LoadConst", "LoadPrev", "StoreVar", "BinOp", "UnOp", "Convert",
    "CallStd", "CallFb", "CallFunc", "CallFbInstance", "Jmp", "JmpIfFalse",
    "Label", "Instr", "INSTRUCTION_TYPES",
    "InstanceDecl", "VarDecl", "IOMap",
    "POUDefinition", "ProgramInstance", "FBInstance", "Task",
    "IRValidationError", "validate_task",
]
