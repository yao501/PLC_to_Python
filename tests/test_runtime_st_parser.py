"""Minimal Stage 3 ST AST/parser tests (WP-20260810-093)."""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import patch

from src.runtime.st_lexer import STLexError
from src.runtime.st_parser import (
    STAssignment, STBinary, STCall, STCallStatement, STCase, STContinue,
    STExit, STFor, STIf, STIndex, STLiteral, STMember, STName, STParseError,
    STReturn,
    STUnary, STWhile, parse_st,
)


class STParserPositiveTests(unittest.TestCase):
    def test_all_five_var_scopes_and_empty_section(self):
        unit = parse_st("""
            VAR A : INT; END_VAR
            VAR_INPUT B : REAL; END_VAR
            VAR_OUTPUT C : BOOL; END_VAR
            VAR_IN_OUT D : TIME; END_VAR
            VAR_TEMP END_VAR
        """)
        self.assertEqual([decl.scope for decl in unit.declarations],
                         ["VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT"])
        self.assertEqual([decl.names[0].canonical for decl in unit.declarations],
                         ["A", "B", "C", "D"])

    def test_global_section_and_ir_aligned_elementary_types(self):
        unit = parse_st(
            "VAR_GLOBAL A:BYTE; B:word; C:DWORD; D:lword; S:STRING; END_VAR")
        self.assertEqual([decl.scope for decl in unit.declarations],
                         ["VAR_GLOBAL"] * 5)
        self.assertEqual([decl.type_name.canonical for decl in unit.declarations],
                         ["BYTE", "WORD", "DWORD", "LWORD", "STRING"])

    def test_multiple_names_user_type_and_expression_initializer(self):
        unit = parse_st("VAR a, B : MyType := Base + 2 * 3; END_VAR")
        declaration = unit.declarations[0]
        self.assertEqual([name.spelling for name in declaration.names], ["a", "B"])
        self.assertEqual([name.canonical for name in declaration.names], ["A", "B"])
        self.assertEqual(declaration.type_name.canonical, "MYTYPE")
        self.assertEqual(declaration.initializer.operator, "+")
        self.assertEqual(declaration.initializer.right.operator, "*")

    def test_assignment_literal_name_and_boolean(self):
        unit = parse_st("A:=1; B:=A; C:=TRUE;")
        self.assertEqual(len(unit.statements), 3)
        self.assertTrue(all(isinstance(item, STAssignment) for item in unit.statements))
        self.assertIsInstance(unit.statements[0].value, STLiteral)
        self.assertIsInstance(unit.statements[1].value, STName)
        self.assertEqual(unit.statements[2].value.kind, "BOOL")

    def test_codesys_precedence_is_structurally_locked(self):
        expression = parse_st("X:=A OR B XOR C AND D = E < F + G * H;").statements[0].value
        # OR/XOR share the weakest level and associate left-to-right.
        self.assertEqual(expression.operator, "XOR")
        self.assertEqual(expression.left.operator, "OR")
        self.assertEqual(expression.right.operator, "AND")
        equality = expression.right.right
        self.assertEqual(equality.operator, "=")
        self.assertEqual(equality.right.operator, "<")
        self.assertEqual(equality.right.right.operator, "+")
        self.assertEqual(equality.right.right.right.operator, "*")

    def test_unary_binds_above_multiplication(self):
        value = parse_st("X := -2 * 3;").statements[0].value
        self.assertIsInstance(value, STBinary)
        self.assertEqual(value.operator, "*")
        self.assertIsInstance(value.left, STUnary)
        self.assertEqual(value.left.operator, "-")

    def test_expt_and_standard_function_call_expressions(self):
        unit = parse_st("X := EXPT(2, 3); Y := SEL(TRUE, A, B);")
        expt = unit.statements[0].value
        selector = unit.statements[1].value
        self.assertIsInstance(expt, STCall)
        self.assertEqual(expt.callee.canonical, "EXPT")
        self.assertEqual([arg.direction for arg in expt.arguments],
                         ["POSITIONAL", "POSITIONAL"])
        self.assertIsInstance(selector, STCall)
        self.assertEqual(selector.callee.canonical, "SEL")

    def test_fb_call_statement_preserves_named_io_and_blank_output(self):
        statement = parse_st(
            "Timer(IN:=TRUE, PT:=T#500ms, Q=>Motor, ET=>);").statements[0]
        self.assertIsInstance(statement, STCallStatement)
        self.assertEqual(statement.call.callee.canonical, "TIMER")
        self.assertEqual(
            [(arg.name.canonical, arg.direction) for arg in statement.call.arguments],
            [("IN", "INPUT"), ("PT", "INPUT"),
             ("Q", "OUTPUT"), ("ET", "OUTPUT")])
        self.assertEqual(statement.call.arguments[2].value.canonical, "MOTOR")
        self.assertIsNone(statement.call.arguments[3].value)

    def test_member_multidimensional_index_and_method_call(self):
        unit = parse_st(
            "Root.Items[I, J + 1].Value := Source[2]; "
            "FB.Method(IN:=Root.Items[1].Value);")
        assignment, invocation = unit.statements
        self.assertIsInstance(assignment.target, STMember)
        self.assertIsInstance(assignment.target.base, STIndex)
        self.assertEqual(len(assignment.target.base.indices), 2)
        self.assertIsInstance(assignment.value, STIndex)
        self.assertIsInstance(invocation, STCallStatement)
        self.assertIsInstance(invocation.call.callee, STMember)

    def test_postfix_ast_is_immutable_and_spans_include_grouping(self):
        source = "X := (F(A[1]));"
        call = parse_st(source).statements[0].value
        self.assertIsInstance(call, STCall)
        self.assertEqual(source[call.span.start:call.span.end], "(F(A[1]))")
        self.assertIsInstance(call.arguments[0].value, STIndex)
        with self.assertRaises(FrozenInstanceError):
            call.arguments[0].direction = "OUTPUT"

    def test_if_elsif_else_and_nested_if_source_model(self):
        source = (
            "IF A THEN X:=1; "
            "ELSIF B THEN IF C THEN X:=2; ELSE X:=3; END_IF; "
            "ELSE X:=4; END_IF;")
        statement = parse_st(source).statements[0]
        self.assertIsInstance(statement, STIf)
        self.assertEqual(len(statement.branches), 2)
        self.assertIsInstance(statement.branches[1].statements[0], STIf)
        self.assertEqual(len(statement.else_statements), 1)
        self.assertEqual(source[statement.span.start:statement.span.end], source)

    def test_if_allows_empty_branches_and_is_immutable(self):
        statement = parse_st("IF A THEN ELSIF B THEN ELSE END_IF;").statements[0]
        self.assertEqual([branch.statements for branch in statement.branches], [(), ()])
        self.assertEqual(statement.else_statements, ())
        with self.assertRaises(FrozenInstanceError):
            statement.branches = ()

    def test_case_lists_ranges_else_and_nested_source_model(self):
        unit = parse_st("""
            CASE Mode OF
              -2, 0: X:=1;
              1..3: IF Flag THEN X:=2; END_IF;
              4: CASE Sub OF 1:Y:=1; ELSE Y:=2; END_CASE
              ELSE X:=9;
            END_CASE
        """)
        statement = unit.statements[0]
        self.assertIsInstance(statement, STCase)
        self.assertEqual(statement.selector.canonical, "MODE")
        self.assertEqual(len(statement.branches), 3)
        self.assertEqual(len(statement.branches[0].labels), 2)
        self.assertIsNotNone(statement.branches[1].labels[0].upper)
        self.assertIsInstance(statement.branches[1].statements[0], STIf)
        self.assertIsInstance(statement.branches[2].statements[0], STCase)
        self.assertEqual(len(statement.else_statements), 1)

    def test_for_default_negative_by_empty_and_nested_source_model(self):
        unit = parse_st("""
            FOR I:=1 TO 3 DO X:=X+I; END_FOR;
            FOR J:=3 TO 1 BY -1 DO
              IF J=2 THEN FOR K:=1 TO 0 DO END_FOR; END_IF;
            END_FOR;
        """)
        first, second = unit.statements
        self.assertIsInstance(first, STFor)
        self.assertIsNone(first.increment)
        self.assertEqual(first.counter.canonical, "I")
        self.assertIsInstance(second.increment, STUnary)
        nested = second.statements[0].branches[0].statements[0]
        self.assertIsInstance(nested, STFor)
        self.assertEqual(nested.statements, ())

    def test_while_exit_continue_source_model_spans_and_immutability(self):
        source = (
            "WHILE Ready DO IF Skip THEN CONTINUE; END_IF; "
            "FOR I:=1 TO 2 DO EXIT; END_FOR; END_WHILE;")
        statement = parse_st(source).statements[0]
        self.assertIsInstance(statement, STWhile)
        self.assertEqual(source[statement.span.start:statement.span.end], source)
        self.assertIsInstance(statement.statements[0].branches[0].statements[0],
                              STContinue)
        nested_for = statement.statements[1]
        self.assertIsInstance(nested_for.statements[0], STExit)
        with self.assertRaises(FrozenInstanceError):
            statement.statements = ()

    def test_loop_transfer_is_syntactic_outside_loop_and_semantic_later(self):
        first, second = parse_st("EXIT; CONTINUE;").statements
        self.assertIsInstance(first, STExit)
        self.assertIsInstance(second, STContinue)

    def test_return_is_an_immutable_exact_statement_with_source_span(self):
        source = "IF Ready THEN RETURN; END_IF;"
        statement = parse_st(source).statements[0].branches[0].statements[0]
        self.assertIsInstance(statement, STReturn)
        self.assertEqual(source[statement.span.start:statement.span.end], "RETURN;")
        with self.assertRaises(FrozenInstanceError):
            statement.span = None

    def test_unit_span_includes_var_section_markers(self):
        source = "  VAR X : INT; END_VAR  "
        unit = parse_st(source)
        self.assertEqual(source[unit.span.start:unit.span.end], "VAR X : INT; END_VAR")

    def test_parentheses_override_precedence_and_extend_span(self):
        statement = parse_st("X := (1 + 2) * 3;").statements[0]
        self.assertEqual(statement.value.operator, "*")
        self.assertEqual(statement.value.left.operator, "+")
        self.assertEqual(statement.value.left.span.start, 5)
        self.assertEqual(statement.value.left.span.end, 12)

    def test_time_string_and_real_spelling_remain_lexical(self):
        unit = parse_st("A:=T#500ms; B:='x$'y'; C:=1.0E-3;")
        values = [statement.value for statement in unit.statements]
        self.assertEqual([(value.kind, value.text) for value in values], [
            ("TIME_LITERAL", "T#500ms"), ("STRING", "'x$'y'"),
            ("REAL", "1.0E-3"),
        ])

    def test_comments_case_and_source_spans_are_preserved(self):
        source = "VAR\r\n x : INT; (*c*)\r\nEND_VAR\r\nx := 2;"
        unit = parse_st(source)
        declaration = unit.declarations[0]
        statement = unit.statements[0]
        self.assertEqual((declaration.names[0].spelling,
                          declaration.names[0].canonical), ("x", "X"))
        self.assertEqual((declaration.names[0].span.line,
                          statement.target.span.line), (2, 4))
        self.assertEqual(source[statement.span.start:statement.span.end], "x := 2;")

    def test_ast_is_immutable_and_repeated_parse_isolated(self):
        left = parse_st("VAR X : INT; END_VAR X:=1;")
        right = parse_st("VAR X : INT; END_VAR X:=1;")
        self.assertEqual(left, right)
        self.assertIsNot(left, right)
        self.assertIsNot(left.declarations[0], right.declarations[0])
        with self.assertRaises(FrozenInstanceError):
            left.declarations[0].scope = "VAR_INPUT"

    def test_empty_source_is_an_empty_unit(self):
        unit = parse_st("")
        self.assertEqual((unit.declarations, unit.statements), ((), ()))
        self.assertEqual((unit.span.start, unit.span.end), (0, 0))


class STParserFailureTests(unittest.TestCase):
    def assert_code(self, source, code):
        with self.assertRaises(STParseError) as caught:
            parse_st(source)
        self.assertEqual(caught.exception.errors[0].code, code)
        return caught.exception

    def test_declaration_shape_errors(self):
        self.assert_code("VAR X INT; END_VAR", "EXPECTED_COLON")
        self.assert_code("VAR X : ; END_VAR", "EXPECTED_TYPE")
        self.assert_code("VAR X : INT END_VAR", "EXPECTED_SEMICOLON")
        self.assert_code("VAR X : INT;", "EXPECTED_END_VAR")

    def test_assignment_shape_errors(self):
        self.assert_code("X 1;", "EXPECTED_ASSIGNMENT")
        self.assert_code("X := 1", "EXPECTED_SEMICOLON")
        self.assert_code("1 := X;", "EXPECTED_ASSIGNMENT_TARGET")
        self.assert_code("X() := 1;", "INVALID_ASSIGNMENT_TARGET")

    def test_postfix_shape_errors_and_control_flow_remains_deferred(self):
        self.assert_code("X := F().Y;", "UNSUPPORTED_CALL_RESULT_ACCESS")
        self.assert_code("X := F()(1);", "UNSUPPORTED_CALL_RESULT_ACCESS")
        self.assert_code("X := A[];", "EXPECTED_INDEX")
        self.assert_code("X := A[1,];", "EXPECTED_EXPRESSION")
        self.assert_code("X := F(Q=>1);", "EXPECTED_REFERENCE")
        self.assert_code("X := F(1 2);", "EXPECTED_ARGUMENT_SEPARATOR")
        self.assert_code("X := F(1,);", "EXPECTED_ARGUMENT")
        self.assert_code("X := 1(2);", "INVALID_CALLEE")
        self.assert_code("X := TRUE.Value;", "INVALID_ACCESS_BASE")
        self.assert_code("X := TRUE[1];", "INVALID_ACCESS_BASE")
        self.assert_code("X := (F)(1);", "UNSUPPORTED_GROUPED_POSTFIX")
        self.assert_code("CASE X 1:X:=1; END_CASE", "EXPECTED_OF")
        self.assert_code("CASE X OF ELSE X:=1; END_CASE", "EXPECTED_CASE_LABEL")
        self.assert_code("CASE X OF 1 X:=1; END_CASE", "EXPECTED_COLON")
        self.assert_code("CASE X OF 1:X:=1;", "EXPECTED_END_CASE")
        self.assert_code("X := +1;", "EXPECTED_EXPRESSION")

    def test_call_argument_and_index_limits(self):
        with patch("src.runtime.st_parser._MAX_CALL_ARGUMENTS", 2):
            self.assert_code("X := F(1, 2, 3);", "CALL_ARGUMENT_LIMIT")
        with patch("src.runtime.st_parser._MAX_INDEX_DIMENSIONS", 2):
            self.assert_code("X := A[1, 2, 3];", "INDEX_DIMENSION_LIMIT")

    def test_if_shape_and_depth_errors(self):
        self.assert_code("IF A X:=1; END_IF;", "EXPECTED_THEN")
        self.assert_code("IF A THEN X:=1;", "EXPECTED_END_IF")
        self.assert_code("IF A THEN END_IF", "EXPECTED_SEMICOLON")
        with patch("src.runtime.st_parser._MAX_CONTROL_FLOW_DEPTH", 1):
            self.assert_code("IF A THEN IF B THEN END_IF; END_IF;",
                             "CONTROL_FLOW_DEPTH")

    def test_for_shape_and_depth_errors(self):
        self.assert_code("FOR I 1 TO 2 DO END_FOR;", "EXPECTED_ASSIGNMENT")
        self.assert_code("FOR I:=1 2 DO END_FOR;", "EXPECTED_TO")
        self.assert_code("FOR I:=1 TO 2 END_FOR;", "EXPECTED_DO")
        self.assert_code("FOR I:=1 TO 2 DO", "EXPECTED_END_FOR")
        self.assert_code("FOR I:=1 TO 2 DO END_FOR", "EXPECTED_SEMICOLON")
        with patch("src.runtime.st_parser._MAX_CONTROL_FLOW_DEPTH", 1):
            self.assert_code(
                "FOR I:=1 TO 2 DO FOR J:=1 TO 2 DO END_FOR; END_FOR;",
                "CONTROL_FLOW_DEPTH")

    def test_while_and_loop_transfer_shape_errors(self):
        self.assert_code("WHILE A END_WHILE;", "EXPECTED_DO")
        self.assert_code("WHILE A DO", "EXPECTED_END_WHILE")
        self.assert_code("WHILE A DO END_WHILE", "EXPECTED_SEMICOLON")
        self.assert_code("EXIT", "EXPECTED_SEMICOLON")
        self.assert_code("CONTINUE", "EXPECTED_SEMICOLON")
        with patch("src.runtime.st_parser._MAX_CONTROL_FLOW_DEPTH", 1):
            self.assert_code(
                "WHILE A DO WHILE B DO END_WHILE; END_WHILE;",
                "CONTROL_FLOW_DEPTH")

    def test_return_requires_exact_empty_semicolon_terminated_form(self):
        self.assert_code("RETURN", "EXPECTED_SEMICOLON")
        self.assert_code("RETURN 1;", "EXPECTED_SEMICOLON")
        self.assert_code("RETURN;;", "EXPECTED_ASSIGNMENT_TARGET")

    def test_declaration_after_statement_is_rejected(self):
        self.assert_code("X:=1; VAR Y : INT; END_VAR", "DECLARATION_AFTER_STATEMENT")

    def test_expression_depth_limit(self):
        self.assert_code("X := " + "(" * 65 + "1" + ")" * 65 + ";",
                         "EXPRESSION_DEPTH")

    def test_lexical_errors_are_not_rewrapped(self):
        with self.assertRaises(STLexError):
            parse_st("X := 16#FF;")


if __name__ == "__main__":
    unittest.main()
