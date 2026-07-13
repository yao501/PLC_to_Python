"""补充样本（PLCopen XML：反馈环 + TON 实例框）识别结果锁定（采集清单 ②③）。"""
import os
import unittest

from prototype_05.import_trial.parse_plcopen import (feedback_edges,
                                                     parse_plcopen, resolve)

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "import_trial",
                      "sample", "test_fb_feedback.xml")


class TestPlcopenSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = parse_plcopen(SAMPLE)
        cls.cfc = next(p for p in cls.r["pous"] if p["lang"] == "CFC")
        cls.m = cls.cfc["model"]

    def test_patch_level_revealed(self):
        """fileHeader 给出精确版本 → TARGET_PROFILE Patch 级别据此补齐。"""
        self.assertEqual(self.r["product_version"], "CODESYS V3.5 SP16 Patch 1")

    def test_task_consistent_with_profile(self):
        t = self.r["tasks"][0]
        self.assertEqual((t["kind"], t["interval"], t["watchdog"]),
                         ("Cyclic", "500ms", False))

    def test_ton_instance_box_recognized(self):
        """③：FB 实例框带 instanceName 与 CallType=functionblock，实例声明在 VAR。"""
        self.assertEqual(self.m.variables, {"A": "REAL", "TON1": "TON"})
        ton = next(b for b in self.m.blocks.values() if b.type_name == "TON")
        self.assertEqual(ton.instance_name, "TON1")
        self.assertEqual(ton.call_type, "functionblock")
        self.assertEqual(set(ton.inputs), {"IN", "PT"})
        self.assertEqual(ton.outputs, ["Q", "ET"])
        self.assertEqual(resolve(self.m, ton.inputs["PT"]), ("in", "T#5S"))
        # 运算框与实例框类别可区分
        others = [b.call_type for b in self.m.blocks.values() if b.type_name != "TON"]
        self.assertEqual(set(others), {"operator"})

    def test_execution_order_explicitly_stored(self):
        """PLCopen XML 载体每元素显式存 executionOrderId（与 .export 自动模式相反）。"""
        orders = {b.type_name: b.exec_order for b in self.m.blocks.values()}
        self.assertEqual(orders, {"ADD": 1, "GE": 3, "TON": 4, "SEL": 5})
        sink_orders = sorted(o for _e, o, _r in self.m.out_vars.values())
        self.assertEqual(sink_orders, [2, 6])

    def test_feedback_loop_recognized(self):
        """②：反馈环存在且可检测——ADD.In2 经 connector 中继接回 ADD 自身输出；
        本载体无显式反馈起点字段，环入口由 executionOrderId 最小（ADD=1）体现。"""
        self.assertEqual(feedback_edges(self.m), [("ADD", "In2", "ADD", "Out")])
        add = next(b for b in self.m.blocks.values() if b.type_name == "ADD")
        self.assertEqual(min(b.exec_order for b in self.m.blocks.values()),
                         add.exec_order)

    def test_no_explicit_feedback_flag_in_this_carrier(self):
        """锁定发现：PLCopen XML 全文无 feedback 字样（IsFeedbackStart 是 .export 专有）。"""
        with open(SAMPLE, encoding="utf-8") as f:
            content = f.read().lower()
        self.assertNotIn("feedback", content)

    def test_sel_wiring_via_connector_chain(self):
        sel = next(b for b in self.m.blocks.values() if b.type_name == "SEL")
        ton_id = next(i for i, b in self.m.blocks.items() if b.type_name == "TON")
        self.assertEqual(resolve(self.m, sel.inputs["In1"]), ("block", ton_id, "Q"))
        self.assertEqual(resolve(self.m, sel.inputs["In2"]), ("in", "A"))
        self.assertEqual(resolve(self.m, sel.inputs["In3"]), ("in", "0"))

    def test_plc_prg_calls_test(self):
        st = next(p for p in self.r["pous"] if p["lang"] == "ST")
        self.assertEqual((st["name"], st["body"]), ("PLC_PRG", "TEST();"))


if __name__ == "__main__":
    unittest.main()
