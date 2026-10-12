from pathlib import Path
import subprocess
import sys
import tempfile

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]


def generate(test_name):
    source = ROOT / 'tests' / 'ok' / (test_name + '.txt')
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'output.ll'
        proc = subprocess.run(
            [sys.executable, str(ROOT / 'compiler.py'), str(source), str(output)],
            capture_output=True, text=True, timeout=20
        )
        assert proc.returncode == 0, f'{test_name}: {proc.stderr}'
        ir_text = output.read_text(encoding='utf-8')
        llvm.parse_assembly(ir_text).verify()
        return ir_text


def main():
    chain = generate('t3_deep_chain_write')
    assert '%"Wrapper" = type {%"Circle", i32}' in chain
    assert 'getelementptr inbounds %"Wrapper"' in chain
    assert 'getelementptr inbounds %"Circle"' in chain
    assert 'getelementptr inbounds %"Point"' in chain
    assert 'i32 0, i32 1' in chain
    assert 'store i32 31' in chain
    assert 'store i32 9' in chain

    temp = generate('t3_result_field_deep_chain')
    assert 'call %"Circle" @"user_newCircle"' in temp
    assert 'alloca %"Circle"' in temp
    assert 'getelementptr inbounds %"Circle"' in temp
    assert 'getelementptr inbounds %"Point"' in temp

    call = generate('t3_struct_result_nested_argument')
    assert 'call %"Point" @"user_make"' in call
    assert 'call i32 @"user_sum"' in call
    assert 'define %"Point" @"user_make"' in call

    wide = generate('t3_implicit_widen_call_arg')
    assert 'sext i32' in wide
    assert 'call i64 @"user_plus"' in wide

    print('PASS: Task 3 IR: nested GEP, mutable writes, temporary fields, '
          'struct-return calls, nested calls, numeric widening')


if __name__ == '__main__':
    main()
