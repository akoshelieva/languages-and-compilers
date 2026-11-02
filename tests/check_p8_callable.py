from pathlib import Path
import re
import sys

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (CallNode, CodeGen, FieldAccessNode, MemberCallNode, VarNode,
                          Parser, SemanticChecker, lex)


def main():
    source = ROOT / 'tests' / 'ok' / 'p8_callable_pdf_task1.txt'
    tree = Parser(lex(source.read_bytes())).parse_program()
    direct = tree.statements[3].argument
    explicit = tree.statements[4].argument
    field = tree.statements[5].argument
    inside = tree.structs[2].methods[0].body[0].init
    assert isinstance(direct, CallNode)
    assert isinstance(explicit, MemberCallNode)
    assert isinstance(field, MemberCallNode)
    assert field.name == 'scale'
    assert isinstance(inside, CallNode)

    tree.accept(SemanticChecker())
    for call in (direct, field, inside):
        assert isinstance(call, MemberCallNode) and call.name == 'call'
        assert call.function.owner.name == 'Scale'
    assert isinstance(direct.receiver, VarNode)
    assert isinstance(field.receiver, FieldAccessNode)
    assert field.receiver.field.name == 'scale'
    assert explicit.name == 'call'
    assert tree.structs[1].method_map['call'].mutating
    assert not tree.structs[0].method_map['call'].mutating
    assert not tree.structs[2].method_map['grown'].mutating

    ir_text = str(tree.accept(CodeGen()))
    llvm.parse_assembly(ir_text).verify()
    assert len(re.findall(r'call i32 @"Scale_call"', ir_text)) == 4
    assert len(re.findall(r'call i32 @"Counter_call"', ir_text)) == 2
    for line in ir_text.splitlines():
        if '@"Scale_call"' in line and 'call i32' in line:
            assert '%"Scale"*' in line

    field_source = ROOT / 'tests' / 'ok' / 'p8_callable_mutable_field.txt'
    field_tree = Parser(lex(field_source.read_bytes())).parse_program()
    field_tree.accept(SemanticChecker())
    assert field_tree.structs[1].method_map['advance'].mutating
    llvm.parse_assembly(str(field_tree.accept(CodeGen()))).verify()
    print('Task 1 AST rewrite, member calls, IR, and mutation propagation: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
