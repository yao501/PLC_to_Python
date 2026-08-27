"""WP-20260803-067：CFC 定序视图的定向契约测试。"""
from __future__ import annotations

import unittest

from src.runtime.cfc_order import (
    CFCOrderDiagnostic,
    CFCOrderEdge,
    CFCOrderError,
    CFCOrderGraph,
    CFCOrderNode,
    resolve_execution_order,
)


def _graph(nodes, edges=(), *, carrier="user_defined", mode="auto", source="user_defined"):
    return CFCOrderGraph(tuple(nodes), tuple(edges), carrier, mode, source)


class TestCFCExecutionOrder(unittest.TestCase):
    def test_user_defined_auto_uses_kahn_and_unicode_node_id_tiebreak(self):
        graph = _graph(
            [CFCOrderNode("z"), CFCOrderNode("a"), CFCOrderNode("中"), CFCOrderNode("A")],
            [CFCOrderEdge("a", "z")],
        )
        self.assertEqual(resolve_execution_order(graph), ("A", "a", "z", "中"))

    def test_auto_order_is_independent_of_node_and_edge_input_order(self):
        first = _graph(
            [CFCOrderNode("D"), CFCOrderNode("B"), CFCOrderNode("C"), CFCOrderNode("A")],
            [CFCOrderEdge("B", "D"), CFCOrderEdge("A", "C"), CFCOrderEdge("A", "D")],
        )
        second = _graph(
            [CFCOrderNode("A"), CFCOrderNode("C"), CFCOrderNode("B"), CFCOrderNode("D")],
            [CFCOrderEdge("A", "D"), CFCOrderEdge("A", "C"), CFCOrderEdge("B", "D")],
        )
        self.assertEqual(resolve_execution_order(first), ("A", "B", "C", "D"))
        self.assertEqual(resolve_execution_order(first), resolve_execution_order(second))

    def test_auto_accepts_empty_graph_and_orders_isolated_nodes(self):
        self.assertEqual(resolve_execution_order(_graph([])), ())
        self.assertEqual(
            resolve_execution_order(_graph([CFCOrderNode("z"), CFCOrderNode("a")])),
            ("a", "z"),
        )

    def test_auto_rejects_any_execution_order_id_including_exact_int(self):
        graph = _graph([CFCOrderNode("n", 0)])
        with self.assertRaises(CFCOrderError) as raised:
            resolve_execution_order(graph)
        self.assertIn("INVALID_ORDER_ID", [item.code for item in raised.exception.errors])

    def test_invalid_config_value_types_aggregate_without_type_error(self):
        graph = _graph([CFCOrderNode("n")], carrier=["user_defined"], mode=0, source=True)
        with self.assertRaises(CFCOrderError) as raised:
            resolve_execution_order(graph)
        self.assertEqual([item.code for item in raised.exception.errors], ["INVALID_CARRIER", "INVALID_ORDER_MODE", "INVALID_ORDER_SOURCE"])

    def test_explicit_order_is_preserved_even_when_it_reverses_dependencies(self):
        graph = _graph(
            [CFCOrderNode("first_by_id", 2), CFCOrderNode("later_by_id", 9)],
            [CFCOrderEdge("later_by_id", "first_by_id")],
            carrier="plcopen_xml", mode="explicit", source="exported",
        )
        self.assertEqual(resolve_execution_order(graph), ("first_by_id", "later_by_id"))

    def test_explicit_order_allows_holes_and_user_defined_explicit(self):
        graph = _graph(
            [CFCOrderNode("later", 99), CFCOrderNode("first", 3)],
            carrier="user_defined", mode="explicit", source="user_defined",
        )
        self.assertEqual(resolve_execution_order(graph), ("first", "later"))

    def test_export_native_is_closed_for_auto_reconstruction_and_explicit(self):
        for mode, source, code in (("auto", "reconstructed", "UNSUPPORTED_RECONSTRUCTION"), ("explicit", "exported", "UNSUPPORTED_CARRIER_MODE")):
            with self.subTest(mode=mode):
                graph = _graph([CFCOrderNode("n", 1)], carrier="export_native", mode=mode, source=source)
                with self.assertRaises(CFCOrderError) as raised:
                    resolve_execution_order(graph)
                self.assertEqual(raised.exception.errors[0].code, code)

    def test_aggregates_structural_and_explicit_order_diagnostics_without_partial_result(self):
        class IntChild(int):
            pass

        graph = _graph(
            [CFCOrderNode("same", True), CFCOrderNode("same", IntChild(2)), CFCOrderNode("", -1)],
            [CFCOrderEdge("same", "missing"), CFCOrderEdge("same", "missing"), CFCOrderEdge("same", "same")],
            carrier="plcopen_xml", mode="explicit", source="exported",
        )
        before = graph
        with self.assertRaises(CFCOrderError) as raised:
            resolve_execution_order(graph)
        self.assertEqual(graph, before)
        codes = tuple(item.code for item in raised.exception.errors)
        self.assertIn("DUPLICATE_NODE", codes)
        self.assertIn("INVALID_NODE_ID", codes)
        self.assertIn("INVALID_ORDER_ID", codes)
        self.assertIn("DUPLICATE_EDGE", codes)
        self.assertIn("DANGLING_EDGE", codes)
        self.assertIn("SELF_EDGE", codes)
        self.assertEqual(raised.exception.errors, tuple(sorted(raised.exception.errors, key=lambda item: item.sort_key())))

    def test_cycles_are_rejected_even_with_feedback_marker(self):
        graph = _graph(
            [CFCOrderNode("a", feedback_marker=True), CFCOrderNode("b")],
            [CFCOrderEdge("a", "b"), CFCOrderEdge("b", "a")],
        )
        with self.assertRaises(CFCOrderError) as raised:
            resolve_execution_order(graph)
        self.assertIn("CYCLE", [item.code for item in raised.exception.errors])

    def test_explicit_order_does_not_allow_a_cycle(self):
        graph = _graph(
            [CFCOrderNode("a", 1), CFCOrderNode("b", 2)],
            [CFCOrderEdge("a", "b"), CFCOrderEdge("b", "a")],
            carrier="plcopen_xml", mode="explicit", source="exported",
        )
        with self.assertRaises(CFCOrderError) as raised:
            resolve_execution_order(graph)
        self.assertIn("CYCLE", [item.code for item in raised.exception.errors])

    def test_value_objects_are_frozen_and_outputs_are_tuples(self):
        node = CFCOrderNode("n")
        with self.assertRaises((AttributeError, TypeError)):
            node.node_id = "changed"
        self.assertIsInstance(resolve_execution_order(_graph([node])), tuple)
        self.assertTrue(CFCOrderDiagnostic("X", "x").code)


if __name__ == "__main__":
    unittest.main()
