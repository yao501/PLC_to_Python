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
from src.runtime.store import (
    Store, StoreSnapshot, RuntimeLayout,
    StoreError, UnknownStoreKeyError, DuplicateStoreKeyError,
    StoreTypeError, InstanceLayoutError,
    build_runtime_store, persistent_key, check_value_type,
)
from src.runtime.process_image import (
    ProcessImageError, InputImageError, OutputImageError,
    InputSnapshot, OutputPending, latch_inputs, make_prev_snapshot,
)
from src.runtime.numeric import (
    NumericMode, NumericError, UnsupportedNumericModeError,
    UnsupportedConversionError, IECMathError,
    quantize_real32, wrap_int, default_value, trunc_div, iec_mod,
)
from src.runtime.executor import (
    Executor, TypedValue, IRExecutionError,
    MissingStdFunctionError, MissingLibraryAdapterError,
)
from src.runtime.engine import (
    ScanEngine, ScanResult,
    ScanError, ScanConfigError, OutputStagingError, ScanReentryError,
)

__all__ = [
    # 五步扫描编排骨架（WP-20260716-006）
    "ScanEngine", "ScanResult",
    "ScanError", "ScanConfigError", "OutputStagingError", "ScanReentryError",
    # 数值策略与执行器（WP-20260714-004）
    "NumericMode", "NumericError", "UnsupportedNumericModeError",
    "UnsupportedConversionError", "IECMathError",
    "quantize_real32", "wrap_int", "default_value", "trunc_div", "iec_mod",
    "Executor", "TypedValue", "IRExecutionError",
    "MissingStdFunctionError", "MissingLibraryAdapterError",
    # 运行时 Store 与实例布局（WP-20260714-003）
    "Store", "StoreSnapshot", "RuntimeLayout",
    "StoreError", "UnknownStoreKeyError", "DuplicateStoreKeyError",
    "StoreTypeError", "InstanceLayoutError",
    "build_runtime_store", "persistent_key", "check_value_type",
    # 过程映像基础（WP-20260714-003）
    "ProcessImageError", "InputImageError", "OutputImageError",
    "InputSnapshot", "OutputPending", "latch_inputs", "make_prev_snapshot",
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
