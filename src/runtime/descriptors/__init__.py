"""L2 组件描述符包（``docs/COMPONENT_CONTRACT.md`` v2.1）。

正式 ``BlockSchema``（纯数据）/ ``RuntimeAdapter``（进程内绑定）/ ``Registry``
核心，以及三个代表性 engineering adapter（TON / APCHSHLLIM / APCM）。
外挂描述符置于本包，**不改动**已迁移的 ``src/blocks`` / ``src/primitives``
（决策 D1）。
"""
from src.runtime.descriptors.model import (
    OMIT_POLICIES,
    PIN_KINDS,
    NUMERIC_VARIANTS,
    AdapterBindingError,
    BlockSchema,
    DescriptorError,
    Pin,
    RuntimeAdapter,
    SchemaValidationError,
    collect_outputs,
    parse_output_access,
)
from src.runtime.descriptors.registry import (
    DuplicateDescriptorError,
    MissingVariantError,
    Registry,
    RegistryError,
    UnknownBlockError,
    variant_for_mode,
)
from src.runtime.descriptors.representative import (
    APCHSHLLIM_ADAPTER,
    APCHSHLLIM_SCHEMA,
    APCM_ADAPTER,
    APCM_SCHEMA,
    TON_ADAPTER,
    TON_SCHEMA,
    build_default_registry,
)

__all__ = [
    "PIN_KINDS", "OMIT_POLICIES", "NUMERIC_VARIANTS",
    "Pin", "BlockSchema", "RuntimeAdapter",
    "collect_outputs", "parse_output_access",
    "DescriptorError", "SchemaValidationError", "AdapterBindingError",
    "Registry", "RegistryError", "DuplicateDescriptorError",
    "UnknownBlockError", "MissingVariantError", "variant_for_mode",
    "TON_SCHEMA", "TON_ADAPTER",
    "APCHSHLLIM_SCHEMA", "APCHSHLLIM_ADAPTER",
    "APCM_SCHEMA", "APCM_ADAPTER",
    "build_default_registry",
]
