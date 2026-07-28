"""五个基础业务块的 engineering ``BlockSchema + RuntimeAdapter``。

承接 ``representative.py``（TON / APCHSHLLIM / APCM）与 ``primitives.py``
（其余七个基础原语），本模块为 ``src/blocks`` 中**五个基础业务块**建立外挂
描述符（决策 D1：**不改动** ``src/blocks`` / ``src/primitives``）：

- ``APCSTATISTICS``：运行统计（min/max/running average）。``step`` 返回
  ``{"MN","MX","AVG"}`` **dict 输出回收**；输入 ``IN:REAL`` / ``RESET:BOOL``
  均 ``required``。输出 ``MN/MX`` 为 ``REAL``，``AVG`` 为 ``LREAL``——与修正版
  ST 源块（``COUNTER:ULINT`` / ``AVG:LREAL``，Welford 增量、Python 原生
  ``float`` = binary64）和 ``docs/RISKS.md::APCSTATISTICS-S6`` 逐一一致；因此
  F1 下 ``IN`` 在输入管脚边界量化为 binary32，而 ``AVG`` 作为 ``LREAL`` 回收
  保持 binary64、**不**再二次量化为 binary32（WP-028 曾复现 Schema 误声明
  ``AVG:REAL`` 造成的 F1 数值差异）。``dt_ms`` 块内 ``del``，不进入统计公式。
  跨拍状态 ``MN/MX/AVG/COUNTER``（``COUNTER`` 为 ST ``ULINT`` 样本计数，非管脚）。
- ``APCHSFOP``：一阶惯性低通滤波。输入 ``IN/TC/KG/TB:REAL`` 均 ``required``
  （源签名与锁定测试明确 ``TB`` 不可省略，故 adapter **不**声明 ``TB=0.5``）；
  ``TB/TC`` 是业务秒参数，``dt_ms`` 不替代不换算。输出 ``AV`` ``return:AV``。
  跨拍状态 ``AV/Ok_1/AV_TEMP``（源 ST 标注 RETAIN，此处仅作 ``state_vars``
  元数据暴露；``retainable`` 留空，**不**据此声称阶段 8 跨进程持久化已实现）。
- ``APCHSRATELIM``：速率限幅。输入 ``IN/HL/LL:REAL`` 均 ``required``；块内
  ``ABS(HL)/ABS(LL)`` 与严格比较边界保持；``HL/LL`` 是**单拍速率正幅值**，
  非输出上下限，``dt_ms`` 不换算成物理速率。输出 ``AV`` ``return:AV``；跨拍
  状态 ``AV/AV_1``。
- ``APCHSACCUM``：离散积算 / 单次回绕。本包只用源类**默认构造**
  ``IV=0.0 / MS=1.797693134862e38 / MC=1.0``（``ctor_args=()``）。输入
  ``I1:REAL=0.0`` / ``RS:BOOL=False`` 均 ``use_default``（省略拍回落 Schema
  默认，**非** keep_previous）；输出 ``AV:REAL`` / ``SS:BOOL`` 分别
  ``return:AV`` / ``return:SS``。跨拍状态
  ``AV/SS/IV/MS/MC/LR/preRS/bPositiveAccum``——``IV/MS/MC`` 是 VAR RETAIN
  配置（**非** step 输入、**不**借 ctor_args 冒充共享依赖），``bPositiveAccum``
  源 ST 保留字段不增行为。``init_overridable/hmi_writable`` 本包留空，非默认
  构造配置留给后续参数装载工作包。
- ``APCHXHCL``：故障检测 + 最近一分钟均值 + 一阶惯性滤波 + 故障均值冻结。
  内部 ``TOF1/TOF2/R_TRIG3`` 由源块自身构造，adapter 只调用一次顶层 ``step``
  （不重复推进内部原语）。``EN:BOOL / PV:REAL / FV:REAL`` ``required``；
  ``PVH/PVL/BHSLH/TL/TC/KG/TB`` ``use_default``，Schema 声明默认与源签名逐一
  一致（``1000000.0 / -100000.0 / 100000.0 / 60.0 / 1.0 / 1.0 / 0.5``）。
  ``dt_ms`` 注入 ``Task.cycle_ms`` 供 ``TOF1/TOF2`` 内部累积，**不**替代
  ``TB/TC/TL`` 业务秒脚。输出 ``AV/GZDV/PV_AVG/FV_AVG`` 全 ``return:<KEY>``；
  跨拍状态精确覆盖源块 20 个实例属性（含 ``PV_TEMP/FV_TEMP`` 500 槽缓存、
  ``SAMPLE_N/SUM/NUM/SUM1/NUM1/GZDV_RAW/INIT_OK/A`` 及三个子块实例名）。

五块统一 ``variant="engineering"``、``descriptor_version="1.0"``，无
``VAR_IN_OUT``，全部输入显式声明 OmitPolicy，全部声明输出由 ``output_access``
完整覆盖；``BlockSchema.to_json()`` 可由 ``json.dumps`` 序列化。除源码与锁定
测试直接支持的字段外，**不**新增 ``retainable / init_overridable /
hmi_writable / serializer`` 语义。

**边界（诚实声明）**：本包只证明这五个业务块 adapter 的 **Python 契约**——
经 Registry→Loader/Store/Executor 与直接调用原块的可观察行为一致。默认注册表
因此从 **10** 扩展到 **15** 个 engineering block type；**仍未**补齐完整 14
业务块 + 8 原语目录（剩余七个更复杂业务块 adapter、参数装载、F2、真机对拍
待后继独立工作包），也**不改动** ``src/blocks``（决策 D1）。这些 Python 对照
**不构成** 与 CODESYS SP16.1 语义一致的证据。
"""
from __future__ import annotations

from src.blocks.apchsaccum import APCHSACCUM
from src.blocks.apchsfop import APCHSFOP
from src.blocks.apchsratelim import APCHSRATELIM
from src.blocks.apchxhcl import APCHXHCL
from src.blocks.apcstatistics import APCSTATISTICS

from src.runtime.descriptors.model import (
    BlockSchema,
    Pin,
    RuntimeAdapter,
    collect_outputs,
)

# ---------------------------------------------------------------------------
# APCSTATISTICS：运行统计（dict 输出回收；IN/RESET required；dt_ms 不入公式）
# ---------------------------------------------------------------------------

APCSTATISTICS_SCHEMA = BlockSchema(
    block_type="APCSTATISTICS",
    inputs=(
        Pin("IN", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("RESET", "BOOL", "VAR_INPUT", omit_policy="required"),
    ),
    outputs=(
        Pin("MN", "REAL", "VAR_OUTPUT"),
        Pin("MX", "REAL", "VAR_OUTPUT"),
        # AVG 形式管脚类型忠实声明为 LREAL（源块 / RISKS::APCSTATISTICS-S6 一致，
        # 用户 2026-07-27 严格类型裁决）。调用方若接入 REAL 变量，须由后续
        # ST/CFC lowering 或显式 IR CONVERT LREAL->REAL 表达窄化，不在此把形式
        # 管脚谎报为 REAL 隐藏转换；F1 下该输出保持 binary64、不二次量化。
        Pin("AVG", "LREAL", "VAR_OUTPUT"),
    ),
    descriptor_version="1.0",
    # 跨拍状态：三路公开输出 + ST ULINT 样本计数 COUNTER（非管脚）
    state_vars=frozenset({"MN", "MX", "AVG", "COUNTER"}),
    output_access={"MN": "return:MN", "MX": "return:MX", "AVG": "return:AVG"},
)


def _apcstatistics_call(instance, dt_ms, resolved_inputs, inout_refs):
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        RESET=resolved_inputs["RESET"])
    return collect_outputs(APCSTATISTICS_SCHEMA.output_access, instance, ret)


APCSTATISTICS_ADAPTER = RuntimeAdapter(cls=APCSTATISTICS,
                                       call_adapter=_apcstatistics_call)


# ---------------------------------------------------------------------------
# APCHSFOP：一阶惯性滤波（return:AV；IN/TC/KG/TB 全 required，TB 不可省略）
# ---------------------------------------------------------------------------

APCHSFOP_SCHEMA = BlockSchema(
    block_type="APCHSFOP",
    inputs=(
        Pin("IN", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("TC", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("KG", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("TB", "REAL", "VAR_INPUT", omit_policy="required"),
    ),
    outputs=(Pin("AV", "REAL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    # 跨拍状态：公开输出 AV + 递推上一拍 Ok_1 + 本拍中间值 AV_TEMP
    # （源 ST 标注 RETAIN；此处仅作 state_vars 元数据暴露，retainable 留空）
    state_vars=frozenset({"AV", "Ok_1", "AV_TEMP"}),
    output_access={"AV": "return:AV"},
)


def _apchsfop_call(instance, dt_ms, resolved_inputs, inout_refs):
    # dt_ms 占位（块内 del dt_ms）；TB/TC 是业务秒脚，dt_ms 不替代不换算
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        TC=resolved_inputs["TC"], KG=resolved_inputs["KG"],
                        TB=resolved_inputs["TB"])
    return collect_outputs(APCHSFOP_SCHEMA.output_access, instance, ret)


APCHSFOP_ADAPTER = RuntimeAdapter(cls=APCHSFOP, call_adapter=_apchsfop_call)


# ---------------------------------------------------------------------------
# APCHSRATELIM：速率限幅（return:AV；IN/HL/LL required；块内 ABS + 严格比较）
# ---------------------------------------------------------------------------

APCHSRATELIM_SCHEMA = BlockSchema(
    block_type="APCHSRATELIM",
    inputs=(
        Pin("IN", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("HL", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("LL", "REAL", "VAR_INPUT", omit_policy="required"),
    ),
    outputs=(Pin("AV", "REAL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    # 跨拍状态：公开输出 AV + 前周期输出 AV_1
    state_vars=frozenset({"AV", "AV_1"}),
    output_access={"AV": "return:AV"},
)


def _apchsratelim_call(instance, dt_ms, resolved_inputs, inout_refs):
    # dt_ms 占位（块内 del dt_ms）；HL/LL 是单拍速率正幅值，dt_ms 不换算成速率
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        HL=resolved_inputs["HL"], LL=resolved_inputs["LL"])
    return collect_outputs(APCHSRATELIM_SCHEMA.output_access, instance, ret)


APCHSRATELIM_ADAPTER = RuntimeAdapter(cls=APCHSRATELIM,
                                      call_adapter=_apchsratelim_call)


# ---------------------------------------------------------------------------
# APCHSACCUM：离散积算 / 单次回绕
# （return:AV/SS；AV:LREAL/SS:BOOL；I1/RS use_default；默认构造）
# ---------------------------------------------------------------------------

APCHSACCUM_SCHEMA = BlockSchema(
    block_type="APCHSACCUM",
    inputs=(
        Pin("I1", "REAL", "VAR_INPUT", default=0.0, omit_policy="use_default"),
        Pin("RS", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
    ),
    outputs=(
        # 源 ST 形式管脚为 AV:LREAL（VAR_OUTPUT RETAIN）。调用方若连接 REAL，
        # 应由显式 CONVERT/lowering 表达窄化；不得在 Schema 中谎报为 REAL。
        # F1 下 AV 保持 binary64 回收，不做第二次 binary32 量化。
        Pin("AV", "LREAL", "VAR_OUTPUT"),
        Pin("SS", "BOOL", "VAR_OUTPUT"),
    ),
    descriptor_version="1.0",
    # 跨拍状态：公开输出 AV/SS + VAR RETAIN 配置 IV/MS/MC + 内部 LR/preRS +
    # 源 ST 保留但 body 未用的 bPositiveAccum（保留字段不增行为，AC4）。
    # IV/MS/MC 是实例级配置（非 step 输入、非 ctor_args 共享依赖）；本包只用
    # 默认构造，非默认配置留给后续参数装载工作包（init_overridable 留空）。
    state_vars=frozenset({"AV", "SS", "IV", "MS", "MC", "LR", "preRS",
                          "bPositiveAccum"}),
    output_access={"AV": "return:AV", "SS": "return:SS"},
)


def _apchsaccum_call(instance, dt_ms, resolved_inputs, inout_refs):
    # I1/RS 均 use_default → resolved_inputs 每拍必含（省略拍回落 Schema 默认）；
    # dt_ms 占位（块内 del dt_ms），离散积算不乘 dt。
    ret = instance.step(dt_ms, I1=resolved_inputs["I1"],
                        RS=resolved_inputs["RS"])
    return collect_outputs(APCHSACCUM_SCHEMA.output_access, instance, ret)


APCHSACCUM_ADAPTER = RuntimeAdapter(cls=APCHSACCUM, call_adapter=_apchsaccum_call)


# ---------------------------------------------------------------------------
# APCHXHCL：故障检测 + 最近一分钟均值 + 一阶滤波 + 故障均值冻结
# （return:<KEY> 四路；EN/PV/FV required，其余 use_default；dt_ms 驱内部 TOF）
# ---------------------------------------------------------------------------

APCHXHCL_SCHEMA = BlockSchema(
    block_type="APCHXHCL",
    inputs=(
        Pin("EN", "BOOL", "VAR_INPUT", omit_policy="required"),
        Pin("PV", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("FV", "REAL", "VAR_INPUT", omit_policy="required"),
        Pin("PVH", "REAL", "VAR_INPUT", default=1_000_000.0,
            omit_policy="use_default"),
        Pin("PVL", "REAL", "VAR_INPUT", default=-100_000.0,
            omit_policy="use_default"),
        Pin("BHSLH", "REAL", "VAR_INPUT", default=100_000.0,
            omit_policy="use_default"),
        Pin("TL", "REAL", "VAR_INPUT", default=60.0, omit_policy="use_default"),
        Pin("TC", "REAL", "VAR_INPUT", default=1.0, omit_policy="use_default"),
        Pin("KG", "REAL", "VAR_INPUT", default=1.0, omit_policy="use_default"),
        Pin("TB", "REAL", "VAR_INPUT", default=0.5, omit_policy="use_default"),
    ),
    outputs=(
        Pin("AV", "REAL", "VAR_OUTPUT"),
        Pin("GZDV", "BOOL", "VAR_OUTPUT"),
        Pin("PV_AVG", "REAL", "VAR_OUTPUT"),
        Pin("FV_AVG", "REAL", "VAR_OUTPUT"),
    ),
    descriptor_version="1.0",
    # 跨拍状态：源块 __init__ 的全部 20 个实例属性——三个子块实例
    # TOF1/TOF2/R_TRIG3、四路公开输出、上一拍/中间值、500 槽 PV_TEMP/FV_TEMP
    # 缓存、窗口样本数 SAMPLE_N、均值累加 SUM/NUM/SUM1/NUM1、原始故障判定
    # GZDV_RAW、首拍标志 INIT_OK、持续不变化计数 A。
    state_vars=frozenset({
        "TOF1", "TOF2", "R_TRIG3",
        "AV", "GZDV", "PV_AVG", "FV_AVG",
        "PV_1", "Ok_1", "AV_TEMP",
        "PV_TEMP", "FV_TEMP",
        "SAMPLE_N", "SUM", "NUM", "SUM1", "NUM1",
        "GZDV_RAW", "INIT_OK", "A",
    }),
    output_access={
        "AV": "return:AV", "GZDV": "return:GZDV",
        "PV_AVG": "return:PV_AVG", "FV_AVG": "return:FV_AVG",
    },
)


def _apchxhcl_call(instance, dt_ms, resolved_inputs, inout_refs):
    # 所有输入均 required 或 use_default → resolved_inputs 每拍必含全部键。
    # dt_ms 注入 Task.cycle_ms，供源块内部 TOF1/TOF2 累积；不替代 TB/TC/TL。
    # 仅调用一次顶层 step（内部 TOF/R_TRIG 由源块自身推进，adapter 不重复推进）。
    ret = instance.step(
        dt_ms,
        EN=resolved_inputs["EN"], PV=resolved_inputs["PV"],
        FV=resolved_inputs["FV"], PVH=resolved_inputs["PVH"],
        PVL=resolved_inputs["PVL"], BHSLH=resolved_inputs["BHSLH"],
        TL=resolved_inputs["TL"], TC=resolved_inputs["TC"],
        KG=resolved_inputs["KG"], TB=resolved_inputs["TB"])
    return collect_outputs(APCHXHCL_SCHEMA.output_access, instance, ret)


APCHXHCL_ADAPTER = RuntimeAdapter(cls=APCHXHCL, call_adapter=_apchxhcl_call)


# ---------------------------------------------------------------------------
# 本包描述符集合（供 build_default_registry 统一注册）
# ---------------------------------------------------------------------------

#: 五个基础业务块的 ``(schema, adapter)`` 对，按 block_type 字母序稳定排列。
BUSINESS_BASIC_DESCRIPTORS = (
    (APCHSACCUM_SCHEMA, APCHSACCUM_ADAPTER),
    (APCHSFOP_SCHEMA, APCHSFOP_ADAPTER),
    (APCHSRATELIM_SCHEMA, APCHSRATELIM_ADAPTER),
    (APCHXHCL_SCHEMA, APCHXHCL_ADAPTER),
    (APCSTATISTICS_SCHEMA, APCSTATISTICS_ADAPTER),
)
