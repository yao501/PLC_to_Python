"""Immutable Structured Text source model and strict Stage 3 subset parser.

The current candidate covers declarations, expressions/references/call source
shapes, assignment/call statements, IF/CASE/FOR/WHILE, and loop EXIT/CONTINUE.
Semantic binding, executable call lowering, implicit conversions, and the
remaining ST statements stay in separate Stage 3 packages.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.runtime.st_lexer import STToken, lex_st


_SCOPES = frozenset({
    "VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_TEMP", "VAR_GLOBAL",
})
_TYPE_KEYWORDS = frozenset({
    "BOOL", "SINT", "USINT", "INT", "UINT", "DINT", "UDINT", "LINT",
    "ULINT", "BYTE", "WORD", "DWORD", "LWORD", "REAL", "LREAL", "TIME",
    "STRING",
})
_LITERAL_KINDS = frozenset({"INTEGER", "REAL", "TIME_LITERAL", "STRING"})
_BINARY_PRECEDENCE = {
    "OR": 1,
    "XOR": 1,
    "AND": 2,
    "=": 3,
    "<>": 3,
    "<": 4,
    ">": 4,
    "<=": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "MOD": 6,
}
_UNARY = frozenset({"-", "NOT"})
_MAX_EXPRESSION_DEPTH = 64
_MAX_CALL_ARGUMENTS = 256
_MAX_INDEX_DIMENSIONS = 32
_MAX_CONTROL_FLOW_DEPTH = 64


@dataclass(frozen=True)
class STSpan:
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class STName:
    spelling: str
    canonical: str
    span: STSpan


@dataclass(frozen=True)
class STLiteral:
    kind: str
    text: str
    normalized: str
    span: STSpan


@dataclass(frozen=True)
class STUnary:
    operator: str
    operand: object
    span: STSpan


@dataclass(frozen=True)
class STBinary:
    operator: str
    left: object
    right: object
    span: STSpan


@dataclass(frozen=True)
class STMember:
    base: object
    member: STName
    span: STSpan


@dataclass(frozen=True)
class STIndex:
    base: object
    indices: tuple
    span: STSpan


@dataclass(frozen=True)
class STCallArgument:
    name: object
    direction: str
    value: object
    span: STSpan


@dataclass(frozen=True)
class STCall:
    callee: object
    arguments: tuple
    span: STSpan


@dataclass(frozen=True)
class STCallStatement:
    call: STCall
    span: STSpan


@dataclass(frozen=True)
class STIfBranch:
    condition: object
    statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STIf:
    branches: tuple
    else_statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STCaseLabel:
    lower: object
    upper: object
    span: STSpan


@dataclass(frozen=True)
class STCaseBranch:
    labels: tuple
    statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STCase:
    selector: object
    branches: tuple
    else_statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STFor:
    counter: STName
    start_value: object
    end_value: object
    increment: object
    statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STWhile:
    condition: object
    statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STExit:
    span: STSpan


@dataclass(frozen=True)
class STContinue:
    span: STSpan


@dataclass(frozen=True)
class STReturn:
    span: STSpan


@dataclass(frozen=True)
class STVariableDecl:
    scope: str
    names: tuple
    type_name: STName
    initializer: object
    span: STSpan


@dataclass(frozen=True)
class STAssignment:
    target: STName
    value: object
    span: STSpan


@dataclass(frozen=True)
class STUnit:
    declarations: tuple
    statements: tuple
    span: STSpan


@dataclass(frozen=True)
class STParseDiagnostic:
    code: str
    message: str
    start: int
    end: int
    line: int
    column: int


class STParseError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("; ".join(
            "%s at %d:%d: %s" % (
                error.code, error.line, error.column, error.message)
            for error in self.errors
        ))


def _token_span(token):
    return STSpan(token.start, token.end, token.line, token.column)


def _node_span(first, last):
    last_span = last.span if hasattr(last, "span") else _token_span(last)
    first_span = first.span if hasattr(first, "span") else _token_span(first)
    return STSpan(first_span.start, last_span.end, first_span.line, first_span.column)


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    def _current(self):
        return self.tokens[self.index]

    def _peek(self, distance=1):
        position = min(self.index + distance, len(self.tokens) - 1)
        return self.tokens[position]

    def _advance(self):
        token = self._current()
        if token.kind != "EOF":
            self.index += 1
        return token

    def _matches(self, normalized):
        return self._current().normalized == normalized

    def _error(self, code, message, token=None):
        actual = self._current() if token is None else token
        raise STParseError((STParseDiagnostic(
            code, message, actual.start, actual.end, actual.line, actual.column),))

    def _expect(self, normalized, code, message):
        if not self._matches(normalized):
            self._error(code, message)
        return self._advance()

    def _parse_name(self, code="EXPECTED_IDENTIFIER"):
        token = self._current()
        if token.kind != "IDENTIFIER":
            self._error(code, "an IEC identifier is required", token)
        self._advance()
        return STName(token.text, token.normalized, _token_span(token))

    def _parse_type_name(self):
        token = self._current()
        if token.kind != "IDENTIFIER" and not (
                token.kind == "KEYWORD" and token.normalized in _TYPE_KEYWORDS):
            self._error("EXPECTED_TYPE", "a basic or user type name is required", token)
        self._advance()
        return STName(token.text, token.normalized, _token_span(token))

    def _parse_call_arguments(self, depth):
        arguments = []
        if self._matches(")"):
            return tuple(arguments)
        while True:
            if len(arguments) >= _MAX_CALL_ARGUMENTS:
                self._error("CALL_ARGUMENT_LIMIT", "call argument limit exceeded")
            first = self._current()
            name = None
            direction = "POSITIONAL"
            value = None
            if first.kind == "IDENTIFIER" and self._peek().normalized in {":=", "=>"}:
                name = self._parse_name()
                operator = self._advance()
                direction = "INPUT" if operator.normalized == ":=" else "OUTPUT"
                if direction == "OUTPUT" and self._current().normalized in {",", ")"}:
                    last = operator
                elif direction == "OUTPUT":
                    value = self._parse_reference(depth + 1)
                    last = value
                else:
                    value = self._parse_expression(1, depth + 1)
                    last = value
            else:
                value = self._parse_expression(1, depth + 1)
                last = value
            arguments.append(STCallArgument(
                name, direction, value, _node_span(first, last)))
            if self._matches(")"):
                return tuple(arguments)
            self._expect(",", "EXPECTED_ARGUMENT_SEPARATOR",
                         "call arguments must be separated by ','")
            if self._matches(")"):
                self._error("EXPECTED_ARGUMENT", "trailing empty call argument is invalid")

    def _parse_reference(self, depth):
        base = self._parse_name("EXPECTED_REFERENCE")
        return self._parse_postfix(base, depth, allow_call=False)

    def _parse_postfix(self, base, depth, allow_call=True):
        while True:
            if self._matches("."):
                if not isinstance(base, (STName, STMember, STIndex)):
                    self._error("INVALID_ACCESS_BASE", "member access requires an ST reference")
                self._advance()
                member = self._parse_name("EXPECTED_MEMBER")
                base = STMember(base, member, _node_span(base, member))
                continue
            if self._matches("["):
                if not isinstance(base, (STName, STMember, STIndex)):
                    self._error("INVALID_ACCESS_BASE", "index access requires an ST reference")
                self._advance()
                if self._matches("]"):
                    self._error("EXPECTED_INDEX", "array access requires an index")
                indices = []
                while True:
                    if len(indices) >= _MAX_INDEX_DIMENSIONS:
                        self._error("INDEX_DIMENSION_LIMIT",
                                    "array index dimension limit exceeded")
                    indices.append(self._parse_expression(1, depth + 1))
                    if self._matches("]"):
                        break
                    self._expect(",", "EXPECTED_INDEX_SEPARATOR",
                                 "array indices must be separated by ','")
                closing = self._advance()
                base = STIndex(base, tuple(indices), _node_span(base, closing))
                continue
            if self._matches("("):
                if not allow_call:
                    self._error("INVALID_REFERENCE", "a call result is not an assignable reference")
                if not isinstance(base, (STName, STMember, STIndex)):
                    self._error("INVALID_CALLEE", "call target must be a named ST reference")
                self._advance()
                arguments = self._parse_call_arguments(depth + 1)
                closing = self._expect(")", "EXPECTED_RPAREN", "call requires ')'")
                base = STCall(base, arguments, _node_span(base, closing))
                if self._matches(".") or self._matches("[") or self._matches("("):
                    self._error(
                        "UNSUPPORTED_CALL_RESULT_ACCESS",
                        "CODESYS does not allow access or another call on a call result")
                return base
            return base

    def _parse_primary(self, depth):
        token = self._current()
        if token.normalized == "EXPT":
            self._advance()
            name = STName(token.text, token.normalized, _token_span(token))
            if not self._matches("("):
                self._error("EXPECTED_CALL", "EXPT requires call syntax")
            return self._parse_postfix(name, depth)
        if token.kind in _LITERAL_KINDS or (
                token.kind == "KEYWORD" and token.normalized in {"TRUE", "FALSE"}):
            self._advance()
            kind = "BOOL" if token.normalized in {"TRUE", "FALSE"} else token.kind
            return self._parse_postfix(
                STLiteral(kind, token.text, token.normalized, _token_span(token)), depth)
        if token.kind == "IDENTIFIER":
            return self._parse_postfix(self._parse_name(), depth)
        if self._matches("("):
            opening = self._advance()
            expression = self._parse_expression(1, depth + 1)
            closing = self._expect(
                ")", "EXPECTED_RPAREN", "parenthesized expression requires ')' ")
            # Parentheses do not need a separate node, but their full source range is
            # kept by rebuilding the immutable expression with an enclosing span.
            span = _node_span(opening, closing)
            if isinstance(expression, STName):
                enclosed = STName(expression.spelling, expression.canonical, span)
            elif isinstance(expression, STLiteral):
                enclosed = STLiteral(expression.kind, expression.text,
                                     expression.normalized, span)
            elif isinstance(expression, STUnary):
                enclosed = STUnary(expression.operator, expression.operand, span)
            elif isinstance(expression, STBinary):
                enclosed = STBinary(
                    expression.operator, expression.left, expression.right, span)
            elif isinstance(expression, STMember):
                enclosed = STMember(expression.base, expression.member, span)
            elif isinstance(expression, STIndex):
                enclosed = STIndex(expression.base, expression.indices, span)
            elif isinstance(expression, STCall):
                enclosed = STCall(expression.callee, expression.arguments, span)
            else:
                enclosed = expression
            if self._matches(".") or self._matches("[") or self._matches("("):
                self._error(
                    "UNSUPPORTED_GROUPED_POSTFIX",
                    "postfix access or call on a parenthesized expression is unsupported")
            return enclosed
        self._error("EXPECTED_EXPRESSION", "an ST expression operand is required", token)

    def _parse_expression(self, minimum_precedence=1, depth=0):
        if depth >= _MAX_EXPRESSION_DEPTH:
            self._error("EXPRESSION_DEPTH", "expression nesting exceeds the supported limit")
        first = self._current()
        if first.normalized in _UNARY:
            operator = self._advance()
            operand = self._parse_expression(7, depth + 1)
            left = STUnary(operator.normalized, operand, _node_span(operator, operand))
        else:
            left = self._parse_primary(depth)

        while True:
            token = self._current()
            precedence = _BINARY_PRECEDENCE.get(token.normalized)
            if precedence is None or precedence < minimum_precedence:
                break
            operator = self._advance()
            # CODESYS evaluates operators with equal binding strength left-to-right.
            right = self._parse_expression(precedence + 1, depth + 1)
            left = STBinary(operator.normalized, left, right, _node_span(left, right))
        return left

    def _parse_declaration(self, scope):
        first = self._current()
        names = [self._parse_name()]
        while self._matches(","):
            self._advance()
            names.append(self._parse_name())
        self._expect(":", "EXPECTED_COLON", "variable declaration requires ':'")
        type_name = self._parse_type_name()
        initializer = None
        if self._matches(":="):
            self._advance()
            initializer = self._parse_expression()
        ending = self._expect(
            ";", "EXPECTED_SEMICOLON", "variable declaration requires ';'")
        return STVariableDecl(
            scope, tuple(names), type_name, initializer, _node_span(first, ending))

    def _parse_var_section(self):
        scope = self._advance().normalized
        declarations = []
        while not self._matches("END_VAR"):
            if self._current().kind == "EOF":
                self._error("EXPECTED_END_VAR", "variable section requires END_VAR")
            declarations.append(self._parse_declaration(scope))
        self._advance()
        return declarations

    def _parse_statement_list(self, stops, depth):
        statements = []
        while self._current().normalized not in stops:
            if self._current().kind == "EOF":
                self._error("EXPECTED_END_IF", "IF statement requires END_IF")
            statements.append(self._parse_statement(depth))
        return tuple(statements)

    def _case_label_start(self):
        return self._current().kind == "INTEGER" or (
            self._matches("-") and self._peek().kind == "INTEGER")

    def _parse_case_value(self):
        first = self._current()
        if self._matches("-"):
            operator = self._advance()
            token = self._current()
            if token.kind != "INTEGER":
                self._error(
                    "EXPECTED_CASE_LABEL",
                    "CASE label sign must be followed by a decimal integer", token)
            self._advance()
            literal = STLiteral(
                "INTEGER", token.text, token.normalized, _token_span(token))
            return STUnary("-", literal, _node_span(operator, literal))
        if first.kind != "INTEGER":
            self._error(
                "EXPECTED_CASE_LABEL",
                "CASE label must be a signed decimal integer in this strict subset",
                first)
        self._advance()
        return STLiteral("INTEGER", first.text, first.normalized, _token_span(first))

    def _parse_case_label(self):
        lower = self._parse_case_value()
        upper = None
        if self._matches(".."):
            self._advance()
            upper = self._parse_case_value()
        return STCaseLabel(lower, upper, _node_span(lower, upper or lower))

    def _parse_case(self, depth):
        if depth >= _MAX_CONTROL_FLOW_DEPTH:
            self._error("CONTROL_FLOW_DEPTH", "control-flow nesting exceeds the limit")
        opening = self._advance()
        selector = self._parse_expression()
        self._expect("OF", "EXPECTED_OF", "CASE selector requires OF")
        branches = []
        while not self._matches("ELSE") and not self._matches("END_CASE"):
            if not self._case_label_start():
                self._error(
                    "EXPECTED_CASE_LABEL",
                    "CASE branch requires at least one supported label")
            labels = [self._parse_case_label()]
            while self._matches(","):
                self._advance()
                labels.append(self._parse_case_label())
            colon = self._expect(":", "EXPECTED_COLON", "CASE labels require ':'")
            statements = []
            while not self._matches("ELSE") and not self._matches("END_CASE") \
                    and not self._case_label_start():
                if self._current().kind == "EOF":
                    self._error("EXPECTED_END_CASE", "CASE statement requires END_CASE")
                statements.append(self._parse_statement(depth + 1))
            last = statements[-1] if statements else colon
            branches.append(STCaseBranch(
                tuple(labels), tuple(statements), _node_span(labels[0], last)))
        if not branches:
            self._error("EXPECTED_CASE_LABEL", "CASE requires at least one label branch")
        else_statements = ()
        if self._matches("ELSE"):
            self._advance()
            statements = []
            while not self._matches("END_CASE"):
                if self._current().kind == "EOF":
                    self._error("EXPECTED_END_CASE", "CASE statement requires END_CASE")
                statements.append(self._parse_statement(depth + 1))
            else_statements = tuple(statements)
        ending = self._expect(
            "END_CASE", "EXPECTED_END_CASE", "CASE statement requires END_CASE")
        return STCase(selector, tuple(branches), else_statements,
                      _node_span(opening, ending))

    def _parse_for(self, depth):
        if depth >= _MAX_CONTROL_FLOW_DEPTH:
            self._error("CONTROL_FLOW_DEPTH", "control-flow nesting exceeds the limit")
        opening = self._advance()
        counter = self._parse_name("EXPECTED_FOR_COUNTER")
        self._expect(":=", "EXPECTED_ASSIGNMENT", "FOR counter requires ':='")
        start_value = self._parse_expression()
        self._expect("TO", "EXPECTED_TO", "FOR start value requires TO")
        end_value = self._parse_expression()
        increment = None
        if self._matches("BY"):
            self._advance()
            increment = self._parse_expression()
        self._expect("DO", "EXPECTED_DO", "FOR header requires DO")
        statements = []
        while not self._matches("END_FOR"):
            if self._current().kind == "EOF":
                self._error("EXPECTED_END_FOR", "FOR statement requires END_FOR")
            statements.append(self._parse_statement(depth + 1))
        self._advance()
        ending = self._expect(";", "EXPECTED_SEMICOLON", "END_FOR requires ';'")
        return STFor(counter, start_value, end_value, increment,
                     tuple(statements), _node_span(opening, ending))

    def _parse_while(self, depth):
        if depth >= _MAX_CONTROL_FLOW_DEPTH:
            self._error("CONTROL_FLOW_DEPTH", "control-flow nesting exceeds the limit")
        opening = self._advance()
        condition = self._parse_expression()
        self._expect("DO", "EXPECTED_DO", "WHILE condition requires DO")
        statements = []
        while not self._matches("END_WHILE"):
            if self._current().kind == "EOF":
                self._error("EXPECTED_END_WHILE", "WHILE statement requires END_WHILE")
            statements.append(self._parse_statement(depth + 1))
        self._advance()
        ending = self._expect(";", "EXPECTED_SEMICOLON", "END_WHILE requires ';'")
        return STWhile(condition, tuple(statements), _node_span(opening, ending))

    def _parse_loop_transfer(self, node_type):
        opening = self._advance()
        ending = self._expect(
            ";", "EXPECTED_SEMICOLON", "%s requires ';'" % opening.normalized)
        return node_type(_node_span(opening, ending))

    def _parse_if(self, depth):
        if depth >= _MAX_CONTROL_FLOW_DEPTH:
            self._error("CONTROL_FLOW_DEPTH", "control-flow nesting exceeds the limit")
        opening = self._advance()
        branches = []
        condition = self._parse_expression()
        then_token = self._expect("THEN", "EXPECTED_THEN", "IF condition requires THEN")
        statements = self._parse_statement_list({"ELSIF", "ELSE", "END_IF"}, depth + 1)
        last = statements[-1] if statements else then_token
        branches.append(STIfBranch(condition, statements, _node_span(condition, last)))
        while self._matches("ELSIF"):
            self._advance()
            condition = self._parse_expression()
            then_token = self._expect(
                "THEN", "EXPECTED_THEN", "ELSIF condition requires THEN")
            statements = self._parse_statement_list(
                {"ELSIF", "ELSE", "END_IF"}, depth + 1)
            last = statements[-1] if statements else then_token
            branches.append(STIfBranch(condition, statements, _node_span(condition, last)))
        else_statements = ()
        if self._matches("ELSE"):
            self._advance()
            else_statements = self._parse_statement_list({"END_IF"}, depth + 1)
        self._expect("END_IF", "EXPECTED_END_IF", "IF statement requires END_IF")
        ending = self._expect(";", "EXPECTED_SEMICOLON", "END_IF requires ';'")
        return STIf(tuple(branches), else_statements, _node_span(opening, ending))

    def _parse_statement(self, depth=0):
        first = self._current()
        if first.normalized == "IF":
            return self._parse_if(depth)
        if first.normalized == "CASE":
            return self._parse_case(depth)
        if first.normalized == "FOR":
            return self._parse_for(depth)
        if first.normalized == "WHILE":
            return self._parse_while(depth)
        if first.normalized == "EXIT":
            return self._parse_loop_transfer(STExit)
        if first.normalized == "CONTINUE":
            return self._parse_loop_transfer(STContinue)
        if first.normalized == "RETURN":
            return self._parse_loop_transfer(STReturn)
        if first.kind != "IDENTIFIER":
            self._error(
                "EXPECTED_ASSIGNMENT_TARGET",
                "assignment target must be a simple IEC identifier", first)
        target_or_call = self._parse_postfix(self._parse_name(), 0)
        if isinstance(target_or_call, STCall):
            if self._matches(":="):
                self._error(
                    "INVALID_ASSIGNMENT_TARGET",
                    "a call result is not an assignable target")
            ending = self._expect(";", "EXPECTED_SEMICOLON", "call statement requires ';'")
            return STCallStatement(
                target_or_call, _node_span(first, ending))
        if not isinstance(target_or_call, (STName, STMember, STIndex)):
            self._error("INVALID_ASSIGNMENT_TARGET", "assignment target is not assignable")
        self._expect(":=", "EXPECTED_ASSIGNMENT", "assignment requires ':='")
        value = self._parse_expression()
        ending = self._expect(";", "EXPECTED_SEMICOLON", "assignment requires ';'")
        return STAssignment(target_or_call, value, _node_span(first, ending))

    def parse(self):
        declarations = []
        statements = []
        while self._current().normalized in _SCOPES:
            declarations.extend(self._parse_var_section())
        while self._current().kind != "EOF":
            if self._current().normalized in _SCOPES:
                self._error(
                    "DECLARATION_AFTER_STATEMENT",
                    "variable sections must precede implementation statements")
            statements.append(self._parse_statement())
        eof = self._current()
        if not declarations and not statements:
            return STUnit((), (), _token_span(eof))
        # Unit range covers all significant syntax, including VAR/END_VAR tokens.
        first_token = self.tokens[0]
        last_token = self.tokens[self.index - 1]
        return STUnit(
            tuple(declarations), tuple(statements), _node_span(first_token, last_token))


def parse_st(source):
    """Parse the current strict Stage 3 source-model subset."""

    return _Parser(lex_st(source)).parse()
