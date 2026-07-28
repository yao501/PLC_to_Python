"""WP-20260723-016：L2 描述符核心（Schema/Adapter 拆分 + Registry）与三个
代表性 adapter 的结构测试。

本文件只锁定 L2 **Python 契约**（纯数据 Schema、注册表拒绝规则、代表性
描述符字段）；``经 Registry/Executor 调用 vs 直接调用原块`` 的行为对照测试
在 ``test_runtime_executor.py``。这些 Python 断言 **不构成** 与 CODESYS
语义一致的证据。
"""
from __future__ import annotations

import json
import inspect
import unittest

from src.runtime.descriptors import (
    APCHSACCUM_ADAPTER,
    APCHSACCUM_SCHEMA,
    APCHSFOP_ADAPTER,
    APCHSFOP_SCHEMA,
    APCHSHLLIM_SCHEMA,
    APCHSRATELIM_ADAPTER,
    APCHSRATELIM_SCHEMA,
    APCHXHCL_ADAPTER,
    APCHXHCL_SCHEMA,
    APCCD_ADAPTER,
    APCCD_SCHEMA,
    APCGCQ_ADAPTER,
    APCGCQ_SCHEMA,
    APCMAUTOPARA_ADAPTER,
    APCMAUTOPARA_SCHEMA,
    APCM_ADAPTER,
    APCM_SCHEMA,
    APCPID_ADAPTER,
    APCPID_SCHEMA,
    APCPIDZZD_ADAPTER,
    APCPIDZZD_SCHEMA,
    APCRSFNAUTOPARA_ADAPTER,
    APCRSFNAUTOPARA_SCHEMA,
    APCSPFINDER_ADAPTER,
    APCSPFINDER_SCHEMA,
    APCSTATISTICS_ADAPTER,
    APCSTATISTICS_SCHEMA,
    AdapterBindingError,
    BLINK_ADAPTER,
    BLINK_SCHEMA,
    BUSINESS_BASIC_DESCRIPTORS,
    BUSINESS_COMPLEX_DESCRIPTORS,
    BlockSchema,
    DuplicateDescriptorError,
    F_TRIG_ADAPTER,
    F_TRIG_SCHEMA,
    MissingVariantError,
    OMIT_POLICIES,
    PRIMITIVE_DESCRIPTORS,
    Pin,
    parse_output_access,
    RS_ADAPTER,
    RS_SCHEMA,
    R_TRIG_ADAPTER,
    R_TRIG_SCHEMA,
    Registry,
    RuntimeAdapter,
    SR_ADAPTER,
    SR_SCHEMA,
    SchemaValidationError,
    TOF_ADAPTER,
    TOF_SCHEMA,
    TON_ADAPTER,
    TON_SCHEMA,
    TP_ADAPTER,
    TP_SCHEMA,
    UnknownBlockError,
    build_default_registry,
    collect_outputs,
    variant_for_mode,
)
from src.blocks.apchsaccum import APCHSACCUM
from src.blocks.apchsfop import APCHSFOP
from src.blocks.apchsratelim import APCHSRATELIM
from src.blocks.apchxhcl import APCHXHCL
from src.blocks.apcstatistics import APCSTATISTICS
from src.blocks.apccd import APCCD
from src.blocks.apcgcq import APCGCQ
from src.blocks.apcmautopara import APCMAUTOPARA
from src.blocks.apcpid import APCPID
from src.blocks.apcpidzzd import APCPIDZZD
from src.blocks.apcrsfnautopara import APCRSFNAUTOPARA
from src.blocks.apcspfinder import APCSPFINDER
from src.primitives.blink import BLINK
from src.primitives.edges import F_TRIG, R_TRIG
from src.primitives.latches import RS, SR
from src.primitives.timers import TOF, TP


# ---------------------------------------------------------------------------
# Pin / BlockSchema 结构校验
# ---------------------------------------------------------------------------

class TestPinValidation(unittest.TestCase):
    def test_valid_pin(self):
        p = Pin("IN", "BOOL", "VAR_INPUT", default=False)
        self.assertEqual(p.to_json()["iec_type"], "BOOL")

    def test_illegal_fields_rejected(self):
        with self.assertRaises(SchemaValidationError):
            Pin("", "BOOL")                          # 空名
        with self.assertRaises(SchemaValidationError):
            Pin("X", "NOPE")                         # 非法 IEC 类型
        with self.assertRaises(SchemaValidationError):
            Pin("X", "BOOL", "VAR_WRONG")            # 非法 kind
        with self.assertRaises(SchemaValidationError):
            Pin("X", "BOOL", "VAR_INPUT", omit_policy="whenever")   # 非法 omit
        with self.assertRaises(SchemaValidationError):
            Pin("X", "REAL", "VAR_INPUT", default=object())         # 非 JSON 默认


class TestSchemaValidation(unittest.TestCase):
    def _schema(self, **kw):
        base = dict(
            block_type="B",
            inputs=(Pin("A", "REAL", "VAR_INPUT", omit_policy="required"),),
            outputs=(Pin("O", "REAL", "VAR_OUTPUT"),),
            output_access={"O": "return:O"},
        )
        base.update(kw)
        return BlockSchema(**base)

    def test_valid(self):
        s = self._schema()
        self.assertEqual(s.variant, "engineering")
        self.assertEqual(s.pin("A").iec_type, "REAL")

    def test_wrong_kind_in_collection(self):
        with self.assertRaises(SchemaValidationError):
            self._schema(inputs=(Pin("A", "REAL", "VAR_OUTPUT"),))

    def test_duplicate_and_cross_collection(self):
        with self.assertRaises(SchemaValidationError):
            self._schema(inputs=(Pin("A", "REAL", "VAR_INPUT"),
                                 Pin("A", "REAL", "VAR_INPUT")))
        with self.assertRaises(SchemaValidationError):        # 跨集合冲突
            self._schema(inputs=(Pin("O", "REAL", "VAR_INPUT"),))

    def test_output_access_rules(self):
        with self.assertRaises(SchemaValidationError):        # 未覆盖输出
            self._schema(output_access={})
        with self.assertRaises(SchemaValidationError):        # 键非输出管脚
            self._schema(output_access={"O": "return:O", "A": "attr:A"})
        with self.assertRaises(SchemaValidationError):        # 规则形态非法
            self._schema(output_access={"O": "self.O"})

    def test_variant_and_version(self):
        with self.assertRaises(SchemaValidationError):
            self._schema(variant="fidelity_f9")
        with self.assertRaises(SchemaValidationError):
            self._schema(descriptor_version="")

    def test_retainable_subset(self):
        with self.assertRaises(SchemaValidationError):
            self._schema(state_vars=frozenset({"x"}),
                         retainable=frozenset({"y"}))

    def test_output_inout_no_omit_policy(self):
        with self.assertRaises(SchemaValidationError):
            self._schema(outputs=(Pin("O", "REAL", "VAR_OUTPUT",
                                      omit_policy="required"),))

    def test_pure_data_and_json_serializable(self):
        s = APCM_SCHEMA
        blob = json.dumps(s.to_json())          # 仅 JSON 基本类型即可序列化
        loaded = json.loads(blob)
        self.assertEqual(loaded["block_type"], "APCM")
        self.assertIn("ZLOUT", [p["name"] for p in loaded["inouts"]])
        # Schema 不得持有 callable
        for attr in ("cls", "call_adapter", "serializer"):
            self.assertFalse(hasattr(s, attr))

    def test_input_mutation_does_not_drift_schema(self):
        inputs = [Pin("A", "REAL", "VAR_INPUT", omit_policy="required")]
        access = {"O": "return:O"}
        s = BlockSchema(block_type="B", inputs=tuple(inputs),
                        outputs=(Pin("O", "REAL", "VAR_OUTPUT"),),
                        output_access=access)
        inputs.append(Pin("X", "REAL", "VAR_INPUT"))    # 事后改传入列表
        access["Z"] = "attr:Z"                          # 事后改传入 dict
        self.assertEqual(len(s.inputs), 1)              # 不漂移
        self.assertEqual(set(s.output_access), {"O"})
        with self.assertRaises(TypeError):              # output_access 只读
            s.output_access["Q"] = "attr:Q"


# ---------------------------------------------------------------------------
# collect_outputs（tuple / dict / attr）
# ---------------------------------------------------------------------------

class TestCollectOutputs(unittest.TestCase):
    def test_tuple_dict_attr(self):
        class _Obj:
            AV = 3.0
        self.assertEqual(collect_outputs({"Q": "return:0", "E": "return:1"},
                                         _Obj(), (True, 42)),
                         {"Q": True, "E": 42})
        self.assertEqual(collect_outputs({"AV": "return:AV"}, _Obj(),
                                         {"AV": 1.5}), {"AV": 1.5})
        self.assertEqual(collect_outputs({"AV": "attr:AV"}, _Obj(), None),
                         {"AV": 3.0})


# ---------------------------------------------------------------------------
# RuntimeAdapter
# ---------------------------------------------------------------------------

class TestRuntimeAdapter(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(AdapterBindingError):
            RuntimeAdapter(cls="not-a-class", call_adapter=lambda *a: {})
        with self.assertRaises(AdapterBindingError):
            RuntimeAdapter(cls=int, call_adapter=None)
        with self.assertRaises(AdapterBindingError):
            RuntimeAdapter(cls=int, call_adapter=lambda *a: {}, ctor_args=("",))
        with self.assertRaises(AdapterBindingError):
            RuntimeAdapter(cls=int, call_adapter=lambda *a: {}, serializer=5)

    def test_construct_resolves_and_fails_closed(self):
        class _Dep:
            def __init__(self, ctx):
                self.ctx = ctx
        ad = RuntimeAdapter(cls=_Dep, call_adapter=lambda *a: {},
                            ctor_args=("license_context",))
        obj = ad.construct({"license_context": "CTX"})
        self.assertEqual(obj.ctx, "CTX")
        with self.assertRaises(AdapterBindingError):
            ad.construct({})                    # 缺依赖 fail-closed


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry(unittest.TestCase):
    def test_register_resolve_and_variants(self):
        r = build_default_registry()
        # WP-20260727-033：默认注册表扩展为 22 个 engineering block type
        self.assertEqual(
            r.block_types(),
            ("APCCD", "APCGCQ", "APCHSACCUM", "APCHSFOP", "APCHSHLLIM",
             "APCHSRATELIM", "APCHXHCL", "APCM", "APCMAUTOPARA", "APCPID",
             "APCPIDZZD", "APCRSFNAUTOPARA", "APCSPFINDER", "APCSTATISTICS",
             "BLINK", "F_TRIG", "RS", "R_TRIG", "SR", "TOF", "TON", "TP"))
        # 精确 22 个 (block_type, "engineering") 键，无 fidelity_f2 变体
        self.assertEqual(len(r.keys()), 22)
        self.assertTrue(all(v == "engineering" for _, v in r.keys()))
        for mode in ("engineering", "fidelity_f1"):
            s, a = r.resolve("TON", mode)
            self.assertEqual(s.variant, "engineering")
            self.assertIs(a, TON_ADAPTER)
        self.assertEqual(variant_for_mode("fidelity_f2"), "fidelity_f2")

    def test_duplicate_rejected(self):
        r = Registry()
        r.register(TON_SCHEMA, TON_ADAPTER)
        with self.assertRaises(DuplicateDescriptorError):
            r.register(TON_SCHEMA, TON_ADAPTER)

    def test_f2_missing_no_silent_fallback(self):
        r = build_default_registry()
        with self.assertRaises(MissingVariantError):
            r.resolve("APCM", "fidelity_f2")

    def test_unknown_block(self):
        r = build_default_registry()
        with self.assertRaises(UnknownBlockError):
            r.resolve("GHOST", "engineering")

    def test_schema_adapter_type_mismatch(self):
        r = Registry()
        with self.assertRaises(Exception):
            r.register(TON_SCHEMA, "not-an-adapter")


# ---------------------------------------------------------------------------
# 代表性描述符字段锁定
# ---------------------------------------------------------------------------

class TestRepresentativeDescriptors(unittest.TestCase):
    def test_ton_tuple_output_and_defaults(self):
        self.assertEqual(TON_SCHEMA.output_access["Q"], "return:0")
        self.assertEqual(TON_SCHEMA.pin("IN").omit_policy, "use_default")
        self.assertEqual(TON_SCHEMA.pin("PT_ms").iec_type, "TIME")

    def test_apchshllim_required_and_dict_output(self):
        self.assertEqual(APCHSHLLIM_SCHEMA.output_access["AV"], "return:AV")
        for name in ("IN", "HL", "LL"):
            self.assertEqual(APCHSHLLIM_SCHEMA.pin(name).omit_policy, "required")

    def test_apcm_omit_policies_and_ctor(self):
        self.assertEqual(APCM_ADAPTER.ctor_args, ("license_context",))
        self.assertEqual(APCM_SCHEMA.pin("RM").omit_policy, "none_means_no_write")
        self.assertEqual(APCM_SCHEMA.pin("ZSYK").omit_policy, "keep_previous")
        for name in ("SP", "PV", "OC", "TS", "TP"):
            self.assertEqual(APCM_SCHEMA.pin(name).omit_policy, "required")
        self.assertEqual([p.name for p in APCM_SCHEMA.inouts], ["ZLOUT"])


# ---------------------------------------------------------------------------
# WP-20260724-023：其余七个基础原语描述符字段锁定
# ---------------------------------------------------------------------------

class TestPrimitiveDescriptors(unittest.TestCase):
    """七个原语 Schema/Adapter 的纯结构锁定（block_type、管脚名、IEC 类型、
    声明默认值、输出访问、state_vars、cls）；行为对照测试在
    ``test_runtime_executor.py``。这些断言 **不构成** 与 CODESYS 一致的证据。"""

    def test_all_inputs_use_default(self):
        # 原语声明输入一律 use_default（省略拍回落 Schema 默认，非 keep_previous）
        for schema in (TOF_SCHEMA, TP_SCHEMA, R_TRIG_SCHEMA, F_TRIG_SCHEMA,
                       SR_SCHEMA, RS_SCHEMA, BLINK_SCHEMA):
            for pin in schema.inputs:
                self.assertEqual(pin.omit_policy, "use_default",
                                 (schema.block_type, pin.name))

    def test_tof_tp_tuple_output_and_state(self):
        for schema, adapter, cls in ((TOF_SCHEMA, TOF_ADAPTER, TOF),
                                     (TP_SCHEMA, TP_ADAPTER, TP)):
            self.assertEqual([p.name for p in schema.inputs], ["IN", "PT_ms"])
            self.assertEqual(schema.pin("IN").iec_type, "BOOL")
            self.assertEqual(schema.pin("IN").default, False)
            self.assertEqual(schema.pin("PT_ms").iec_type, "TIME")
            self.assertEqual(schema.pin("PT_ms").default, 0)
            self.assertEqual([p.name for p in schema.outputs], ["Q", "ET_ms"])
            self.assertEqual(schema.output_access["Q"], "return:0")
            self.assertEqual(schema.output_access["ET_ms"], "return:1")
            self.assertIs(adapter.cls, cls)
            self.assertEqual(adapter.ctor_args, ())
        # TP 额外暴露块内跨拍私有状态（不可重触发状态机）
        self.assertEqual(TOF_SCHEMA.state_vars, frozenset({"Q", "ET_ms"}))
        self.assertEqual(TP_SCHEMA.state_vars,
                         frozenset({"Q", "ET_ms", "_IN_prev", "_armed"}))

    def test_edge_detectors_clk_and_attr_output(self):
        for schema, adapter, cls in ((R_TRIG_SCHEMA, R_TRIG_ADAPTER, R_TRIG),
                                     (F_TRIG_SCHEMA, F_TRIG_ADAPTER, F_TRIG)):
            self.assertEqual([p.name for p in schema.inputs], ["CLK"])
            self.assertEqual(schema.pin("CLK").iec_type, "BOOL")
            self.assertEqual([p.name for p in schema.outputs], ["Q"])
            # 标量返回 → attr 回收（不做 tuple 下标猜测）
            self.assertEqual(schema.output_access["Q"], "attr:Q")
            self.assertEqual(schema.state_vars, frozenset({"Q", "_CLK_prev"}))
            self.assertIs(adapter.cls, cls)

    def test_latches_setreset_names_and_priority_pins(self):
        # SR 的 set 脚为 SET1、复位脚为 RESET；RS 的 set 脚为 SET、复位脚为
        # RESET1——与源类真实 step 关键字精确一致（不可互换）
        self.assertEqual([p.name for p in SR_SCHEMA.inputs], ["SET1", "RESET"])
        self.assertEqual([p.name for p in RS_SCHEMA.inputs], ["SET", "RESET1"])
        for schema, adapter, cls in ((SR_SCHEMA, SR_ADAPTER, SR),
                                     (RS_SCHEMA, RS_ADAPTER, RS)):
            self.assertEqual([p.name for p in schema.outputs], ["Q1"])
            self.assertEqual(schema.output_access["Q1"], "attr:Q1")
            self.assertEqual(schema.state_vars, frozenset({"Q1"}))
            self.assertIs(adapter.cls, cls)

    def test_blink_pins_and_state(self):
        self.assertEqual([p.name for p in BLINK_SCHEMA.inputs],
                         ["ENABLE", "TIMELOW_ms", "TIMEHIGH_ms"])
        self.assertEqual(BLINK_SCHEMA.pin("ENABLE").iec_type, "BOOL")
        self.assertEqual(BLINK_SCHEMA.pin("TIMELOW_ms").iec_type, "TIME")
        self.assertEqual(BLINK_SCHEMA.pin("TIMEHIGH_ms").iec_type, "TIME")
        self.assertEqual([p.name for p in BLINK_SCHEMA.outputs], ["OUT"])
        self.assertEqual(BLINK_SCHEMA.output_access["OUT"], "attr:OUT")
        self.assertEqual(BLINK_SCHEMA.state_vars,
                         frozenset({"OUT", "_elapsed_ms"}))
        self.assertIs(BLINK_ADAPTER.cls, BLINK)

    def test_no_inouts_and_json_serializable(self):
        # 七个原语均无 VAR_IN_OUT，且纯数据 Schema 可 JSON 序列化
        for schema in (TOF_SCHEMA, TP_SCHEMA, R_TRIG_SCHEMA, F_TRIG_SCHEMA,
                       SR_SCHEMA, RS_SCHEMA, BLINK_SCHEMA):
            self.assertEqual(schema.inouts, ())
            self.assertEqual(schema.variant, "engineering")
            blob = json.dumps(schema.to_json())
            self.assertEqual(json.loads(blob)["block_type"], schema.block_type)

    def test_primitive_descriptors_collection(self):
        # PRIMITIVE_DESCRIPTORS 恰含七个原语，block_type 唯一且与常量一致
        types = [s.block_type for s, _ in PRIMITIVE_DESCRIPTORS]
        self.assertEqual(sorted(types),
                         ["BLINK", "F_TRIG", "RS", "R_TRIG", "SR", "TOF", "TP"])
        self.assertEqual(len(types), len(set(types)))

    def test_default_registry_registers_ten_without_duplicate(self):
        # build_default_registry 现稳定注册 22 个（10 原有 + 12 业务块），且原有
        # 三代表性块不被覆盖/改写
        r = build_default_registry()
        self.assertEqual(len(r.keys()), 22)
        self.assertIs(r.resolve("TON", "engineering")[1], TON_ADAPTER)
        self.assertIs(r.resolve("BLINK", "engineering")[1], BLINK_ADAPTER)
        # 七个原语缺 fidelity_f2 变体：加载期显式失败，绝不静默降级
        for bt in ("TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK"):
            with self.assertRaises(MissingVariantError):
                r.resolve(bt, "fidelity_f2")


# ---------------------------------------------------------------------------
# WP-20260727-026：五个基础业务块描述符字段锁定
# ---------------------------------------------------------------------------

class TestBusinessBasicDescriptors(unittest.TestCase):
    """五个基础业务块 Schema/Adapter 的纯结构锁定（block_type、管脚名、IEC
    类型、OmitPolicy、声明默认值、输出访问、state_vars、cls、ctor_args）；
    逐块行为对照测试在 ``test_runtime_executor.py``。这些断言 **不构成** 与
    CODESYS 语义一致的证据。"""

    def test_apcstatistics_required_dict_output_and_state(self):
        s, a = APCSTATISTICS_SCHEMA, APCSTATISTICS_ADAPTER
        self.assertEqual([p.name for p in s.inputs], ["IN", "RESET"])
        self.assertEqual(s.pin("IN").iec_type, "REAL")
        self.assertEqual(s.pin("RESET").iec_type, "BOOL")
        for name in ("IN", "RESET"):
            self.assertEqual(s.pin(name).omit_policy, "required")
        self.assertEqual([p.name for p in s.outputs], ["MN", "MX", "AVG"])
        for name in ("MN", "MX", "AVG"):
            self.assertEqual(s.output_access[name], "return:%s" % name)
        # WP-20260727-029：MN/MX 仍为 REAL，AVG 忠实声明为 LREAL（源块
        # AVG:LREAL / Welford binary64 与 RISKS::APCSTATISTICS-S6 一致，用户严格
        # 类型裁决）。两类不得混同：LREAL ≠ REAL，即便都映射到 Python float。
        self.assertEqual(s.pin("MN").iec_type, "REAL")
        self.assertEqual(s.pin("MX").iec_type, "REAL")
        self.assertEqual(s.pin("AVG").iec_type, "LREAL")
        self.assertNotEqual(s.pin("AVG").iec_type, s.pin("MN").iec_type)
        self.assertEqual(s.state_vars,
                         frozenset({"MN", "MX", "AVG", "COUNTER"}))
        self.assertEqual(a.ctor_args, ())
        self.assertIs(a.cls, APCSTATISTICS)

    def test_apcstatistics_avg_lreal_survives_json_serialization(self):
        # AVG:LREAL 是纯数据 Schema 的一部分，须原样穿过 to_json/json.dumps，
        # 不得在序列化时被折叠成 REAL（结构映射 ≠ 类型相同）。
        s = APCSTATISTICS_SCHEMA
        loaded = json.loads(json.dumps(s.to_json()))
        out_types = {p["name"]: p["iec_type"] for p in loaded["outputs"]}
        self.assertEqual(out_types, {"MN": "REAL", "MX": "REAL", "AVG": "LREAL"})

    def test_apchsfop_all_required_and_tb_not_omittable(self):
        s, a = APCHSFOP_SCHEMA, APCHSFOP_ADAPTER
        self.assertEqual([p.name for p in s.inputs], ["IN", "TC", "KG", "TB"])
        for name in ("IN", "TC", "KG", "TB"):
            self.assertEqual(s.pin(name).iec_type, "REAL")
            # TB 不可省略：required，adapter 不擅自声明 TB=0.5 默认
            self.assertEqual(s.pin(name).omit_policy, "required")
            self.assertIsNone(s.pin(name).default)
        self.assertEqual([p.name for p in s.outputs], ["AV"])
        self.assertEqual(s.output_access["AV"], "return:AV")
        self.assertEqual(s.state_vars, frozenset({"AV", "Ok_1", "AV_TEMP"}))
        # 源 ST RETAIN 元数据仅作 state_vars 暴露，retainable 留空（不声称阶段 8）
        self.assertEqual(s.retainable, frozenset())
        self.assertEqual(a.ctor_args, ())
        self.assertIs(a.cls, APCHSFOP)

    def test_apchsratelim_required_and_state(self):
        s, a = APCHSRATELIM_SCHEMA, APCHSRATELIM_ADAPTER
        self.assertEqual([p.name for p in s.inputs], ["IN", "HL", "LL"])
        for name in ("IN", "HL", "LL"):
            self.assertEqual(s.pin(name).iec_type, "REAL")
            self.assertEqual(s.pin(name).omit_policy, "required")
        self.assertEqual([p.name for p in s.outputs], ["AV"])
        self.assertEqual(s.output_access["AV"], "return:AV")
        self.assertEqual(s.state_vars, frozenset({"AV", "AV_1"}))
        self.assertEqual(a.ctor_args, ())
        self.assertIs(a.cls, APCHSRATELIM)

    def test_apchsaccum_use_default_dict_output_and_state(self):
        s, a = APCHSACCUM_SCHEMA, APCHSACCUM_ADAPTER
        self.assertEqual([p.name for p in s.inputs], ["I1", "RS"])
        self.assertEqual(s.pin("I1").iec_type, "REAL")
        self.assertEqual(s.pin("I1").default, 0.0)
        self.assertEqual(s.pin("RS").iec_type, "BOOL")
        self.assertEqual(s.pin("RS").default, False)
        for name in ("I1", "RS"):
            self.assertEqual(s.pin(name).omit_policy, "use_default")
        self.assertEqual([p.name for p in s.outputs], ["AV", "SS"])
        self.assertEqual(s.output_access["AV"], "return:AV")
        self.assertEqual(s.output_access["SS"], "return:SS")
        # WP-20260727-032：源 ST AV:LREAL；SS 仍为 BOOL。不得因调用方可能
        # 连接 REAL 变量而把形式输出谎报为 REAL。
        self.assertEqual(s.pin("AV").iec_type, "LREAL")
        self.assertEqual(s.pin("SS").iec_type, "BOOL")
        self.assertEqual(s.state_vars, frozenset({
            "AV", "SS", "IV", "MS", "MC", "LR", "preRS", "bPositiveAccum"}))
        # IV/MS/MC 是实例级配置，不冒充 step 输入或 ctor_args 共享依赖
        self.assertEqual(a.ctor_args, ())
        # 非默认构造配置留给后续参数装载工作包：本包不声明 init_overridable/hmi
        self.assertEqual(s.init_overridable, frozenset())
        self.assertEqual(s.hmi_writable, frozenset())
        self.assertIs(a.cls, APCHSACCUM)

    def test_apchsaccum_av_lreal_survives_json_serialization(self):
        # AV:LREAL 必须原样穿过纯数据 Schema 序列化；Python float 的共同
        # 宿主表示不意味着 IEC REAL/LREAL 可合并。
        loaded = json.loads(json.dumps(APCHSACCUM_SCHEMA.to_json()))
        out_types = {p["name"]: p["iec_type"] for p in loaded["outputs"]}
        self.assertEqual(out_types, {"AV": "LREAL", "SS": "BOOL"})

    def test_apchxhcl_pins_defaults_output_and_state(self):
        s, a = APCHXHCL_SCHEMA, APCHXHCL_ADAPTER
        self.assertEqual([p.name for p in s.inputs],
                         ["EN", "PV", "FV", "PVH", "PVL", "BHSLH",
                          "TL", "TC", "KG", "TB"])
        for name in ("EN", "PV", "FV"):
            self.assertEqual(s.pin(name).omit_policy, "required")
        self.assertEqual(s.pin("EN").iec_type, "BOOL")
        # use_default 脚的声明默认值与源块 step 签名逐一一致
        expected_defaults = {
            "PVH": 1_000_000.0, "PVL": -100_000.0, "BHSLH": 100_000.0,
            "TL": 60.0, "TC": 1.0, "KG": 1.0, "TB": 0.5,
        }
        for name, default in expected_defaults.items():
            self.assertEqual(s.pin(name).omit_policy, "use_default")
            self.assertEqual(s.pin(name).default, default, name)
            self.assertEqual(s.pin(name).iec_type, "REAL")
        self.assertEqual([p.name for p in s.outputs],
                         ["AV", "GZDV", "PV_AVG", "FV_AVG"])
        self.assertEqual(s.pin("GZDV").iec_type, "BOOL")
        for name in ("AV", "GZDV", "PV_AVG", "FV_AVG"):
            self.assertEqual(s.output_access[name], "return:%s" % name)
        # state_vars 精确覆盖源块 __init__ 全部 20 个实例属性（含子块实例名）
        self.assertEqual(s.state_vars, frozenset({
            "TOF1", "TOF2", "R_TRIG3",
            "AV", "GZDV", "PV_AVG", "FV_AVG",
            "PV_1", "Ok_1", "AV_TEMP", "PV_TEMP", "FV_TEMP",
            "SAMPLE_N", "SUM", "NUM", "SUM1", "NUM1",
            "GZDV_RAW", "INIT_OK", "A"}))
        self.assertEqual(len(s.state_vars), 20)
        self.assertEqual(a.ctor_args, ())
        self.assertIs(a.cls, APCHXHCL)

    def test_no_inouts_engineering_and_json_serializable(self):
        for s in (APCSTATISTICS_SCHEMA, APCHSFOP_SCHEMA, APCHSRATELIM_SCHEMA,
                  APCHSACCUM_SCHEMA, APCHXHCL_SCHEMA):
            self.assertEqual(s.inouts, ())
            self.assertEqual(s.variant, "engineering")
            self.assertEqual(s.descriptor_version, "1.0")
            blob = json.dumps(s.to_json())
            self.assertEqual(json.loads(blob)["block_type"], s.block_type)

    def test_business_basic_descriptors_collection(self):
        types = [s.block_type for s, _ in BUSINESS_BASIC_DESCRIPTORS]
        self.assertEqual(types,
                         ["APCHSACCUM", "APCHSFOP", "APCHSRATELIM",
                          "APCHXHCL", "APCSTATISTICS"])
        self.assertEqual(len(types), len(set(types)))

    def test_default_registry_registers_fifteen_business_included(self):
        r = build_default_registry()
        self.assertEqual(len(r.keys()), 22)
        for bt, adapter in (("APCSTATISTICS", APCSTATISTICS_ADAPTER),
                            ("APCHSFOP", APCHSFOP_ADAPTER),
                            ("APCHSRATELIM", APCHSRATELIM_ADAPTER),
                            ("APCHSACCUM", APCHSACCUM_ADAPTER),
                            ("APCHXHCL", APCHXHCL_ADAPTER)):
            # engineering / fidelity_f1 均解析 engineering
            for mode in ("engineering", "fidelity_f1"):
                self.assertIs(r.resolve(bt, mode)[1], adapter, (bt, mode))
            # 缺 fidelity_f2 变体：加载期显式失败，绝不静默降级
            with self.assertRaises(MissingVariantError):
                r.resolve(bt, "fidelity_f2")


# ---------------------------------------------------------------------------
# WP-20260727-033：七个复杂／组合／授权业务块描述符
# ---------------------------------------------------------------------------

class TestBusinessComplexDescriptors(unittest.TestCase):
    """逐项锁定真实签名、默认/OmitPolicy、输出、state_vars 与构造依赖。"""

    _CASES = (
        (APCCD, APCCD_SCHEMA, APCCD_ADAPTER),
        (APCGCQ, APCGCQ_SCHEMA, APCGCQ_ADAPTER),
        (APCMAUTOPARA, APCMAUTOPARA_SCHEMA, APCMAUTOPARA_ADAPTER),
        (APCPID, APCPID_SCHEMA, APCPID_ADAPTER),
        (APCPIDZZD, APCPIDZZD_SCHEMA, APCPIDZZD_ADAPTER),
        (APCRSFNAUTOPARA, APCRSFNAUTOPARA_SCHEMA,
         APCRSFNAUTOPARA_ADAPTER),
        (APCSPFINDER, APCSPFINDER_SCHEMA, APCSPFINDER_ADAPTER),
    )

    @staticmethod
    def _instance(cls):
        # 两个授权类构造时只保存依赖句柄；结构测试无需调用授权逻辑。
        return cls(object()) if cls in (APCPID, APCPIDZZD) else cls()

    def test_step_signature_defaults_and_types_match_schema(self):
        py_to_iec = {"float": "REAL", "int": "INT", "bool": "BOOL"}
        for cls, schema, _ in self._CASES:
            sig = inspect.signature(cls.step)
            source_params = [
                p for p in sig.parameters.values()
                if p.name not in ("self", "dt_ms", "ZLOUT")
            ]
            self.assertEqual([p.name for p in schema.inputs],
                             [p.name for p in source_params], cls.__name__)
            for param in source_params:
                pin = schema.pin(param.name)
                annotation = param.annotation
                annotation_name = (annotation if isinstance(annotation, str)
                                   else annotation.__name__)
                self.assertEqual(pin.iec_type, py_to_iec[annotation_name],
                                 (cls.__name__, param.name))
                if param.default is inspect.Parameter.empty:
                    self.assertEqual(pin.omit_policy, "required",
                                     (cls.__name__, param.name))
                    self.assertIsNone(pin.default)
                else:
                    self.assertEqual(pin.omit_policy, "use_default",
                                     (cls.__name__, param.name))
                    self.assertEqual(pin.default, param.default,
                                     (cls.__name__, param.name))

    def test_output_access_types_and_counts(self):
        expected_counts = {
            "APCCD": 2, "APCGCQ": 3, "APCMAUTOPARA": 87,
            "APCPID": 1, "APCPIDZZD": 2, "APCRSFNAUTOPARA": 56,
            "APCSPFINDER": 10,
        }
        int_outputs = {
            "MATCH_LEVEL", "DATA_REASON", "SP_SOURCE", "SP_REASON",
            "PID_REASON", "RSF_REASON", "GC_REASON", "CD_REASON",
        }
        bool_outputs = {
            "RUNNING", "WINDOW_DONE", "FINAL_VALID", "FINAL_STRONG",
            "FINAL_WEAK", "WINDOW_VALID", "SP_VALID", "SP_AUTO_OK",
            "SP_TAG_BAD", "PID_OK", "RSF_OK", "GC_OK", "CD_OK",
            "PID_FORMULA_VALID",
        }
        for _, schema, _ in self._CASES:
            self.assertEqual(len(schema.outputs),
                             expected_counts[schema.block_type])
            self.assertEqual(set(schema.output_access),
                             {p.name for p in schema.outputs})
            for pin in schema.outputs:
                expected = ("BOOL" if pin.name in bool_outputs else
                            "INT" if pin.name in int_outputs else "REAL")
                self.assertEqual(pin.iec_type, expected,
                                 (schema.block_type, pin.name))
                prefix = "return:" if schema.block_type in (
                    "APCCD", "APCGCQ") else "attr:"
                self.assertEqual(schema.output_access[pin.name],
                                 prefix + pin.name)

    def test_state_vars_exactly_match_constructed_instance(self):
        for cls, schema, _ in self._CASES:
            actual = set(vars(self._instance(cls)))
            # _ctx 是 Python 注入依赖句柄，不是 PLC 跨拍实例内存字段。
            actual.discard("_ctx")
            self.assertEqual(schema.state_vars, frozenset(actual), cls.__name__)
        self.assertEqual(len(APCRSFNAUTOPARA_SCHEMA.state_vars), 176)
        self.assertEqual(len(APCMAUTOPARA_SCHEMA.state_vars), 299)

    def test_constructor_dependencies_and_composition(self):
        self.assertEqual(APCPIDZZD_ADAPTER.ctor_args,
                         ("license_context",))
        self.assertEqual(APCPID_ADAPTER.ctor_args, ("license_context",))
        for cls, _, adapter in self._CASES:
            if cls not in (APCPID, APCPIDZZD):
                self.assertEqual(adapter.ctor_args, (), cls.__name__)
        ctx = object()
        pid = APCPID_ADAPTER.construct({"license_context": ctx})
        zzd = APCPIDZZD_ADAPTER.construct({"license_context": ctx})
        self.assertIs(pid._ctx, ctx)
        self.assertIs(pid.PIDZZD1._ctx, ctx)
        self.assertIs(zzd._ctx, ctx)
        with self.assertRaises(AdapterBindingError):
            APCPID_ADAPTER.construct({})
        with self.assertRaises(AdapterBindingError):
            APCPIDZZD_ADAPTER.construct({})

    def test_apccd_zlout_only_inout(self):
        s = APCCD_SCHEMA
        self.assertEqual([p.name for p in s.inouts], ["ZLOUT"])
        self.assertEqual(s.inouts[0].iec_type, "REAL")
        self.assertNotIn("ZLOUT", [p.name for p in s.inputs])
        self.assertNotIn("ZLOUT", [p.name for p in s.outputs])

    def test_all_json_serializable_and_no_unclaimed_metadata(self):
        for _, schema, _ in self._CASES:
            loaded = json.loads(json.dumps(schema.to_json()))
            self.assertEqual(loaded["block_type"], schema.block_type)
            self.assertEqual(schema.variant, "engineering")
            self.assertEqual(schema.descriptor_version, "1.0")
            self.assertEqual(schema.retainable, frozenset())
            self.assertEqual(schema.init_overridable, frozenset())
            self.assertEqual(schema.hmi_writable, frozenset())

    def test_collection_and_registry_exact_22(self):
        expected_complex = [
            "APCCD", "APCGCQ", "APCMAUTOPARA", "APCPID", "APCPIDZZD",
            "APCRSFNAUTOPARA", "APCSPFINDER",
        ]
        self.assertEqual(
            [s.block_type for s, _ in BUSINESS_COMPLEX_DESCRIPTORS],
            expected_complex)
        registry = build_default_registry()
        self.assertEqual(len(registry.keys()), 22)
        expected_all = {
            "TON", "TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK",
            "APCHSHLLIM", "APCM", "APCHSACCUM", "APCHSFOP",
            "APCHSRATELIM", "APCHXHCL", "APCSTATISTICS",
            *expected_complex,
        }
        self.assertEqual({block_type for block_type, _ in registry.keys()},
                         expected_all)
        for block_type in expected_complex:
            self.assertEqual(
                registry.resolve(block_type, "engineering")[0].block_type,
                block_type)
            self.assertIs(
                registry.resolve(block_type, "fidelity_f1")[0],
                registry.resolve(block_type, "engineering")[0])
            with self.assertRaises(MissingVariantError):
                registry.resolve(block_type, "fidelity_f2")


# ---------------------------------------------------------------------------
# WP-20260728-040：22/22 engineering adapter 目录级集中验收（独立反证）
#
# 前面各测试类按块/家族分散锁定字段；本类把整份默认目录当作一个可审计单元，
# 独立复算精确注册键集合并逐项验证结构契约，不依赖上文任何散点断言，也不以
# “已注册 22 个”这一数量替代逐项校验。这些 Python 断言 **不构成** 与 CODESYS
# 语义一致的证据，仅锁定 L2 Python 目录契约。
# ---------------------------------------------------------------------------


class TestCatalog22DirectoryAcceptance(unittest.TestCase):
    """默认注册表 22 个 engineering 键的集中目录验收（任务书要求 1–4）。"""

    #: 8 个原语 + 14 个业务块 = 22（独立书写，不复用上文散点常量）。
    _PRIMITIVES = ("TON", "TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK")
    _BUSINESS = ("APCHSHLLIM", "APCM", "APCHSACCUM", "APCHSFOP",
                 "APCHSRATELIM", "APCHXHCL", "APCSTATISTICS", "APCCD",
                 "APCGCQ", "APCMAUTOPARA", "APCPID", "APCPIDZZD",
                 "APCRSFNAUTOPARA", "APCSPFINDER")
    _CATALOG = _PRIMITIVES + _BUSINESS
    #: 精确需要 license_context 的授权块（其余 19 项不得暗含该依赖）。
    _LICENSED = frozenset({"APCM", "APCPID", "APCPIDZZD"})

    def test_catalog_universe_is_exactly_22_unique(self):
        # 期望集合自身先自证：8 原语 + 14 业务、无重叠、合计精确 22。
        self.assertEqual(len(self._PRIMITIVES), 8)
        self.assertEqual(len(self._BUSINESS), 14)
        self.assertEqual(len(self._CATALOG), 22)
        self.assertEqual(len(set(self._CATALOG)), 22)
        self.assertEqual(set(self._PRIMITIVES) & set(self._BUSINESS), set())

    def test_registry_locks_exactly_22_engineering_keys(self):
        # 要求 1：独立复算注册键集合，逐键锁定 (block_type, "engineering")，
        # 且没有任何其它变体键；不以数量断言替代精确集合断言。
        r = build_default_registry()
        keys = r.keys()
        self.assertEqual(len(keys), 22)
        self.assertEqual(set(keys),
                         {(bt, "engineering") for bt in self._CATALOG})
        self.assertTrue(all(variant == "engineering" for _, variant in keys))
        self.assertEqual(r.block_types(), tuple(sorted(self._CATALOG)))

    def test_each_entry_schema_serializable_and_metadata_complete(self):
        # 要求 2（前半）：逐项 Schema 可 JSON 序列化、block_type/variant/
        # descriptor_version 完整、Schema/Adapter 绑定一致（engineering 与
        # fidelity_f1 解析到同一对象、adapter 为可构造的 RuntimeAdapter）。
        r = build_default_registry()
        for bt in self._CATALOG:
            with self.subTest(block_type=bt):
                schema, adapter = r.resolve(bt, "engineering")
                self.assertIsInstance(schema, BlockSchema)
                self.assertIsInstance(adapter, RuntimeAdapter)
                self.assertEqual(schema.block_type, bt)
                self.assertEqual(schema.variant, "engineering")
                self.assertIsInstance(schema.descriptor_version, str)
                self.assertTrue(schema.descriptor_version)
                # 纯数据 Schema 原样穿过 JSON，且不持有任何 callable。
                blob = json.dumps(schema.to_json())
                self.assertEqual(json.loads(blob)["block_type"], bt)
                for forbidden in ("cls", "call_adapter", "serializer"):
                    self.assertFalse(hasattr(schema, forbidden))
                # 绑定一致：同键 engineering / fidelity_f1 返回同一 (schema,
                # adapter) 对象；adapter.cls 为类、call_adapter 可调用。
                self.assertIs(r.resolve(bt, "engineering"),
                              r.resolve(bt, "fidelity_f1"))
                self.assertIsInstance(adapter.cls, type)
                self.assertTrue(callable(adapter.call_adapter))

    def test_each_input_has_explicit_legal_omit_policy(self):
        # 要求 2（中）：每个 VAR_INPUT 均有合法 OmitPolicy；输出/inout 不得
        # 携带输入省略语义（一律 use_default，由 Schema 结构强约束再独立复核）。
        r = build_default_registry()
        for bt in self._CATALOG:
            with self.subTest(block_type=bt):
                schema, _ = r.resolve(bt, "engineering")
                self.assertTrue(schema.inputs, bt)   # 22 块均至少一个输入
                for pin in schema.inputs:
                    self.assertIn(pin.omit_policy, OMIT_POLICIES,
                                  (bt, pin.name))
                for pin in schema.outputs + schema.inouts:
                    self.assertEqual(pin.omit_policy, "use_default",
                                     (bt, pin.name, pin.kind))

    def test_each_output_has_exactly_one_parseable_access_no_impersonation(self):
        # 要求 2（后）：每个 VAR_OUTPUT 有且仅有一条可解析 output_access；
        # 非输出管脚（输入/inout）不得被冒充为声明输出（键集合精确等于输出名）。
        r = build_default_registry()
        for bt in self._CATALOG:
            with self.subTest(block_type=bt):
                schema, _ = r.resolve(bt, "engineering")
                out_names = [p.name for p in schema.outputs]
                self.assertEqual(len(out_names), len(set(out_names)), bt)
                # 双射：output_access 键集合精确等于声明输出集合。
                self.assertEqual(set(schema.output_access), set(out_names), bt)
                for name in out_names:
                    kind, key = parse_output_access(schema.output_access[name])
                    self.assertIn(kind, ("return", "attr"), (bt, name))
                    self.assertTrue(key, (bt, name))
                # 输入 / inout 名不得出现在 output_access（不冒充输出）。
                non_output = ({p.name for p in schema.inputs}
                              | {p.name for p in schema.inouts})
                self.assertEqual(non_output & set(schema.output_access),
                                 set(), bt)
                # 三集合管脚名两两不相交（方向闭环独立复核）。
                self.assertEqual(
                    len(schema.inputs) + len(schema.outputs) + len(schema.inouts),
                    len({p.name for p in schema.all_pins()}), bt)

    def test_engineering_equals_f1_and_f2_missing_for_all(self):
        # 要求 3：逐项 engineering 与 fidelity_f1 解析同一 engineering
        # descriptor；fidelity_f2 全部显式 MissingVariantError，绝不静默回退。
        r = build_default_registry()
        for bt in self._CATALOG:
            with self.subTest(block_type=bt):
                eng_schema, eng_adapter = r.resolve(bt, "engineering")
                f1_schema, f1_adapter = r.resolve(bt, "fidelity_f1")
                self.assertIs(eng_schema, f1_schema)
                self.assertIs(eng_adapter, f1_adapter)
                self.assertEqual(eng_schema.variant, "engineering")
                with self.assertRaises(MissingVariantError):
                    r.resolve(bt, "fidelity_f2")

    def test_construction_dependency_graph_exactly_locked(self):
        # 要求 4：只有 APCM/APCPID/APCPIDZZD 需要 license_context；其余 19
        # 项 ctor_args 为空。授权块缺依赖 fail-closed；同一注入 ctx 在
        # APCPID 顶层与内嵌 PIDZZD1 之间共享；不同依赖图不得串扰。
        r = build_default_registry()
        for bt in self._CATALOG:
            with self.subTest(block_type=bt):
                _, adapter = r.resolve(bt, "engineering")
                if bt in self._LICENSED:
                    self.assertEqual(adapter.ctor_args, ("license_context",))
                    with self.assertRaises(AdapterBindingError):
                        adapter.construct({})           # 缺依赖 fail-closed
                else:
                    self.assertEqual(adapter.ctor_args, (), bt)

        # 授权三块：同一 ctx 注入后共享同一 LicenseContext 句柄。
        ctx = object()
        apcm = r.resolve("APCM", "engineering")[1].construct(
            {"license_context": ctx})
        pid = r.resolve("APCPID", "engineering")[1].construct(
            {"license_context": ctx})
        zzd = r.resolve("APCPIDZZD", "engineering")[1].construct(
            {"license_context": ctx})
        self.assertIs(apcm._ctx, ctx)
        self.assertIs(pid._ctx, ctx)
        self.assertIs(pid.PIDZZD1._ctx, ctx)            # 内嵌子块共享同一 ctx
        self.assertIs(zzd._ctx, ctx)

        # 不同依赖图不得共享：独立 ctx 构造出彼此隔离的句柄。
        ctx_a, ctx_b = object(), object()
        pid_a = r.resolve("APCPID", "engineering")[1].construct(
            {"license_context": ctx_a})
        pid_b = r.resolve("APCPID", "engineering")[1].construct(
            {"license_context": ctx_b})
        self.assertIs(pid_a._ctx, ctx_a)
        self.assertIs(pid_a.PIDZZD1._ctx, ctx_a)
        self.assertIs(pid_b._ctx, ctx_b)
        self.assertIsNot(pid_a._ctx, pid_b._ctx)

    def test_nineteen_unlicensed_blocks_construct_without_dependency(self):
        # 要求 4（补）：19 个非授权块行为反证——construct({}) 成功，不暗含
        # 任何注入依赖（若某块偷偷要求 ctx，此处会 AdapterBindingError）。
        r = build_default_registry()
        for bt in self._CATALOG:
            if bt in self._LICENSED:
                continue
            with self.subTest(block_type=bt):
                _, adapter = r.resolve(bt, "engineering")
                instance = adapter.construct({})
                self.assertIsInstance(instance, adapter.cls)


if __name__ == "__main__":
    unittest.main()
