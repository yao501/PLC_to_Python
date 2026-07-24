"""三个代表性 engineering adapter（本工作包 L2 接入的覆盖面）。

覆盖三类异构块接口（COMPONENT_CONTRACT §2 不变式 2 / §7 验收）：

- ``TON``：有状态原语，``dt_ms=Task.cycle_ms``，**tuple 输出回收**
  （``step`` 返回 ``(Q, ET_ms)``），输入省略/默认（``use_default``）。
- ``APCHSHLLIM``：普通业务块，**返回 dict 输出回收**（``return:AV``），
  ``dt_ms`` 占位不改业务参数语义（块内 ``del dt_ms``），输入 ``required``。
- ``APCM``：``LicenseContext`` **构造依赖共享**（``ctor_args``），``ZLOUT``
  的 ``RealRef`` / ``VAR_IN_OUT`` **写透**，``None=本拍不覆盖``
  （``none_means_no_write``）与"需保持上次值"（``keep_previous``）输入省略。
  **不改变** WP-APCM 原子整理修复——adapter 只按真实签名转调 ``step``。

**边界**：本包只接三块代表性 adapter，证明 L2 核心的 Python 契约；完整
14 业务块 + 8 原语目录须由后继独立工作包补齐（不得据本包把 L2 标记为
全部完成）。这些 Python 对照 ≠ 与 CODESYS 语义一致。
"""
from __future__ import annotations

from src.blocks.apchshllim import APCHSHLLIM
from src.blocks.apcm import APCM, RealRef
from src.primitives.timers import TON

from src.runtime.descriptors.model import (
    BlockSchema,
    Pin,
    RuntimeAdapter,
    collect_outputs,
)
from src.runtime.descriptors.registry import Registry

# ---------------------------------------------------------------------------
# TON：有状态原语 + tuple 输出回收
# ---------------------------------------------------------------------------

TON_SCHEMA = BlockSchema(
    block_type="TON",
    inputs=(
        Pin("IN", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
        Pin("PT_ms", "TIME", "VAR_INPUT", default=0, omit_policy="use_default"),
    ),
    outputs=(
        Pin("Q", "BOOL", "VAR_OUTPUT"),
        Pin("ET_ms", "TIME", "VAR_OUTPUT"),
    ),
    descriptor_version="1.0",
    state_vars=frozenset({"Q", "ET_ms"}),
    output_access={"Q": "return:0", "ET_ms": "return:1"},
)


def _ton_call(instance, dt_ms, resolved_inputs, inout_refs):
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        PT_ms=resolved_inputs["PT_ms"])
    return collect_outputs(TON_SCHEMA.output_access, instance, ret)


TON_ADAPTER = RuntimeAdapter(cls=TON, call_adapter=_ton_call)


# ---------------------------------------------------------------------------
# APCHSHLLIM：普通业务块 + 返回 dict 输出回收
# ---------------------------------------------------------------------------

APCHSHLLIM_SCHEMA = BlockSchema(
    block_type="APCHSHLLIM",
    inputs=(
        Pin("IN", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("HL", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("LL", "REAL", "VAR_INPUT", omit_policy="required"),
    ),
    outputs=(Pin("AV", "REAL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    output_access={"AV": "return:AV"},
)


def _apchshllim_call(instance, dt_ms, resolved_inputs, inout_refs):
    # dt_ms 仅占位（块内 del dt_ms），不改业务参数语义
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        HL=resolved_inputs["HL"], LL=resolved_inputs["LL"])
    return collect_outputs(APCHSHLLIM_SCHEMA.output_access, instance, ret)


APCHSHLLIM_ADAPTER = RuntimeAdapter(cls=APCHSHLLIM, call_adapter=_apchshllim_call)


# ---------------------------------------------------------------------------
# APCM：LicenseContext 构造依赖 + RealRef VAR_IN_OUT 写透 + None=不覆盖
# ---------------------------------------------------------------------------

#: 每拍必给的过程量输入（required）。
_APCM_REQUIRED = ("SP", "PV", "OC", "TS", "TP")
#: 可选实时覆盖项（None=本拍不覆盖；omit_policy 见下）。
_APCM_OPTIONAL = ("RM", "OUTT", "OUTB", "SADD", "SSUB", "ZLEN", "ZSYK")

APCM_SCHEMA = BlockSchema(
    block_type="APCM",
    inputs=(
        Pin("SP", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("PV", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("OC", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("TS", "BOOL", "VAR_INPUT", omit_policy="required"),
        Pin("TP", "REAL", "VAR_INPUT", omit_policy="required"),
        # None=本拍不覆盖（APCM `if RM is not None: self.RM = RM`）
        Pin("RM", "INT", "VAR_INPUT", omit_policy="none_means_no_write"),
        Pin("OUTT", "REAL", "VAR_INPUT", omit_policy="none_means_no_write"),
        Pin("OUTB", "REAL", "VAR_INPUT", omit_policy="none_means_no_write"),
        Pin("SADD", "BOOL", "VAR_INPUT", omit_policy="none_means_no_write"),
        Pin("SSUB", "BOOL", "VAR_INPUT", omit_policy="none_means_no_write"),
        Pin("ZLEN", "BOOL", "VAR_INPUT", omit_policy="none_means_no_write"),
        # 需保持上次值：keep_previous——**首拍**用 Schema 声明 default（须与
        # APCM 源块 `self.ZSYK: float = 1.0` 一致，避免纯数据 Schema 与块实际
        # 初值分叉），此后省略保持块内该字段上次值。
        Pin("ZSYK", "REAL", "VAR_INPUT", default=1.0,
            omit_policy="keep_previous"),
    ),
    outputs=(
        Pin("AV", "REAL", "VAR_OUTPUT"),
        Pin("AV_P", "REAL", "VAR_OUTPUT"),
        Pin("AV_R", "REAL", "VAR_OUTPUT"),
        Pin("AV_GC", "REAL", "VAR_OUTPUT"),
        Pin("AV_J", "REAL", "VAR_OUTPUT"),
        Pin("AV_D", "REAL", "VAR_OUTPUT"),
        Pin("AV_C", "REAL", "VAR_OUTPUT"),
    ),
    inouts=(Pin("ZLOUT", "REAL", "VAR_IN_OUT"),),
    descriptor_version="1.0",
    output_access={
        "AV": "attr:AV", "AV_P": "attr:AV_P", "AV_R": "attr:AV_R",
        "AV_GC": "attr:AV_GC", "AV_J": "attr:AV_J", "AV_D": "attr:AV_D",
        "AV_C": "attr:AV_C",
    },
)


def _apcm_call(instance, dt_ms, resolved_inputs, inout_refs):
    # VAR_IN_OUT 写透：step 前把当前值塞进 RealRef，step 后回读写回
    ref = RealRef(inout_refs["ZLOUT"].value)
    kwargs = {name: resolved_inputs[name] for name in _APCM_REQUIRED}
    for name in _APCM_OPTIONAL:
        # 省略（none_means_no_write / keep_previous）→ 不传 → 块保持上次值
        if name in resolved_inputs:
            kwargs[name] = resolved_inputs[name]
    instance.step(dt_ms, zlout_ref=ref, **kwargs)
    inout_refs["ZLOUT"].value = ref.value
    return collect_outputs(APCM_SCHEMA.output_access, instance, None)


APCM_ADAPTER = RuntimeAdapter(cls=APCM, call_adapter=_apcm_call,
                              ctor_args=("license_context",))


# ---------------------------------------------------------------------------
# 默认注册表构造
# ---------------------------------------------------------------------------

def build_default_registry() -> Registry:
    """构造含三个代表性 engineering 变体的注册表。

    仅 engineering 变体（E/F1 共用）；fidelity_f2 变体属独立按需立项，
    本包不注册（F2 解析时注册表将按 §5 加载期失败）。
    """
    registry = Registry()
    registry.register(TON_SCHEMA, TON_ADAPTER)
    registry.register(APCHSHLLIM_SCHEMA, APCHSHLLIM_ADAPTER)
    registry.register(APCM_SCHEMA, APCM_ADAPTER)
    return registry
