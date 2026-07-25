"""剩余七个基础原语的 engineering ``BlockSchema + RuntimeAdapter``。

承接 ``representative.py`` 的三个代表性 adapter（TON / APCHSHLLIM / APCM），
本模块为 ``src/primitives`` 中**其余七个基础原语**建立外挂描述符：

- ``TOF`` / ``TP``：有状态定时器，``dt_ms=Task.cycle_ms``，``step`` 返回
  ``(Q, ET_ms)`` **tuple 输出回收**（``return:0`` / ``return:1``，与 TON 同形）。
- ``R_TRIG`` / ``F_TRIG``：边沿检测，真实 ``step(CLK)`` **不接 ``dt_ms``**，
  adapter 不臆造该参数；输出 ``Q`` 由块 ``self.Q`` **attr 回收**（``step``
  返回标量而非 tuple，故按属性读，不做下标猜测）。
- ``SR`` / ``RS``：电平触发双稳态锁存，真实 ``step`` 亦**不接 ``dt_ms``**；
  输出 ``Q1`` 经 ``attr:Q1`` 回收。
- ``BLINK``：方波振荡器，``dt_ms=Task.cycle_ms``；输出 ``OUT`` 经
  ``attr:OUT`` 回收（``step`` 返回标量 ``self.OUT``）。

全部原语输入脚显式选择 ``omit_policy="use_default"``（省略拍每拍回落 Schema
声明默认，**非** ``keep_previous`` 保持上次值），与 TON 同口径。

``state_vars`` 按 ``COMPONENT_CONTRACT`` v2.1 §3「跨周期状态属性名」列出块
实例**真实**的跨拍状态属性——含 TP 的 ``_IN_prev`` / ``_armed``、边沿的
``_CLK_prev``、BLINK 的 ``_elapsed_ms`` 等块内私有字段，忠实反映源类状态，
不只列输出管脚。这些名字仅作元数据暴露（实例内存 / RETAIN 选择 / 序列化
用途，见 §3），``retainable`` 为空、不参与本包管脚分配。

**边界（诚实声明）**：本包只证明这七个原语 adapter 的 **Python 契约**——
经 Registry→Loader/Store/Executor 与直接调用原块的可观察行为一致。默认
注册表因此从 3 扩展到 **10** 个 engineering block type；**仍未**补齐完整
14 业务块 + 8 原语目录（剩余 12 个业务块 adapter 待后继独立工作包），
也**不改动** ``src/primitives``（决策 D1）。这些 Python 对照 **不构成** 与
CODESYS SP16.1 语义一致的证据（BLINK/定时器真机对拍属后续阶段）。
"""
from __future__ import annotations

from src.primitives.blink import BLINK
from src.primitives.edges import F_TRIG, R_TRIG
from src.primitives.latches import RS, SR
from src.primitives.timers import TOF, TP

from src.runtime.descriptors.model import (
    BlockSchema,
    Pin,
    RuntimeAdapter,
    collect_outputs,
)

# ---------------------------------------------------------------------------
# TOF：断开延时定时器（有状态 + dt_ms 注入 + tuple 输出回收）
# ---------------------------------------------------------------------------

TOF_SCHEMA = BlockSchema(
    block_type="TOF",
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


def _tof_call(instance, dt_ms, resolved_inputs, inout_refs):
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        PT_ms=resolved_inputs["PT_ms"])
    return collect_outputs(TOF_SCHEMA.output_access, instance, ret)


TOF_ADAPTER = RuntimeAdapter(cls=TOF, call_adapter=_tof_call)


# ---------------------------------------------------------------------------
# TP：脉冲定时器（有状态 + dt_ms 注入 + tuple 输出回收；不可重触发）
# ---------------------------------------------------------------------------

TP_SCHEMA = BlockSchema(
    block_type="TP",
    inputs=(
        Pin("IN", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
        Pin("PT_ms", "TIME", "VAR_INPUT", default=0, omit_policy="use_default"),
    ),
    outputs=(
        Pin("Q", "BOOL", "VAR_OUTPUT"),
        Pin("ET_ms", "TIME", "VAR_OUTPUT"),
    ),
    descriptor_version="1.0",
    # 跨拍状态属性：输出 Q/ET_ms + 块内私有 _IN_prev/_armed（IEC 不可重触发状态机）
    state_vars=frozenset({"Q", "ET_ms", "_IN_prev", "_armed"}),
    output_access={"Q": "return:0", "ET_ms": "return:1"},
)


def _tp_call(instance, dt_ms, resolved_inputs, inout_refs):
    ret = instance.step(dt_ms, IN=resolved_inputs["IN"],
                        PT_ms=resolved_inputs["PT_ms"])
    return collect_outputs(TP_SCHEMA.output_access, instance, ret)


TP_ADAPTER = RuntimeAdapter(cls=TP, call_adapter=_tp_call)


# ---------------------------------------------------------------------------
# R_TRIG：上升沿检测（真实 step(CLK) 无 dt_ms；标量输出经 attr 回收）
# ---------------------------------------------------------------------------

R_TRIG_SCHEMA = BlockSchema(
    block_type="R_TRIG",
    inputs=(
        Pin("CLK", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
    ),
    outputs=(Pin("Q", "BOOL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    # 跨拍状态属性：输出 Q + 块内私有 _CLK_prev（上一拍 CLK；R_TRIG 初值 False）
    state_vars=frozenset({"Q", "_CLK_prev"}),
    output_access={"Q": "attr:Q"},
)


def _r_trig_call(instance, dt_ms, resolved_inputs, inout_refs):
    # 真实签名 step(CLK)：不传 dt_ms（adapter 不臆造参数）
    instance.step(CLK=resolved_inputs["CLK"])
    return collect_outputs(R_TRIG_SCHEMA.output_access, instance, None)


R_TRIG_ADAPTER = RuntimeAdapter(cls=R_TRIG, call_adapter=_r_trig_call)


# ---------------------------------------------------------------------------
# F_TRIG：下降沿检测（真实 step(CLK) 无 dt_ms；标量输出经 attr 回收）
# ---------------------------------------------------------------------------

F_TRIG_SCHEMA = BlockSchema(
    block_type="F_TRIG",
    inputs=(
        Pin("CLK", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
    ),
    outputs=(Pin("Q", "BOOL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    # 跨拍状态属性：输出 Q + 块内私有 _CLK_prev（F_TRIG 初值 True，IEC 冷启动约定）
    state_vars=frozenset({"Q", "_CLK_prev"}),
    output_access={"Q": "attr:Q"},
)


def _f_trig_call(instance, dt_ms, resolved_inputs, inout_refs):
    instance.step(CLK=resolved_inputs["CLK"])
    return collect_outputs(F_TRIG_SCHEMA.output_access, instance, None)


F_TRIG_ADAPTER = RuntimeAdapter(cls=F_TRIG, call_adapter=_f_trig_call)


# ---------------------------------------------------------------------------
# SR：Set 优先双稳态锁存（真实 step(SET1, RESET) 无 dt_ms；attr 回收 Q1）
# ---------------------------------------------------------------------------

SR_SCHEMA = BlockSchema(
    block_type="SR",
    inputs=(
        Pin("SET1", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
        Pin("RESET", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
    ),
    outputs=(Pin("Q1", "BOOL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    state_vars=frozenset({"Q1"}),
    output_access={"Q1": "attr:Q1"},
)


def _sr_call(instance, dt_ms, resolved_inputs, inout_refs):
    instance.step(SET1=resolved_inputs["SET1"], RESET=resolved_inputs["RESET"])
    return collect_outputs(SR_SCHEMA.output_access, instance, None)


SR_ADAPTER = RuntimeAdapter(cls=SR, call_adapter=_sr_call)


# ---------------------------------------------------------------------------
# RS：Reset 优先双稳态锁存（真实 step(SET, RESET1) 无 dt_ms；attr 回收 Q1）
# ---------------------------------------------------------------------------

RS_SCHEMA = BlockSchema(
    block_type="RS",
    inputs=(
        Pin("SET", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
        Pin("RESET1", "BOOL", "VAR_INPUT", default=False, omit_policy="use_default"),
    ),
    outputs=(Pin("Q1", "BOOL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    state_vars=frozenset({"Q1"}),
    output_access={"Q1": "attr:Q1"},
)


def _rs_call(instance, dt_ms, resolved_inputs, inout_refs):
    instance.step(SET=resolved_inputs["SET"], RESET1=resolved_inputs["RESET1"])
    return collect_outputs(RS_SCHEMA.output_access, instance, None)


RS_ADAPTER = RuntimeAdapter(cls=RS, call_adapter=_rs_call)


# ---------------------------------------------------------------------------
# BLINK：方波振荡器（dt_ms 注入；标量输出 OUT 经 attr 回收）
# ---------------------------------------------------------------------------

BLINK_SCHEMA = BlockSchema(
    block_type="BLINK",
    inputs=(
        Pin("ENABLE", "BOOL", "VAR_INPUT", default=False,
            omit_policy="use_default"),
        Pin("TIMELOW_ms", "TIME", "VAR_INPUT", default=0,
            omit_policy="use_default"),
        Pin("TIMEHIGH_ms", "TIME", "VAR_INPUT", default=0,
            omit_policy="use_default"),
    ),
    outputs=(Pin("OUT", "BOOL", "VAR_OUTPUT"),),
    descriptor_version="1.0",
    # 跨拍状态属性：输出 OUT + 块内私有 _elapsed_ms（相位余数，ENABLE=FALSE 冻结）
    state_vars=frozenset({"OUT", "_elapsed_ms"}),
    output_access={"OUT": "attr:OUT"},
)


def _blink_call(instance, dt_ms, resolved_inputs, inout_refs):
    instance.step(dt_ms, ENABLE=resolved_inputs["ENABLE"],
                  TIMELOW_ms=resolved_inputs["TIMELOW_ms"],
                  TIMEHIGH_ms=resolved_inputs["TIMEHIGH_ms"])
    return collect_outputs(BLINK_SCHEMA.output_access, instance, None)


BLINK_ADAPTER = RuntimeAdapter(cls=BLINK, call_adapter=_blink_call)


# ---------------------------------------------------------------------------
# 本包描述符集合（供 build_default_registry 统一注册）
# ---------------------------------------------------------------------------

#: 七个原语的 ``(schema, adapter)`` 对，按 block_type 字母序稳定排列。
PRIMITIVE_DESCRIPTORS = (
    (BLINK_SCHEMA, BLINK_ADAPTER),
    (F_TRIG_SCHEMA, F_TRIG_ADAPTER),
    (RS_SCHEMA, RS_ADAPTER),
    (R_TRIG_SCHEMA, R_TRIG_ADAPTER),
    (SR_SCHEMA, SR_ADAPTER),
    (TOF_SCHEMA, TOF_ADAPTER),
    (TP_SCHEMA, TP_ADAPTER),
)
