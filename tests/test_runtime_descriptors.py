"""WP-20260723-016：L2 描述符核心（Schema/Adapter 拆分 + Registry）与三个
代表性 adapter 的结构测试。

本文件只锁定 L2 **Python 契约**（纯数据 Schema、注册表拒绝规则、代表性
描述符字段）；``经 Registry/Executor 调用 vs 直接调用原块`` 的行为对照测试
在 ``test_runtime_executor.py``。这些 Python 断言 **不构成** 与 CODESYS
语义一致的证据。
"""
from __future__ import annotations

import json
import unittest

from src.runtime.descriptors import (
    APCHSHLLIM_SCHEMA,
    APCM_ADAPTER,
    APCM_SCHEMA,
    AdapterBindingError,
    BLINK_ADAPTER,
    BLINK_SCHEMA,
    BlockSchema,
    DuplicateDescriptorError,
    F_TRIG_ADAPTER,
    F_TRIG_SCHEMA,
    MissingVariantError,
    PRIMITIVE_DESCRIPTORS,
    Pin,
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
        # WP-20260724-023：默认注册表由 3 扩展为 10 个 engineering block type
        self.assertEqual(
            r.block_types(),
            ("APCHSHLLIM", "APCM", "BLINK", "F_TRIG", "RS", "R_TRIG",
             "SR", "TOF", "TON", "TP"))
        # 精确 10 个 (block_type, "engineering") 键，无 fidelity_f2 变体
        self.assertEqual(len(r.keys()), 10)
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
        # build_default_registry 稳定注册 10 个，且原有三块不被覆盖/改写
        r = build_default_registry()
        self.assertEqual(len(r.keys()), 10)
        self.assertIs(r.resolve("TON", "engineering")[1], TON_ADAPTER)
        self.assertIs(r.resolve("BLINK", "engineering")[1], BLINK_ADAPTER)
        # 七个原语缺 fidelity_f2 变体：加载期显式失败，绝不静默降级
        for bt in ("TOF", "TP", "R_TRIG", "F_TRIG", "SR", "RS", "BLINK"):
            with self.assertRaises(MissingVariantError):
                r.resolve(bt, "fidelity_f2")


if __name__ == "__main__":
    unittest.main()
