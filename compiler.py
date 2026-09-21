from llvmlite import ir
import llvmlite.binding as llvm
import sys, os

class CompileError(Exception): pass

class Token:
    def __init__(self, kind, text, line, col):
        self.kind, self.text, self.line, self.col = kind, text, line, col

KEYWORDS = {b"i32", b"mut", b"exit"}

def is_alpha(b): return 65 <= b <= 90 or 97 <= b <= 122 or b == 95
def is_digit(b): return 48 <= b <= 57

def lex(data):
    lines, toks, state, i, line, col = [], [], "START", 0, 1, 1
    start = 0
    sl = sc = 1

    while i <= len(data):
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None: break
            if b in (32, 9, 13):
                i += 1; col += 1; continue

            if b == 10:
                toks.append(Token("endline", "\n", line, col))
                lines.append(toks); toks = []
                i += 1; line += 1; col = 1; continue

            if is_alpha(b):
                state, start, sl, sc = "IDENT", i, line, col
                i += 1; col += 1; continue

            if is_digit(b):
                state, start, sl, sc = "NUMBER", i, line, col
                i += 1; col += 1; continue

            if b in (ord("{"), ord("}")):
                toks.append(Token("block", chr(b), line, col))
                i += 1; col += 1; continue

            if b in (ord("+"), ord("-"), ord("*")):
                toks.append(Token("operator", chr(b), line, col))
                i += 1; col += 1; continue

            if b == ord(":"):
                state, sl, sc = "COLON", line, col
                i += 1; col += 1; continue

            bad = repr(chr(b)) if 32 <= b <= 126 else f"0x{b:02x}"
            raise CompileError(f"line {line}:{col}: unexpected byte {bad}")

        elif state == "IDENT":
            if b is not None and (is_alpha(b) or is_digit(b)):
                i += 1; col += 1; continue

            word = data[start:i]
            kind = "keyword" if word in KEYWORDS else "identifier"
            toks.append(Token(kind, word.decode("ascii"), sl, sc))
            state = "START"

        elif state == "NUMBER":
            if b is not None and is_digit(b):
                i += 1; col += 1; continue

            if b is not None and is_alpha(b):
                raise CompileError(f"line {sl}:{sc}: letter inside number")

            toks.append(Token("number", data[start:i].decode("ascii"), sl, sc))
            state = "START"

        elif state == "COLON":
            if b != ord("="):
                raise CompileError(f"line {sl}:{sc}: ':' must be followed by '='")

            toks.append(Token("operator", ":=", sl, sc))
            state = "START"; i += 1; col += 1

    if toks: lines.append(toks)
    return lines


class ASTNode:
    def children(self): return []

    def accept(self, visitor):
        name = type(self).__name__[:-4].lower()
        return getattr(visitor, "visit_" + name)(self)

    def dump(self, prefix="", last=True, root=True):
        print(self.label() if root else prefix + ("└── " if last else "├── ") + self.label())
        prefix = "" if root else prefix + ("    " if last else "│   ")
        kids = self.children()

        for i, child in enumerate(kids):
            child.dump(prefix, i == len(kids) - 1, False)


class ProgramNode(ASTNode):
    def __init__(self, statements, exit_node):
        self.statements, self.exit_node = statements, exit_node

    def label(self): return "Program"
    def children(self): return self.statements + [self.exit_node]


class DeclNode(ASTNode):
    def __init__(self, line, col, name, mutable, init):
        self.line, self.col, self.name = line, col, name
        self.mutable, self.init = mutable, init

    def label(self): return f"Decl {self.name} [{'mut' if self.mutable else 'const'}]"
    def children(self): return [self.init]


class AssignNode(ASTNode):
    def __init__(self, line, col, name, value):
        self.line, self.col, self.name, self.value = line, col, name, value

    def label(self): return f"Assign {self.name}"
    def children(self): return [self.value]


class ExitNode(ASTNode):
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value

    def label(self): return "Exit"
    def children(self): return [self.value]


class BinOpNode(ASTNode):
    def __init__(self, line, col, op, left, right):
        self.line, self.col, self.op = line, col, op
        self.left, self.right = left, right

    def label(self): return f"BinOp {self.op}"
    def children(self): return [self.left, self.right]


class VarNode(ASTNode):
    def __init__(self, line, col, name):
        self.line, self.col, self.name = line, col, name

    def label(self): return f"Var {self.name}"


class ConstNode(ASTNode):
    def __init__(self, line, col, value):
        self.line, self.col, self.value = line, col, value

    def label(self): return f"Const {self.value}"


class Parser:
    def __init__(self, lines):
        self.lines = [[t for t in line if t.kind != "endline"] for line in lines]
        self.toks, self.pos = [], 0

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        t = self.peek()
        if t: self.pos += 1
        return t

    def fail(self, message):
        t = self.peek()

        if t:
            raise CompileError(f"line {t.line}:{t.col}: {message}")

        if self.toks:
            t = self.toks[-1]
            raise CompileError(f"line {t.line}:{t.col + len(t.text)}: {message}")

        raise CompileError(message)

    def expect_text(self, text):
        if not self.peek() or self.peek().text != text:
            self.fail(f"expected '{text}'")
        return self.eat()

    def expect_kind(self, kind, what):
        if not self.peek() or self.peek().kind != kind:
            self.fail(f"expected {what}")
        return self.eat()

    def parse_program(self):
        statements, exit_node = [], None

        for line in self.lines:
            if not line: continue
            self.toks, self.pos = line, 0
            first = self.peek()

            if exit_node:
                self.fail("nothing is allowed after exit")

            if first.text == "i32":
                statements.append(self.parse_decl())
            elif first.kind == "identifier":
                statements.append(self.parse_assign())
            elif first.text == "exit":
                exit_node = self.parse_exit()
            else:
                self.fail(f"cannot start statement with '{first.text}'")

            if self.peek():
                self.fail(f"unexpected '{self.peek().text}' after the statement")

        if not exit_node:
            nonempty = [line for line in self.lines if line]

            if nonempty:
                t = nonempty[-1][-1]
                raise CompileError(
                    f"line {t.line}:{t.col + len(t.text)}: program has no exit statement"
                )

            raise CompileError("line 1:1: program has no exit statement")

        return ProgramNode(statements, exit_node)

    def parse_decl(self):
        self.expect_text("i32")
        mutable = bool(self.peek() and self.peek().text == "mut")
        if mutable: self.eat()

        name = self.expect_kind("identifier", "a variable name")

        if not self.peek() or self.peek().text != "{":
            raise CompileError(
                f"line {name.line}:{name.col}: variable '{name.text}' needs an initialiser in {{}}"
            )

        self.eat()
        init = self.parse_expr()
        self.expect_text("}")

        return DeclNode(name.line, name.col, name.text, mutable, init)

    def parse_assign(self):
        name = self.expect_kind("identifier", "a variable name")

        if not self.peek() or self.peek().text != ":=":
            got = "end of line" if not self.peek() else f"'{self.peek().text}'"
            self.fail(f"expected ':=' after '{name.text}', got {got}")

        self.eat()
        value = self.parse_expr()
        return AssignNode(name.line, name.col, name.text, value)

    def parse_exit(self):
        t = self.expect_text("exit")
        return ExitNode(t.line, t.col, self.parse_factor())

    def parse_expr(self):
        node = self.parse_term()

        while self.peek() and self.peek().kind == "operator" and self.peek().text in ("+", "-"):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_term())

        return node

    def parse_term(self):
        node = self.parse_factor()

        while self.peek() and self.peek().kind == "operator" and self.peek().text == "*":
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_factor())

        return node

    def parse_factor(self):
        t = self.peek()

        if not t:
            self.fail("expected a constant or a variable")

        if t.kind == "number":
            self.eat()
            return ConstNode(t.line, t.col, int(t.text))

        if t.kind == "identifier":
            self.eat()
            return VarNode(t.line, t.col, t.text)

        self.fail(f"expected a constant or a variable, got '{t.text}'")


class CodeGen:
    def __init__(self):
        self.I32, self.I8 = ir.IntType(32), ir.IntType(8)
        self.module = ir.Module(name="practice3")
        self.module.triple = llvm.get_default_triple()

        main = ir.Function(self.module, ir.FunctionType(self.I32, []), name="main")
        self.builder = ir.IRBuilder(main.append_basic_block("entry"))

        self.printf = ir.Function(
            self.module,
            ir.FunctionType(self.I32, [ir.PointerType(self.I8)], var_arg=True),
            name="printf"
        )

        text = b"Program exit with result %d\n\0"
        self.fmt = ir.GlobalVariable(
            self.module, ir.ArrayType(self.I8, len(text)), name="fmt"
        )
        self.fmt.linkage = "private"
        self.fmt.global_constant = True
        self.fmt.initializer = ir.Constant(
            ir.ArrayType(self.I8, len(text)), bytearray(text)
        )

        self.symbols = {}

    def fail(self, node, message):
        raise CompileError(f"line {node.line}:{node.col}: {message}")

    def visit_program(self, node):
        for statement in node.statements:
            statement.accept(self)

        node.exit_node.accept(self)
        return self.module

    def visit_decl(self, node):
        if node.name in self.symbols:
            self.fail(node, f"variable '{node.name}' is already declared")

        value = node.init.accept(self)
        ptr = self.builder.alloca(self.I32, name=node.name)
        self.builder.store(value, ptr)
        self.symbols[node.name] = {"ptr": ptr, "mut": node.mutable}

    def visit_assign(self, node):
        if node.name not in self.symbols:
            self.fail(node, f"variable '{node.name}' is used before its declaration")

        if not self.symbols[node.name]["mut"]:
            self.fail(node, f"cannot assign to '{node.name}': it is not mut")

        self.builder.store(node.value.accept(self), self.symbols[node.name]["ptr"])

    def visit_exit(self, node):
        fmt = self.builder.bitcast(self.fmt, ir.PointerType(self.I8))
        self.builder.call(self.printf, [fmt, node.value.accept(self)])
        self.builder.ret(ir.Constant(self.I32, 0))

    def visit_binop(self, node):
        left, right = node.left.accept(self), node.right.accept(self)

        if node.op == "+": return self.builder.add(left, right)
        if node.op == "-": return self.builder.sub(left, right)
        return self.builder.mul(left, right)

    def visit_var(self, node):
        if node.name not in self.symbols:
            self.fail(node, f"variable '{node.name}' is used before its declaration")

        return self.builder.load(self.symbols[node.name]["ptr"])

    def visit_const(self, node):
        return ir.Constant(self.I32, node.value)


if len(sys.argv) != 3:
    print(
        "usage:\n"
        "  python3 compiler.py input.txt output.ll\n"
        "  python3 compiler.py --ast input.txt",
        file=sys.stderr
    )
    sys.exit(1)

ast_mode = sys.argv[1] == "--ast"
input_path, output_path = (sys.argv[2], None) if ast_mode else (sys.argv[1], sys.argv[2])

if output_path and os.path.exists(output_path):
    os.remove(output_path)

try:
    with open(input_path, "rb") as f:
        tree = Parser(lex(f.read())).parse_program()

    if ast_mode:
        tree.dump()
    else:
        module = tree.accept(CodeGen())

        with open(output_path, "w") as f:
            f.write(str(module))

except CompileError as e:
    print(f"compilation error: {e}", file=sys.stderr)
    sys.exit(1)