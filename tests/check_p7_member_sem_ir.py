
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (
    CodeGen, Parser, SemanticChecker,
    lex,
)

FIX = ROOT / 'tests' / 'p7_task2'


def parse(source):
    tree = Parser(lex(source.read_bytes())).parse_program()
    tree.accept(SemanticChecker())
    return tree


def run_cli(*args):
    return subprocess.run([sys.executable, str(ROOT / 'compiler.py'),
                           *map(str, args)], cwd=ROOT, capture_output=True,
                          text=True, timeout=20)


def verify():
    runner = shutil.which('lli') or shutil.which('clang')
    if runner is None:
        raise RuntimeError('lli or clang is required for executable tests')
    passed = failed = 0
    for source in sorted((FIX/'ok').glob('*.txt')):
        try:
            tree = parse(source)
            ir_text = str(tree.accept(CodeGen()))
            llvm.parse_assembly(ir_text).verify()


            in_function = False
            current_block = None
            for ir_line in ir_text.splitlines():
                stripped = ir_line.strip()
                if stripped.startswith('define '):
                    in_function, current_block = True, None
                elif in_function and stripped == '}':
                    in_function, current_block = False, None
                elif in_function and stripped.endswith(':'):
                    current_block = stripped[:-1].strip('"')
                elif in_function and ' alloca ' in ir_line:
                    assert current_block == 'entry', (
                        f'alloca outside entry in {source.name}: {ir_line}')
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder)/'test.ll'
                output.write_text(ir_text)
                if Path(runner).name == 'lli':
                    proc = subprocess.run([runner, str(output)], capture_output=True,
                                          text=True, timeout=20)
                else:
                    exe = Path(folder)/'compiled'
                    build = subprocess.run([runner, str(output), '-o', str(exe)],
                                           capture_output=True, text=True, timeout=20)
                    if build.returncode != 0:
                        raise AssertionError(build.stderr)
                    proc = subprocess.run([str(exe)], capture_output=True,
                                          text=True, timeout=20)
                expected = source.with_suffix('.expected').read_text()
                assert proc.returncode == 0, proc.stderr
                assert proc.stdout == expected, f'{proc.stdout!r} != {expected!r}'
                assert not proc.stderr, proc.stderr
            if source.stem == 'pdf_task1':
                assert re.search(r'define i32 @"Point_shift"\(%"Point"\* %"this"', ir_text)
                body = ir_text.split('define i32 @"Point_shift"')[1].split('\n}')[0]
                for op in ('getelementptr', 'load i32', 'add i32', 'store i32'):
                    assert op in body, f'Point_shift must contain {op}'
                assert 'call i32 @"Point_shift"(%"Point"*' in ir_text
            if source.stem == 'underscore_name_collision':
                assert 'define i32 @"A_B_c"' in ir_text
                assert 'define i32 @"p7_member_1_A_3_B_c"' in ir_text
                assert 'define i32 @"user_A_B_c"' in ir_text
            if source.stem == 'local_and_param_shadow_fields':
                methods = {m.name:m for m in tree.structs[0].methods}
                assert not any(m.mutating for m in methods.values())
            if source.stem == 'forward_mutual_and_effects':
                methods = {m.name:m for m in tree.structs[0].methods}
                assert all(methods[n].mutating for n in ('leaf', 'middle', 'relay',
                                                          'even', 'odd'))
            if source.stem == 'local_copy_does_not_mutate_this':
                methods = {m.name:m for m in tree.structs[0].methods}
                assert methods['shift'].mutating and not methods['localCopy'].mutating
            passed += 1
            print(f'PASS OK {source.name}')
        except Exception as error:
            failed += 1
            print(f'FAIL OK {source.name}: {error}')
    for source in sorted((FIX/'err').glob('*.txt')):
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder)/'test.ll'
                proc = run_cli(source, output)
                assert proc.returncode == 1, f'expected reject: {proc.stdout!r}'
                assert not output.exists(), 'invalid program produced IR file'
                assert not proc.stdout, f'unexpected stdout {proc.stdout!r}'
                assert re.search(r'compilation error: line \d+:\d+:', proc.stderr), proc.stderr
                fragment = source.with_suffix('.error').read_text().strip()
                assert fragment in proc.stderr, f'expected {fragment!r}, got {proc.stderr!r}'
            passed += 1
            print(f'PASS ERR {source.name}')
        except Exception as error:
            failed += 1
            print(f'FAIL ERR {source.name}: {error}')
    print(f'\n{passed} passed, {failed} failed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(verify())
