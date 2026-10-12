from pathlib import Path
import subprocess
import sys
import tempfile

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]


def compile_ir(name):
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'output.ll'
        source = ROOT / 'tests' / 'ok' / (name + '.txt')
        result = subprocess.run(
            [sys.executable, str(ROOT / 'compiler.py'), str(source), str(output)],
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, f'{name}: {result.stderr}'
        ir_text = output.read_text(encoding='utf-8')
        llvm.parse_assembly(ir_text).verify()
        return ir_text


def main():
    ring = compile_ir('p6_nested_initializer_ring')
    assert '%"Point" = type {i32, i32}' in ring
    assert '%"Circle" = type {%"Point", i64}' in ring
    assert '%"Ring" = type {%"Circle", %"Circle"}' in ring
    assert 'insertvalue %"Point"' in ring
    assert 'insertvalue %"Circle"' in ring
    assert ring.count('insertvalue ') >= 10
    assert 'getelementptr inbounds %"Ring"' in ring

    deep = compile_ir('add_deep_four_levels_42')
    assert '%"Holder" = type {%"Ring", i32}' in deep
    for name in ('Point', 'Circle', 'Ring'):
        assert f'insertvalue %"{name}"' in deep
    assert deep.count('insertvalue ') >= 10

    wide = compile_ir('add_widen_nested')
    assert 'sext i32' in wide
    assert 'insertvalue %"Info"' in wide

    calls = compile_ir('add_calls_in_nested')
    assert 'call i32 @"user_inc"' in calls
    assert 'insertvalue %"Point"' in calls

    returned = compile_ir('add_nested_in_function_result')
    assert 'define %"Circle" @"user_make"' in returned
    assert 'insertvalue %"Point"' in returned
    assert 'ret %"Circle"' in returned

    print('PASS: nested init IR — PDF Ring example, deep aggregates, numeric '
          'widening, nested calls, struct-returning functions')


if __name__ == '__main__':
    main()
