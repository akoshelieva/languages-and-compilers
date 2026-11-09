"""Focused checks for Practice 8's additional mutable-capture semantics."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import sys

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (CodeGen, LambdaNode, MemberCallNode, Parser,
                          SemanticChecker, lex)


def parse(name):
    path = ROOT / "tests" / "ok" / (name + ".txt")
    return Parser(lex(path.read_bytes())).parse_program()


def ast(tree):
    output = StringIO()
    with redirect_stdout(output):
        tree.dump()
    return output.getvalue()


def verify():
    tree = parse("p8_mut_pdf")
    before = ast(tree)
    assert "Capture n mut" in before and "Capture next mut" in before
    assert "Struct lambda.next" not in before

    checker = SemanticChecker()
    tree.accept(checker)
    after = ast(tree)
    assert "Field n i32 mut" in after
    assert "Decl next lambda.next mut" in after
    assert "Field next lambda.next mut" in after
    assert "Decl twice lambda.twice mut" in after
    assert "CallableCall next" in after and "CallableCall twice" in after
    names = [node for node in tree.statements if isinstance(node, LambdaNode)]
    next_lambda, twice_lambda = names
    assert next_lambda.synthetic_struct.methods[0].mutating
    # 'twice' changes its captured 'next' only via a member call.
    assert twice_lambda.synthetic_struct.methods[0].mutating
    assert next_lambda.synthetic_struct.fields[0].mutable
    assert twice_lambda.synthetic_struct.fields[0].mutable
    assert isinstance(tree.statements[2].argument, MemberCallNode)

    text = str(tree.accept(CodeGen()))
    llvm.parse_assembly(text).verify()
    fragments = (
        '%"lambda.next" = type {i32}',
        '%"lambda.twice" = type {%"lambda.next"}',
        'store i32 %"n.value", i32* %"next.0.ptr"',
        'store %"lambda.next" %"next.value", %"lambda.next"* %"twice.0.ptr"',
        'define i32 @"lambda.next_call"',
        'define i32 @"lambda.twice_call"',
        'store i32 %".3", i32* %"this.n.ptr.1"',
        'call i32 @"lambda.next_call"(%"lambda.next"* %"this.next.ptr")',
    )
    for fragment in fragments:
        assert fragment in text, fragment
    main_ir = text.split('define i32 @"main"()', 1)[1].split('declare i32 @"printf"', 1)[0]
    # Mutations must target the closure field, not the original local.
    assert len(re.findall(r'store i32 .*?, i32\* %"n"(?:\n|$)', main_ir)) == 1
    print("PASS PDF mutable-closure AST, transitive mutation and LLVM copies")

    positives = (
        "p8_mut_const_source", "p8_mut_independent", "p8_mut_mixed",
        "p8_mut_loop", "p8_mut_nested", "p8_mut_capture_const_struct",
        "p8_mut_member_scope", "p8_mut_nested_from_const",
    )
    for name in positives:
        program = parse(name)
        program.accept(SemanticChecker())
        llvm.parse_assembly(str(program.accept(CodeGen()))).verify()
        print("PASS", name, "valid LLVM IR")

    negatives = (
        "p8_mut_assign_const_mixed", "p8_mut_const_lambda_copy",
        "p8_mut_const_mixed_field", "p8_mut_const_struct_copy",
        "p8_mut_nested_const_copy", "p8_mut_transitive_const_copy",
        "p8_mut_duplicate_capture", "p8_mut_unknown_capture",
    )
    from src.compiler import CompileError
    for name in negatives:
        path = ROOT / "tests" / "err" / (name + ".txt")
        wanted = path.with_suffix('.expected').read_text().strip()
        try:
            Parser(lex(path.read_bytes())).parse_program().accept(SemanticChecker())
        except CompileError as error:
            assert f"compilation error: {error}" == wanted, name
            assert re.search(r'line \d+:\d+:', wanted), wanted
        else:
            raise AssertionError(f"Accepted {name}")
        print("PASS", name, "source-located diagnostic")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
