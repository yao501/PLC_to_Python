"""PLATFORM-IMPORT-TRIAL-1：真实 SP16.1 导出样本的识别结果锁定（回归锁）。"""
import os
import unittest

from prototype_05.import_trial.parse_export import (build_graph,
                                                    derive_dataflow_order,
                                                    parse_export)

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "import_trial",
                      "sample", "test.export")


class TestImportTrial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = parse_export(SAMPLE)

    def test_device_and_task_recognized(self):
        self.assertEqual(self.r["device"], "CODESYS Control Win V3 x64")
        t = self.r["tasks"][0]
        self.assertEqual((t.name, t.kind, t.priority, t.interval),
                         ("MainTask", "Cyclic", "1", "500ms"))
        self.assertFalse(t.watchdog_enabled)          # 样本工程未启用任务 watchdog
        self.assertIn("PLC_PRG", t.pous)

    def test_st_pous_recognized(self):
        by_name = {p.name: p for p in self.r["pous_st"]}
        self.assertEqual(set(by_name), {"PLC_PRG", "ST_TEST"})
        st = by_name["ST_TEST"]
        self.assertIn("\tB: REAL;", st.interface)
        self.assertEqual([x.strip() for x in st.body if x.strip()],
                         ["IF B>200 THEN", "B:=0;", "ELSE", "B:=B+1;", "END_IF"])
        self.assertEqual([x.strip() for x in by_name["PLC_PRG"].body if x.strip()],
                         ["CFC_TEST();", "ST_TEST();"])

    def test_cfc_structure_recognized(self):
        cfc = self.r["pous_cfc"][0]
        self.assertEqual(cfc.name, "CFC_TEST")
        self.assertFalse(cfc.explicit_order)          # 自动数据流模式，导出无每元素序号
        g = build_graph(cfc)
        boxes = {n.text: n for n in g["nodes"].values() if n.kind == "box"}
        self.assertEqual(set(boxes), {"GE", "SEL", "ADD"})
        self.assertEqual(len(boxes["SEL"].in_pins), 3)
        self.assertTrue(all(n.kind_of_call == "Operator" for n in boxes.values()))
        self.assertFalse(any(n.is_feedback_start for n in boxes.values()))
        sinks = [n for n in g["nodes"].values() if n.kind == "sink"]
        self.assertEqual([s.text for s in sinks], ["A", "A"])
        self.assertEqual(len(g["edges"]), 9)          # 与编辑器截图连线数一致

    def test_sel_input_wiring(self):
        """SEL 三个输入脚的连线来源：GE 输出 → 第 1 脚(G)，A → 第 2 脚，0 → 第 3 脚。"""
        cfc = self.r["pous_cfc"][0]
        g = build_graph(cfc)
        sel = next(n for n in g["nodes"].values() if n.text == "SEL")
        pin_src = {dp: g["nodes"][s] for s, _d, _sp, dp in
                   [(s, d, sp, dp) for s, d, sp, dp in g["edges"]
                    if d == sel.id]}
        p1, p2, p3 = [p for p, _n in sel.in_pins]
        self.assertEqual(pin_src[p1].text, "GE")
        self.assertEqual(pin_src[p2].text, "A")
        self.assertEqual(pin_src[p3].text, "0")

    def test_derived_dataflow_order_matches_editor_numbers(self):
        """派生执行序与编辑器显示的 0..4 一致（单样本吻合，仍标待真机对拍，
        见 RISKS::PLATFORM-CFC-AUTOORDER-1）。"""
        cfc = self.r["pous_cfc"][0]
        g = build_graph(cfc)
        order = [g["nodes"][i].text or g["nodes"][i].kind
                 for i in derive_dataflow_order(g)]
        self.assertEqual(order, ["GE", "SEL", "A", "ADD", "A"])


if __name__ == "__main__":
    unittest.main()
