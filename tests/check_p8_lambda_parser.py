from pathlib import Path
import difflib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (
    CallNode, CaptureNode, FunctionNode, LambdaNode, MemberFnNode,
    Parser, lex,
)

FIXTURES = ROOT / "tests" / "p8_lambda_parser"


def cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "compiler.py"), *map(str, args)],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )


def parse(name):
    return Parser(lex((FIXTURES / "ok" / name).read_bytes())).parse_program()


def verify():
    passed = failed = 0
    for kind, expected_suffix in (("ok", ".ast"), ("err", ".err")):
        sources = sorted((FIXTURES / kind).glob("*.src"))
        for source in sources:
            expected = source.with_suffix(expected_suffix).read_text(encoding="utf-8")
            result = cli("--ast", source)
            actual = result.stdout if kind == "ok" else result.stderr
            success = (result.returncode == (0 if kind == "ok" else 1)
                       and not (result.stderr if kind == "ok" else result.stdout)
                       and actual == expected)
            if success:
                passed += 1
                print(f"PASS {kind.upper()} {source.name}")
            else:
                failed += 1
                diff = "".join(difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile="expected", tofile="actual",
                ))
                print(f"FAIL {kind.upper()} {source.name}: status {result.returncode}\n{diff}")

    top = parse("top_empty.src")
    captures = parse("captures_mut.src").statements[-1]
    nested = parse("nested_lambda.src").statements[1]
    function = parse("in_function.src").functions[0]
    member = parse("in_member.src").structs[0].methods[0]
    branches = parse("in_if_while.src").statements
    duplicate = parse("duplicate_capture_is_syntax.src").statements[-1]
    before = parse("global_function_before_lambda.src")
    after = parse("lambda_after_statement.src").statements
    tokens = lex((FIXTURES / "ok" / "captures_mut.src").read_bytes())
    brackets = [t for line in tokens for t in line if t.text in ("[", "]")]

    structural = all((
        isinstance(top.functions[0], FunctionNode),
        isinstance(top.statements[0], LambdaNode),
        top.statements[0].captures == [],
        isinstance(top.statements[0].body[0].value, CallNode),
        top.statements[0].body[0].value.name == "square",
        isinstance(captures, LambdaNode),
        captures.name == "mix" and captures.result_type == "i64",
        len(captures.params) == 2,
        all(isinstance(c, CaptureNode) for c in captures.captures),
        [(c.name, c.mutable) for c in captures.captures]
            == [("n", True), ("m", False), ("k", True)],
        [(c.line, c.col) for c in captures.captures]
            == [(4, 16), (4, 19), (4, 26)],
        isinstance(function.body[0], LambdaNode),
        isinstance(member, MemberFnNode),
        isinstance(member.body[0], LambdaNode),
        isinstance(branches[1].body.statements[0], LambdaNode),
        isinstance(branches[1].else_block.statements[0], LambdaNode),
        isinstance(branches[2].body.statements[0], LambdaNode),
        isinstance(nested, LambdaNode),
        isinstance(nested.body[0], LambdaNode),
        isinstance(nested.body[1], LambdaNode),
        nested.body[1].captures[0].name == "inner",
        [c.name for c in duplicate.captures] == ["k", "k"],
        len(before.functions) == 1,
        isinstance(before.statements[-1], LambdaNode),
        isinstance(after[2], LambdaNode),
        [(t.text, t.kind) for t in brackets]
            == [("[", "punctuation"), ("]", "punctuation")],
    ))
    if structural:
        passed += 1
        print("PASS lexer, captures, AST nodes, nesting, and original Call nodes")
    else:
        failed += 1
        print("FAIL lexer, captures, AST nodes, nesting, or original Call nodes")

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "output.ll"
        result = cli(FIXTURES / "ok" / "top_empty.src", output)
        if (result.returncode == 0 and not result.stderr and output.exists()
                and 'lambda.no_captures_call' in output.read_text(encoding='utf-8')):
            passed += 1
            print("PASS parser fixture also lowers to a callable struct")
        else:
            failed += 1
            print(f"FAIL parser compilation: {result.returncode} {result.stderr!r}")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(verify())
