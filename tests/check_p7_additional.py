
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import llvmlite.binding as llvm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import CodeGen, Parser, SemanticChecker, lex, MemberCallNode, FieldAccessNode

FIX = ROOT/'tests'/'p7_additional'


def check_allocas(ir_text):
    block = None
    active = False
    for line in ir_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('define '):
            active, block = True, None
        elif active and stripped == '}':
            active, block = False, None
        elif active and stripped.endswith(':'):
            block = stripped[:-1].strip('"')
        elif active and re.search(r'\balloca\b', stripped):
            assert block == 'entry', f'alloca not in entry: {line}'


def run_backend(name, ir_file, stdin, folder):
    if name == 'jit':
        command = [sys.executable, str(ROOT/'tests'/'jit_run.py'), str(ir_file)]
    elif name == 'lli':
        command = ['lli', str(ir_file)]
    else:
        executable = folder/'binary'
        build = subprocess.run(['clang', str(ir_file), '-o', str(executable)],
                               capture_output=True, timeout=30)
        assert build.returncode == 0, build.stderr.decode(errors='replace')
        command = [str(executable)]
    return subprocess.run(command, input=stdin, capture_output=True, timeout=30)


def check():
    backends = ['jit'] + [name for name in ('lli', 'clang') if shutil.which(name)]
    passed=failed=0
    for source in sorted((FIX/'ok').glob('*.txt')):
        try:
            tree = Parser(lex(source.read_bytes())).parse_program()
            tree.accept(SemanticChecker())
            text_ir = str(tree.accept(CodeGen()))
            llvm.parse_assembly(text_ir).verify()
            check_allocas(text_ir)
            if source.stem in ('pdf_additional', 'nested_field_extractvalue',
                               'global_member_field_chain'):
                assert 'extractvalue' in text_ir, source.name
            if source.stem == 'pdf_additional':
                assert re.search(r'define i32 @"Point_shift"\(%"Point"\* %"this"', text_ir)
                assert 'call i32 @"Point_shift"(%"Point"* %"field.tmp"' in text_ir
                statements={stmt.name:stmt for stmt in tree.statements if hasattr(stmt,'name')}
                assert isinstance(statements['a'].init,MemberCallNode)
                assert isinstance(statements['a'].init.receiver,MemberCallNode)
                assert isinstance(statements['b'].init,FieldAccessNode)
            with tempfile.TemporaryDirectory() as directory:
                folder=Path(directory)
                ir_file=folder/'output.ll'
                ir_file.write_text(text_ir)
                inp=source.with_suffix('.in')
                stdin=inp.read_bytes() if inp.exists() else b''
                expected=source.with_suffix('.expected').read_bytes()
                for backend in backends:
                    proc=run_backend(backend, ir_file, stdin, folder)
                    assert proc.returncode == 0, f'{backend} exit {proc.returncode} {proc.stderr!r}'
                    assert proc.stdout == expected, f'{backend} {source.name}: {proc.stdout!r} != {expected!r}'
                    assert proc.stderr == b'', f'{backend} stderr: {proc.stderr!r}'
            passed+=1
            print('PASS OK',source.name)
        except Exception as exc:
            failed+=1
            print('FAIL OK',source.name,repr(exc))
    for source in sorted((FIX/'err').glob('*.txt')):
        try:
            expected=source.with_suffix('.expected').read_bytes()
            with tempfile.TemporaryDirectory() as directory:
                output=Path(directory)/'output.ll'
                proc=subprocess.run([sys.executable, str(ROOT/'compiler.py'),str(source),str(output)],
                                    capture_output=True,timeout=20)
                assert proc.returncode == 1, f'compiler accepted bad program: {source.name}'
                assert proc.stdout == b'' and proc.stderr == expected, (proc.stdout,proc.stderr,expected)
                assert not output.exists(), 'compiler produced IR on error'

                if source.stem.startswith('primitive_'):
                    lines=source.read_text().splitlines()
                    line=int(re.search(rb'line (\d+):',expected).group(1))
                    col=int(re.search(rb'line \d+:(\d+):',expected).group(1))
                    assert lines[line-1][col-1] == '.', (source.name,line,col)
            passed+=1
            print('PASS ERR',source.name)
        except Exception as exc:
            failed+=1
            print('FAIL ERR',source.name,repr(exc))
    print(f'Additional task: {passed} passed, {failed} failed; runtime backends={backends}')
    return 1 if failed else 0


if __name__=='__main__':
    sys.exit(check())
