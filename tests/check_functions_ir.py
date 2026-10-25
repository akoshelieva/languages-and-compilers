from pathlib import Path
import subprocess
import sys
import tempfile

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]


def ir_for(name):
    with tempfile.TemporaryDirectory() as tmp:
        src = ROOT / 'tests' / 'ok' / (name + '.txt')
        dest = Path(tmp) / 'test.ll'
        result = subprocess.run(
            [sys.executable, str(ROOT / 'compiler.py'), str(src), str(dest)],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        ir_text = dest.read_text()
        llvm.parse_assembly(ir_text).verify()
        return ir_text


def main():
    sample = ir_for('p6_full_program_28')
    assert 'define i32 @"user_add"' in sample
    assert 'define i1 @"user_isXZero"' in sample
    assert 'define %"Point" @"user_moved"' in sample
    assert 'call i32 @"user_add"' in sample
    assert 'call %"Point" @"user_moved"' in sample
    assert 'getelementptr inbounds %"Circle"' in sample
    assert 'getelementptr inbounds %"Point"' in sample
    assert 'ret %"Point"' in sample

    recurse = ir_for('p6_fib_recursive')
    assert 'define i32 @"user_fib"' in recurse
    assert recurse.count('call i32 @"user_fib"') >= 3
    assert 'br i1' in recurse

    nested = ir_for('p6_nested_initializer_ring')
    assert 'insertvalue %"Point"' in nested
    assert 'insertvalue %"Circle"' in nested

    names = ir_for('p6_fn_named_main_printf')
    assert 'define i32 @"user_main"' in names
    assert 'define i32 @"user_printf"' in names

    print('PASS: LLVM function defs, calls, recursion, returns, indexed GEP and insertvalue')


if __name__ == '__main__':
    main()
