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
    b"mut", b"exit", b"struct", b"fn", b"if", b"else", b"while",
    b"true", b"false", b"read", b"print"
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

            if b == ord('"'):
                state, start, sl, sc = "STRING", i + 1, line, col
                i += 1
                col += 1
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

        elif state == "STRING":
            if b == ord('"'):

                toks.append(Token("string", data[start:i], sl, sc))
                i += 1
                col += 1
                state = "START"
            elif b is None or b == 10:
                raise CompileError(f"line {sl}:{sc}: unterminated string literal")
            else:
                i += 1
                col += 1

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

    def dump(self, depth=0):
        print("  " * depth + self.label())
        for child in self.children():
            child.dump(depth + 1)

    def dump_tree(self, prefix="", last=True, root=True):
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
    def __init__(self, line, col, name, fields, methods=None):
        self.line, self.col = line, col
        self.name = name
        self.fields = fields
        self.methods = methods if methods is not None else []
        self.field_map = None
        self.method_map = {}

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields + self.methods


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


class StringNode(ASTNode):


    def __init__(self, line, col, data):
        self.line, self.col, self.data = line, col, data
        self.type = "string"

    def label(self):
        return f"String {self.data!r}"


class PrintNode(ASTNode):
    def __init__(self, line, col, argument):
        self.line, self.col, self.argument = line, col, argument

    def label(self):
        return "Print"

    def children(self):
        return [self.argument]


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
        self.owner = None
        self.mutating = False

    def label(self):
        return f"Function {self.name} -> {self.result_type}"

    def children(self):
        return self.params + self.body


class MemberFnNode(FunctionNode):


    def label(self):
        return f"MemberFn {self.name} -> {self.result_type}"

    def children(self):


        return self.params + [BlockNode(self.line, self.col, self.body)]


class MemberCallNode(ASTNode):


    def __init__(self, line, col, name, receiver, args, dot_line=None, dot_col=None):
        self.line, self.col = line, col
        self.dot_line = line if dot_line is None else dot_line
        self.dot_col = col if dot_col is None else dot_col
        self.name, self.receiver, self.args = name, receiver, args
        self.function = None
        self.type = None

    def label(self):
        return f"MemberCall {self.name}"

    def children(self):
        return [self.receiver] + self.args


class BlockNode(ASTNode):
    def __init__(self, line, col, statements):
        self.line, self.col = line, col
        self.statements = statements

    def label(self):
        return "Block"

    def children(self):
        return self.statements


class IfNode(ASTNode):
    def __init__(self, line, col, condition, body, else_block=None):
        self.line, self.col = line, col
        self.condition = condition
        self.body = body
        self.else_block = else_block

    def label(self):
        return "If"

    def children(self):
        children = [self.condition, self.body]
        if self.else_block is not None:
            children.append(self.else_block)
        return children


class WhileNode(ASTNode):
    def __init__(self, line, col, condition, body):
        self.line, self.col = line, col
        self.condition = condition
        self.body = body

    def label(self):
        return "While"

    def children(self):
        return [self.condition, self.body]


class FieldAccessNode(ASTNode):
    def __init__(self, line, col, base, name, dot_line=None, dot_col=None):
        self.line, self.col = line, col
        self.dot_line = line if dot_line is None else dot_line
        self.dot_col = col if dot_col is None else dot_col
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
        self.implicit_member = False

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
        return "Not"

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


        self.known_struct_names = set()
        self.value_scopes = [set()]

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
            if text == "}" and self.peek() is None:
                self.fail("expected '}', found end of line")
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
        self.toks, self.pos = self.lines[index], 0
        self.expect_text("struct")
        name = self.expect_kind("identifier", "a struct name")
        self.known_struct_names.add(name.text)
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
        fields, methods = [], []
        field_names, method_names = set(), set()
        while index < len(self.lines):
            line = self.lines[index]
            if not line:
                index += 1
                continue
            self.toks, self.pos = line, 0
            if self.peek().text == "}":
                self.eat()
                if self.peek():
                    self.fail("unexpected token after struct '}'")
                return StructDeclNode(name.line, name.col, name.text,
                                      fields, methods), index + 1

            if self.peek().text == "fn":
                if not fields:
                    self.fail("member function must follow at least one field")
                method, index = self.parse_function(index, member=True,
                                                    implicit_names=field_names)
                if method.name in field_names or method.name in method_names:
                    raise CompileError(f"line {method.line}:{method.col}: "
                                       f"duplicate field or member function '{method.name}'")
                method_names.add(method.name)
                methods.append(method)
                continue

            if methods:
                self.fail("field declaration cannot follow a member function")
            type_tok = self.expect_type()
            mutable = bool(self.peek() and self.peek().text == "mut")
            if mutable:
                self.eat()
            field = self.expect_kind("identifier", "a field name")
            if self.peek():
                self.fail("unexpected token after field declaration")
            fields.append(FieldDeclNode(field.line, field.col, field.text,
                                        type_tok.text, mutable))
            field_names.add(field.text)
            index += 1

        raise CompileError(f"line {name.line}:{name.col}: unclosed struct '{name.text}'")

    def parse_block(self, index, keyword=None):
        while index < len(self.lines) and not self.lines[index]:
            index += 1
        if index >= len(self.lines):
            if keyword is not None:
                raise CompileError(
                    f"line {keyword.line}:{keyword.col}: expected '{{' on its own line "
                    f"after '{keyword.text}', found end of input")
            raise CompileError("line 1:1: expected '{' for block")
        self.toks, self.pos = self.lines[index], 0
        if self.peek().text != "{":
            if keyword is not None:
                self.fail(f"expected '{{' on its own line after '{keyword.text}', "
                          f"got '{self.peek().text}'")
            self.fail("expected '{'")
        opening = self.eat()
        if self.peek():
            self.fail("'{' must be on its own line")
        index += 1
        body = []
        has_exit = False
        self.value_scopes.append(set())
        while index < len(self.lines):
            line = self.lines[index]
            if not line:
                index += 1
                continue
            self.toks, self.pos = line, 0
            if line[0].text == "}":
                self.eat()
                if self.peek():
                    self.fail("'}' must be on its own line")
                if not body:
                    raise CompileError(f"line {opening.line}:{opening.col}: empty block")
                self.value_scopes.pop()
                return BlockNode(opening.line, opening.col, body), index + 1
            if has_exit:
                self.fail("statement after 'exit' in the same block")
            if line[0].text in ("struct", "fn"):
                self.fail("structs and functions are only allowed at top level")
            if line[0].text == "else":
                self.fail("'else' without an 'if'")
            if line[0].text in ("if", "while"):
                if line[0].text == "if":
                    stmt, index = self.parse_if(index)
                else:
                    stmt, index = self.parse_while(index)
            else:
                stmt = self.parse_statement()
                if self.peek():
                    self.fail(f"unexpected '{self.peek().text}' after the statement")
                index += 1
            body.append(stmt)
            has_exit = isinstance(stmt, ExitNode)
        raise CompileError(f"line {opening.line}:{opening.col}: '{{' is never closed")

    def parse_if(self, index):
        self.toks, self.pos = self.lines[index], 0
        token = self.expect_text("if")
        condition = self.parse_expr()
        if self.peek():
            self.fail(f"unexpected '{self.peek().text}' after the statement")
        body, next_index = self.parse_block(index + 1, token)
        look = next_index
        while look < len(self.lines) and not self.lines[look]:
            look += 1
        else_block = None
        if look < len(self.lines) and self.lines[look][0].text == "else":
            self.toks, self.pos = self.lines[look], 0
            else_token = self.eat()
            if self.peek():
                self.fail("unexpected token after 'else'")
            else_block, next_index = self.parse_block(look + 1, else_token)
        return IfNode(token.line, token.col, condition, body, else_block), next_index

    def parse_while(self, index):
        self.toks, self.pos = self.lines[index], 0
        token = self.expect_text("while")
        condition = self.parse_expr()
        if self.peek():
            self.fail("unexpected token after while condition")
        body, next_index = self.parse_block(index + 1, token)
        return WhileNode(token.line, token.col, condition, body), next_index

    def parse_function(self, index, member=False, implicit_names=()):
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
        self.value_scopes.append(set(implicit_names) | {p.name for p in params})
        body, next_index = self.parse_block(index + 1)
        self.value_scopes.pop()
        if not body.statements or not isinstance(body.statements[-1], ExitNode):
            raise CompileError(
                f"line {name.line}:{name.col}: function '{name.text}' must end with exit")
        node_class = MemberFnNode if member else FunctionNode
        return node_class(name.line, name.col, name.text, params,
                          result.text, body.statements), next_index

    def parse_print(self):
        token = self.expect_text("print")
        self.expect_text("(")
        if not self.peek() or self.peek().text == ")":
            self.fail("print expects exactly one argument")
        if self.peek().kind == "string":
            string = self.eat()
            argument = StringNode(string.line, string.col, string.text)
        else:
            argument = self.parse_expr()
        if self.peek() and self.peek().text == ",":
            self.fail("print expects exactly one argument")
        self.expect_text(")")
        return PrintNode(token.line, token.col, argument)

    def parse_statement(self):
        first = self.peek()
        if first.text == "print":
            return self.parse_print()
        if first.text in ("i32", "i64", "bool"):
            decl = self.parse_decl()
            self.value_scopes[-1].add(decl.name)
            return decl
        if first.kind == "identifier":
            following = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
            if following and (following.kind == "identifier" or following.text == "mut"):
                decl = self.parse_decl()
                self.value_scopes[-1].add(decl.name)
                return decl
            return self.parse_assign()
        if first.text == "exit":
            return self.parse_exit()
        if first.text == "else":
            self.fail("'else' without an 'if'")
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
            elif first.text == "while":
                stmt, index = self.parse_while(index)
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


        self.pos -= 1
        target = self.parse_factor()
        cursor = target
        while isinstance(cursor, FieldAccessNode):
            cursor = cursor.base
        if isinstance(cursor, (CallNode, MemberCallNode)):
            if isinstance(target, (CallNode, MemberCallNode)) and (
                    not self.peek() or self.peek().text != ":="):
                col = (target.col + len(target.name) if isinstance(target, MemberCallNode)
                       else name.col + len(name.text))
                raise CompileError(f"line {name.line}:{col}: "
                                   f"expected ':=' after '{name.text}', got '('")
            self.fail("cannot assign to a function result or a field of a temporary")
        if not isinstance(target, (VarNode, FieldAccessNode)):
            self.fail("a call cannot be used as a statement or assignment target")
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

    def parse_call_args(self):
        self.expect_text("(")
        args = []
        if self.peek() and self.peek().text != ")":
            args.append(self.parse_expr())
            while self.peek() and self.peek().text == ",":
                self.eat()
                args.append(self.parse_expr())
        self.expect_text(")")
        return args

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
        if t.kind == "string":
            raise CompileError(f"line {t.line}:{t.col}: string literal allowed only in print")
        if t.text == "print":
            raise CompileError(f"line {t.line}:{t.col}: print is a statement, not a value")
        if t.text == "read":
            self.eat()
            if not self.peek() or self.peek().text != "(":
                raise CompileError(f"line {t.line}:{t.col}: read must be called as read()")
            args = self.parse_call_args()
            if args:
                raise CompileError(f"line {t.line}:{t.col}: read() takes no arguments")
            node = CallNode(t.line, t.col, t.text, [])
        elif t.kind == "identifier":
            self.eat()
            if self.peek() and self.peek().text == "(":
                node = CallNode(t.line, t.col, t.text, self.parse_call_args())
            else:
                node = VarNode(t.line, t.col, t.text)
        else:
            self.fail(f"expected a constant or a variable, got '{t.text}'")




        while self.peek() and self.peek().text == ".":
            dot = self.eat()
            field = self.expect_kind("identifier", "a field or member function name")
            if self.peek() and self.peek().text == "(":
                root = self._field_root(node)
                if (root is not None and root.name in self.known_struct_names
                        and not any(root.name in scope for scope in self.value_scopes)):
                    raise CompileError(f"line {root.line}:{root.col}: "
                                       f"cannot call a member function on type '{root.name}'")
                node = MemberCallNode(field.line, field.col, field.text,
                                      node, self.parse_call_args(), dot.line, dot.col)
            else:
                node = FieldAccessNode(field.line, field.col, node, field.text,
                                       dot.line, dot.col)
        return node

    @staticmethod
    def _field_root(node):
        while isinstance(node, FieldAccessNode):
            node = node.base
        return node if isinstance(node, VarNode) else None


class SemanticChecker:
    def __init__(self):
        self.symbols = {}
        self.scopes = [self.symbols]
        self.structs = {}
        self.functions = {}
        self.current_function = None
        self.current_struct = None
        self.current_member = None
        self.member_calls = []
        self.member_edges = {}

    def fail_dot(self, node, message):
        raise CompileError(f"line {node.dot_line}:{node.dot_col}: {message}")

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

    def validate_signature(self, fn):
        if not self.known_type(fn.result_type):
            self.fail(fn, f"unknown result type '{fn.result_type}'")
        seen = set()
        for param in fn.params:
            if not self.known_type(param.type_name):
                self.fail(param, f"unknown parameter type '{param.type_name}'")
            if param.name in seen:
                self.fail(param, f"parameter '{param.name}' is already declared")
            seen.add(param.name)

    def visit_program(self, node):



        for struct in node.structs:
            struct.accept(self)

        for struct in node.structs:
            for method in struct.methods:
                self.validate_signature(method)
                method.owner = struct
                struct.method_map[method.name] = method
                self.member_edges[method] = set()

        for function in node.functions:
            if function.name in self.functions:
                self.fail(function, f"function '{function.name}' is already declared")
            self.validate_signature(function)
            self.functions[function.name] = function

        for struct in node.structs:
            for method in struct.methods:
                method.accept(self)
        for function in node.functions:
            function.accept(self)



        changed = True
        while changed:
            changed = False
            for method, callees in self.member_edges.items():
                if not method.mutating and any(callee.mutating for callee in callees):
                    method.mutating = True
                    changed = True


        for call in self.member_calls:
            if call.function.mutating:
                self.check_mutating_receiver(call)

        self.symbols = {}
        self.scopes = [self.symbols]
        for statement in node.statements:
            statement.accept(self)
        node.exit_node.accept(self)


        for call in self.member_calls:
            if call.function.mutating and not getattr(call, "mutation_checked", False):
                self.check_mutating_receiver(call)

    def visit_function(self, node):
        old_state = (self.scopes, self.symbols, self.current_function,
                     self.current_struct, self.current_member)
        self.symbols = {}
        self.scopes = [self.symbols]
        self.current_function = node
        self.current_member = node if isinstance(node, MemberFnNode) else None
        self.current_struct = node.owner if self.current_member else None
        try:
            for param in node.params:
                self.symbols[param.name] = param
            for statement in node.body:
                statement.accept(self)
        finally:
            (self.scopes, self.symbols, self.current_function,
             self.current_struct, self.current_member) = old_state

    def visit_memberfn(self, node):
        return self.visit_function(node)

    @staticmethod
    def receiver_root(node):
        while isinstance(node, FieldAccessNode):
            node = node.base
        return node

    def receiver_from_this(self, receiver):
        root = self.receiver_root(receiver)
        return (self.current_member is not None
                and isinstance(root, VarNode)
                and isinstance(root.decl, FieldDeclNode))

    def check_mutating_receiver(self, node):


        if isinstance(node, CallNode) and node.implicit_member:
            node.mutation_checked = True
            return

        links = []
        cursor = node.receiver
        while isinstance(cursor, FieldAccessNode):
            links.append(cursor)
            cursor = cursor.base
        if isinstance(cursor, (CallNode, MemberCallNode)):



            pass
        elif isinstance(cursor, VarNode):
            if isinstance(cursor.decl, FieldDeclNode) and not cursor.decl.mutable:
                self.fail(node, f"mutating member call '{node.name}' through const field '{cursor.name}'")
            if not isinstance(cursor.decl, FieldDeclNode) and not cursor.decl.mutable:
                self.fail(node, f"mutating member call '{node.name}' on const object '{cursor.name}'")
        else:
            self.fail(node, "a mutating member call requires an object or struct temporary")
        for link in reversed(links):
            if not link.field.mutable:
                self.fail(link, f"mutating member call '{node.name}' through const field '{link.name}'")
        node.mutation_checked = True

    def visit_if(self, node):
        cond_type = node.condition.accept(self)
        if cond_type != "bool":
            if self.current_function is not None:
                self.fail(node.condition, "if condition must have type bool")
            self.fail(node, f"the condition of 'if' must be bool, got {cond_type}")
        node.body.accept(self)
        if node.else_block is not None:
            node.else_block.accept(self)

    def lookup(self, node, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        if self.current_struct is not None and name in self.current_struct.field_map:
            return self.current_struct.field_map[name]
        self.fail(node, f"variable '{name}' is used before its declaration")

    def visit_block(self, node):
        self.scopes.append({})
        self.symbols = self.scopes[-1]
        try:
            for stmt in node.statements:
                stmt.accept(self)
        finally:
            self.scopes.pop()
            self.symbols = self.scopes[-1]

    def visit_while(self, node):
        cond_type = node.condition.accept(self)
        if cond_type != "bool":
            self.fail(node, f"the condition of 'while' must be bool, got {cond_type}")
        node.body.accept(self)

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
        method_names = set()
        for method in node.methods:
            if method.name in names or method.name in method_names:
                self.fail(method, f"duplicate field or member function '{method.name}'")
            method_names.add(method.name)
        self.structs[node.name] = node

    def fully_mutable(self, type_name):
        if type_name not in self.structs:
            return True
        return all(field.mutable and self.fully_mutable(field.type_name)
                   for field in self.structs[type_name].fields)

    def fail_init(self, node, message):
        raise CompileError(f"line {node.brace_line}:{node.brace_col}: {message}")

    def check_field_init(self, init, type_name, name, already_checked=False):
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
            self.fail(node, f"variable '{node.name}' is already declared in this block")
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


            if self.receiver_from_this(node.target):
                self.current_member.mutating = True
        else:
            node.decl = self.lookup(node, node.name)
            node.type_name = node.decl.type_name
            if not node.decl.mutable:
                self.fail(node, f"cannot assign to '{node.name}': it is not mut")
            if not self.fully_mutable(node.type_name):
                self.fail(node, f"cannot replace '{node.name}': some struct fields are not mut")
            if self.current_member is not None and isinstance(node.decl, FieldDeclNode):
                self.current_member.mutating = True
        node.value.accept(self)
        self.check_assignable(node.value, node.type_name, node,
                              f"assign to '{node.name}'")

    def visit_print(self, node):

        if isinstance(node.argument, StringNode):
            return
        argument_type = node.argument.accept(self)
        if argument_type not in ("i32", "i64", "bool") and argument_type not in self.structs:
            self.fail(node.argument, f"cannot print value of type '{argument_type}'")

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
            if isinstance(node.base, (CallNode, MemberCallNode)):
                self.fail_dot(node, f"cannot access field '{node.name}' on type {base_type}")
            self.fail(node, f"cannot access field '{node.name}' on type {base_type}")
        field = self.structs[base_type].field_map.get(node.name)
        if field is None:
            self.fail(node, f"unknown field '{node.name}' in struct '{base_type}'")
        node.field = field
        node.type = field.type_name
        return node.type

    def check_call_arguments(self, node, function):
        params = function.params
        if len(node.args) != len(params):
            self.fail(node, f"function '{node.name}' expects {len(params)} argument(s), "
                            f"got {len(node.args)}")
        for param, arg in zip(params, node.args):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg,
                                  f"pass argument '{param.name}'")
        node.type = function.result_type
        return node.type

    def visit_call(self, node):
        if node.name == "read":
            if node.args:
                self.fail(node, "read() takes no arguments")
            node.type = "i32"
            return node.type
        method = (self.current_struct.method_map.get(node.name)
                  if self.current_struct is not None else None)
        if method is not None:
            node.function = method
            node.implicit_member = True
            if self.current_member is not None:
                self.member_edges[self.current_member].add(method)
            self.member_calls.append(node)
        else:
            if node.name not in self.functions:
                self.fail(node, f"unknown function '{node.name}'")
            node.function = self.functions[node.name]
        return self.check_call_arguments(node, node.function)

    def visit_membercall(self, node):
        receiver_type = node.receiver.accept(self)
        if receiver_type not in self.structs:
            if isinstance(node.receiver, (CallNode, MemberCallNode)):
                self.fail_dot(node, f"cannot call member function '{node.name}' on type '{receiver_type}'")
            self.fail(node, f"cannot call member function '{node.name}' on type '{receiver_type}'")
        function = self.structs[receiver_type].method_map.get(node.name)
        if function is None:
            self.fail(node, f"unknown member function '{node.name}' in struct '{receiver_type}'")
        node.function = function
        if self.receiver_from_this(node.receiver):
            self.member_edges[self.current_member].add(function)
        self.member_calls.append(node)
        return self.check_call_arguments(node, function)

    def visit_not(self, node):
        operand_type = node.operand.accept(self)
        if operand_type != "bool":
            self.fail(node, f"cannot apply '!' to {operand_type}")
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
        node.decl = self.lookup(node, node.name)
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
        self.struct_defs = {}
        self.string_count = 0
        self.current_function = None
        self.this_ptr = None

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
        self.scanf = ir.Function(
            self.module,
            ir.FunctionType(self.I32, [i8ptr], var_arg=True),
            name="scanf"
        )
        self.fmt_scan_i32 = self.make_string("fmt_scan_i32", b"%d\0")
        self.fmt_print_i32 = self.make_string("fmt_print_i32", b"%d\0")
        self.fmt_print_i64 = self.make_string("fmt_print_i64", b"%lld\0")
        self.fmt_print_str = self.make_string("fmt_print_str", b"%s\0")
        self.fmt_print_i32_line = self.make_string("fmt_print_i32_line", b"%d\n\0")
        self.fmt_print_i64_line = self.make_string("fmt_print_i64_line", b"%lld\n\0")
        self.fmt_print_str_line = self.make_string("fmt_print_str_line", b"%s\n\0")
        self.fmt_print_char = self.make_string("fmt_print_char", b"%c\0")
        self.str_newline = self.make_string("str_newline", b"\n\0")

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

    def entry_alloca(self, type_name, name):
        block = self.builder.function.entry_basic_block
        insert = ir.IRBuilder(block)
        insert.position_at_start(block)
        pointer = insert.alloca(self.llvm_type(type_name), name=name)
        if self.builder.block is block:
            self.builder.position_at_end(block)
        return pointer

    def visit_block(self, node):
        for statement in node.statements:
            if self.builder.block.is_terminated:
                break
            statement.accept(self)

    def visit_while(self, node):
        function = self.builder.function
        cond_block = function.append_basic_block("while.cond")
        body_block = function.append_basic_block("while.body")
        end_block = function.append_basic_block("while.end")
        self.builder.branch(cond_block)
        self.builder.position_at_end(cond_block)
        cond = node.condition.accept(self)
        self.builder.cbranch(cond, body_block, end_block)
        self.builder.position_at_end(body_block)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_block)
        self.builder.position_at_end(end_block)

    def visit_program(self, node):
        for struct in node.structs:
            self.struct_defs[struct.name] = struct
            self.struct_types[struct.name] = self.module.context.get_identified_type(
                struct.name)
        for struct in node.structs:
            struct.accept(self)



        for function in node.functions:
            signature = ir.FunctionType(self.llvm_type(function.result_type),
                [self.llvm_type(param.type_name) for param in function.params])
            function.ir_function = ir.Function(
                self.module, signature, name="user_" + function.name)
        for struct in node.structs:
            for method in struct.methods:
                signature = ir.FunctionType(self.llvm_type(method.result_type),
                    [self.struct_types[struct.name].as_pointer()] +
                    [self.llvm_type(param.type_name) for param in method.params])
                preferred = f"{struct.name}_{method.name}"
                name = preferred
                if name in self.module.globals:


                    name = (f"p7_member_{len(struct.name)}_{struct.name}_"
                            f"{len(method.name)}_{method.name}")
                    counter = 1
                    while name in self.module.globals:
                        counter += 1
                        name = (f"p7_member_{len(struct.name)}_{struct.name}_"
                                f"{len(method.name)}_{method.name}_{counter}")
                method.ir_function = ir.Function(self.module, signature, name=name)

        for struct in node.structs:
            for method in struct.methods:
                method.accept(self)
        for function in node.functions:
            function.accept(self)

        for statement in node.statements:
            statement.accept(self)
        node.exit_node.accept(self)
        return self.module

    def visit_function(self, node):
        saved_builder, saved_function, saved_this = (self.builder, self.current_function,
                                                     self.this_ptr)
        self.current_function = node
        self.this_ptr = None
        self.builder = ir.IRBuilder(node.ir_function.append_basic_block("entry"))
        try:
            for parameter, value in zip(node.params, node.ir_function.args):
                value.name = parameter.name
                parameter.ir_ptr = self.entry_alloca(parameter.type_name, parameter.name + ".slot")
                self.builder.store(value, parameter.ir_ptr)
            for statement in node.body:
                if self.builder.block.is_terminated:
                    break
                statement.accept(self)
        finally:
            self.builder, self.current_function, self.this_ptr = (saved_builder, saved_function,
                                                                 saved_this)

    def visit_memberfn(self, node):
        saved_builder, saved_function, saved_this = (self.builder, self.current_function,
                                                     self.this_ptr)
        self.current_function = node
        self.builder = ir.IRBuilder(node.ir_function.append_basic_block("entry"))
        self.this_ptr = node.ir_function.args[0]
        self.this_ptr.name = "this"
        try:
            for parameter, value in zip(node.params, node.ir_function.args[1:]):
                value.name = parameter.name
                parameter.ir_ptr = self.entry_alloca(parameter.type_name, parameter.name + ".slot")
                self.builder.store(value, parameter.ir_ptr)
            for statement in node.body:
                if self.builder.block.is_terminated:
                    break
                statement.accept(self)
        finally:
            self.builder, self.current_function, self.this_ptr = (saved_builder, saved_function,
                                                                 saved_this)

    def visit_if(self, node):
        condition = node.condition.accept(self)
        function = self.builder.function
        then_block = function.append_basic_block("if.then")
        else_block = (function.append_basic_block("if.else")
                      if node.else_block is not None else None)
        end_block = function.append_basic_block("if.end")
        self.builder.cbranch(condition, then_block, else_block or end_block)
        self.builder.position_at_end(then_block)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(end_block)
        if else_block is not None:
            self.builder.position_at_end(else_block)
            node.else_block.accept(self)
            if not self.builder.block.is_terminated:
                self.builder.branch(end_block)
        self.builder.position_at_end(end_block)

    def visit_structdecl(self, node):
        self.struct_types[node.name].set_body(
            *(self.llvm_type(field.type_name) for field in node.fields))

    def visit_decl(self, node):
        node.ir_ptr = self.entry_alloca(node.type_name, node.name)

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

    def field_decl_ptr(self, field):
        return self.builder.gep(
            self.this_ptr,
            [ir.Constant(self.I32, 0), ir.Constant(self.I32, field.index)],
            inbounds=True, name=f"this.{field.name}.ptr")

    def field_ptr(self, node):
        if isinstance(node, VarNode):
            if isinstance(node.decl, FieldDeclNode):
                return self.field_decl_ptr(node.decl)
            return node.decl.ir_ptr
        if isinstance(node, FieldAccessNode):
            parent = self.field_ptr(node.base)
            return self.builder.gep(
                parent,
                [ir.Constant(self.I32, 0), ir.Constant(self.I32, node.field.index)],
                inbounds=True, name=f"field.{node.field.index}.ptr")
        value = node.accept(self)
        tmp = self.entry_alloca(node.type, "field.tmp")
        self.builder.store(value, tmp)
        return tmp

    def visit_fieldaccess(self, node):



        root = node
        while isinstance(root, FieldAccessNode):
            root = root.base
        if isinstance(root, (CallNode, MemberCallNode)):
            aggregate = node.base.accept(self)
            return self.builder.extract_value(aggregate, node.field.index,
                                              name="field.temporary.value")
        return self.builder.load(self.field_ptr(node), name="field.value")

    def visit_call(self, node):
        if node.name == "read":
            slot = self.entry_alloca("i32", "read.slot")
            self.builder.call(self.scanf, [self.as_i8ptr(self.fmt_scan_i32), slot])
            return self.builder.load(slot, name="read.value")
        args = [self.this_ptr] if node.implicit_member else []
        for actual, formal in zip(node.args, node.function.params):
            value = actual.accept(self)
            args.append(self.coerce(value, actual.type, formal.type_name))
        return self.builder.call(node.function.ir_function, args,
                                 name="call." + node.name)

    def visit_membercall(self, node):
        address = self.field_ptr(node.receiver)
        args = [address]
        for actual, formal in zip(node.args, node.function.params):
            value = actual.accept(self)
            args.append(self.coerce(value, actual.type, formal.type_name))
        return self.builder.call(node.function.ir_function, args,
                                 name="call.member." + node.name)

    def visit_not(self, node):
        value = node.operand.accept(self)
        return self.builder.not_(value, name="bool.not")

    def visit_nestedinit(self, node):
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
        ptr = (self.field_ptr(node.target) if node.target is not None else
               self.field_decl_ptr(node.decl) if isinstance(node.decl, FieldDeclNode)
               else node.decl.ir_ptr)
        self.builder.store(value, ptr)

    def _printf_text(self, data):

        parts = data.split(b"\0")
        for index, part in enumerate(parts):
            if part:
                self.string_count += 1
                string = self.make_string(f"print_text_{self.string_count}", part + b"\0")
                self.builder.call(self.printf, [self.as_i8ptr(self.fmt_print_str),
                                                self.as_i8ptr(string)])
            if index < len(parts) - 1:

                self.builder.call(self.printf, [self.as_i8ptr(self.fmt_print_char),
                                                ir.Constant(self.I32, 0)])

    def _print_primitive(self, value, type_name, newline=False):
        if type_name == "i32":
            fmt = self.fmt_print_i32_line if newline else self.fmt_print_i32
            self.builder.call(self.printf, [self.as_i8ptr(fmt), value])
        elif type_name == "i64":
            fmt = self.fmt_print_i64_line if newline else self.fmt_print_i64
            self.builder.call(self.printf, [self.as_i8ptr(fmt), value])
        elif type_name == "bool":
            text = self.builder.select(value, self.as_i8ptr(self.str_true),
                                       self.as_i8ptr(self.str_false))
            fmt = self.fmt_print_str_line if newline else self.fmt_print_str
            self.builder.call(self.printf, [self.as_i8ptr(fmt), text])
        else:
            raise AssertionError(f"non-primitive printable: {type_name}")

    def _print_struct(self, address, type_name):
        struct = self.struct_defs[type_name]
        self._printf_text(type_name.encode('utf-8') + b'{')
        for index, field in enumerate(struct.fields):
            if index:
                self._printf_text(b', ')
            self._printf_text(field.name.encode('utf-8') + b': ')
            ptr = self.builder.gep(address, [ir.Constant(self.I32, 0),
                                             ir.Constant(self.I32, field.index)],
                                   inbounds=True, name='print.field.ptr')
            if field.type_name in self.struct_defs:
                self._print_struct(ptr, field.type_name)
            else:
                self._print_primitive(self.builder.load(ptr), field.type_name)
        self._printf_text(b'}')

    def visit_print(self, node):
        arg = node.argument
        if isinstance(arg, StringNode):
            if b"\0" not in arg.data:
                self.string_count += 1
                text = self.make_string(f"print_text_{self.string_count}", arg.data + b"\0")
                self.builder.call(self.printf, [self.as_i8ptr(self.fmt_print_str_line),
                                                self.as_i8ptr(text)])
            else:

                self._printf_text(arg.data)
                self.builder.call(self.printf, [self.as_i8ptr(self.str_newline)])
            return
        value = arg.accept(self)
        if arg.type in self.struct_defs:


            tmp = self.entry_alloca(arg.type, 'print.struct.tmp')
            self.builder.store(value, tmp)
            self._print_struct(tmp, arg.type)
            self.builder.call(self.printf, [self.as_i8ptr(self.str_newline)])
        else:
            self._print_primitive(value, arg.type, newline=True)

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
            self.field_ptr(node),
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


def parse_and_check(data):
    tree = Parser(lex(data)).parse_program()
    tree.accept(SemanticChecker())
    return tree


def dump_tokens(lines):
    for line in lines:
        for token in line:
            text = "\\n" if token.kind == "endline" else token.text
            print(f"{token.line}:{token.col} {token.kind} {text}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] in ("--tokens", "--ast"):
        mode, input_path, output_path = argv[0], argv[1], None
    elif len(argv) == 2 and not argv[0].startswith("--"):
        mode, input_path, output_path = "compile", argv[0], argv[1]
    else:
        print("usage: python3 compiler.py [--ast|--tokens] input.txt OR "
              "python3 compiler.py input.txt output.ll", file=sys.stderr)
        return 1
    if output_path is not None and os.path.abspath(input_path) == os.path.abspath(output_path):
        print("compilation error: input and output paths must be different", file=sys.stderr)
        return 1
    try:


        if output_path is not None and os.path.exists(output_path):
            os.remove(output_path)
        with open(input_path, "rb") as source:
            data = source.read()
        tokens = lex(data)
        if mode == "--tokens":
            dump_tokens(tokens)
            return 0
        tree = Parser(tokens).parse_program()
        if mode == "--ast":
            tree.dump()
            return 0
        tree.accept(SemanticChecker())
        module = tree.accept(CodeGen())
        with open(output_path, "w", encoding="utf-8") as result:
            result.write(str(module))
        return 0
    except CompileError as error:
        if output_path is not None and os.path.exists(output_path):
            os.remove(output_path)
        print(f"compilation error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"compiler I/O error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
