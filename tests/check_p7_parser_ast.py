



from pathlib import Path
import difflib

import llvmlite.binding as llvm
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (
    BinOpNode, CallNode, DeclNode, FieldAccessNode, FunctionNode,
    MemberCallNode, MemberFnNode, ParamNode, Parser, VarNode, lex,
)

FIXTURES = ROOT / "tests" / "p7_parser"


def cli(*arguments):
    return subprocess.run(
        [sys.executable, str(ROOT / "compiler.py"), *map(str, arguments)],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )


def compare(expected, actual):
    return "".join(difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile="expected", tofile="actual",
    ))


def verify():
    passed = failed = 0
    for source in sorted((FIXTURES / "ok").glob("*.src")):
        expected = source.with_suffix(".ast").read_text(encoding="utf-8")
        result = cli("--ast", source)
        error = (f"exit status: {result.returncode}; stderr: {result.stderr}\n"
                 + compare(expected, result.stdout)) if (result.returncode or
                       result.stderr or result.stdout != expected) else None
        if error:
            failed += 1
            print(f"FAIL AST {source.name}: {error}")
        else:
            passed += 1
            print(f"PASS AST {source.name}")

    for source in sorted((FIXTURES / "err").glob("*.src")):
        expected = source.with_suffix(".err").read_text(encoding="utf-8")
        result = cli("--ast", source)
        error = (f"exit status: {result.returncode}; stdout: {result.stdout}\n"
                 + compare(expected, result.stderr)) if (result.returncode != 1 or
                       result.stdout or result.stderr != expected) else None
        if error:
            failed += 1
            print(f"FAIL ERR {source.name}: {error}")
        else:
            passed += 1
            print(f"PASS ERR {source.name}")


    task1 = Parser(lex((FIXTURES / "ok" / "task1_pdf.src").read_bytes())).parse_program()
    point, circle = task1.structs
    declarations = {node.name: node for node in task1.statements if isinstance(node, DeclNode)}
    cd = declarations["cd"].init
    mixed = Parser(lex((FIXTURES / "ok" / "mixed_calls.src").read_bytes())).parse_program()
    a = next(node for node in mixed.statements if isinstance(node, DeclNode) and node.name == "a")
    b = next(node for node in mixed.statements if isinstance(node, DeclNode) and node.name == "b")
    structure_ok = all((
        len(point.fields) == 2 and len(point.methods) == 2,
        all(isinstance(node, MemberFnNode) for node in point.methods),
        isinstance(point.methods[1].params[0], ParamNode),
        point.methods[1].params[0].name == "dx",
        isinstance(circle.methods[0], MemberFnNode),
        isinstance(cd, MemberCallNode) and cd.name == "d2",
        isinstance(cd.receiver, FieldAccessNode) and cd.receiver.name == "center",
        isinstance(cd.receiver.base, VarNode) and cd.receiver.base.name == "c",
        isinstance(a.init, MemberCallNode) and len(a.init.args) == 2,
        isinstance(a.init.args[0], CallNode),
        isinstance(a.init.args[1], MemberCallNode),
        isinstance(b.init, CallNode) and isinstance(b.init.args[0], MemberCallNode),
        isinstance(mixed.functions[0], FunctionNode),
        isinstance(mixed.exit_node.value, MemberCallNode),
        isinstance(mixed.exit_node.value.args[0], VarNode),
    ))
    if structure_ok:
        passed += 1
        print("PASS structural AST nodes, receivers and nested call arguments")
    else:
        failed += 1
        print("FAIL structural AST nodes, receivers and nested call arguments")


    for source in sorted((ROOT / "tests").rglob("*.txt")):
        golden = source.with_suffix(".ast")
        if not golden.exists():
            continue
        expected = golden.read_text(encoding="utf-8")
        result = cli("--ast", source)
        error = (f"exit status: {result.returncode}; stderr: {result.stderr}\n"
                 + compare(expected, result.stdout)) if (result.returncode or
                       result.stderr or result.stdout != expected) else None
        if error:
            failed += 1
            print(f"FAIL LEGACY AST {source.name}: {error}")
        else:
            passed += 1
            print(f"PASS LEGACY AST {source.name}")



    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp) / "output.ll"
        result = cli(FIXTURES / "ok" / "task1_pdf.src", output)
        try:
            assert result.returncode == 0 and not result.stderr and output.exists(), result.stderr
            llvm.parse_assembly(output.read_text(encoding="utf-8")).verify()
            passed += 1
            print("PASS Task 1 PDF program: compile and LLVM verifier")
        except Exception as exc:
            failed += 1
            print("FAIL Task 1 PDF compile or LLVM verifier:", exc)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(verify())
