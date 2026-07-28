"""七个复杂／组合／授权业务块的 engineering 描述符。

本模块只提供外挂 ``BlockSchema + RuntimeAdapter``，不修改
``src/blocks``。管脚、默认值、构造依赖、返回回收和跨拍状态均来自当前源类
的公开 ``__init__`` / ``step`` 契约：

* ``APCGCQ`` / ``APCCD``：组合块；APCCD 的 ``ZLOUT`` 以
  ``VAR_IN_OUT`` 引用读入并从返回 dict 写回。
* ``APCPIDZZD`` / ``APCPID``：授权块；二者通过
  ``ctor_args=("license_context",)`` 接收同一任务注入的共享上下文。
* ``APCSPFINDER`` / ``APCRSFNAUTOPARA`` / ``APCMAUTOPARA``：
  状态分析／参数推荐块；后两者复用各自顶层实例内部真实的
  ``APCSPFINDER``，adapter 不重复推进子块。

所有 Python ``float / int / bool`` 形式管脚按当前迁移基线分别声明为
``REAL / INT / BOOL``。本模块不实现 F2、持久化、参数装载、隐式类型转换或
CODESYS 语义证明。
"""
from __future__ import annotations

from src.blocks.apccd import APCCD
from src.blocks.apcgcq import APCGCQ
from src.blocks.apcmautopara import APCMAUTOPARA
from src.blocks.apcpid import APCPID
from src.blocks.apcpidzzd import APCPIDZZD
from src.blocks.apcrsfnautopara import APCRSFNAUTOPARA
from src.blocks.apcspfinder import APCSPFINDER

from src.runtime.descriptors.model import (
    BlockSchema,
    Pin,
    RuntimeAdapter,
    collect_outputs,
)


_REQUIRED = object()


def _input_pins(specs):
    """把 ``(name, IEC type, default-or-_REQUIRED)`` 转为不可变 Pin tuple。"""
    pins = []
    for name, iec_type, default in specs:
        if default is _REQUIRED:
            pins.append(Pin(name, iec_type, "VAR_INPUT",
                            omit_policy="required"))
        else:
            pins.append(Pin(name, iec_type, "VAR_INPUT", default=default,
                            omit_policy="use_default"))
    return tuple(pins)


def _output_pins(names, *, bools=(), ints=()):
    bool_names = frozenset(bools)
    int_names = frozenset(ints)
    return tuple(
        Pin(name, "BOOL" if name in bool_names else
            "INT" if name in int_names else "REAL", "VAR_OUTPUT")
        for name in names
    )


def _attr_call(schema):
    """生成一次顶层 step + ``attr:`` 输出回收的通用 adapter。"""
    def call(instance, dt_ms, resolved_inputs, inout_refs):
        del inout_refs
        instance.step(dt_ms, **resolved_inputs)
        return collect_outputs(schema.output_access, instance, None)
    return call


def _attr_access(names):
    return {name: "attr:" + name for name in names}


# ---------------------------------------------------------------------------
# APCGCQ
# ---------------------------------------------------------------------------

_APCGCQ_OUTPUTS = ("GCAV", "JTAV", "DTAV")

APCGCQ_SCHEMA = BlockSchema(
    block_type="APCGCQ",
    inputs=_input_pins((
        ("IN", "REAL", _REQUIRED),
        ("TC", "REAL", _REQUIRED),
        ("TZ", "REAL", _REQUIRED),
        ("K", "REAL", _REQUIRED),
        ("INSP", "REAL", _REQUIRED),
        ("GC1", "REAL", _REQUIRED),
        ("GC2", "REAL", _REQUIRED),
        ("OUTH", "REAL", _REQUIRED),
        ("OUTL", "REAL", _REQUIRED),
        ("OUTV", "REAL", _REQUIRED),
    )),
    outputs=_output_pins(_APCGCQ_OUTPUTS),
    descriptor_version="1.0",
    state_vars=frozenset(
        "GCAV JTAV DTAV AV JZ_ZUP1 JZ_ZUP JZ_Z "
        "BLINK01 R_TRIG1 STAT01 FOP01 RLIM01 LIM01".split()),
    output_access={name: "return:" + name for name in _APCGCQ_OUTPUTS},
)


def _apcgcq_call(instance, dt_ms, resolved_inputs, inout_refs):
    del inout_refs
    ret = instance.step(dt_ms, **resolved_inputs)
    return collect_outputs(APCGCQ_SCHEMA.output_access, instance, ret)


APCGCQ_ADAPTER = RuntimeAdapter(cls=APCGCQ, call_adapter=_apcgcq_call)


# ---------------------------------------------------------------------------
# APCCD（ZLOUT 是 VAR_IN_OUT）
# ---------------------------------------------------------------------------

_APCCD_OUTPUTS = ("AV", "CD_BH")

APCCD_SCHEMA = BlockSchema(
    block_type="APCCD",
    inputs=_input_pins((
        ("SP", "REAL", _REQUIRED),
        ("PV", "REAL", _REQUIRED),
        ("TS", "BOOL", _REQUIRED),
        ("TC", "REAL", _REQUIRED),
        ("TZ", "REAL", _REQUIRED),
        ("CDH", "REAL", _REQUIRED),
        ("CDL", "REAL", _REQUIRED),
        ("TL", "REAL", _REQUIRED),
        ("CD_K_J", "REAL", 1.0),
        ("CD_K_D", "REAL", 1.0),
        ("CD_K_FD", "REAL", 1.0),
        ("CD_GD", "REAL", 2.0),
        ("CD_K", "REAL", 0.5),
        ("AD", "BOOL", True),
    )),
    outputs=_output_pins(_APCCD_OUTPUTS),
    inouts=(Pin("ZLOUT", "REAL", "VAR_IN_OUT"),),
    descriptor_version="1.0",
    state_vars=frozenset(
        "AV CD_BH JZ_ZUP3 JZ_ZUP2 JZ_Z1 AV_TEMP FLG "
        "BLINK1 R_TRIG1 TON1 STAT1 FOP1 R_TRIG2".split()),
    output_access={name: "return:" + name for name in _APCCD_OUTPUTS},
)


def _apccd_call(instance, dt_ms, resolved_inputs, inout_refs):
    ref = inout_refs["ZLOUT"]
    ret = instance.step(dt_ms, ZLOUT=ref.value, **resolved_inputs)
    # 原子性：先完整回收声明输出、再读取 ZLOUT 返回值，全部成功后才提交 inout
    # 引用。顶层 step 异常、返回结构缺失/错误或输出回收失败时，ref 均不被写入，
    # 不形成半写回 ZLOUT（自 adapter 内即 all-or-nothing，不依赖调用方用一次性
    # 暂存引用兜底）。
    outputs = collect_outputs(APCCD_SCHEMA.output_access, instance, ret)
    new_zlout = ret["ZLOUT"]
    ref.value = new_zlout
    return outputs


APCCD_ADAPTER = RuntimeAdapter(cls=APCCD, call_adapter=_apccd_call)


# ---------------------------------------------------------------------------
# APCPIDZZD
# ---------------------------------------------------------------------------

_APCPIDZZD_OUTPUTS = ("PT1", "TI1")

APCPIDZZD_SCHEMA = BlockSchema(
    block_type="APCPIDZZD",
    inputs=_input_pins((
        ("AV", "REAL", _REQUIRED),
        ("SP", "REAL", _REQUIRED),
        ("PV", "REAL", _REQUIRED),
        ("PT", "REAL", _REQUIRED),
        ("TI", "REAL", _REQUIRED),
        ("RM", "INT", 1),
        ("PVMU", "REAL", _REQUIRED),
        ("PVMD", "REAL", _REQUIRED),
        ("MU", "REAL", _REQUIRED),
        ("MD", "REAL", _REQUIRED),
        ("SADD", "BOOL", _REQUIRED),
        ("SSUB", "BOOL", _REQUIRED),
        ("PT1K", "REAL", 1.0),
        ("TI1K", "REAL", 1.0),
    )),
    outputs=_output_pins(_APCPIDZZD_OUTPUTS),
    descriptor_version="1.0",
    state_vars=frozenset(
        "PT1 TI1 TON1 TON2 HSACCUM1 HLLIM1 R_TRIG1 R_TRIG2 JS_Z JS_F "
        "ZJSBZ FJSBZ SLL11 SLL12 SLL21 SLL22 JSSJ JSSJ2 ZDPC SBCGBZ "
        "PVMAXDI JSTI JSSJZ JSSJF SQSJ N M _hsaccum_last_I1".split()),
    output_access=_attr_access(_APCPIDZZD_OUTPUTS),
)

APCPIDZZD_ADAPTER = RuntimeAdapter(
    cls=APCPIDZZD,
    call_adapter=_attr_call(APCPIDZZD_SCHEMA),
    ctor_args=("license_context",),
)


# ---------------------------------------------------------------------------
# APCPID
# ---------------------------------------------------------------------------

_APCPID_OUTPUTS = ("AV",)

APCPID_SCHEMA = BlockSchema(
    block_type="APCPID",
    inputs=_input_pins((
        ("SP", "REAL", _REQUIRED),
        ("PV", "REAL", _REQUIRED),
        ("IC", "REAL", 0.0),
        ("OC", "REAL", 0.0),
        ("TP", "REAL", _REQUIRED),
        ("TS", "BOOL", _REQUIRED),
        ("RM", "INT", _REQUIRED),
        ("OutT", "REAL", _REQUIRED),
        ("OutB", "REAL", _REQUIRED),
        ("SADD", "BOOL", _REQUIRED),
        ("SSUB", "BOOL", _REQUIRED),
        ("PT", "REAL", _REQUIRED),
        ("TI", "REAL", _REQUIRED),
        ("KD", "REAL", 1.0),
        ("TD", "REAL", 0.0),
    )),
    outputs=_output_pins(_APCPID_OUTPUTS),
    descriptor_version="1.0",
    state_vars=frozenset(
        "AV CYCLE OutRH OutRL MU MD OutM AD TM MI MS MM ATE PVMU PVMD DI "
        "SVH SVL KP KI PT1K TI1K preRM nowRM UK_1 DU_1 EK_1 EK_2 DEK "
        "DEK_1 DEK_2 PV_LAST deadenter TIi PX PT1 TI1 UK LASTUKOUT EK "
        "UKOUT DUOUT DU DU_TEMP SI B1 B2 C1 C2 C3 C4 PTt DI_SJ SVH_SJ "
        "SVL_SJ AV_TEMP EK_LAST PIDZZD1".split()),
    output_access=_attr_access(_APCPID_OUTPUTS),
)

APCPID_ADAPTER = RuntimeAdapter(
    cls=APCPID,
    call_adapter=_attr_call(APCPID_SCHEMA),
    ctor_args=("license_context",),
)


# ---------------------------------------------------------------------------
# APCSPFINDER
# ---------------------------------------------------------------------------

_APCSPFINDER_OUTPUTS = (
    "SP_USE", "SP_VALID", "SP_SOURCE", "SP_REASON", "SP_AUTO",
    "SP_AUTO_OK", "SP_AUTO_CONF", "SP_TAG_BAD", "SP_STABLE_T_OUT",
    "SP_STABLE_PV_RANGE",
)

APCSPFINDER_SCHEMA = BlockSchema(
    block_type="APCSPFINDER",
    inputs=_input_pins((
        ("EN", "BOOL", _REQUIRED),
        ("RESET", "BOOL", _REQUIRED),
        ("CYCLE", "REAL", 0.5),
        ("SAMPLE_OK", "BOOL", _REQUIRED),
        ("SP_MAN", "REAL", 0.0),
        ("SP_MAN_EN", "BOOL", False),
        ("SP_TAG", "REAL", 0.0),
        ("SP_TAG_EN", "BOOL", True),
        ("SP_AUTO_EN", "BOOL", True),
        ("SP_AUTO_REPLACE_BAD_TAG", "BOOL", False),
        ("PV", "REAL", _REQUIRED),
        ("AV", "REAL", _REQUIRED),
        ("PVMU", "REAL", 100.0),
        ("PVMD", "REAL", 0.0),
        ("OUTT", "REAL", 100.0),
        ("OUTB", "REAL", 0.0),
        ("SP_STABLE_T", "REAL", 300.0),
        ("SP_CONF_T", "REAL", 900.0),
        ("PV_STABLE_K", "REAL", 0.002),
        ("AV_STABLE_K", "REAL", 0.001),
        ("PV_STABLE_ABS", "REAL", 0.0),
        ("AV_STABLE_ABS", "REAL", 0.0),
        ("SP_BAD_K", "REAL", 0.05),
        ("SP_BAD_ABS", "REAL", 0.0),
    )),
    outputs=_output_pins(
        _APCSPFINDER_OUTPUTS,
        bools=("SP_VALID", "SP_AUTO_OK", "SP_TAG_BAD"),
        ints=("SP_SOURCE", "SP_REASON"),
    ),
    descriptor_version="1.0",
    state_vars=frozenset(
        "SP_USE SP_VALID SP_SOURCE SP_REASON SP_AUTO SP_AUTO_OK SP_AUTO_CONF "
        "SP_TAG_BAD SP_STABLE_T_OUT SP_STABLE_PV_RANGE INIT_DONE CYCLE_S "
        "PV_RANGE OUT_RANGE PV_TH AV_TH SP_BAD_TH D_PV D_AV PV_1 AV_1 "
        "STABLE_ACTIVE STABLE_T STABLE_N STABLE_PV_SUM STABLE_PV_MAX "
        "STABLE_PV_MIN STABLE_SP_TEMP STABLE_SPAN_K".split()),
    output_access=_attr_access(_APCSPFINDER_OUTPUTS),
)

APCSPFINDER_ADAPTER = RuntimeAdapter(
    cls=APCSPFINDER,
    call_adapter=_attr_call(APCSPFINDER_SCHEMA),
)


# ---------------------------------------------------------------------------
# APCRSFNAUTOPARA
# ---------------------------------------------------------------------------

_APCRSFNAUTOPARA_OUTPUTS = (
    "RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG", "FINAL_WEAK",
    "MATCH_LEVEL", "WINDOW_VALID", "DATA_REASON", "SP_USE", "SP_AUTO",
    "SP_VALID", "SP_AUTO_OK", "SP_TAG_BAD", "SP_SOURCE", "SP_REASON",
    "SP_AUTO_CONF", "SP_STABLE_T_OUT", "RSF_OK", "RSF_REASON",
    "HISTORY_COUNT", "SIMILAR_COUNT", "FUSE_WEIGHT", "WINDOW_EVENT_N",
    "WINDOW_T", "AUTO_SAMPLE_T", "MAN_EVENT_N", "CROSS_COUNT",
    "RSF_TRIGGER_N", "RSF_LOCK_N", "ERR_ABS_AVG", "ERR_AREA_POS",
    "ERR_AREA_NEG", "ERR_PEAK_ABS", "AVG_CROSS_T", "PV_DELTA", "AV_DELTA",
    "NOISE_EST", "PROCESS_GAIN", "TL_REC", "TL1_REC", "TL2_REC", "TL3_REC",
    "TL4_REC", "E1_REC", "E2_REC", "E3_REC", "E4_REC", "AO1_REC",
    "AO2_REC", "AO3_REC", "AO4_REC", "RSF_LOCK_T_REC", "RSF_HYS_REC",
    "RSF_FAST_HYS_REC", "RSF_TLOUT_K_REC", "ZF_K_REC",
)

_APCRSFNAUTOPARA_STATE = frozenset("""
RUNNING WINDOW_DONE FINAL_VALID FINAL_STRONG FINAL_WEAK MATCH_LEVEL WINDOW_VALID
DATA_REASON SP_USE SP_AUTO SP_VALID SP_AUTO_OK SP_TAG_BAD SP_SOURCE SP_REASON
SP_AUTO_CONF SP_STABLE_T_OUT RSF_OK RSF_REASON HISTORY_COUNT SIMILAR_COUNT
FUSE_WEIGHT WINDOW_EVENT_N WINDOW_T AUTO_SAMPLE_T MAN_EVENT_N CROSS_COUNT
RSF_TRIGGER_N RSF_LOCK_N ERR_ABS_AVG ERR_AREA_POS ERR_AREA_NEG ERR_PEAK_ABS
AVG_CROSS_T PV_DELTA AV_DELTA NOISE_EST PROCESS_GAIN TL_REC TL1_REC TL2_REC
TL3_REC TL4_REC E1_REC E2_REC E3_REC E4_REC AO1_REC AO2_REC AO3_REC AO4_REC
RSF_LOCK_T_REC RSF_HYS_REC RSF_FAST_HYS_REC RSF_TLOUT_K_REC ZF_K_REC INIT_DONE
CALC_OLD CALC_R CYCLE_S OUT_RANGE OUT_RANGE_USE RANGE_OK H_N H_N_OLD MATCH_STAGE
BLEND MAN_TH SPF1 SP_WORK ERR ERR_1 ABS_ERR D_PV D_AV PV_1 AV_1 TP_1 RSF_LEVEL_1
RSF_LOCK_LEVEL_1 AUTO_SAMPLE MAN_SAMPLE SAMPLE_OK WIN_ELAPSED WIN_AUTO_T
WIN_MAN_T WIN_N WIN_SP_SUM WIN_PV_SUM WIN_AV_SUM WIN_ERR_ABS_SUM
WIN_ERR_AREA_POS WIN_ERR_AREA_NEG WIN_ERR_PEAK_ABS WIN_PV_MAX WIN_PV_MIN
WIN_AV_MAX WIN_AV_MIN WIN_INIT WIN_NOISE_SUM WIN_NOISE_N WIN_CROSS_N WIN_SEG_T
WIN_SEG_T_SUM WIN_RSF_EVENT_N WIN_MAN_EVENT_N WIN_LOCK_N WIN_ZONE1_T WIN_ZONE2_T
WIN_ZONE3_T WIN_ZONE4_T WIN_SP_AVG WIN_PV_AVG WIN_AV_AVG WIN_ERR_AVG
WIN_PV_DELTA WIN_AV_DELTA WIN_WEIGHT WIN_SCALE W_TL W_TL1 W_TL2 W_TL3 W_TL4
W_E1 W_E2 W_E3 W_E4 W_AO1 W_AO2 W_AO3 W_AO4 W_RSF_LOCK_T W_RSF_HYS
W_RSF_FAST_HYS W_RSF_TLOUT_K W_ZF_K W_RESP_T W_GAIN W_BASE_E W_OSC W_SLOW
W_NOISE_HIGH SIM_SP_TH SIM_PV_TH SIM_AV_TH SIM_ERR_TH SIM_RELAX_USE FUSE_SUM_W
FUSE_W FUSE_STRONG H_IDX H_VALID H_WEIGHT H_SP H_PV H_AV H_ERR H_TL H_TL1 H_TL2
H_TL3 H_TL4 H_E1 H_E2 H_E3 H_E4 H_AO1 H_AO2 H_AO3 H_AO4 H_RSF_LOCK_T
H_RSF_HYS H_RSF_FAST_HYS H_RSF_TLOUT_K H_ZF_K
""".split())

APCRSFNAUTOPARA_SCHEMA = BlockSchema(
    block_type="APCRSFNAUTOPARA",
    inputs=_input_pins((
        ("EN", "BOOL", _REQUIRED),
        ("RESET", "BOOL", _REQUIRED),
        ("CALC_NOW", "BOOL", _REQUIRED),
        ("CYCLE", "REAL", 0.5),
        ("COLLECT_MODE", "INT", 1),
        ("SP", "REAL", _REQUIRED),
        ("SP_MAN", "REAL", 0.0),
        ("SP_MAN_EN", "BOOL", False),
        ("SP_TAG_EN", "BOOL", True),
        ("SP_AUTO_EN", "BOOL", True),
        ("SP_AUTO_REPLACE_BAD_TAG", "BOOL", False),
        ("SP_STABLE_T", "REAL", 300.0),
        ("SP_CONF_T", "REAL", 900.0),
        ("SP_PV_STABLE_ABS", "REAL", 0.0),
        ("SP_AV_STABLE_ABS", "REAL", 0.0),
        ("PV", "REAL", _REQUIRED),
        ("AV", "REAL", _REQUIRED),
        ("TP", "REAL", _REQUIRED),
        ("TS", "BOOL", _REQUIRED),
        ("MU", "REAL", 100.0),
        ("MD", "REAL", 0.0),
        ("PHY_RANGE_EN", "BOOL", False),
        ("PHY_MU", "REAL", 100.0),
        ("PHY_MD", "REAL", 0.0),
        ("RSF_LEVEL", "REAL", _REQUIRED),
        ("RSF_LOCK_LEVEL_IN", "REAL", _REQUIRED),
        ("RSF_STEP", "REAL", _REQUIRED),
        ("WIN_T", "REAL", 7200.0),
        ("MIN_WIN_T", "REAL", 300.0),
        ("MIN_STORE_EVENT", "REAL", 1.0),
        ("MIN_VALID_EVENT", "REAL", 5.0),
        ("HISTORY_N", "INT", 24),
        ("FUSE_MIN_N", "REAL", 3.0),
        ("FUSE_MIN_WEIGHT", "REAL", 3.0),
        ("SIM_SP_K", "REAL", 0.05),
        ("SIM_PV_K", "REAL", 0.05),
        ("SIM_AV_K", "REAL", 0.10),
        ("SIM_ERR_K", "REAL", 0.05),
        ("SIM_SP_ABS", "REAL", 0.0),
        ("SIM_PV_ABS", "REAL", 0.0),
        ("SIM_AV_ABS", "REAL", 0.0),
        ("SIM_ERR_ABS", "REAL", 0.0),
        ("SIM_RELAX_K", "REAL", 2.0),
        ("MAN_AV_MIN", "REAL", 0.1),
        ("AO_GAIN_K", "REAL", 0.5),
        ("REC_BLEND", "REAL", 0.7),
        ("TL_IN", "REAL", 10.0),
        ("TL1_IN", "REAL", 60.0),
        ("TL2_IN", "REAL", 60.0),
        ("TL3_IN", "REAL", 60.0),
        ("TL4_IN", "REAL", 60.0),
        ("E1_IN", "REAL", 1.0),
        ("E2_IN", "REAL", 2.0),
        ("E3_IN", "REAL", 3.0),
        ("E4_IN", "REAL", 4.0),
        ("AO1_IN", "REAL", 1.0),
        ("AO2_IN", "REAL", 2.0),
        ("AO3_IN", "REAL", 3.0),
        ("AO4_IN", "REAL", 4.0),
        ("RSF_LOCK_T_IN", "REAL", 30.0),
        ("RSF_HYS_IN", "REAL", 0.8),
        ("RSF_FAST_HYS_IN", "REAL", 0.5),
        ("RSF_TLOUT_K_IN", "REAL", 0.5),
        ("ZF_K_IN", "REAL", 0.0),
    )),
    outputs=_output_pins(
        _APCRSFNAUTOPARA_OUTPUTS,
        bools=("RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG",
               "FINAL_WEAK", "WINDOW_VALID", "SP_VALID", "SP_AUTO_OK",
               "SP_TAG_BAD", "RSF_OK"),
        ints=("MATCH_LEVEL", "DATA_REASON", "SP_SOURCE", "SP_REASON",
              "RSF_REASON"),
    ),
    descriptor_version="1.0",
    state_vars=_APCRSFNAUTOPARA_STATE,
    output_access=_attr_access(_APCRSFNAUTOPARA_OUTPUTS),
)

APCRSFNAUTOPARA_ADAPTER = RuntimeAdapter(
    cls=APCRSFNAUTOPARA,
    call_adapter=_attr_call(APCRSFNAUTOPARA_SCHEMA),
)


# ---------------------------------------------------------------------------
# APCMAUTOPARA
# ---------------------------------------------------------------------------

_APCMAUTOPARA_OUTPUTS = (
    "RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG", "FINAL_WEAK",
    "MATCH_LEVEL", "WINDOW_VALID", "DATA_REASON", "SP_USE", "SP_AUTO",
    "SP_VALID", "SP_AUTO_OK", "SP_TAG_BAD", "SP_SOURCE", "SP_REASON",
    "SP_AUTO_CONF", "SP_STABLE_T_OUT", "PID_OK", "RSF_OK", "GC_OK", "CD_OK",
    "PID_REASON", "RSF_REASON", "GC_REASON", "CD_REASON", "HISTORY_COUNT",
    "SIMILAR_COUNT", "FUSE_WEIGHT", "WINDOW_EVENT_N", "WINDOW_T",
    "AUTO_SAMPLE_T", "MAN_EVENT_N", "MAN_RESP_T_AUTO", "MAN_RESP_T_USE",
    "CROSS_COUNT", "ERR_ABS_AVG", "ERR_AREA_POS", "ERR_AREA_NEG",
    "ERR_PEAK_ABS", "AVG_CROSS_T", "PV_DELTA", "AV_DELTA", "NOISE_EST",
    "PROCESS_GAIN", "PT_REC", "TI_REC", "TD_REC", "DI_REC", "SVH_REC",
    "SVL_REC", "PID_FORMULA_VALID", "PT_FORMULA_REC", "TI_FORMULA_REC",
    "PID_MODEL_GAIN_REC", "PID_MODEL_T_REC", "PID_MODEL_L_REC",
    "PID_MODEL_LAMBDA_REC", "PID_FORMULA_BLEND_REC", "TL_REC", "TL1_REC",
    "TL2_REC", "TL3_REC", "TL4_REC", "E1_REC", "E2_REC", "E3_REC", "E4_REC",
    "AO1_REC", "AO2_REC", "AO3_REC", "AO4_REC", "RSF_LOCK_T_REC", "TC_REC",
    "TZ_REC", "GC1_REC", "GC2_REC", "OUTH_REC", "OUTL_REC", "CD_GD_REC",
    "CD_K_REC", "CD_K_FD_REC", "CD_K_J_REC", "CD_K_D_REC", "CDH_REC",
    "CDL_REC", "TC_CD_REC", "TZ_CD_REC",
)

_APCMAUTOPARA_STATE = frozenset("""
RUNNING WINDOW_DONE FINAL_VALID FINAL_STRONG FINAL_WEAK MATCH_LEVEL WINDOW_VALID
DATA_REASON SP_USE SP_AUTO SP_VALID SP_AUTO_OK SP_TAG_BAD SP_SOURCE SP_REASON
SP_AUTO_CONF SP_STABLE_T_OUT PID_OK RSF_OK GC_OK CD_OK PID_REASON RSF_REASON
GC_REASON CD_REASON HISTORY_COUNT SIMILAR_COUNT FUSE_WEIGHT WINDOW_EVENT_N
WINDOW_T AUTO_SAMPLE_T MAN_EVENT_N MAN_RESP_T_AUTO MAN_RESP_T_USE CROSS_COUNT
ERR_ABS_AVG ERR_AREA_POS ERR_AREA_NEG ERR_PEAK_ABS AVG_CROSS_T PV_DELTA AV_DELTA
NOISE_EST PROCESS_GAIN PT_REC TI_REC TD_REC DI_REC SVH_REC SVL_REC
PID_FORMULA_VALID PT_FORMULA_REC TI_FORMULA_REC PID_MODEL_GAIN_REC
PID_MODEL_T_REC PID_MODEL_L_REC PID_MODEL_LAMBDA_REC PID_FORMULA_BLEND_REC TL_REC
TL1_REC TL2_REC TL3_REC TL4_REC E1_REC E2_REC E3_REC E4_REC AO1_REC AO2_REC
AO3_REC AO4_REC RSF_LOCK_T_REC TC_REC TZ_REC GC1_REC GC2_REC OUTH_REC OUTL_REC
CD_GD_REC CD_K_REC CD_K_FD_REC CD_K_J_REC CD_K_D_REC CDH_REC CDL_REC TC_CD_REC
TZ_CD_REC INIT_DONE CALC_OLD CALC_R I H_IDX H_N H_N_OLD CYCLE_S PV_RANGE
OUT_RANGE OUT_LIMIT_RANGE OUT_LIMIT_MARGIN RANGE_OK MAN_TH AUTO_ALLOWED
MAN_ALLOWED SPF1 SP_WORK WIN_ELAPSED WIN_AUTO_T WIN_AUTO_N WIN_SP_SUM WIN_PV_SUM
WIN_AV_SUM WIN_ERR_AREA_TOTAL WIN_ERR_AREA_POS WIN_ERR_AREA_NEG WIN_ERR_PEAK_POS
WIN_ERR_PEAK_NEG WIN_ERR_PEAK_ABS WIN_CROSS_N WIN_SEG_T WIN_SEG_AREA
WIN_LAST_AREA1 WIN_LAST_AREA2 WIN_LAST_AREA3 WIN_LAST_T1 WIN_LAST_T2 WIN_LAST_T3
WIN_SEG_PEAK WIN_LAST_PEAK1 WIN_LAST_PEAK2 WIN_LAST_PEAK3 WIN_PV_MAX WIN_PV_MIN
WIN_AV_MAX WIN_AV_MIN WIN_PV_STEP_SUM WIN_PV_STEP_MAX WIN_QUIET_STEP_SUM
WIN_QUIET_N WIN_AUTO_AV_EVENT_N WIN_EVENT_N WIN_FIRST ERR ERR_1 ABS_ERR PV_1
AV_1 PV_STEP AV_STEP AUTO_AV_MOVING AUTO_AV_QUIET_T MAN_INIT MAN_LAST_AV MAN_DAV
MAN_ACTIVE MAN_NO_CHANGE_T MAN_START_AV MAN_END_AV MAN_SUM_ABS_AV MAN_START_PV
MAN_RESP_ACTIVE MAN_RESP_CT MAN_RESP_START_PV MAN_RESP_PV_MAX MAN_RESP_PV_MIN
MAN_RESP_NET_AV MAN_RESP_SUM_ABS_AV MAN_EVENT_CNT MAN_GAIN_SUM MAN_GAIN_N
MAN_BAD_N MAN_RESP_DELTA MAN_RESP_VALID_T_SUM MAN_RESP_VALID_N W_SP_AVG W_PV_AVG
W_AV_AVG W_EVENT_N W_GAIN W_NOISE_HIGH W_OSC W_SLOW W_AREA_RATIO12
W_AREA_RATIO23 W_AREA_BALANCE W_PEAK_RATIO12 W_PEAK_RATIO23 W_PID_AREA_VALID
W_PID_AREA_DIVERGE W_PID_AREA_EQUAL W_PID_AREA_OSC W_PID_PEAK_VALID W_PT W_TI
W_TD W_DI W_SVH W_SVL W_TL W_TL1 W_TL2 W_TL3 W_TL4 W_E1 W_E2 W_E3 W_E4
W_AO1 W_AO2 W_AO3 W_AO4 W_RSF_LOCK_T W_TC W_TZ W_GC1 W_GC2 W_OUTH W_OUTL
W_CD_GD W_CD_K W_CD_K_FD W_CD_K_J W_CD_K_D W_CDH W_CDL W_TC_CD W_TZ_CD BASE_T
BASE_AO E4_BASE PID_PEAK_MIN W_PT_ABS W_PID_FORMULA_VALID W_MODEL_GAIN_N
W_MODEL_T W_MODEL_L W_MODEL_LAMBDA W_PT_FORMULA W_TI_FORMULA PID_BLEND_USE
W_TI_THEORY W_TI_TARGET LIMIT_TEMP FUSE_W FUSE_SUM_W FUSE_STRONG MATCH_STAGE
MATCH_OK SIM_RELAX_USE SIM_SP_PROP SIM_PV_PROP SIM_AV_PROP SIM_ERR_PROP
SIM_SP_LIMIT SIM_PV_LIMIT SIM_AV_LIMIT SIM_ERR_LIMIT H_VALID H_SP_AVG H_PV_AVG
H_AV_AVG H_ERR_ABS_AVG H_EVENT_N H_NOISE_EST H_GAIN H_PT H_TI H_TD H_DI H_SVH
H_SVL H_TL H_TL1 H_TL2 H_TL3 H_TL4 H_E1 H_E2 H_E3 H_E4 H_AO1 H_AO2 H_AO3
H_AO4 H_RSF_LOCK_T H_TC H_TZ H_GC1 H_GC2 H_OUTH H_OUTL H_CD_GD H_CD_K
H_CD_K_FD H_CD_K_J H_CD_K_D H_CDH H_CDL H_TC_CD H_TZ_CD
""".split())

APCMAUTOPARA_SCHEMA = BlockSchema(
    block_type="APCMAUTOPARA",
    inputs=_input_pins((
        ("EN", "BOOL", False),
        ("RESET", "BOOL", False),
        ("CALC_NOW", "BOOL", False),
        ("CYCLE", "REAL", 0.5),
        ("COLLECT_MODE", "INT", 1),
        ("SP", "REAL", 0.0),
        ("SP_MAN", "REAL", 0.0),
        ("SP_MAN_EN", "BOOL", False),
        ("SP_TAG_EN", "BOOL", True),
        ("SP_AUTO_EN", "BOOL", True),
        ("SP_AUTO_REPLACE_BAD_TAG", "BOOL", False),
        ("SP_STABLE_T", "REAL", 300.0),
        ("SP_CONF_T", "REAL", 900.0),
        ("SP_PV_STABLE_ABS", "REAL", 0.0),
        ("SP_AV_STABLE_ABS", "REAL", 0.0),
        ("PV", "REAL", 0.0),
        ("AV", "REAL", 0.0),
        ("RM", "INT", 1),
        ("TS", "BOOL", False),
        ("PVMU", "REAL", 100.0),
        ("PVMD", "REAL", 0.0),
        ("MU", "REAL", 100.0),
        ("MD", "REAL", 0.0),
        ("OUTT", "REAL", 100.0),
        ("OUTB", "REAL", 0.0),
        ("WIN_T", "REAL", 7200.0),
        ("MIN_WIN_T", "REAL", 300.0),
        ("MIN_STORE_EVENT", "REAL", 1.0),
        ("MIN_VALID_EVENT", "REAL", 5.0),
        ("HISTORY_N", "INT", 24),
        ("FUSE_MIN_N", "REAL", 3.0),
        ("FUSE_MIN_WEIGHT", "REAL", 3.0),
        ("SIM_SP_K", "REAL", 0.05),
        ("SIM_PV_K", "REAL", 0.05),
        ("SIM_AV_K", "REAL", 0.10),
        ("SIM_ERR_K", "REAL", 0.05),
        ("SIM_SP_ABS", "REAL", 0.0),
        ("SIM_PV_ABS", "REAL", 0.0),
        ("SIM_AV_ABS", "REAL", 0.0),
        ("SIM_ERR_ABS", "REAL", 0.0),
        ("SIM_RELAX_K", "REAL", 2.0),
        ("MAN_MERGE_T", "REAL", 10.0),
        ("MAN_RESP_T", "REAL", 60.0),
        ("MAN_RESP_T_MAX", "REAL", 7200.0),
        ("MAN_AV_MIN", "REAL", 0.1),
        ("PT_IN", "REAL", 300.0),
        ("TI_IN", "REAL", 50.0),
        ("TD_IN", "REAL", 0.0),
        ("DI_IN", "REAL", 0.0),
        ("SVH_IN", "REAL", 30.0),
        ("SVL_IN", "REAL", 0.0),
        ("PID_FORMULA_EN", "BOOL", True),
        ("PID_LAMBDA_K", "REAL", 1.5),
        ("PID_MODEL_L_K", "REAL", 0.2),
        ("PID_FORMULA_BLEND", "REAL", 0.8),
        ("TL_IN", "REAL", 10.0),
        ("TL1_IN", "REAL", 120.0),
        ("TL2_IN", "REAL", 120.0),
        ("TL3_IN", "REAL", 120.0),
        ("TL4_IN", "REAL", 120.0),
        ("E1_IN", "REAL", 1.0),
        ("E2_IN", "REAL", 2.0),
        ("E3_IN", "REAL", 3.0),
        ("E4_IN", "REAL", 4.0),
        ("AO1_IN", "REAL", 0.3),
        ("AO2_IN", "REAL", 0.4),
        ("AO3_IN", "REAL", 0.5),
        ("AO4_IN", "REAL", 0.6),
        ("RSF_LOCK_T_IN", "REAL", 30.0),
        ("TC_IN", "REAL", 10.0),
        ("TZ_IN", "REAL", 20.0),
        ("GC1_IN", "REAL", 1.0),
        ("GC2_IN", "REAL", 6.0),
        ("OUTH_IN", "REAL", 5.0),
        ("OUTL_IN", "REAL", -5.0),
        ("CD_GD_IN", "REAL", 0.0),
        ("CD_K_IN", "REAL", 0.5),
        ("CD_K_FD_IN", "REAL", 1.0),
        ("CD_K_J_IN", "REAL", 1.0),
        ("CD_K_D_IN", "REAL", 1.0),
        ("CDH_IN", "REAL", 5.0),
        ("CDL_IN", "REAL", -5.0),
        ("TC_CD_IN", "REAL", 10.0),
        ("TZ_CD_IN", "REAL", 20.0),
    )),
    outputs=_output_pins(
        _APCMAUTOPARA_OUTPUTS,
        bools=("RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG",
               "FINAL_WEAK", "WINDOW_VALID", "SP_VALID", "SP_AUTO_OK",
               "SP_TAG_BAD", "PID_OK", "RSF_OK", "GC_OK", "CD_OK",
               "PID_FORMULA_VALID"),
        ints=("MATCH_LEVEL", "DATA_REASON", "SP_SOURCE", "SP_REASON",
              "PID_REASON", "RSF_REASON", "GC_REASON", "CD_REASON"),
    ),
    descriptor_version="1.0",
    state_vars=_APCMAUTOPARA_STATE,
    output_access=_attr_access(_APCMAUTOPARA_OUTPUTS),
)

APCMAUTOPARA_ADAPTER = RuntimeAdapter(
    cls=APCMAUTOPARA,
    call_adapter=_attr_call(APCMAUTOPARA_SCHEMA),
)


# 按 block_type 字母序稳定注册。
BUSINESS_COMPLEX_DESCRIPTORS = (
    (APCCD_SCHEMA, APCCD_ADAPTER),
    (APCGCQ_SCHEMA, APCGCQ_ADAPTER),
    (APCMAUTOPARA_SCHEMA, APCMAUTOPARA_ADAPTER),
    (APCPID_SCHEMA, APCPID_ADAPTER),
    (APCPIDZZD_SCHEMA, APCPIDZZD_ADAPTER),
    (APCRSFNAUTOPARA_SCHEMA, APCRSFNAUTOPARA_ADAPTER),
    (APCSPFINDER_SCHEMA, APCSPFINDER_ADAPTER),
)
