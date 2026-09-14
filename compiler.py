from llvmlite import ir
import llvmlite.binding as llvm
import sys
import os

class CompileError(Exception):
    pass

class Token:
    def __init__(self, kind, text, line, col):
        self.kind = kind
        self.text = text
        self.line = line
        self.col = col

    def __repr__(self):
        return f"({self.text!r}, {self.kind}, {self.line}:{self.col})"

KEYWORDS = {b"i32", b"mut", b"exit"}

def is_alpha(b):
    return 65 <= b <= 90 or 97 <= b <= 122 or b == 95

def is_digit(b):
    return 48 <= b <= 57

def lex(data):
    lines, tokens = [], []
    state = "START"
    i = 0
    line = col = 1
    start = 0
    start_line = start_col = 1
    braces = []

    while i <= len(data):
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None:
                break

            if b in (32, 9):
                i += 1
                col += 1
                continue

            if b == 10:
                if braces:
                    t = braces[0]
                    raise CompileError(
                        f"line {t.line}:{t.col}: "
                        "'{' is not closed before the end of the line"
                    )

                tokens.append(Token("endline", "\n", line, col))
                lines.append(tokens)
                tokens = []

                i += 1
                line += 1
                col = 1
                continue

            if is_alpha(b):
                state = "IDENT"
                start, start_line, start_col = i, line, col
                i += 1
                col += 1
                continue

            if is_digit(b):
                state = "NUMBER"
                start, start_line, start_col = i, line, col
                i += 1
                col += 1
                continue

            if b == ord("{"):
                t = Token("block", "{", line, col)
                tokens.append(t)
                braces.append(t)
                i += 1
                col += 1
                continue

            if b == ord("}"):
                tokens.append(Token("block", "}", line, col))
                if braces:
                    braces.pop()
                i += 1
                col += 1
                continue

            if b in (ord("+"), ord("-"), ord("*")):
                tokens.append(Token("operator", chr(b), line, col))
                i += 1
                col += 1
                continue

            if b == ord(":"):
                state = "COLON"
                start_line, start_col = line, col
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

            tokens.append(
                Token(
                    kind,
                    word.decode("ascii"),
                    start_line,
                    start_col
                )
            )

            state = "START"
            continue

        elif state == "NUMBER":
            if b is not None and is_digit(b):
                i += 1
                col += 1
                continue

            if b is not None and is_alpha(b):
                raise CompileError(
                    f"line {start_line}:{start_col}: "
                    "letter inside number"
                )

            tokens.append(
                Token(
                    "number",
                    data[start:i].decode("ascii"),
                    start_line,
                    start_col
                )
            )

            state = "START"
            continue

        elif state == "COLON":
            if b == ord("="):
                tokens.append(
                    Token("operator", ":=", start_line, start_col)
                )

                state = "START"
                i += 1
                col += 1
                continue

            raise CompileError(
                f"line {start_line}:{start_col}: "
                "':' must be followed by '='"
            )

    if braces:
        t = braces[0]
        raise CompileError(
            f"line {t.line}:{t.col}: "
            "'{' is not closed before the end of the line"
        )

    if tokens:
        lines.append(tokens)

    return lines

def error(token, message):
    raise CompileError(
        f"line {token.line}:{token.col}: {message}"
    )

if len(sys.argv) != 3:
    print(
        "usage: python3 compiler.py <input> <output.ll>",
        file=sys.stderr
    )
    sys.exit(1)

input_path, output_path = sys.argv[1], sys.argv[2]

if os.path.exists(output_path):
    os.remove(output_path)

try:
    with open(input_path, "rb") as f:
        token_lines = lex(f.read())

    program = [
        [t for t in line if t.kind != "endline"]
        for line in token_lines
    ]
    program = [line for line in program if line]

    I32 = ir.IntType(32)
    I8 = ir.IntType(8)

    module = ir.Module(name="practice2")
    module.triple = llvm.get_default_triple()

    main = ir.Function(
        module,
        ir.FunctionType(I32, []),
        name="main"
    )

    builder = ir.IRBuilder(
        main.append_basic_block("entry")
    )

    printf = ir.Function(
        module,
        ir.FunctionType(
            I32,
            [ir.PointerType(I8)],
            var_arg=True
        ),
        name="printf"
    )

    text = b"Program exit with result %d\n\0"

    fmt = ir.GlobalVariable(
        module,
        ir.ArrayType(I8, len(text)),
        name="fmt"
    )

    fmt.linkage = "private"
    fmt.global_constant = True
    fmt.initializer = ir.Constant(
        ir.ArrayType(I8, len(text)),
        bytearray(text)
    )

    symbols = {}

    def value(t):
        if t.kind == "number":
            return ir.Constant(I32, int(t.text))

        if t.kind == "identifier":
            if t.text not in symbols:
                error(
                    t,
                    f"variable '{t.text}' is used "
                    "before its declaration"
                )

            return builder.load(
                symbols[t.text]["ptr"]
            )

        error(t, "expected a number or variable")

    def expr(ts):
        if len(ts) == 1:
            return value(ts[0])

        if (
            len(ts) == 3
            and ts[1].kind == "operator"
            and ts[1].text in ("+", "-", "*")
        ):
            a = value(ts[0])
            b = value(ts[2])

            if ts[1].text == "+":
                return builder.add(a, b)

            if ts[1].text == "-":
                return builder.sub(a, b)

            return builder.mul(a, b)

        error(
            ts[3] if len(ts) > 3 else ts[-1],
            "invalid expression"
        )

    exit_found = False

    for index, ts in enumerate(program):
        first = ts[0]

        if first.kind == "keyword" and first.text == "i32":
            p = 1

            mutable = (
                p < len(ts)
                and ts[p].kind == "keyword"
                and ts[p].text == "mut"
            )

            if mutable:
                p += 1

            if p >= len(ts) or ts[p].kind != "identifier":
                error(
                    ts[p] if p < len(ts) else first,
                    "expected variable name"
                )

            name_t = ts[p]
            name = name_t.text
            p += 1

            if name in symbols:
                error(
                    name_t,
                    f"variable '{name}' is already declared"
                )

            if p >= len(ts) or ts[p].text != "{":
                error(
                    name_t,
                    f"variable '{name}' needs "
                    "an initialiser in {}"
                )

            close = next(
                (
                    j for j in range(p + 1, len(ts))
                    if ts[j].text == "}"
                ),
                None
            )

            if close is None:
                error(
                    name_t,
                    f"variable '{name}' needs "
                    "an initialiser in {{}}"
                )

            if close != len(ts) - 1:
                error(
                    ts[close + 1],
                    "extra tokens after declaration"
                )

            if close == p + 1:
                error(
                    ts[close],
                    "empty initialiser"
                )

            init = expr(ts[p + 1:close])

            ptr = builder.alloca(I32, name=name)
            builder.store(init, ptr)

            symbols[name] = {
                "ptr": ptr,
                "mut": mutable
            }

        elif first.kind == "identifier":
            if (
                len(ts) < 2
                or ts[1].kind != "operator"
                or ts[1].text != ":="
            ):
                error(
                    ts[1] if len(ts) > 1 else first,
                    "expected :="
                )

            name = first.text

            if name not in symbols:
                error(
                    first,
                    f"variable '{name}' is used "
                    "before its declaration"
                )

            if not symbols[name]["mut"]:
                error(
                    first,
                    f"cannot assign to '{name}': it is not mut"
                )

            if len(ts) < 3:
                error(
                    ts[1],
                    "missing assignment value"
                )

            builder.store(
                expr(ts[2:]),
                symbols[name]["ptr"]
            )

        elif (
            first.kind == "keyword"
            and first.text == "exit"
        ):
            if len(ts) != 2:
                error(
                    first,
                    "exit expects one value"
                )

            if index != len(program) - 1:
                error(
                    first,
                    "exit must be the last statement"
                )

            result = value(ts[1])

            fmt_ptr = builder.bitcast(
                fmt,
                ir.PointerType(I8)
            )

            builder.call(
                printf,
                [fmt_ptr, result]
            )

            builder.ret(
                ir.Constant(I32, 0)
            )

            exit_found = True

        else:
            error(
                first,
                "cannot parse line"
            )

    if not exit_found:
        if program:
            error(
                program[-1][0],
                "program has no exit statement"
            )

        raise CompileError(
            "line 1:1: program has no exit statement"
        )

    with open(output_path, "w") as f:
        f.write(str(module))

except CompileError as e:
    print(
        f"compilation error: {e}",
        file=sys.stderr
    )
    sys.exit(1)
