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
    b"mut", b"exit",
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
            if b != ord("="):
                raise CompileError(
                    f"line {sl}:{sc}: expected '!=' "
                    f"(a single '!' is not an operator)"
                )

            toks.append(Token("operator", "!=", sl, sc))
            state = "START"
            i += 1
            col += 1

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
    def __init__(self, statements, exit_node):
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Program"

    def children(self):
        return self.statements + [self.exit_node]


class DeclNode(ASTNode):
    def __init__(
        self,
        line,
        col,
        name,
        type_name,
        mutable,
        init
    ):
        self.line = line
        self.col = col
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.init = init
        self.ir_ptr = None

    def label(self):
        kind = "mut" if self.mutable else "const"
        return f"Decl {self.name} {self.type_name} {kind}"

    def children(self):
        return [self.init]


class AssignNode(ASTNode):
    def __init__(self, line, col, name, value):
        self.line = line
        self.col = col
        self.name = name
        self.value = value
        self.decl = None

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return [self.value]


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

    def parse_program(self):
        statements = []
        exit_node = None

        for line in self.lines:
            if not line:
                continue

            self.toks = line
            self.pos = 0
            first = self.peek()

            if exit_node:
                self.fail(
                    "nothing is allowed after exit"
                )

            if first.text in (
                "i32",
                "i64",
                "bool"
            ):
                statements.append(
                    self.parse_decl()
                )

            elif first.kind == "identifier":
                statements.append(
                    self.parse_assign()
                )

            elif first.text == "exit":
                exit_node = self.parse_exit()

            else:
                self.fail(
                    "cannot start statement with "
                    f"'{first.text}'"
                )

            if self.peek():
                self.fail(
                    f"unexpected "
                    f"'{self.peek().text}' "
                    f"after the statement"
                )

        if not exit_node:
            nonempty = [
                line
                for line in self.lines
                if line
            ]

            if nonempty:
                t = nonempty[-1][-1]

                raise CompileError(
                    f"line {t.line}:"
                    f"{t.col + len(t.text)}: "
                    "program has no exit statement"
                )

            raise CompileError(
                "line 1:1: "
                "program has no exit statement"
            )

        return ProgramNode(
            statements,
            exit_node
        )

    def parse_decl(self):
        type_tok = self.eat()

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

        self.eat()

        init = self.parse_expr()

        self.expect_text("}")

        return DeclNode(
            name.line,
            name.col,
            name.text,
            type_tok.text,
            mutable,
            init
        )

    def parse_assign(self):
        name = self.expect_kind(
            "identifier",
            "a variable name"
        )

        if (
            not self.peek()
            or self.peek().text != ":="
        ):
            got = (
                "end of line"
                if not self.peek()
                else f"'{self.peek().text}'"
            )

            self.fail(
                f"expected ':=' "
                f"after '{name.text}', "
                f"got {got}"
            )

        self.eat()

        value = self.parse_expr()

        return AssignNode(
            name.line,
            name.col,
            name.text,
            value
        )

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
            self.fail(
                "expected a constant "
                "or a variable"
            )

        if t.kind == "number":
            self.eat()

            return ConstNode(
                t.line,
                t.col,
                int(t.text)
            )

        if t.text in (
            "true",
            "false"
        ):
            self.eat()

            return BoolNode(
                t.line,
                t.col,
                t.text == "true"
            )

        if t.kind == "identifier":
            self.eat()

            return VarNode(
                t.line,
                t.col,
                t.text
            )

        self.fail(
            "expected a constant "
            "or a variable, got "
            f"'{t.text}'"
        )


class SemanticChecker:
    def __init__(self):
        self.symbols = {}

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
        for statement in node.statements:
            statement.accept(self)

        node.exit_node.accept(self)

    def visit_decl(self, node):
        if node.name in self.symbols:
            self.fail(
                node,
                f"variable '{node.name}' "
                "is already declared"
            )

        node.init.accept(self)

        self.check_assignable(
            node.init,
            node.type_name,
            node,
            f"initialise '{node.name}'"
        )

        self.symbols[node.name] = node

    def visit_assign(self, node):
        if node.name not in self.symbols:
            self.fail(
                node,
                f"variable '{node.name}' "
                "is used before its declaration"
            )

        decl = self.symbols[node.name]
        node.decl = decl

        if not decl.mutable:
            self.fail(
                node,
                f"cannot assign to "
                f"'{node.name}': "
                "it is not mut"
            )

        node.value.accept(self)

        self.check_assignable(
            node.value,
            decl.type_name,
            node,
            f"assign to '{node.name}'"
        )

    def visit_exit(self, node):
        node.value.accept(self)
        return node.value.type

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
            name="practice4"
        )

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
        return {
            "i32": self.I32,
            "i64": self.I64,
            "bool": self.I1
        }[type_name]

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
        for statement in node.statements:
            statement.accept(self)

        node.exit_node.accept(self)

        return self.module

    def visit_decl(self, node):
        value = node.init.accept(self)

        value = self.coerce(
            value,
            node.init.type,
            node.type_name
        )

        node.ir_ptr = self.builder.alloca(
            self.llvm_type(node.type_name),
            name=node.name
        )

        self.builder.store(
            value,
            node.ir_ptr
        )

    def visit_assign(self, node):
        value = node.value.accept(self)

        value = self.coerce(
            value,
            node.value.type,
            node.decl.type_name
        )

        self.builder.store(
            value,
            node.decl.ir_ptr
        )

    def visit_exit(self, node):
        value = node.value.accept(self)

        if node.value.type in (
            "i32",
            "i64"
        ):
            value = self.coerce(
                value,
                node.value.type,
                "i64"
            )

            self.builder.call(
                self.printf,
                [
                    self.as_i8ptr(
                        self.fmt_int
                    ),
                    value
                ]
            )

        else:
            true_ptr = self.as_i8ptr(
                self.str_true
            )

            false_ptr = self.as_i8ptr(
                self.str_false
            )

            text_ptr = self.builder.select(
                value,
                true_ptr,
                false_ptr,
                name="booltext"
            )

            self.builder.call(
                self.printf,
                [
                    self.as_i8ptr(
                        self.fmt_str
                    ),
                    text_ptr
                ]
            )

        self.builder.ret(
            ir.Constant(
                self.I32,
                0
            )
        )

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
