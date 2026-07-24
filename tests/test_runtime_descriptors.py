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
    BlockSchema,
    DuplicateDescriptorError,
    MissingVariantError,
    Pin,
    Registry,
    RuntimeAdapter,
    SchemaValidationError,
    TON_ADAPTER,
    TON_SCHEMA,
    UnknownBlockError,
    build_default_registry,
    collect_outputs,
    variant_for_mode,
)


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
        self.assertEqual(r.block_types(), ("APCHSHLLIM", "APCM", "TON"))
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


if __name__ == "__main__":
    unittest.main()
