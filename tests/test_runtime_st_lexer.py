"""Stage 3 ST lexical foundation tests (WP-20260810-092)."""
import unittest
from unittest.mock import patch

from src.runtime.st_lexer import STLexError, lex_st


def _without_eof(source):
    return lex_st(source)[:-1]


class STLexerPositiveTests(unittest.TestCase):
    def test_declaration_assignment_and_case_insensitive_keywords(self):
        tokens = _without_eof("var x : REAL; end_var x := 1.25E+2;")
        self.assertEqual(
            [(token.kind, token.text, token.normalized) for token in tokens],
            [
                ("KEYWORD", "var", "VAR"),
                ("IDENTIFIER", "x", "X"),
                ("PUNCTUATION", ":", ":"),
                ("KEYWORD", "REAL", "REAL"),
                ("PUNCTUATION", ";", ";"),
                ("KEYWORD", "end_var", "END_VAR"),
                ("IDENTIFIER", "x", "X"),
                ("OPERATOR", ":=", ":="),
                ("REAL", "1.25E+2", "1.25E+2"),
                ("PUNCTUATION", ";", ";"),
            ])

    def test_control_flow_standard_function_and_fb_call_shape(self):
        tokens = _without_eof(
            "IF A>=10 AND NOT B THEN T1(IN:=TRUE, PT:=T#500ms); "
            "Y:=LIMIT(0.0,X,100.0); END_IF;")
        self.assertEqual(tokens[0].normalized, "IF")
        self.assertEqual(tokens[-2].normalized, "END_IF")
        self.assertEqual([t.text for t in tokens if t.kind == "TIME_LITERAL"],
                         ["T#500ms"])
        self.assertIn("LIMIT", [t.normalized for t in tokens])

    def test_while_exit_continue_are_reserved_control_flow_words(self):
        tokens = _without_eof(
            "WHILE Ready DO IF Skip THEN CONTINUE; END_IF; EXIT; END_WHILE;")
        words = [token.normalized for token in tokens if token.kind == "KEYWORD"]
        self.assertEqual(words, [
            "WHILE", "DO", "IF", "THEN", "CONTINUE", "END_IF", "EXIT",
            "END_WHILE",
        ])

    def test_expt_is_the_codesys_exponentiation_keyword(self):
        tokens = _without_eof("Y := 2 EXPT 3;")
        expt = next(token for token in tokens if token.normalized == "EXPT")
        self.assertEqual((expt.kind, expt.text), ("KEYWORD", "EXPT"))

    def test_ir_aligned_types_and_global_section_are_reserved_words(self):
        tokens = _without_eof(
            "var_global A:byte; B:WORD; C:dword; D:LWORD; S:string; end_var")
        keywords = [token.normalized for token in tokens if token.kind == "KEYWORD"]
        self.assertEqual(keywords, [
            "VAR_GLOBAL", "BYTE", "WORD", "DWORD", "LWORD", "STRING",
            "END_VAR",
        ])

    def test_untyped_negative_is_operator_plus_literal(self):
        tokens = _without_eof("X := -3.2;")
        self.assertEqual([(t.kind, t.text) for t in tokens[-3:-1]],
                         [("OPERATOR", "-"), ("REAL", "3.2")])

    def test_time_spellings_preserve_text_and_normalize(self):
        tokens = _without_eof(
            "A:=T#12d23h34m15s7ms; B:=time#100s12ms;")
        times = [t for t in tokens if t.kind == "TIME_LITERAL"]
        self.assertEqual([t.text for t in times],
                         ["T#12d23h34m15s7ms", "time#100s12ms"])
        self.assertEqual([t.normalized for t in times],
                         ["T#12D23H34M15S7MS", "TIME#100S12MS"])

    def test_string_escape_keeps_escaped_quote_inside_literal(self):
        tokens = _without_eof("S := 'A$'B$$$41';")
        literal = next(token for token in tokens if token.kind == "STRING")
        self.assertEqual(literal.text, "'A$'B$$$41'")

    def test_line_and_nested_block_comments_are_skipped(self):
        tokens = _without_eof(
            "A:=1; // ignored\n(* outer (* nested *) end *) B:=2;")
        self.assertEqual([t.text for t in tokens],
                         ["A", ":=", "1", ";", "B", ":=", "2", ";"])

    def test_crlf_cr_lf_positions_and_half_open_offsets(self):
        source = "A\r\nB\rC\nD"
        tokens = _without_eof(source)
        self.assertEqual([(t.text, t.line, t.column) for t in tokens],
                         [("A", 1, 1), ("B", 2, 1), ("C", 3, 1), ("D", 4, 1)])
        self.assertEqual([(t.start, t.end) for t in tokens],
                         [(0, 1), (3, 4), (5, 6), (7, 8)])

    def test_range_and_member_dot_use_maximal_munch(self):
        tokens = _without_eof("FOR I:=1 TO 10 DO A[I].X:=1; END_FOR;")
        self.assertIn(("PUNCTUATION", "."), [(t.kind, t.text) for t in tokens])
        range_tokens = _without_eof("R:=1..10;")
        self.assertIn(("OPERATOR", ".."), [(t.kind, t.text) for t in range_tokens])

    def test_empty_source_has_one_eof_at_origin(self):
        tokens = lex_st("")
        self.assertEqual(len(tokens), 1)
        self.assertEqual((tokens[0].kind, tokens[0].start, tokens[0].end,
                          tokens[0].line, tokens[0].column),
                         ("EOF", 0, 0, 1, 1))


class STLexerFailureTests(unittest.TestCase):
    def assert_code(self, source, code):
        with self.assertRaises(STLexError) as caught:
            lex_st(source)
        self.assertIn(code, [error.code for error in caught.exception.errors])
        return caught.exception

    def test_non_exact_string_source_is_rejected_without_observation(self):
        class StringSubclass(str):
            def __len__(self):
                raise BaseException("must not run")
        error = self.assert_code(StringSubclass("VAR"), "INVALID_SOURCE_TYPE")
        self.assertEqual(str(error),
                         "INVALID_SOURCE_TYPE at 1:1: ST source must be an exact str")

    def test_invalid_identifier_consecutive_underscores(self):
        error = self.assert_code("VAR A__B : INT; END_VAR", "INVALID_IDENTIFIER")
        diagnostic = error.errors[0]
        self.assertEqual((diagnostic.start, diagnostic.end, diagnostic.line,
                          diagnostic.column), (4, 8, 1, 5))

    def test_unterminated_nested_comment(self):
        self.assert_code("A:=1; (* outer (* inner *)", "UNTERMINATED_COMMENT")

    def test_unterminated_string_and_escape(self):
        self.assert_code("S:='abc\n", "UNTERMINATED_STRING")
        self.assert_code("S:='abc$", "UNTERMINATED_STRING_ESCAPE")

    def test_invalid_time_and_unsupported_typed_or_base_literals(self):
        self.assert_code("X:=T#foo;", "INVALID_TIME_LITERAL")
        self.assert_code("X:=T#1s1h;", "INVALID_TIME_LITERAL")
        self.assert_code("X:=T#1h2h;", "INVALID_TIME_LITERAL")
        self.assert_code("X:=T#1ms1s;", "INVALID_TIME_LITERAL")
        self.assert_code("X:=INT#10;", "UNSUPPORTED_TYPED_LITERAL")
        self.assert_code("X:=16#FF;", "UNSUPPORTED_BASE_LITERAL")

    def test_unknown_incomplete_and_nonhex_string_escapes_fail_closed(self):
        self.assert_code("S:='$Z';", "INVALID_STRING_ESCAPE")
        self.assert_code("S:='$4';", "INVALID_STRING_ESCAPE")
        self.assert_code("S:='$4G';", "INVALID_STRING_ESCAPE")

    def test_invalid_real_exponent(self):
        self.assert_code("X:=1.0E+;", "INVALID_REAL_LITERAL")

    def test_unsupported_numeric_separator_and_suffix_do_not_split(self):
        self.assert_code("X:=1_000;", "UNSUPPORTED_NUMERIC_SEPARATOR")
        self.assert_code("X:=123abc;", "INVALID_NUMERIC_SUFFIX")

    def test_non_ascii_nul_and_unknown_character_are_stable(self):
        self.assert_code("X:=λ;", "NON_ASCII_CHARACTER")
        self.assert_code("X:=\x00;", "INVALID_CONTROL_CHARACTER")
        self.assert_code("X:=@;", "INVALID_CHARACTER")

    def test_multiple_recoverable_errors_follow_source_order(self):
        with self.assertRaises(STLexError) as caught:
            lex_st("A__B := @; C__D := λ;")
        self.assertEqual([error.code for error in caught.exception.errors], [
            "INVALID_IDENTIFIER", "INVALID_CHARACTER",
            "INVALID_IDENTIFIER", "NON_ASCII_CHARACTER",
        ])
        self.assertEqual([error.start for error in caught.exception.errors],
                         sorted(error.start for error in caught.exception.errors))

    def test_source_and_token_limits_fail_closed(self):
        with patch("src.runtime.st_lexer._MAX_SOURCE_LENGTH", 3):
            self.assert_code("ABCD", "SOURCE_LIMIT")
        with patch("src.runtime.st_lexer._MAX_TOKENS", 2):
            self.assert_code("A B C", "TOKEN_LIMIT")


if __name__ == "__main__":
    unittest.main()
