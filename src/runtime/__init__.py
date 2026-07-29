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
    LibraryRuntimeError,
)
from src.runtime.descriptors import (
    Pin, BlockSchema, RuntimeAdapter,
    PIN_KINDS, OMIT_POLICIES, NUMERIC_VARIANTS,
    collect_outputs, parse_output_access,
    DescriptorError, SchemaValidationError, AdapterBindingError,
    Registry, RegistryError, DuplicateDescriptorError,
    UnknownBlockError, MissingVariantError, variant_for_mode,
    TON_SCHEMA, TON_ADAPTER,
    APCHSHLLIM_SCHEMA, APCHSHLLIM_ADAPTER,
    APCM_SCHEMA, APCM_ADAPTER,
    TOF_SCHEMA, TOF_ADAPTER,
    TP_SCHEMA, TP_ADAPTER,
    R_TRIG_SCHEMA, R_TRIG_ADAPTER,
    F_TRIG_SCHEMA, F_TRIG_ADAPTER,
    SR_SCHEMA, SR_ADAPTER,
    RS_SCHEMA, RS_ADAPTER,
    BLINK_SCHEMA, BLINK_ADAPTER,
    PRIMITIVE_DESCRIPTORS,
    APCSTATISTICS_SCHEMA, APCSTATISTICS_ADAPTER,
    APCHSFOP_SCHEMA, APCHSFOP_ADAPTER,
    APCHSRATELIM_SCHEMA, APCHSRATELIM_ADAPTER,
    APCHSACCUM_SCHEMA, APCHSACCUM_ADAPTER,
    APCHXHCL_SCHEMA, APCHXHCL_ADAPTER,
    BUSINESS_BASIC_DESCRIPTORS,
    APCCD_SCHEMA, APCCD_ADAPTER,
    APCGCQ_SCHEMA, APCGCQ_ADAPTER,
    APCMAUTOPARA_SCHEMA, APCMAUTOPARA_ADAPTER,
    APCPID_SCHEMA, APCPID_ADAPTER,
    APCPIDZZD_SCHEMA, APCPIDZZD_ADAPTER,
    APCRSFNAUTOPARA_SCHEMA, APCRSFNAUTOPARA_ADAPTER,
    APCSPFINDER_SCHEMA, APCSPFINDER_ADAPTER,
    BUSINESS_COMPLEX_DESCRIPTORS,
    build_default_registry,
)
from src.runtime.engine import (
    ScanEngine, ScanResult,
    ScanError, ScanConfigError, OutputStagingError, ScanReentryError,
)
from src.runtime.output_policy import (
    OutputPolicy, SafetySnapshot, SafetyStateService, OutputPolicyService,
    OutputPolicyError, OutputPolicyConfigError, OutputPolicyReentryError,
    SafetyStateError,
)
from src.runtime.scan_runner import (
    CommitPort, OuterScanRunner,
    ScanRunnerError, ScanRunnerConfigError, ScanRunnerReentryError,
    SafeCommitSignal, ScanFaultSafeCommit, WatchdogSafeCommit,
)
from src.runtime.commit_supervisor import (
    CommitSupervisor, CommitReceipt, ChannelCommitStatus, CommitOutcome,
    CommitSupervisorError, CommitSupervisorConfigError,
    CommitSupervisorReentryError, PartialCommitError,
)
from src.runtime.parameters import (
    build_runtime, RuntimeAssembly, StartupWarning,
    StartupError, StartupValidationError,
)
from src.runtime.monitor import (
    SoftwareCycleMonitor, CycleToken, CycleObservation, WatchdogTimeoutEvent,
    MonitorError, MonitorConfigError, MonitorStateError, MonitorClockError,
)

__all__ = [
    # 软件周期监视器与一次性 watchdog 超时事件源（WP-20260729-043）
    "SoftwareCycleMonitor", "CycleToken", "CycleObservation",
    "WatchdogTimeoutEvent",
    "MonitorError", "MonitorConfigError", "MonitorStateError",
    "MonitorClockError",
    # 启动期参数装载核心与失败关闭装配入口（WP-20260728-041）
    "build_runtime", "RuntimeAssembly", "StartupWarning",
    "StartupError", "StartupValidationError",
    # 提交监督器：驱动确认提交证据 + 逐通道 commit_fault/channel_fault（WP-20260721-009）
    "CommitSupervisor", "CommitReceipt", "ChannelCommitStatus", "CommitOutcome",
    "CommitSupervisorError", "CommitSupervisorConfigError",
    "CommitSupervisorReentryError", "PartialCommitError",
    # 外层安全扫描运行器与扫描/看门狗故障安全提交（WP-20260720-008）
    "CommitPort", "OuterScanRunner",
    "ScanRunnerError", "ScanRunnerConfigError", "ScanRunnerReentryError",
    "SafeCommitSignal", "ScanFaultSafeCommit", "WatchdogSafeCommit",
    # 生产 OutputPolicy 核心与原子安全状态快照（WP-20260716-007）
    "OutputPolicy", "SafetySnapshot", "SafetyStateService", "OutputPolicyService",
    "OutputPolicyError", "OutputPolicyConfigError", "OutputPolicyReentryError",
    "SafetyStateError",
    # 五步扫描编排骨架（WP-20260716-006）
    "ScanEngine", "ScanResult",
    "ScanError", "ScanConfigError", "OutputStagingError", "ScanReentryError",
    # 数值策略与执行器（WP-20260714-004）
    "NumericMode", "NumericError", "UnsupportedNumericModeError",
    "UnsupportedConversionError", "IECMathError",
    "quantize_real32", "wrap_int", "default_value", "trunc_div", "iec_mod",
    "Executor", "TypedValue", "IRExecutionError",
    "MissingStdFunctionError", "MissingLibraryAdapterError",
    "LibraryRuntimeError",
    # L2 组件描述符核心与代表性 adapter（WP-20260723-017 检查点恢复）
    "Pin", "BlockSchema", "RuntimeAdapter",
    "PIN_KINDS", "OMIT_POLICIES", "NUMERIC_VARIANTS",
    "collect_outputs", "parse_output_access",
    "DescriptorError", "SchemaValidationError", "AdapterBindingError",
    "Registry", "RegistryError", "DuplicateDescriptorError",
    "UnknownBlockError", "MissingVariantError", "variant_for_mode",
    "TON_SCHEMA", "TON_ADAPTER",
    "APCHSHLLIM_SCHEMA", "APCHSHLLIM_ADAPTER",
    "APCM_SCHEMA", "APCM_ADAPTER",
    # L2 其余七个基础原语 adapter（WP-20260724-023）
    "TOF_SCHEMA", "TOF_ADAPTER",
    "TP_SCHEMA", "TP_ADAPTER",
    "R_TRIG_SCHEMA", "R_TRIG_ADAPTER",
    "F_TRIG_SCHEMA", "F_TRIG_ADAPTER",
    "SR_SCHEMA", "SR_ADAPTER",
    "RS_SCHEMA", "RS_ADAPTER",
    "BLINK_SCHEMA", "BLINK_ADAPTER",
    "PRIMITIVE_DESCRIPTORS",
    # L2 五个基础业务块 adapter（WP-20260727-026）
    "APCSTATISTICS_SCHEMA", "APCSTATISTICS_ADAPTER",
    "APCHSFOP_SCHEMA", "APCHSFOP_ADAPTER",
    "APCHSRATELIM_SCHEMA", "APCHSRATELIM_ADAPTER",
    "APCHSACCUM_SCHEMA", "APCHSACCUM_ADAPTER",
    "APCHXHCL_SCHEMA", "APCHXHCL_ADAPTER",
    "BUSINESS_BASIC_DESCRIPTORS",
    # L2 七个复杂／组合／授权业务块 adapter（WP-20260727-033）
    "APCCD_SCHEMA", "APCCD_ADAPTER",
    "APCGCQ_SCHEMA", "APCGCQ_ADAPTER",
    "APCMAUTOPARA_SCHEMA", "APCMAUTOPARA_ADAPTER",
    "APCPID_SCHEMA", "APCPID_ADAPTER",
    "APCPIDZZD_SCHEMA", "APCPIDZZD_ADAPTER",
    "APCRSFNAUTOPARA_SCHEMA", "APCRSFNAUTOPARA_ADAPTER",
    "APCSPFINDER_SCHEMA", "APCSPFINDER_ADAPTER",
    "BUSINESS_COMPLEX_DESCRIPTORS",
    "build_default_registry",
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
