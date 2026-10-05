from llvmlite import ir
import llvmlite.binding as llvm
import os
import sys


class CompileError(Exception):
    pass


class Token:
    def __init__(self, kind, text, line, col):
        self.kind = kind
        self.text = text
        self.line = line
        self.col = col


KEYWORDS = {
    b"i32",
    b"i64",
    b"bool",
    b"mut",
    b"exit",
    b"true",
    b"false",
    b"if",
    b"else",
    b"while",
}

INT32_MAX = 2**31 - 1
INT64_MAX = 2**63 - 1


def is_alpha(byte):
    return 65 <= byte <= 90 or 97 <= byte <= 122 or byte == 95


def is_digit(byte):
    return 48 <= byte <= 57


def lex(data):
    lines = []
    toks = []
    state = "START"
    i = 0
    line = 1
    col = 1
    start = 0
    start_line = 1
    start_col = 1

    while i <= len(data):
        byte = data[i] if i < len(data) else None

        if state == "START":
            if byte is None:
                break

            if byte in (32, 9, 13):
                i += 1
                col += 1
                continue

            if byte == 10:
                toks.append(Token("endline", "\n", line, col))
                lines.append(toks)
                toks = []
                i += 1
                line += 1
                col = 1
                continue

            if is_alpha(byte):
                state = "IDENT"
                start = i
                start_line = line
                start_col = col
                i += 1
                col += 1
                continue

            if is_digit(byte):
                state = "NUMBER"
                start = i
                start_line = line
                start_col = col
                i += 1
                col += 1
                continue

            if byte in (ord("{"), ord("}")):
                toks.append(Token("block", chr(byte), line, col))
                i += 1
                col += 1
                continue

            if byte in (ord("+"), ord("-"), ord("*")):
                toks.append(Token("operator", chr(byte), line, col))
                i += 1
                col += 1
                continue

            if byte == ord(":"):
                state = "COLON"
                start_line = line
                start_col = col
                i += 1
                col += 1
                continue

            if byte == ord("="):
                state = "EQUAL"
                start_line = line
                start_col = col
                i += 1
                col += 1
                continue

            if byte == ord("!"):
                state = "BANG"
                start_line = line
                start_col = col
                i += 1
                col += 1
                continue

            bad = repr(chr(byte)) if 32 <= byte <= 126 else f"0x{byte:02x}"
            raise CompileError(f"line {line}:{col}: unexpected byte {bad}")

        elif state == "IDENT":
            if byte is not None and (is_alpha(byte) or is_digit(byte)):
                i += 1
                col += 1
                continue

            word = data[start:i]
            kind = "keyword" if word in KEYWORDS else "identifier"
            toks.append(Token(kind, word.decode("ascii"), start_line, start_col))
            state = "START"

        elif state == "NUMBER":
            if byte is not None and is_digit(byte):
                i += 1
                col += 1
                continue

            if byte is not None and is_alpha(byte):
                raise CompileError(f"line {start_line}:{start_col}: letter inside number")

            toks.append(
                Token("number", data[start:i].decode("ascii"), start_line, start_col)
            )
            state = "START"

        elif state == "COLON":
            if byte != ord("="):
                raise CompileError(
                    f"line {start_line}:{start_col}: ':' must be followed by '='"
                )
            toks.append(Token("operator", ":=", start_line, start_col))
            state = "START"
            i += 1
            col += 1

        elif state == "EQUAL":
            if byte != ord("="):
                raise CompileError(
                    f"line {start_line}:{start_col}: expected '==' "
                    "(a single '=' is not an operator)"
                )
            toks.append(Token("operator", "==", start_line, start_col))
            state = "START"
            i += 1
            col += 1

        elif state == "BANG":
            if byte == ord("="):
                toks.append(Token("operator", "!=", start_line, start_col))
                i += 1
                col += 1
            else:
                toks.append(Token("operator", "!", start_line, start_col))
            state = "START"

    if toks:
        lines.append(toks)

    return lines


def tokens_without_endline(line):
    return [token for token in line if token.kind != "endline"]


class ASTNode:
    def __init__(self, line, col):
        self.line = line
        self.col = col

    def children(self):
        return []

    def accept(self, visitor):
        name = type(self).__name__[:-4].lower()
        return getattr(visitor, "visit_" + name)(self)

    def dump(self, depth=0):
        print("  " * depth + self.label())
        for child in self.children():
            child.dump(depth + 1)


class StmtNode(ASTNode):
    pass


class ExprNode(ASTNode):
    def __init__(self, line, col):
        super().__init__(line, col)
        self.type = None


class ProgramNode(ASTNode):
    def __init__(self, line, col, statements, exit_node):
        super().__init__(line, col)
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Program"

    def children(self):
        return self.statements + [self.exit_node]


class BlockNode(ASTNode):
    def __init__(self, line, col, statements, exit_node=None):
        super().__init__(line, col)
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Block"

    def children(self):
        children = list(self.statements)
        if self.exit_node is not None:
            children.append(self.exit_node)
        return children


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, init):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.init = init
        self.ir_ptr = None

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]


class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        super().__init__(line, col)
        self.name = name
        self.value = value
        self.decl = None

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return [self.value]


class IfNode(StmtNode):
    def __init__(self, line, col, condition, then_block, else_block=None):
        super().__init__(line, col)
        self.condition = condition
        self.then_block = then_block
        self.else_block = else_block

    def label(self):
        return "If"

    def children(self):
        children = [self.condition, self.then_block]
        if self.else_block is not None:
            children.append(self.else_block)
        return children


class WhileNode(StmtNode):
    def __init__(self, line, col, condition, body):
        super().__init__(line, col)
        self.condition = condition
        self.body = body

    def label(self):
        return "While"

    def children(self):
        return [self.condition, self.body]


class ExitNode(StmtNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]


class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        super().__init__(line, col)
        self.op = op
        self.left = left
        self.right = right

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]


class NotNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)
        self.operand = operand

    def label(self):
        return "Not"

    def children(self):
        return [self.operand]


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name
        self.decl = None

    def label(self):
        return f"Var {self.name}"


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"


class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value
        self.type = "bool"

    def label(self):
        return "Bool true" if self.value else "Bool false"


class Parser:
    def __init__(self, lines):
        self.lines = [tokens_without_endline(line) for line in lines]
        self.line_index = 0
        self.toks = []
        self.pos = 0

    def _skip_blank_lines(self):
        while self.line_index < len(self.lines) and not self.lines[self.line_index]:
            self.line_index += 1

    def _load_current_line(self):
        self.toks = self.lines[self.line_index]
        self.pos = 0

    def _first_token(self):
        self._skip_blank_lines()
        if self.line_index >= len(self.lines):
            return None
        return self.lines[self.line_index][0]

    def peek(self):
        if self.pos < len(self.toks):
            return self.toks[self.pos]
        return None

    def eat(self):
        token = self.peek()
        if token is not None:
            self.pos += 1
        return token

    def fail(self, message, token=None):
        token = token or self.peek()
        if token is not None:
            raise CompileError(f"line {token.line}:{token.col}: {message}")
        if self.toks:
            last = self.toks[-1]
            raise CompileError(
                f"line {last.line}:{last.col + len(last.text)}: {message}"
            )
        raise CompileError(message)

    def expect_text(self, text):
        if self.peek() is None or self.peek().text != text:
            self.fail(f"expected '{text}'")
        return self.eat()

    def expect_kind(self, kind, what):
        if self.peek() is None or self.peek().kind != kind:
            self.fail(f"expected {what}")
        return self.eat()

    def finish_statement(self):
        if self.peek() is not None:
            self.fail(f"unexpected '{self.peek().text}' after the statement")

    def parse_program(self):
        statements = []
        exit_node = None
        first_program_token = self._first_token()

        while self.line_index < len(self.lines):
            self._skip_blank_lines()
            if self.line_index >= len(self.lines):
                break

            token = self.lines[self.line_index][0]
            if exit_node is not None:
                self.fail("nothing is allowed after exit", token)

            if token.text == "exit":
                exit_node = self.parse_exit_line()
            else:
                statements.append(self.parse_statement())

        if exit_node is None:
            if first_program_token is None:
                raise CompileError("line 1:1: program has no exit statement")
            last_nonempty = None
            for line in self.lines:
                if line:
                    last_nonempty = line[-1]
            raise CompileError(
                f"line {last_nonempty.line}:"
                f"{last_nonempty.col + len(last_nonempty.text)}: "
                "program has no exit statement"
            )

        line = first_program_token.line if first_program_token else 1
        col = first_program_token.col if first_program_token else 1
        return ProgramNode(line, col, statements, exit_node)

    def parse_statement(self):
        self._skip_blank_lines()
        if self.line_index >= len(self.lines):
            raise CompileError("unexpected end of input")

        token = self.lines[self.line_index][0]
        if token.text in ("i32", "i64", "bool"):
            return self.parse_decl_line()
        if token.kind == "identifier":
            return self.parse_assign_line()
        if token.text == "if":
            return self.parse_if()
        if token.text == "while":
            return self.parse_while()
        if token.text == "else":
            self.fail("'else' without an 'if'", token)
        if token.text == "exit":
            self.fail("exit is only allowed as the last line of a block or program", token)
        self.fail(f"cannot start statement with '{token.text}'", token)

    def parse_decl_line(self):
        self._load_current_line()
        type_tok = self.eat()
        mutable = self.peek() is not None and self.peek().text == "mut"
        if mutable:
            self.eat()

        name = self.expect_kind("identifier", "a variable name")
        if self.peek() is None or self.peek().text != "{":
            raise CompileError(
                f"line {name.line}:{name.col}: variable '{name.text}' "
                "needs an initialiser in {}"
            )

        opening = self.eat()
        init = self.parse_expr()
        if self.peek() is None:
                raise CompileError(
                f"line {opening.line}:{opening.col}: "
                "'{' is not closed before the end of the line"
            )
        self.expect_text("}")
        self.finish_statement()
        self.line_index += 1
        return DeclNode(name.line, name.col, name.text, type_tok.text, mutable, init)

    def parse_assign_line(self):
        self._load_current_line()
        name = self.expect_kind("identifier", "a variable name")
        if self.peek() is None or self.peek().text != ":=":
            got = "end of line" if self.peek() is None else f"'{self.peek().text}'"
            self.fail(f"expected ':=' after '{name.text}', got {got}")
        self.eat()
        value = self.parse_expr()
        self.finish_statement()
        self.line_index += 1
        return AssignNode(name.line, name.col, name.text, value)

    def parse_exit_line(self):
        self._load_current_line()
        token = self.expect_text("exit")
        value = self.parse_factor()
        self.finish_statement()
        self.line_index += 1
        return ExitNode(token.line, token.col, value)

    def _expect_block_after(self, keyword):
        self._skip_blank_lines()
        if self.line_index >= len(self.lines):
            raise CompileError(
                f"line {keyword.line}:{keyword.col}: expected '{{' on its own line "
                f"after '{keyword.text}', found end of input"
            )
        line = self.lines[self.line_index]
        first = line[0]
        if first.text != "{":
            raise CompileError(
                f"line {first.line}:{first.col}: expected '{{' on its own line "
                f"after '{keyword.text}', got '{first.text}'"
            )
        if len(line) != 1:
            extra = line[1]
            raise CompileError(
                f"line {extra.line}:{extra.col}: '{{' must be on its own line"
            )
        return self.parse_block()

    def parse_if(self):
        self._load_current_line()
        if_tok = self.expect_text("if")
        condition = self.parse_expr()
        self.finish_statement()
        self.line_index += 1

        then_block = self._expect_block_after(if_tok)
        else_block = None

        saved = self.line_index
        self._skip_blank_lines()
        if self.line_index < len(self.lines):
            line = self.lines[self.line_index]
            if line and line[0].text == "else":
                if len(line) != 1:
                    extra = line[1]
                    self.fail("unexpected token after 'else'", extra)
                else_tok = line[0]
                self.line_index += 1
                else_block = self._expect_block_after(else_tok)
            else:
                self.line_index = saved
        else:
            self.line_index = saved

        return IfNode(if_tok.line, if_tok.col, condition, then_block, else_block)

    def parse_while(self):
        self._load_current_line()
        while_tok = self.expect_text("while")
        condition = self.parse_expr()
        self.finish_statement()
        self.line_index += 1
        body = self._expect_block_after(while_tok)
        return WhileNode(while_tok.line, while_tok.col, condition, body)

    def parse_block(self):
        self._skip_blank_lines()
        opening_line = self.lines[self.line_index]
        opening = opening_line[0]
        self.line_index += 1
        statements = []
        exit_node = None
        has_content = False

        while True:
            self._skip_blank_lines()
            if self.line_index >= len(self.lines):
                raise CompileError(
                    f"line {opening.line}:{opening.col}: '{{' is never closed"
                )

            line = self.lines[self.line_index]
            first = line[0]

            if first.text == "}":
                if len(line) != 1:
                    extra = line[1]
                    self.fail("'}' must be on its own line", extra)
                if not has_content:
                    raise CompileError(
                        f"line {opening.line}:{opening.col}: empty block"
                    )
                self.line_index += 1
                return BlockNode(opening.line, opening.col, statements, exit_node)

            if exit_node is not None:
                self.fail("statement after 'exit' in the same block", first)

            if first.text == "exit":
                exit_node = self.parse_exit_line()
                has_content = True
                continue

            if first.text == "else":
                self.fail("'else' without an 'if'", first)

            statements.append(self.parse_statement())
            has_content = True

    def parse_expr(self):
        node = self.parse_arith()
        if (
            self.peek() is not None
            and self.peek().kind == "operator"
            and self.peek().text in ("==", "!=")
        ):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_arith())
        return node

    def parse_arith(self):
        node = self.parse_term()
        while (
            self.peek() is not None
            and self.peek().kind == "operator"
            and self.peek().text in ("+", "-")
        ):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_factor()
        while (
            self.peek() is not None
            and self.peek().kind == "operator"
            and self.peek().text == "*"
        ):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_factor())
        return node

    def parse_factor(self):
        token = self.peek()
        if token is None:
            self.fail("expected a constant or a variable")

        if token.kind == "operator" and token.text == "!":
            self.eat()
            return NotNode(token.line, token.col, self.parse_factor())

        if token.kind == "number":
            self.eat()
            return ConstNode(token.line, token.col, int(token.text))

        if token.text in ("true", "false"):
            self.eat()
            return BoolNode(token.line, token.col, token.text == "true")

        if token.kind == "identifier":
            self.eat()
            return VarNode(token.line, token.col, token.text)

        self.fail(f"expected a constant or a variable, got '{token.text}'", token)


class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]

    def fail(self, node, message):
        raise CompileError(f"line {node.line}:{node.col}: {message}")

    @staticmethod
    def is_int(type_name):
        return type_name in ("i32", "i64")

    @staticmethod
    def wider(left, right):
        return "i64" if "i64" in (left, right) else "i32"

    def lookup(self, node, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        self.fail(node, f"variable '{name}' is used before its declaration")

    def visit_program(self, node):
        for statement in node.statements:
            statement.accept(self)
        node.exit_node.accept(self)

    def visit_block(self, node):
        self.scopes.append({})
        try:
            for statement in node.statements:
                statement.accept(self)
            if node.exit_node is not None:
                node.exit_node.accept(self)
        finally:
            self.scopes.pop()

    def visit_decl(self, node):
        frame = self.scopes[-1]
        if node.name in frame:
            self.fail(node, f"variable '{node.name}' is already declared in this block")

        node.init.accept(self)
        self.check_assignable(
            node.init,
            node.type_name,
            node,
            f"initialise '{node.name}'",
        )
        frame[node.name] = node

    def visit_assign(self, node):
        decl = self.lookup(node, node.name)
        node.decl = decl
        if not decl.mutable:
            self.fail(node, f"cannot assign to '{node.name}': it is not mut")

        node.value.accept(self)
        self.check_assignable(
            node.value,
            decl.type_name,
            node,
            f"assign to '{node.name}'",
        )

    def visit_if(self, node):
        condition_type = node.condition.accept(self)
        if condition_type != "bool":
            self.fail(
                node,
                f"the condition of 'if' must be bool, got {condition_type}",
            )
        node.then_block.accept(self)
        if node.else_block is not None:
            node.else_block.accept(self)

    def visit_while(self, node):
        condition_type = node.condition.accept(self)
        if condition_type != "bool":
            self.fail(
                node,
                f"the condition of 'while' must be bool, got {condition_type}",
            )
        node.body.accept(self)

    def visit_exit(self, node):
        node.value.accept(self)
        return node.value.type

    def visit_not(self, node):
        operand_type = node.operand.accept(self)
        if operand_type != "bool":
            self.fail(node, f"cannot apply '!' to {operand_type}")
        node.type = "bool"
        return node.type

    def visit_binop(self, node):
        left_type = node.left.accept(self)
        right_type = node.right.accept(self)

        if node.op in ("+", "-", "*"):
            if not self.is_int(left_type):
                self.fail(node, f"cannot apply '{node.op}' to {left_type}")
            if not self.is_int(right_type):
                self.fail(node, f"cannot apply '{node.op}' to {right_type}")
            node.type = self.wider(left_type, right_type)
        else:
            both_int = self.is_int(left_type) and self.is_int(right_type)
            both_bool = left_type == "bool" and right_type == "bool"
            if not (both_int or both_bool):
                self.fail(node, f"cannot compare {left_type} with {right_type}")
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
            self.fail(node, f"constant {node.value} does not fit in i64")
        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if isinstance(expr, ConstNode) and want in ("i32", "i64"):
            limit = INT32_MAX if want == "i32" else INT64_MAX
            if expr.value > limit:
                self.fail(expr, f"constant {expr.value} does not fit in {want}")

        if have == want or (have == "i32" and want == "i64"):
            return
        self.fail(
            at,
            f"cannot {what} of type {want} with a value of type {have}",
        )


class CodeGen:
    def __init__(self):
        self.I1 = ir.IntType(1)
        self.I8 = ir.IntType(8)
        self.I32 = ir.IntType(32)
        self.I64 = ir.IntType(64)

        self.module = ir.Module(name="practice5")
        self.module.triple = llvm.get_default_triple()

        self.function = ir.Function(
            self.module,
            ir.FunctionType(self.I32, []),
            name="main",
        )
        self.entry = self.function.append_basic_block("entry")
        self.builder = ir.IRBuilder(self.entry)

        i8ptr = ir.PointerType(self.I8)
        self.printf = ir.Function(
            self.module,
            ir.FunctionType(self.I32, [i8ptr], var_arg=True),
            name="printf",
        )

        self.fmt_int = self.make_string("fmt_int", b"Program exit with result %lld\n\0")
        self.fmt_str = self.make_string("fmt_str", b"Program exit with result %s\n\0")
        self.str_true = self.make_string("str_true", b"true\0")
        self.str_false = self.make_string("str_false", b"false\0")

    def make_string(self, name, data):
        array_type = ir.ArrayType(self.I8, len(data))
        global_value = ir.GlobalVariable(self.module, array_type, name=name)
        global_value.linkage = "private"
        global_value.global_constant = True
        global_value.initializer = ir.Constant(array_type, bytearray(data))
        return global_value

    def llvm_type(self, type_name):
        return {"i32": self.I32, "i64": self.I64, "bool": self.I1}[type_name]

    def as_i8ptr(self, global_string):
        return self.builder.bitcast(global_string, ir.PointerType(self.I8))

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, self.I64, name="wide")
        return value

    def entry_alloca(self, type_name, name):
        current_block = self.builder.block
        self.builder.position_at_start(self.entry)
        pointer = self.builder.alloca(self.llvm_type(type_name), name=name)
        self.builder.position_at_end(current_block)
        return pointer

    def visit_program(self, node):
        for statement in node.statements:
            statement.accept(self)
        node.exit_node.accept(self)
        return self.module

    def visit_block(self, node):
        for statement in node.statements:
            statement.accept(self)
        if node.exit_node is not None:
            node.exit_node.accept(self)

    def visit_decl(self, node):
        value = node.init.accept(self)
        value = self.coerce(value, node.init.type, node.type_name)
        node.ir_ptr = self.entry_alloca(node.type_name, node.name)
        self.builder.store(value, node.ir_ptr)

    def visit_assign(self, node):
        value = node.value.accept(self)
        value = self.coerce(value, node.value.type, node.decl.type_name)
        self.builder.store(value, node.decl.ir_ptr)

    def visit_if(self, node):
        condition = node.condition.accept(self)
        then_bb = self.function.append_basic_block("then")
        else_bb = (
            self.function.append_basic_block("else")
            if node.else_block is not None
            else None
        )
        merge_bb = self.function.append_basic_block("merge")

        self.builder.cbranch(condition, then_bb, else_bb or merge_bb)

        self.builder.position_at_end(then_bb)
        node.then_block.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        if else_bb is not None:
            self.builder.position_at_end(else_bb)
            node.else_block.accept(self)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def visit_while(self, node):
        condition_bb = self.function.append_basic_block("while.cond")
        body_bb = self.function.append_basic_block("while.body")
        end_bb = self.function.append_basic_block("while.end")

        self.builder.branch(condition_bb)
        self.builder.position_at_end(condition_bb)
        condition = node.condition.accept(self)
        self.builder.cbranch(condition, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(condition_bb)

        self.builder.position_at_end(end_bb)

    def visit_exit(self, node):
        value = node.value.accept(self)
        if node.value.type in ("i32", "i64"):
            value = self.coerce(value, node.value.type, "i64")
            self.builder.call(
                self.printf,
                [self.as_i8ptr(self.fmt_int), value],
            )
        else:
            true_ptr = self.as_i8ptr(self.str_true)
            false_ptr = self.as_i8ptr(self.str_false)
            text_ptr = self.builder.select(value, true_ptr, false_ptr, name="booltext")
            self.builder.call(
                self.printf,
                [self.as_i8ptr(self.fmt_str), text_ptr],
            )
        self.builder.ret(ir.Constant(self.I32, 0))

    def visit_not(self, node):
        value = node.operand.accept(self)
        return self.builder.xor(value, ir.Constant(self.I1, 1), name="not")

    def visit_binop(self, node):
        left = node.left.accept(self)
        right = node.right.accept(self)

        if node.op in ("+", "-", "*"):
            want = "i64" if "i64" in (node.left.type, node.right.type) else "i32"
            left = self.coerce(left, node.left.type, want)
            right = self.coerce(right, node.right.type, want)
            if node.op == "+":
                return self.builder.add(left, right)
            if node.op == "-":
                return self.builder.sub(left, right)
            return self.builder.mul(left, right)

        if node.left.type in ("i32", "i64"):
            want = "i64" if "i64" in (node.left.type, node.right.type) else "i32"
            left = self.coerce(left, node.left.type, want)
            right = self.coerce(right, node.right.type, want)
        return self.builder.icmp_signed(node.op, left, right)

    def visit_var(self, node):
        return self.builder.load(node.decl.ir_ptr, name=node.name + ".value")

    def visit_const(self, node):
        return ir.Constant(self.llvm_type(node.type), node.value)

    def visit_bool(self, node):
        return ir.Constant(self.I1, int(node.value))


def parse_and_check(data):
    tree = Parser(lex(data)).parse_program()
    tree.accept(SemanticChecker())
    return tree


def dump_tokens(lines):
    for line in lines:
        for token in line:
            text = "\\n" if token.kind == "endline" else token.text
            print(f"{token.line}:{token.col} {token.kind} {text}")


def usage():
    print(
        "usage:\n"
        "  python3 src/compiler.py input.txt output.ll\n"
        "  python3 src/compiler.py --ast input.txt\n"
        "  python3 src/compiler.py --tokens input.txt",
        file=sys.stderr,
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = "compile"
    input_path = None
    output_path = None

    if len(argv) == 2 and argv[0] in ("--ast", "--tokens"):
        mode = argv[0][2:]
        input_path = argv[1]
    elif len(argv) == 2 and not argv[0].startswith("--"):
        input_path, output_path = argv
    else:
        usage()
        return 1

    if output_path is not None and os.path.abspath(input_path) == os.path.abspath(output_path):
        print("compilation error: input and output paths must be different", file=sys.stderr)
        return 1

    try:
        with open(input_path, "rb") as input_file:
            data = input_file.read()

        if mode == "tokens":
            dump_tokens(lex(data))
            return 0

        tree = parse_and_check(data)
        if mode == "ast":
            tree.dump()
            return 0

        if os.path.exists(output_path):
            os.remove(output_path)

        module = tree.accept(CodeGen())
        with open(output_path, "w", encoding="utf-8") as output_file:
            output_file.write(str(module))
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
