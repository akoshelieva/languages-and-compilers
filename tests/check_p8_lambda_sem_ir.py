"""Focused Task 2 checks independent of the general executable test suite."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import subprocess
import sys

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (CallNode, CodeGen, CompileError, LambdaNode,
                          MemberCallNode, Parser, SemanticChecker, lex)


def load(name):
    return Parser(lex((ROOT / "tests" / "ok" / (name + ".txt")).read_bytes())).parse_program()


def dump(tree):
    stream = StringIO()
    with redirect_stdout(stream):
        tree.dump()
    return stream.getvalue()


def main():
    tree = load("p8_lambda_pdf")
    original = dump(tree)
    assert "Lambda add_k -> i32" in original
    assert "Capture k const" in original
    assert "Call add_k" in original
    assert "Struct lambda.add_k" not in original
    add_k = next(node for node in tree.statements if isinstance(node, LambdaNode))
    assert isinstance(tree.exit_node.value, CallNode)

    tree.accept(SemanticChecker())
    checked = dump(tree)
    assert "Struct lambda.add_k" in checked
    assert "Struct lambda.times" in checked
    assert "MemberFn call -> i32" in checked
    assert "Decl add_k lambda.add_k const" in checked
    assert "Field k i32 const" in checked
    assert "CallableCall add_k" in checked
    assert not re.search(r"(?m)^\s+Call add_k$", checked)
    assert isinstance(tree.exit_node.value, MemberCallNode)
    assert add_k.synthetic_decl.initializers[0].name == "k"
    assert add_k.synthetic_struct.fields[0].mutable is False

    ir_text = str(tree.accept(CodeGen()))
    llvm.parse_assembly(ir_text).verify()
    for fragment in ('%"lambda.add_k" = type {i32}',
                     'define i32 @"lambda.add_k_call"',
                     'getelementptr inbounds %"lambda.add_k"',
                     'store i32 %"k.value"',
                     'call i32 @"lambda.add_k_call"',
                     '%"lambda.times" = type {i32}'):
        assert fragment in ir_text, fragment
    print("PASS PDF example, original/checked AST and LLVM struct/member-call lowering")

    for name, expected_types in (
            ("p8_lambda_empty", ("lambda.f",)),
            ("p8_lambda_nested", ("lambda.outer", "lambda.inner", "lambda.copy")),
            ("p8_lambda_same_name", ("lambda.repeat", "lambda.repeat.2")),
            ("p8_lambda_loop", ("lambda.current",)),
            ("p8_lambda_member", ("lambda.local",))):
        checked_tree = load(name)
        checked_tree.accept(SemanticChecker())
        names = [s.name for s in checked_tree.generated_structs]
        assert all(name in names for name in expected_types), (name, names)
        if name == "p8_lambda_empty":
            assert checked_tree.generated_structs[0].fields == []
        ir_text = str(checked_tree.accept(CodeGen()))
        llvm.parse_assembly(ir_text).verify()
        for t in expected_types:
            assert '"' + t + '" = type' in ir_text, t
        print("PASS", name, "synthetic structures and valid LLVM IR")

    for name in ("p8_lambda_duplicate_capture", "p8_lambda_unknown_capture",
                 "p8_lambda_missing_capture", "p8_lambda_assign_const_capture",
                 "p8_lambda_param_capture_conflict", "p8_lambda_recursive",
                 "p8_lambda_pass_argument", "p8_lambda_exit_value",
                 "p8_lambda_print", "p8_lambda_assignment",
                 "p8_lambda_decl_initializer", "p8_lambda_capture_field_access"):
        source = (ROOT / "tests" / "err" / (name + ".txt")).read_bytes()
        try:
            Parser(lex(source)).parse_program().accept(SemanticChecker())
        except CompileError as error:
            assert re.match(r"line \d+:\d+: ", str(error)), str(error)
        else:
            raise AssertionError(f"invalid lambda program accepted: {name}")
    print("PASS rejected captures, recursion and lambda-as-value diagnostics")

    with __import__('tempfile').TemporaryDirectory() as folder:
        path = Path(folder) / 'lambda.txt'
        path.write_text('i32 n{2}\nfn f := [mut n]() -> i32\n{\n    exit n\n}\nexit 0\n')
        checked = subprocess.run([sys.executable, str(ROOT / "compiler.py"),
                                  "--ast-checked", str(path)],
                                 capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
        assert "Struct lambda.f" in checked.stdout
        assert "Field n i32 mut" in checked.stdout
        assert "Decl f lambda.f mut" in checked.stdout
        original = subprocess.run([sys.executable, str(ROOT / "compiler.py"),
                                   "--ast", str(path)],
                                  capture_output=True, text=True)
        assert original.returncode == 0 and "Capture n mut" in original.stdout
        assert "Struct lambda.f" not in original.stdout
    print("PASS mut syntax preserved, checked AST now lowers mutable captures")
    return 0


if __name__ == '__main__':
    sys.exit(main())
