from llvmlite import ir
import llvmlite.binding as llvm
import sys, os


class CompileError(Exception):
    pass


class Token:
    def __init__(self, kind, text, line, col):
        self.kind, self.text, self.line, self.col = kind, text, line, col


KEYWORDS = {
    b"i32", b"i64", b"bool",
    b"mut", b"exit", b"struct", b"fn", b"if",
    b"true", b"false"
}

INT32_MAX = 2**31 - 1
INT64_MAX = 2**63 - 1


def is_alpha(b):
    return 65 <= b <= 90 or 97 <= b <= 122 or b == 95


def is_digit(b):
    return 48 <= b <= 57


def lex(data):
    lines, toks, state, i, line, col = [], [], "START", 0, 1, 1
    start = 0
    sl = sc = 1

    while i <= len(data):
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None:
                break

            if b in (32, 9, 13):
                i += 1
                col += 1
                continue

            if b == 10:
                toks.append(Token("endline", "\n", line, col))
                lines.append(toks)
                toks = []
                i += 1
                line += 1
                col = 1
                continue

            if is_alpha(b):
                state, start, sl, sc = "IDENT", i, line, col
                i += 1
                col += 1
                continue

            if is_digit(b):
                state, start, sl, sc = "NUMBER", i, line, col
                i += 1
                col += 1
                continue

            if b in (ord("{"), ord("}")):
                toks.append(Token("block", chr(b), line, col))
                i += 1
                col += 1
                continue

            if b in (ord(","), ord("("), ord(")"), ord(".")):
                toks.append(Token("punctuation", chr(b), line, col))
                i += 1
                col += 1
                continue

            if b == ord("-") and i + 1 < len(data) and data[i + 1] == ord(">"):
                toks.append(Token("operator", "->", line, col))
                i += 2
                col += 2
                continue

            if b in (ord("+"), ord("-"), ord("*")):
                toks.append(Token("operator", chr(b), line, col))
                i += 1
                col += 1
                continue

            if b == ord(":"):
                state, sl, sc = "COLON", line, col
                i += 1
                col += 1
                continue

            if b == ord("="):
                state, sl, sc = "EQUAL", line, col
                i += 1
                col += 1
                continue

            if b == ord("!"):
                state, sl, sc = "BANG", line, col
                i += 1
                col += 1
                continue

            bad = repr(chr(b)) if 32 <= b <= 126 else f"0x{b:02x}"
            raise CompileError(
                f"line {line}:{col}: unexpected byte {bad}"
            )

        elif state == "IDENT":
            if b is not None and (is_alpha(b) or is_digit(b)):
                i += 1
                col += 1
                continue

            word = data[start:i]
            kind = "keyword" if word in KEYWORDS else "identifier"
            toks.append(
                Token(kind, word.decode("ascii"), sl, sc)
            )
            state = "START"

        elif state == "NUMBER":
            if b is not None and is_digit(b):
                i += 1
                col += 1
                continue

            if b is not None and is_alpha(b):
                raise CompileError(
                    f"line {sl}:{sc}: letter inside number"
                )

            toks.append(
                Token(
                    "number",
                    data[start:i].decode("ascii"),
                    sl,
                    sc
                )
            )
            state = "START"

        elif state == "COLON":
            if b != ord("="):
                raise CompileError(
                    f"line {sl}:{sc}: ':' must be followed by '='"
                )

            toks.append(Token("operator", ":=", sl, sc))
            state = "START"
            i += 1
            col += 1

        elif state == "EQUAL":
            if b != ord("="):
                raise CompileError(
                    f"line {sl}:{sc}: expected '==' "
                    f"(a single '=' is not an operator)"
                )

            toks.append(Token("operator", "==", sl, sc))
            state = "START"
            i += 1
            col += 1

        elif state == "BANG":
            if b == ord("="):
                toks.append(Token("operator", "!=", sl, sc))
                i += 1
                col += 1
            else:
                toks.append(Token("operator", "!", sl, sc))
            state = "START"

    if toks:
        lines.append(toks)

    return lines


class ASTNode:
    def children(self):
        return []

    def accept(self, visitor):
        name = type(self).__name__[:-4].lower()
        return getattr(visitor, "visit_" + name)(self)

    def dump(self, prefix="", last=True, root=True):
        if root:
            print(self.label())
        else:
            print(
                prefix
                + ("└── " if last else "├── ")
                + self.label()
            )

        prefix = (
            ""
            if root
            else prefix + ("    " if last else "│   ")
        )

        kids = self.children()

        for i, child in enumerate(kids):
            child.dump(
                prefix,
                i == len(kids) - 1,
                False
            )


class ProgramNode(ASTNode):
    def __init__(self, statements, exit_node, structs=None, functions=None):
        self.structs = structs if structs is not None else []
        self.functions = functions if functions is not None else []
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Program"

    def children(self):
        return self.structs + self.functions + self.statements + [self.exit_node]


class StructDeclNode(ASTNode):
    def __init__(self, line, col, name, fields):
        self.line, self.col = line, col
        self.name = name
        self.fields = fields
        self.field_map = None

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields


class FieldDeclNode(ASTNode):
    def __init__(self, line, col, name, type_name, mutable):
        self.line, self.col = line, col
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.index = None

    def label(self):
        kind = "mut" if self.mutable else "const"
        return f"Field {self.name} {self.type_name} {kind}"


class DeclNode(ASTNode):
    def __init__(
        self,
        line,
        col,
        name,
        type_name,
        mutable,
        initializers,
        brace_line,
        brace_col
    ):
        self.line = line
        self.col = col
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.initializers = initializers
        self.init = initializers[0] if len(initializers) == 1 else None
        self.brace_line = brace_line
        self.brace_col = brace_col
        self.struct_fields = None
        self.struct_copy = False
        self.ir_ptr = None

    def label(self):
        kind = "mut" if self.mutable else "const"
        return f"Decl {self.name} {self.type_name} {kind}"

    def children(self):
        return self.initializers


class AssignNode(ASTNode):
    def __init__(self, line, col, name, value):
        self.line = line
        self.col = col
        self.name = name
        self.value = value
        self.decl = None
        self.target = None
        self.type_name = None

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return ([self.target] if self.target is not None else []) + [self.value]


class ExitNode(ASTNode):
    def __init__(self, line, col, value):
        self.line = line
        self.col = col
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]


class BinOpNode(ASTNode):
    def __init__(
        self,
        line,
        col,
        op,
        left,
        right
    ):
        self.line = line
        self.col = col
        self.op = op
        self.left = left
        self.right = right
        self.type = None

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]


class VarNode(ASTNode):
    def __init__(self, line, col, name):
        self.line = line
        self.col = col
        self.name = name
        self.type = None
        self.decl = None

    def label(self):
        return f"Var {self.name}"


class ConstNode(ASTNode):
    def __init__(self, line, col, value):
        self.line = line
        self.col = col
        self.value = value
        self.type = None

    def label(self):
        return f"Const {self.value}"


class BoolNode(ASTNode):
    def __init__(self, line, col, value):
        self.line = line
        self.col = col
        self.value = value
        self.type = "bool"

    def label(self):
        return (
            "Bool true"
            if self.value
            else "Bool false"
        )


class NestedInitNode(ASTNode):
    """Brace-delimited value for a struct field (not a general expression)."""
    def __init__(self, line, col, initializers):
        self.line, self.col = line, col
        self.initializers = initializers
        self.type = None
        self.struct_fields = None

    def label(self):
        return "Nested initialiser"

    def children(self):
        return self.initializers


class ParamNode(ASTNode):
    def __init__(self, line, col, name, type_name):
        self.line, self.col = line, col
        self.name, self.type_name = name, type_name
        self.mutable = False
        self.ir_ptr = None

    def label(self):
        return f"Param {self.name} {self.type_name} const"


class FunctionNode(ASTNode):
    def __init__(self, line, col, name, params, result_type, body):
        self.line, self.col = line, col
        self.name = name
        self.params = params
        self.result_type = result_type
        self.body = body
        self.ir_function = None

    def label(self):
        return f"Function {self.name} -> {self.result_type}"

    def children(self):
        return self.params + self.body


class IfNode(ASTNode):
    def __init__(self, line, col, condition, body):
        self.line, self.col = line, col
        self.condition, self.body = condition, body

    def label(self):
        return "If"

    def children(self):
        return [self.condition] + self.body


class FieldAccessNode(ASTNode):
    def __init__(self, line, col, base, name):
        self.line, self.col = line, col
        self.base, self.name = base, name
        self.field = None
        self.type = None

    def label(self):
        return f"Field .{self.name}"

    def children(self):
        return [self.base]


class CallNode(ASTNode):
    def __init__(self, line, col, name, args):
        self.line, self.col = line, col
        self.name, self.args = name, args
        self.function = None
        self.type = None

    def label(self):
        return f"Call {self.name}"

    def children(self):
        return self.args


class NotNode(ASTNode):
    def __init__(self, line, col, operand):
        self.line, self.col = line, col
        self.operand = operand
        self.type = None

    def label(self):
        return "Not !"

    def children(self):
        return [self.operand]


class Parser:
    def __init__(self, lines):
        self.lines = [
            [
                t
                for t in line
                if t.kind != "endline"
            ]
            for line in lines
        ]

        self.toks = []
        self.pos = 0

    def peek(self):
        if self.pos < len(self.toks):
            return self.toks[self.pos]
        return None

    def eat(self):
        t = self.peek()

        if t:
            self.pos += 1

        return t

    def fail(self, message):
        t = self.peek()

        if t:
            raise CompileError(
                f"line {t.line}:{t.col}: {message}"
            )

        if self.toks:
            t = self.toks[-1]
            raise CompileError(
                f"line {t.line}:"
                f"{t.col + len(t.text)}: "
                f"{message}"
            )

        raise CompileError(message)

    def expect_text(self, text):
        if (
            not self.peek()
            or self.peek().text != text
        ):
            self.fail(f"expected '{text}'")

        return self.eat()

    def expect_kind(self, kind, what):
        if (
            not self.peek()
            or self.peek().kind != kind
        ):
            self.fail(f"expected {what}")

        return self.eat()

    def expect_type(self):
        t = self.peek()
        if t and (t.kind == "identifier" or t.text in ("i32", "i64", "bool")):
            return self.eat()
        self.fail("expected a type")

    def parse_struct(self, index):
        """Parse a multi-line struct declaration; return node and next line index."""
        self.toks, self.pos = self.lines[index], 0
        self.expect_text("struct")
        name = self.expect_kind("identifier", "a struct name")
        if self.peek():
            self.fail("struct name must be followed by a newline")

        index += 1
        while index < len(self.lines) and not self.lines[index]:
            index += 1

        if index >= len(self.lines):
            raise CompileError(f"line {name.line}:{name.col}: expected '{{' after struct name")

        self.toks, self.pos = self.lines[index], 0
        self.expect_text("{")
        if self.peek():
            self.fail("expected newline after '{'")

        index += 1
        fields = []
        while index < len(self.lines):
            line = self.lines[index]
            index += 1
            if not line:
                continue
            self.toks, self.pos = line, 0
            if self.peek().text == "}":
                self.eat()
                if self.peek():
                    self.fail("unexpected token after struct '}'")
                return StructDeclNode(name.line, name.col, name.text, fields), index

            type_tok = self.expect_type()
            mutable = bool(self.peek() and self.peek().text == "mut")
            if mutable:
                self.eat()
            field = self.expect_kind("identifier", "a field name")
            if self.peek():
                self.fail("unexpected token after field declaration")
            fields.append(FieldDeclNode(field.line, field.col, field.text,
                                        type_tok.text, mutable))

        raise CompileError(f"line {name.line}:{name.col}: unclosed struct '{name.text}'")

    def parse_block(self, index):
        """Read a brace-delimited block, allowing nested if statements."""
        while index < len(self.lines) and not self.lines[index]:
            index += 1
        if index >= len(self.lines):
            raise CompileError("line 1:1: expected '{' for block")
        self.toks, self.pos = self.lines[index], 0
        self.expect_text("{")
        if self.peek():
            self.fail("expected newline after '{'")
        index += 1
        body = []
        has_exit = False
        while index < len(self.lines):
            line = self.lines[index]
            if not line:
                index += 1
                continue
            self.toks, self.pos = line, 0
            if line[0].text == "}":
                self.eat()
                if self.peek():
                    self.fail("unexpected token after '}'")
                return body, index + 1
            if has_exit:
                self.fail("nothing is allowed after exit")
            if line[0].text in ("struct", "fn"):
                self.fail("structs and functions are only allowed at top level")
            if line[0].text == "if":
                if_node, index = self.parse_if(index)
                body.append(if_node)
                continue
            stmt = self.parse_statement()
            if self.peek():
                self.fail(f"unexpected '{self.peek().text}' after the statement")
            body.append(stmt)
            has_exit = isinstance(stmt, ExitNode)
            index += 1
        t = self.lines[index - 1][-1]
        raise CompileError(f"line {t.line}:{t.col}: unclosed block")

    def parse_if(self, index):
        self.toks, self.pos = self.lines[index], 0
        t = self.expect_text("if")
        condition = self.parse_expr()
        if self.peek():
            self.fail("unexpected token after if condition")
        body, next_index = self.parse_block(index + 1)
        return IfNode(t.line, t.col, condition, body), next_index

    def parse_function(self, index):
        self.toks, self.pos = self.lines[index], 0
        self.expect_text("fn")
        name = self.expect_kind("identifier", "a function name")
        self.expect_text(":=")
        self.expect_text("(")
        params = []
        if self.peek() and self.peek().text != ")":
            while True:
                type_tok = self.expect_type()
                arg = self.expect_kind("identifier", "a parameter name")
                params.append(ParamNode(arg.line, arg.col, arg.text, type_tok.text))
                if not self.peek() or self.peek().text != ",":
                    break
                self.eat()
        self.expect_text(")")
        self.expect_text("->")
        result = self.expect_type()
        if self.peek():
            self.fail("expected newline after function signature")
        body, next_index = self.parse_block(index + 1)
        if not body or not isinstance(body[-1], ExitNode):
            raise CompileError(
                f"line {name.line}:{name.col}: function '{name.text}' must end with exit")
        return FunctionNode(name.line, name.col, name.text, params,
                            result.text, body), next_index

    def parse_statement(self):
        first = self.peek()
        if first.text in ("i32", "i64", "bool"):
            return self.parse_decl()
        if first.kind == "identifier":
            following = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
            if following and (following.kind == "identifier" or following.text == "mut"):
                return self.parse_decl()
            return self.parse_assign()
        if first.text == "exit":
            return self.parse_exit()
        self.fail(f"cannot start statement with '{first.text}'")

    def parse_program(self):
        structs, functions, statements = [], [], []
        exit_node = None
        phase = "structs"
        index = 0
        while index < len(self.lines):
            line = self.lines[index]
            if not line:
                index += 1
                continue
            self.toks, self.pos = line, 0
            first = self.peek()
            if exit_node is not None:
                self.fail("nothing is allowed after exit")
            if first.text == "struct":
                if phase == "statements":
                    self.fail("struct declarations must come before statements")
                if phase == "functions":
                    self.fail("struct declarations must come before functions")
                struct, index = self.parse_struct(index)
                structs.append(struct)
                continue
            if first.text == "fn":
                if phase == "statements":
                    self.fail("function declarations must come before statements")
                phase = "functions"
                function, index = self.parse_function(index)
                functions.append(function)
                continue
            phase = "statements"
            if first.text == "if":
                stmt, index = self.parse_if(index)
            else:
                stmt = self.parse_statement()
                if self.peek():
                    self.fail(f"unexpected '{self.peek().text}' after the statement")
                index += 1
            if isinstance(stmt, ExitNode):
                exit_node = stmt
            else:
                statements.append(stmt)
        if exit_node is None:
            nonempty = [line for line in self.lines if line]
            if nonempty:
                t = nonempty[-1][-1]
                raise CompileError(
                    f"line {t.line}:{t.col + len(t.text)}: program has no exit statement")
            raise CompileError("line 1:1: program has no exit statement")
        return ProgramNode(statements, exit_node, structs, functions)

    def parse_decl(self):
        type_tok = self.expect_type()

        mutable = bool(
            self.peek()
            and self.peek().text == "mut"
        )

        if mutable:
            self.eat()

        name = self.expect_kind(
            "identifier",
            "a variable name"
        )

        if (
            not self.peek()
            or self.peek().text != "{"
        ):
            raise CompileError(
                f"line {name.line}:{name.col}: "
                f"variable '{name.text}' "
                "needs an initialiser in {}"
            )

        brace = self.eat()

        initializers = []
        if self.peek() and self.peek().text != "}":
            initializers.append(self.parse_initializer())
            while self.peek() and self.peek().text == ",":
                self.eat()
                initializers.append(self.parse_initializer())

        self.expect_text("}")

        return DeclNode(
            name.line,
            name.col,
            name.text,
            type_tok.text,
            mutable,
            initializers,
            brace.line,
            brace.col
        )

    def parse_initializer(self):
        if self.peek() and self.peek().text == "{":
            brace = self.eat()
            items = []
            if self.peek() and self.peek().text != "}":
                items.append(self.parse_initializer())
                while self.peek() and self.peek().text == ",":
                    self.eat()
                    items.append(self.parse_initializer())
            self.expect_text("}")
            return NestedInitNode(brace.line, brace.col, items)
        return self.parse_expr()

    def parse_assign(self):
        name = self.expect_kind("identifier", "a variable name")
        target = VarNode(name.line, name.col, name.text)
        while self.peek() and self.peek().text == ".":
            self.eat()
            field = self.expect_kind("identifier", "a field name")
            target = FieldAccessNode(field.line, field.col, target, field.text)
        if not self.peek() or self.peek().text != ":=":
            got = "end of line" if not self.peek() else f"'{self.peek().text}'"
            self.fail(f"expected ':=' after '{name.text}', got {got}")
        self.eat()
        value = self.parse_expr()
        node = AssignNode(name.line, name.col, name.text, value)
        if isinstance(target, FieldAccessNode):
            node.target = target
        return node

    def parse_exit(self):
        t = self.expect_text("exit")

        return ExitNode(
            t.line,
            t.col,
            self.parse_factor()
        )

    def parse_expr(self):
        node = self.parse_arith()

        if (
            self.peek()
            and self.peek().kind == "operator"
            and self.peek().text in (
                "==",
                "!="
            )
        ):
            op = self.eat()

            node = BinOpNode(
                op.line,
                op.col,
                op.text,
                node,
                self.parse_arith()
            )

        return node

    def parse_arith(self):
        node = self.parse_term()

        while (
            self.peek()
            and self.peek().kind == "operator"
            and self.peek().text in (
                "+",
                "-"
            )
        ):
            op = self.eat()

            node = BinOpNode(
                op.line,
                op.col,
                op.text,
                node,
                self.parse_term()
            )

        return node

    def parse_term(self):
        node = self.parse_factor()

        while (
            self.peek()
            and self.peek().kind == "operator"
            and self.peek().text == "*"
        ):
            op = self.eat()

            node = BinOpNode(
                op.line,
                op.col,
                op.text,
                node,
                self.parse_factor()
            )

        return node

    def parse_factor(self):
        t = self.peek()
        if not t:
            self.fail("expected a constant or a variable")
        if t.text == "!":
            self.eat()
            return NotNode(t.line, t.col, self.parse_factor())
        if t.kind == "number":
            self.eat()
            return ConstNode(t.line, t.col, int(t.text))
        if t.text in ("true", "false"):
            self.eat()
            return BoolNode(t.line, t.col, t.text == "true")
        if t.kind == "identifier":
            self.eat()
            if self.peek() and self.peek().text == "(":
                self.eat()
                args = []
                if self.peek() and self.peek().text != ")":
                    args.append(self.parse_expr())
                    while self.peek() and self.peek().text == ",":
                        self.eat()
                        args.append(self.parse_expr())
                self.expect_text(")")
                node = CallNode(t.line, t.col, t.text, args)
            else:
                node = VarNode(t.line, t.col, t.text)
            while self.peek() and self.peek().text == ".":
                self.eat()
                field = self.expect_kind("identifier", "a field name")
                node = FieldAccessNode(field.line, field.col, node, field.text)
            return node
        self.fail(f"expected a constant or a variable, got '{t.text}'")


class SemanticChecker:
    def __init__(self):
        self.symbols = {}
        self.structs = {}
        self.functions = {}
        self.current_function = None

    def fail(self, node, message):
        raise CompileError(
            f"line {node.line}:"
            f"{node.col}: "
            f"{message}"
        )

    @staticmethod
    def is_int(type_name):
        return type_name in (
            "i32",
            "i64"
        )

    @staticmethod
    def wider(left, right):
        if "i64" in (left, right):
            return "i64"

        return "i32"

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)

        for function in node.functions:
            if function.name in self.functions:
                self.fail(function, f"function '{function.name}' is already declared")
            if not self.known_type(function.result_type):
                self.fail(function, f"unknown result type '{function.result_type}'")
            seen = set()
            for param in function.params:
                if not self.known_type(param.type_name):
                    self.fail(param, f"unknown parameter type '{param.type_name}'")
                if param.name in seen:
                    self.fail(param, f"parameter '{param.name}' is already declared")
                seen.add(param.name)
            self.functions[function.name] = function

        for function in node.functions:
            function.accept(self)

        self.symbols = {}
        for statement in node.statements:
            statement.accept(self)

        node.exit_node.accept(self)

    def visit_function(self, node):
        saved_symbols, saved_function = self.symbols, self.current_function
        self.symbols = {param.name: param for param in node.params}
        self.current_function = node
        for statement in node.body:
            statement.accept(self)
        self.symbols, self.current_function = saved_symbols, saved_function

    def visit_if(self, node):
        condition_type = node.condition.accept(self)
        if condition_type != "bool":
            self.fail(node.condition, "if condition must have type bool")
        saved_symbols = self.symbols
        self.symbols = dict(saved_symbols)
        for statement in node.body:
            statement.accept(self)
        self.symbols = saved_symbols

    def known_type(self, name):
        return name in ("i32", "i64", "bool") or name in self.structs

    def visit_structdecl(self, node):
        if node.name in self.structs:
            self.fail(node, f"struct '{node.name}' is already declared")
        if not node.fields:
            self.fail(node, f"struct '{node.name}' must have at least one field")

        names = set()
        for index, field in enumerate(node.fields):
            if field.name in names:
                self.fail(field, f"field '{field.name}' is already declared")
            names.add(field.name)
            if not self.known_type(field.type_name):
                self.fail(field, f"unknown or not yet declared type '{field.type_name}'")
            field.index = index

        node.field_map = {field.name: field for field in node.fields}
        self.structs[node.name] = node

    def fully_mutable(self, type_name):
        if type_name not in self.structs:
            return True
        return all(field.mutable and self.fully_mutable(field.type_name)
                   for field in self.structs[type_name].fields)

    def fail_init(self, node, message):
        raise CompileError(f"line {node.brace_line}:{node.brace_col}: {message}")

    def check_field_init(self, init, type_name, name, already_checked=False):
        """Validate a field recursively using its declared (contextual) type.

        Brace-delimited values are legal only for struct fields.  Report an
        invalid brace or field count at the opening brace at the *current*
        nesting level, including arbitrarily deeply nested structures.
        """
        if isinstance(init, NestedInitNode):
            if type_name not in self.structs:
                self.fail(init, f"nested '{{}}' requires a struct field, got {type_name}")
            struct = self.structs[type_name]
            init.type = type_name
            init.struct_fields = struct.fields
            if len(init.initializers) != len(struct.fields):
                self.fail(init, f"struct '{type_name}' needs {len(struct.fields)} "
                                f"value(s), got {len(init.initializers)}")
            for field, child in zip(struct.fields, init.initializers):
                self.check_field_init(child, field.type_name, field.name)
        else:
            if not already_checked:
                init.accept(self)
            self.check_assignable(init, type_name, init,
                                  f"initialise field '{name}'")

    def visit_decl(self, node):
        if node.name in self.symbols:
            self.fail(node, f"variable '{node.name}' is already declared")
        if not self.known_type(node.type_name):
            self.fail(node, f"unknown type '{node.type_name}'")
        if node.type_name in self.structs:
            struct = self.structs[node.type_name]
            node.struct_fields = struct.fields
            probed_init = None
            if (len(node.initializers) == 1
                    and not isinstance(node.initializers[0], NestedInitNode)):
                probed_init = node.initializers[0]
                probed_init.accept(self)
                if probed_init.type == node.type_name:
                    node.struct_copy = True
            if not node.struct_copy:
                if len(node.initializers) != len(struct.fields):
                    self.fail_init(node, f"struct '{node.type_name}' needs {len(struct.fields)} "
                                         f"value(s), got {len(node.initializers)}")
                for field, init in zip(struct.fields, node.initializers):
                    self.check_field_init(init, field.type_name, field.name,
                                          already_checked=(init is probed_init))
        else:
            if len(node.initializers) != 1:
                self.fail_init(node, f"type '{node.type_name}' needs exactly one value")
            if isinstance(node.init, NestedInitNode):
                self.fail(node.init, f"nested '{{}}' requires a struct field, got {node.type_name}")
            node.init.accept(self)
            self.check_assignable(node.init, node.type_name, node,
                                  f"initialise '{node.name}'")
        self.symbols[node.name] = node

    def visit_assign(self, node):
        if node.target is not None:
            node.type_name = node.target.accept(self)
            links = []
            cursor = node.target
            while isinstance(cursor, FieldAccessNode):
                links.append(cursor)
                cursor = cursor.base
            if not isinstance(cursor, VarNode) or not cursor.decl.mutable:
                self.fail(node, f"cannot assign to '{node.name}': it is not mut")
            for link in reversed(links):
                if not link.field.mutable:
                    self.fail(link, f"cannot assign through field '{link.name}': it is not mut")
            if not self.fully_mutable(node.type_name):
                self.fail(node, "cannot replace struct field: some struct fields are not mut")
        else:
            if node.name not in self.symbols:
                self.fail(node, f"variable '{node.name}' is used before its declaration")
            node.decl = self.symbols[node.name]
            node.type_name = node.decl.type_name
            if not node.decl.mutable:
                self.fail(node, f"cannot assign to '{node.name}': it is not mut")
            if not self.fully_mutable(node.type_name):
                self.fail(node, f"cannot replace '{node.name}': some struct fields are not mut")
        node.value.accept(self)
        self.check_assignable(node.value, node.type_name, node,
                              f"assign to '{node.name}'")

    def visit_exit(self, node):
        node.value.accept(self)
        if self.current_function is not None:
            self.check_assignable(node.value, self.current_function.result_type,
                                  node, f"return from '{self.current_function.name}'")
        elif node.value.type not in ("i32", "i64", "bool"):
            self.fail(node, "top-level exit requires i32, i64 or bool")
        return node.value.type

    def visit_fieldaccess(self, node):
        base_type = node.base.accept(self)
        if base_type not in self.structs:
            self.fail(node, f"cannot access field '{node.name}' on type {base_type}")
        field = self.structs[base_type].field_map.get(node.name)
        if field is None:
            self.fail(node, f"unknown field '{node.name}' in struct '{base_type}'")
        node.field = field
        node.type = field.type_name
        return node.type

    def visit_call(self, node):
        if node.name not in self.functions:
            self.fail(node, f"unknown function '{node.name}'")
        node.function = self.functions[node.name]
        params = node.function.params
        if len(node.args) != len(params):
            self.fail(node, f"function '{node.name}' expects {len(params)} argument(s), "
                            f"got {len(node.args)}")
        for param, arg in zip(params, node.args):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg,
                                  f"pass argument '{param.name}'")
        node.type = node.function.result_type
        return node.type

    def visit_not(self, node):
        operand_type = node.operand.accept(self)
        if operand_type != "bool":
            self.fail(node, "operator '!' requires bool")
        node.type = "bool"
        return node.type

    def visit_binop(self, node):
        lt = node.left.accept(self)
        rt = node.right.accept(self)

        if node.op in (
            "+",
            "-",
            "*"
        ):
            if not self.is_int(lt):
                self.fail(
                    node,
                    f"cannot apply "
                    f"'{node.op}' "
                    f"to {lt}"
                )

            if not self.is_int(rt):
                self.fail(
                    node,
                    f"cannot apply "
                    f"'{node.op}' "
                    f"to {rt}"
                )

            node.type = self.wider(
                lt,
                rt
            )

        else:
            both_int = (
                self.is_int(lt)
                and self.is_int(rt)
            )

            both_bool = (
                lt == "bool"
                and rt == "bool"
            )

            if not (
                both_int
                or both_bool
            ):
                self.fail(
                    node,
                    f"cannot compare "
                    f"{lt} with {rt}"
                )

            node.type = "bool"

        return node.type

    def visit_var(self, node):
        if node.name not in self.symbols:
            self.fail(
                node,
                f"variable '{node.name}' "
                "is used before its declaration"
            )

        node.decl = self.symbols[node.name]
        node.type = node.decl.type_name

        return node.type

    def visit_const(self, node):
        if node.value <= INT32_MAX:
            node.type = "i32"

        elif node.value <= INT64_MAX:
            node.type = "i64"

        else:
            self.fail(
                node,
                f"constant {node.value} "
                "does not fit in i64"
            )

        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type

    def check_assignable(
        self,
        expr,
        want,
        at,
        what
    ):
        have = expr.type

        if (
            isinstance(expr, ConstNode)
            and want in ("i32", "i64")
        ):
            limit = (
                INT32_MAX
                if want == "i32"
                else INT64_MAX
            )

            if expr.value > limit:
                self.fail(
                    expr,
                    f"constant {expr.value} "
                    f"does not fit in {want}"
                )

        if have == want:
            return

        if (
            have == "i32"
            and want == "i64"
        ):
            return

        self.fail(
            at,
            f"cannot {what} "
            f"of type {want} "
            f"with a value "
            f"of type {have}"
        )


class CodeGen:
    def __init__(self):
        self.I1 = ir.IntType(1)
        self.I8 = ir.IntType(8)
        self.I32 = ir.IntType(32)
        self.I64 = ir.IntType(64)

        self.module = ir.Module(
            name="practice6_task3",
            context=ir.Context()
        )

        self.struct_types = {}
        self.current_function = None

        self.module.triple = (
            llvm.get_default_triple()
        )

        main = ir.Function(
            self.module,
            ir.FunctionType(
                self.I32,
                []
            ),
            name="main"
        )

        self.main_function = main
        self.builder = ir.IRBuilder(
            main.append_basic_block(
                "entry"
            )
        )

        i8ptr = ir.PointerType(self.I8)

        self.printf = ir.Function(
            self.module,
            ir.FunctionType(
                self.I32,
                [i8ptr],
                var_arg=True
            ),
            name="printf"
        )

        self.fmt_int = self.make_string(
            "fmt_int",
            b"Program exit with result %lld\n\0"
        )

        self.fmt_str = self.make_string(
            "fmt_str",
            b"Program exit with result %s\n\0"
        )

        self.str_true = self.make_string(
            "str_true",
            b"true\0"
        )

        self.str_false = self.make_string(
            "str_false",
            b"false\0"
        )

    def make_string(self, name, data):
        arr = ir.ArrayType(
            self.I8,
            len(data)
        )

        g = ir.GlobalVariable(
            self.module,
            arr,
            name=name
        )

        g.linkage = "private"
        g.global_constant = True

        g.initializer = ir.Constant(
            arr,
            bytearray(data)
        )

        return g

    def llvm_type(self, type_name):
        builtins = {
            "i32": self.I32,
            "i64": self.I64,
            "bool": self.I1
        }
        if type_name in builtins:
            return builtins[type_name]
        return self.struct_types[type_name]

    def as_i8ptr(self, global_string):
        return self.builder.bitcast(
            global_string,
            ir.PointerType(self.I8)
        )

    def coerce(
        self,
        value,
        have,
        want
    ):
        if (
            have == "i32"
            and want == "i64"
        ):
            return self.builder.sext(
                value,
                self.I64,
                name="wide"
            )

        return value

    def visit_program(self, node):
        for struct in node.structs:
            self.struct_types[struct.name] = self.module.context.get_identified_type(
                struct.name)
        for struct in node.structs:
            struct.accept(self)

        for function in node.functions:
            signature = ir.FunctionType(self.llvm_type(function.result_type),
                [self.llvm_type(param.type_name) for param in function.params])
            function.ir_function = ir.Function(
                self.module, signature, name="user_" + function.name)
        for function in node.functions:
            function.accept(self)

        for statement in node.statements:
            statement.accept(self)
        node.exit_node.accept(self)
        return self.module

    def visit_function(self, node):
        saved_builder, saved_function = self.builder, self.current_function
        self.current_function = node
        self.builder = ir.IRBuilder(node.ir_function.append_basic_block("entry"))
        for parameter, value in zip(node.params, node.ir_function.args):
            value.name = parameter.name
            parameter.ir_ptr = self.builder.alloca(self.llvm_type(parameter.type_name),
                                                   name=parameter.name + ".slot")
            self.builder.store(value, parameter.ir_ptr)
        for statement in node.body:
            statement.accept(self)
        self.builder, self.current_function = saved_builder, saved_function

    def visit_if(self, node):
        condition = node.condition.accept(self)
        function = self.builder.function
        then_block = function.append_basic_block("if.then")
        end_block = function.append_basic_block("if.end")
        self.builder.cbranch(condition, then_block, end_block)
        self.builder.position_at_end(then_block)
        for statement in node.body:
            statement.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(end_block)
        self.builder.position_at_end(end_block)

    def visit_structdecl(self, node):
        self.struct_types[node.name].set_body(
            *(self.llvm_type(field.type_name) for field in node.fields))

    def visit_decl(self, node):
        node.ir_ptr = self.builder.alloca(
            self.llvm_type(node.type_name),
            name=node.name
        )

        if node.struct_fields is not None and not node.struct_copy:
            for field, init in zip(node.struct_fields, node.initializers):
                field_ptr = self.builder.gep(
                    node.ir_ptr,
                    [ir.Constant(self.I32, 0), ir.Constant(self.I32, field.index)],
                    inbounds=True,
                    name=f"{node.name}.{field.index}.ptr"
                )
                value = init.accept(self)
                value = self.coerce(value, init.type, field.type_name)
                self.builder.store(value, field_ptr)
        else:
            init = node.initializers[0]
            value = init.accept(self)
            value = self.coerce(value, init.type, node.type_name)
            self.builder.store(value, node.ir_ptr)

    def field_ptr(self, node):
        """Produce an address using only indexes resolved in SemanticChecker."""
        if isinstance(node, VarNode):
            return node.decl.ir_ptr
        if isinstance(node, FieldAccessNode):
            parent = self.field_ptr(node.base)
            return self.builder.gep(
                parent,
                [ir.Constant(self.I32, 0), ir.Constant(self.I32, node.field.index)],
                inbounds=True, name=f"field.{node.field.index}.ptr")
        value = node.accept(self)
        tmp = self.builder.alloca(self.llvm_type(node.type), name="field.tmp")
        self.builder.store(value, tmp)
        return tmp

    def visit_fieldaccess(self, node):
        return self.builder.load(self.field_ptr(node), name="field.value")

    def visit_call(self, node):
        args = []
        for actual, formal in zip(node.args, node.function.params):
            value = actual.accept(self)
            args.append(self.coerce(value, actual.type, formal.type_name))
        return self.builder.call(node.function.ir_function, args,
                                 name="call." + node.name)

    def visit_not(self, node):
        value = node.operand.accept(self)
        return self.builder.not_(value, name="bool.not")

    def visit_nestedinit(self, node):
        """Build a by-value nested struct, field by field, with LLVM IRBuilder.

        The semantic checker has already attached the expected struct type
        and each resolved field index.  A child may itself be a nested brace
        value (recursion), an ordinary expression, or a function call.
        """
        aggregate = ir.Constant(self.llvm_type(node.type), ir.Undefined)
        for field, init in zip(node.struct_fields, node.initializers):
            value = init.accept(self)
            value = self.coerce(value, init.type, field.type_name)
            aggregate = self.builder.insert_value(aggregate, value, field.index,
                                                  name="struct.insert")
        return aggregate

    def visit_assign(self, node):
        value = node.value.accept(self)
        value = self.coerce(value, node.value.type, node.type_name)
        ptr = self.field_ptr(node.target) if node.target is not None else node.decl.ir_ptr
        self.builder.store(value, ptr)

    def visit_exit(self, node):
        value = node.value.accept(self)
        if self.current_function is not None:
            value = self.coerce(value, node.value.type,
                                self.current_function.result_type)
            self.builder.ret(value)
            return

        if node.value.type in ("i32", "i64"):
            value = self.coerce(value, node.value.type, "i64")
            self.builder.call(self.printf, [self.as_i8ptr(self.fmt_int), value])
        else:
            true_ptr = self.as_i8ptr(self.str_true)
            false_ptr = self.as_i8ptr(self.str_false)
            text_ptr = self.builder.select(value, true_ptr, false_ptr,
                                           name="booltext")
            self.builder.call(self.printf, [self.as_i8ptr(self.fmt_str), text_ptr])
        self.builder.ret(ir.Constant(self.I32, 0))

    def visit_binop(self, node):
        left = node.left.accept(self)
        right = node.right.accept(self)

        if node.op in (
            "+",
            "-",
            "*"
        ):
            want = (
                "i64"
                if "i64" in (
                    node.left.type,
                    node.right.type
                )
                else "i32"
            )

            left = self.coerce(
                left,
                node.left.type,
                want
            )

            right = self.coerce(
                right,
                node.right.type,
                want
            )

            if node.op == "+":
                return self.builder.add(
                    left,
                    right
                )

            if node.op == "-":
                return self.builder.sub(
                    left,
                    right
                )

            return self.builder.mul(
                left,
                right
            )

        if node.left.type in (
            "i32",
            "i64"
        ):
            want = (
                "i64"
                if "i64" in (
                    node.left.type,
                    node.right.type
                )
                else "i32"
            )

            left = self.coerce(
                left,
                node.left.type,
                want
            )

            right = self.coerce(
                right,
                node.right.type,
                want
            )

        return self.builder.icmp_signed(
            node.op,
            left,
            right
        )

    def visit_var(self, node):
        return self.builder.load(
            node.decl.ir_ptr,
            name=node.name + ".value"
        )

    def visit_const(self, node):
        return ir.Constant(
            self.llvm_type(node.type),
            node.value
        )

    def visit_bool(self, node):
        return ir.Constant(
            self.I1,
            int(node.value)
        )


def main():
    if len(sys.argv) != 3:
        print(
            "usage:\n"
            "  python3 compiler.py "
            "input.txt output.ll\n"
            "  python3 compiler.py "
            "--ast input.txt",
            file=sys.stderr
        )

        return 1

    ast_mode = (
        sys.argv[1] == "--ast"
    )

    if ast_mode:
        input_path = sys.argv[2]
        output_path = None
    else:
        input_path = sys.argv[1]
        output_path = sys.argv[2]

    if (
        output_path
        and os.path.exists(output_path)
    ):
        os.remove(output_path)

    try:
        with open(
            input_path,
            "rb"
        ) as f:
            tree = Parser(
                lex(f.read())
            ).parse_program()

        tree.accept(
            SemanticChecker()
        )

        if ast_mode:
            tree.dump()

        else:
            module = tree.accept(
                CodeGen()
            )

            with open(
                output_path,
                "w"
            ) as f:
                f.write(str(module))

        return 0

    except CompileError as e:
        print(
            f"compilation error: {e}",
            file=sys.stderr
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
