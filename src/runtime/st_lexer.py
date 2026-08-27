"""Stage 3 Structured Text lexical foundation.

This module deliberately stops at the lexical boundary.  It preserves literal
spelling and source locations; parsing, literal value interpretation, IEC type
resolution, AST construction, and lowering to the executable IR belong to later
Stage 3 packages.

The accepted subset is intentionally explicit: ASCII IEC identifiers, the
keywords/operators needed by the roadmap's initial ST subset, decimal integer
and REAL spellings, TIME/T literals, single-quoted STRING literals, and CODESYS
line/nested block comments.  Unsupported or malformed shapes fail closed with
stable diagnostics instead of being guessed into a different token sequence.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_MAX_SOURCE_LENGTH = 1_000_000
_MAX_TOKENS = 100_000

_KEYWORDS = frozenset({
    "PROGRAM", "END_PROGRAM", "FUNCTION", "END_FUNCTION",
    "FUNCTION_BLOCK", "END_FUNCTION_BLOCK",
    "VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_TEMP", "VAR_GLOBAL",
    "END_VAR", "IF", "THEN", "ELSIF", "ELSE", "END_IF", "CASE",
    "OF", "END_CASE", "FOR", "TO", "BY", "DO", "END_FOR", "WHILE",
    "END_WHILE", "RETURN", "EXIT", "CONTINUE", "TRUE", "FALSE", "AND", "OR",
    "XOR", "NOT", "MOD", "EXPT", "BOOL", "SINT", "USINT", "INT", "UINT",
    "DINT", "UDINT", "LINT", "ULINT", "BYTE", "WORD", "DWORD", "LWORD",
    "REAL", "LREAL", "TIME", "STRING",
})

_MULTI_OPERATORS = (":=", "=>", "<=", ">=", "<>", "..")
_SINGLE_OPERATORS = frozenset("+-*/=<> &".replace(" ", ""))
_PUNCTUATION = frozenset(";,:()[].")
_TIME_BODY = re.compile(
    r"(?:\d+D)?(?:\d+H)?(?:\d+M)?(?:\d+S)?(?:\d+MS)?",
    re.IGNORECASE,
)
_STRING_SIMPLE_ESCAPES = frozenset("$'LlNnPpRrTt")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class STToken:
    """One immutable lexical token with a half-open source range."""

    kind: str
    text: str
    normalized: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class STLexDiagnostic:
    """Stable lexer diagnostic; message never embeds untrusted values."""

    code: str
    message: str
    start: int
    end: int
    line: int
    column: int


class STLexError(ValueError):
    """The source cannot be tokenized within the supported Stage 3 subset."""

    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("; ".join(
            "%s at %d:%d: %s" % (
                error.code, error.line, error.column, error.message)
            for error in self.errors
        ))


class _Scanner:
    def __init__(self, source):
        self.source = source
        self.length = len(source)
        self.index = 0
        self.line = 1
        self.column = 1
        self.tokens = []
        self.errors = []
        self.stopped = False

    def _peek(self, distance=0):
        position = self.index + distance
        if position >= self.length:
            return ""
        return self.source[position]

    def _advance(self):
        if self.index >= self.length:
            return ""
        char = self.source[self.index]
        self.index += 1
        if char == "\r":
            if self.index < self.length and self.source[self.index] == "\n":
                self.index += 1
                char = "\r\n"
            self.line += 1
            self.column = 1
        elif char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _position(self):
        return self.index, self.line, self.column

    def _diagnose(self, code, message, start, line, column, end=None):
        self.errors.append(STLexDiagnostic(
            code, message, start, self.index if end is None else end, line, column))

    def _emit(self, kind, start, line, column, normalized=None):
        if len(self.tokens) >= _MAX_TOKENS:
            self._diagnose(
                "TOKEN_LIMIT", "token count exceeds the supported limit",
                start, line, column)
            self.stopped = True
            return
        text = self.source[start:self.index]
        self.tokens.append(STToken(
            kind, text, text if normalized is None else normalized,
            start, self.index, line, column))

    def _skip_line_comment(self):
        self._advance()
        self._advance()
        while self.index < self.length and self._peek() not in "\r\n":
            self._advance()

    def _skip_block_comment(self):
        start, line, column = self._position()
        self._advance()
        self._advance()
        depth = 1
        while self.index < self.length:
            if self._peek() == "(" and self._peek(1) == "*":
                self._advance()
                self._advance()
                depth += 1
            elif self._peek() == "*" and self._peek(1) == ")":
                self._advance()
                self._advance()
                depth -= 1
                if depth == 0:
                    return
            else:
                self._advance()
        self._diagnose(
            "UNTERMINATED_COMMENT", "nested block comment is not terminated",
            start, line, column)
        self.stopped = True

    def _scan_string(self):
        start, line, column = self._position()
        self._advance()
        while self.index < self.length:
            char = self._peek()
            if char == "'":
                self._advance()
                self._emit("STRING", start, line, column)
                return
            if char in "\r\n":
                self._diagnose(
                    "UNTERMINATED_STRING", "string literal ends before the line break",
                    start, line, column)
                return
            if char == "$":
                self._advance()
                if self.index >= self.length or self._peek() in "\r\n":
                    self._diagnose(
                        "UNTERMINATED_STRING_ESCAPE",
                        "string escape is missing its escaped character",
                        start, line, column)
                    return
                first = self._peek()
                self._advance()
                if first in _HEX_DIGITS:
                    if self._peek() in _HEX_DIGITS:
                        self._advance()
                    else:
                        self._diagnose(
                            "INVALID_STRING_ESCAPE",
                            "hex STRING escape requires exactly two hex digits",
                            start, line, column)
                elif first not in _STRING_SIMPLE_ESCAPES:
                    self._diagnose(
                        "INVALID_STRING_ESCAPE",
                        "STRING escape is not supported by the strict subset",
                        start, line, column)
                continue
            self._advance()
        self._diagnose(
            "UNTERMINATED_STRING", "string literal is not terminated",
            start, line, column)
        self.stopped = True

    def _scan_identifier(self):
        start, line, column = self._position()
        while True:
            char = self._peek()
            if not (char and (char.isascii() and (char.isalnum() or char == "_"))):
                break
            self._advance()
        text = self.source[start:self.index]
        normalized = text.upper()

        if self._peek() == "#":
            self._advance()
            while True:
                char = self._peek()
                if not (char and char.isascii() and (char.isalnum() or char == "_")):
                    break
                self._advance()
            if normalized in {"T", "TIME"}:
                body = self.source[start + len(text) + 1:self.index]
                if body and _TIME_BODY.fullmatch(body):
                    self._emit("TIME_LITERAL", start, line, column,
                               self.source[start:self.index].upper())
                else:
                    self._diagnose(
                        "INVALID_TIME_LITERAL",
                        "TIME groups must be unique and ordered D,H,M,S,MS",
                        start, line, column)
                return
            self._diagnose(
                "UNSUPPORTED_TYPED_LITERAL",
                "typed literals other than T#/TIME# are not in this lexer baseline",
                start, line, column)
            return

        if "__" in text:
            self._diagnose(
                "INVALID_IDENTIFIER",
                "IEC identifiers cannot contain consecutive underscores",
                start, line, column)
            return
        kind = "KEYWORD" if normalized in _KEYWORDS else "IDENTIFIER"
        self._emit(kind, start, line, column, normalized)

    def _scan_number(self):
        start, line, column = self._position()
        while self._peek().isdigit() and self._peek().isascii():
            self._advance()

        if self._peek() == "#":
            self._advance()
            while True:
                char = self._peek()
                if not (char and char.isascii() and (char.isalnum() or char == "_")):
                    break
                self._advance()
            self._diagnose(
                "UNSUPPORTED_BASE_LITERAL",
                "base-prefixed numeric literals are not in this lexer baseline",
                start, line, column)
            return

        if self._peek() == "_":
            while True:
                char = self._peek()
                if not (char and char.isascii() and (char.isalnum() or char == "_")):
                    break
                self._advance()
            self._diagnose(
                "UNSUPPORTED_NUMERIC_SEPARATOR",
                "numeric separators are not in this lexer baseline",
                start, line, column)
            return

        is_real = False
        if self._peek() == "." and self._peek(1) != "." and self._peek(1).isdigit():
            is_real = True
            self._advance()
            while self._peek().isdigit() and self._peek().isascii():
                self._advance()

        if self._peek() in "eE" and self._peek() != "":
            is_real = True
            self._advance()
            if self._peek() in "+-":
                self._advance()
            exponent_start = self.index
            while self._peek().isdigit() and self._peek().isascii():
                self._advance()
            if self.index == exponent_start:
                self._diagnose(
                    "INVALID_REAL_LITERAL", "REAL exponent requires decimal digits",
                    start, line, column)
                return

        if self._peek() and self._peek().isascii() and \
                (self._peek().isalpha() or self._peek() == "_"):
            while True:
                char = self._peek()
                if not (char and char.isascii() and (char.isalnum() or char == "_")):
                    break
                self._advance()
            self._diagnose(
                "INVALID_NUMERIC_SUFFIX",
                "decimal numeric literal has an unsupported suffix",
                start, line, column)
            return
        self._emit("REAL" if is_real else "INTEGER", start, line, column)

    def scan(self):
        while self.index < self.length and not self.stopped:
            char = self._peek()
            if char in " \t\r\n":
                self._advance()
                continue
            if char == "/" and self._peek(1) == "/":
                self._skip_line_comment()
                continue
            if char == "(" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            if char == "'":
                self._scan_string()
                continue
            if char.isascii() and (char.isalpha() or char == "_"):
                self._scan_identifier()
                continue
            if char.isascii() and char.isdigit():
                self._scan_number()
                continue

            start, line, column = self._position()
            pair = self.source[self.index:self.index + 2]
            if pair in _MULTI_OPERATORS:
                self._advance()
                self._advance()
                self._emit("OPERATOR", start, line, column, pair.upper())
                continue
            if char in _SINGLE_OPERATORS:
                self._advance()
                self._emit("OPERATOR", start, line, column, char.upper())
                continue
            if char in _PUNCTUATION:
                self._advance()
                self._emit("PUNCTUATION", start, line, column, char)
                continue

            self._advance()
            if ord(char) < 32 or ord(char) == 127:
                code = "INVALID_CONTROL_CHARACTER"
                message = "unsupported control character in ST source"
            elif not char.isascii():
                code = "NON_ASCII_CHARACTER"
                message = "non-ASCII lexical characters are not in this baseline"
            else:
                code = "INVALID_CHARACTER"
                message = "character is not valid in the supported ST lexical subset"
            self._diagnose(code, message, start, line, column)

        if not self.errors:
            start, line, column = self._position()
            self._emit("EOF", start, line, column, "")
        if self.errors:
            raise STLexError(tuple(self.errors))
        return tuple(self.tokens)


def lex_st(source):
    """Tokenize an exact ``str`` using the frozen Stage 3 lexical subset."""

    if type(source) is not str:
        raise STLexError((STLexDiagnostic(
            "INVALID_SOURCE_TYPE", "ST source must be an exact str",
            0, 0, 1, 1),))
    if len(source) > _MAX_SOURCE_LENGTH:
        raise STLexError((STLexDiagnostic(
            "SOURCE_LIMIT", "ST source exceeds the supported length limit",
            0, 0, 1, 1),))
    return _Scanner(source).scan()
