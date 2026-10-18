
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
    Parser, SemanticChecker, CodeGen, lex, PrintNode, StringNode, CallNode,
)

OK = ROOT / 'tests' / 'ok'
ERR = ROOT / 'tests' / 'err'


def run_binary(command, stdin=b''):
    return subprocess.run(command, cwd=ROOT, input=stdin,
                          capture_output=True, timeout=20)


def check_entry_allocas(ir_text):
    current_block = None
    in_function = False
    for line in ir_text.splitlines():
        text = line.strip()
        if text.startswith('define '):
            in_function, current_block = True, None
        elif in_function and text == '}':
            in_function, current_block = False, None
        elif in_function and text.endswith(':'):
            current_block = text[:-1].strip('"')
        elif in_function and ' alloca ' in line:
            assert current_block == 'entry', ('alloca outside entry: ' + line)


def check():
    backends = []
    if shutil.which('lli'):
        backends.append('lli')
    if shutil.which('clang'):
        backends.append('clang')
    assert backends, 'No lli or clang available'
    passed = failed = 0
    for path in sorted(list(OK.glob('p7_*.txt')) +
                       list((ROOT / 'tests' / 'p7_io' / 'ok').glob('p7_*.txt'))):
        try:
            tree = Parser(lex(path.read_bytes())).parse_program()
            tree.accept(SemanticChecker())
            ir_text = str(tree.accept(CodeGen()))
            llvm.parse_assembly(ir_text).verify()
            check_entry_allocas(ir_text)
            assert len(re.findall(r'declare i32 @"printf"', ir_text)) == 1
            assert len(re.findall(r'declare i32 @"scanf"', ir_text)) == 1
            expected = path.with_suffix('.expected').read_bytes()
            input_path = path.with_suffix('.in')
            data = input_path.read_bytes() if input_path.exists() else b''
            with tempfile.TemporaryDirectory() as temp:
                ll = Path(temp) / 'program.ll'
                ll.write_text(ir_text, encoding='utf-8')
                for backend in backends:
                    if backend == 'lli':
                        proc = run_binary(['lli', str(ll)], data)
                    else:
                        exe = Path(temp) / 'program'
                        build = run_binary(['clang', str(ll), '-o', str(exe)])
                        assert build.returncode == 0, build.stderr.decode(errors='replace')
                        proc = run_binary([str(exe)], data)
                    assert proc.returncode == 0, proc.stderr.decode(errors='replace')
                    assert proc.stdout == expected, (
                        f'{backend}: expected {expected!r}, got {proc.stdout!r}')
                    assert not proc.stderr, proc.stderr
            if path.stem == 'p7_read_print_pdf':
                assert 'call i32 (i8*, ...) @"scanf"' in ir_text
                assert 'alloca i32' in ir_text
                assert 'getelementptr' in ir_text
            if path.stem == 'p7_print_raw_bytes':
                assert b'A\x00B\xff%' in expected
                assert 'fmt_print_char' in ir_text
            if path.stem == 'p7_print_every_kind':
                assert any(isinstance(s, PrintNode) and
                           isinstance(s.argument, StringNode) for s in tree.statements)
            if path.stem == 'p7_exit_read':
                assert isinstance(tree.exit_node.value, CallNode)
                assert tree.exit_node.value.name == 'read'
            print('PASS OK', path.name)
            passed += 1
        except Exception as exc:
            print('FAIL OK', path.name, repr(exc))
            failed += 1
    for path in sorted(ERR.glob('p7_*.txt')):
        try:

            with tempfile.TemporaryDirectory() as temp:
                ll = Path(temp) / 'output.ll'
                result = run_binary([sys.executable, str(ROOT/'compiler.py'),
                                     str(path), str(ll)])
                expected = path.with_suffix('.expected').read_bytes()
                assert result.returncode == 1, result.returncode
                assert not ll.exists(), 'generated IR for an invalid program'
                assert result.stdout == b'', result.stdout
                assert result.stderr == expected, (result.stderr, expected)
                assert re.search(rb'compilation error: line \d+:\d+:', result.stderr)
            print('PASS ERR', path.name)
            passed += 1
        except Exception as exc:
            print('FAIL ERR', path.name, repr(exc))
            failed += 1
    print(f'Practice 7 Task 3: {passed} passed, {failed} failed; backends={backends}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(check())
