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
from src.runtime.descriptors.primitives import (
    BLINK_ADAPTER, BLINK_SCHEMA,
    F_TRIG_ADAPTER, F_TRIG_SCHEMA,
    PRIMITIVE_DESCRIPTORS,
    RS_ADAPTER, RS_SCHEMA,
    R_TRIG_ADAPTER, R_TRIG_SCHEMA,
    SR_ADAPTER, SR_SCHEMA,
    TOF_ADAPTER, TOF_SCHEMA,
    TP_ADAPTER, TP_SCHEMA,
)
from src.runtime.descriptors.business_basic import (
    APCHSACCUM_ADAPTER, APCHSACCUM_SCHEMA,
    APCHSFOP_ADAPTER, APCHSFOP_SCHEMA,
    APCHSRATELIM_ADAPTER, APCHSRATELIM_SCHEMA,
    APCHXHCL_ADAPTER, APCHXHCL_SCHEMA,
    APCSTATISTICS_ADAPTER, APCSTATISTICS_SCHEMA,
    BUSINESS_BASIC_DESCRIPTORS,
)
from src.runtime.descriptors.business_complex import (
    APCCD_ADAPTER, APCCD_SCHEMA,
    APCGCQ_ADAPTER, APCGCQ_SCHEMA,
    APCMAUTOPARA_ADAPTER, APCMAUTOPARA_SCHEMA,
    APCPID_ADAPTER, APCPID_SCHEMA,
    APCPIDZZD_ADAPTER, APCPIDZZD_SCHEMA,
    APCRSFNAUTOPARA_ADAPTER, APCRSFNAUTOPARA_SCHEMA,
    APCSPFINDER_ADAPTER, APCSPFINDER_SCHEMA,
    BUSINESS_COMPLEX_DESCRIPTORS,
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
    "TOF_SCHEMA", "TOF_ADAPTER",
    "TP_SCHEMA", "TP_ADAPTER",
    "R_TRIG_SCHEMA", "R_TRIG_ADAPTER",
    "F_TRIG_SCHEMA", "F_TRIG_ADAPTER",
    "SR_SCHEMA", "SR_ADAPTER",
    "RS_SCHEMA", "RS_ADAPTER",
    "BLINK_SCHEMA", "BLINK_ADAPTER",
    "PRIMITIVE_DESCRIPTORS",
    "APCSTATISTICS_SCHEMA", "APCSTATISTICS_ADAPTER",
    "APCHSFOP_SCHEMA", "APCHSFOP_ADAPTER",
    "APCHSRATELIM_SCHEMA", "APCHSRATELIM_ADAPTER",
    "APCHSACCUM_SCHEMA", "APCHSACCUM_ADAPTER",
    "APCHXHCL_SCHEMA", "APCHXHCL_ADAPTER",
    "BUSINESS_BASIC_DESCRIPTORS",
    "APCCD_SCHEMA", "APCCD_ADAPTER",
    "APCGCQ_SCHEMA", "APCGCQ_ADAPTER",
    "APCMAUTOPARA_SCHEMA", "APCMAUTOPARA_ADAPTER",
    "APCPID_SCHEMA", "APCPID_ADAPTER",
    "APCPIDZZD_SCHEMA", "APCPIDZZD_ADAPTER",
    "APCRSFNAUTOPARA_SCHEMA", "APCRSFNAUTOPARA_ADAPTER",
    "APCSPFINDER_SCHEMA", "APCSPFINDER_ADAPTER",
    "BUSINESS_COMPLEX_DESCRIPTORS",
    "build_default_registry",
]
