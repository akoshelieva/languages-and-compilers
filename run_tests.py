from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import llvmlite.binding as llvm

from compiler import CodeGen, CompileError, Parser, SemanticChecker, lex

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
TIMEOUT = 15


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          timeout=TIMEOUT)


def run_binary(cmd, stdin=None):


    return subprocess.run(cmd, cwd=ROOT, capture_output=True, input=stdin,
                          timeout=TIMEOUT)


def compile_source(source, generate_ir):
    try:
        tree = Parser(lex(source.read_bytes())).parse_program()
        tree.accept(SemanticChecker())
        if generate_ir:
            return str(tree.accept(CodeGen())), None
        return None, None
    except CompileError as exc:
        return None, f"compilation error: {exc}"


def execute_ir(ir_path, tmpdir, runner, stdin=None):
    if runner == "lli":
        return run_binary(["lli", str(ir_path)], stdin=stdin)
    executable = tmpdir / "program"
    build = run(["clang", str(ir_path), "-o", str(executable)])
    if build.returncode != 0:
        return build
    return run_binary([str(executable)], stdin=stdin)


def run_positive(source, runner):
    expected = source.with_suffix(".expected").read_bytes()
    ir_text, error = compile_source(source, generate_ir=True)
    if error is not None:
        return "compiler rejected valid program: " + error
    try:
        llvm.parse_assembly(ir_text).verify()
    except Exception as exc:
        return f"invalid LLVM IR: {exc}"
    with tempfile.TemporaryDirectory() as directory:
        tmpdir = Path(directory)
        ir_path = tmpdir / "output.ll"
        ir_path.write_text(ir_text, encoding="utf-8")
        input_file = source.with_suffix(".in")
        stdin = input_file.read_bytes() if input_file.exists() else None
        result = execute_ir(ir_path, tmpdir, runner, stdin=stdin)
        if result.returncode != 0:
            return f"{runner} execution failed: {result.stderr.decode(errors='replace').strip()}"
        actual = result.stdout
        if actual != expected:
            return f"expected {expected!r}, got {actual!r}"
    return None


def run_negative(source):
    expected = source.with_suffix(".expected").read_text(encoding="utf-8").strip()
    ir_text, error = compile_source(source, generate_ir=False)
    if error is None:
        return "compiler accepted invalid program"
    if error != expected:
        return f"expected {expected!r}, got {error!r}"
    return None


def cli_smoke_check():
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'output.ll'
        src_ok = TESTS / 'ok' / 'p6_full_program_28.txt'
        good = run([sys.executable, str(ROOT / 'compiler.py'), str(src_ok), str(output)])
        if good.returncode != 0 or not output.exists():
            return f'CLI positive smoke test failed: {good.stderr}'
        llvm.parse_assembly(output.read_text(encoding='utf-8')).verify()
        src_err = TESTS / 'err' / 't3_unknown_field_after_chain.txt'
        bad = run([sys.executable, str(ROOT / 'compiler.py'), str(src_err), str(output)])
        if bad.returncode == 0 or output.exists():
            return 'CLI negative smoke test failed'
    return None


def main():
    if shutil.which("lli"):
        runner = "lli"
    elif shutil.which("clang"):
        runner = "clang"
    else:
        print("Neither lli nor clang installed; cannot execute positive tests", file=sys.stderr)
        return 1
    print(f"LLVM execution backend: {runner}", flush=True)
    passed = failed = 0
    for folder, test_method in (("ok", lambda p: run_positive(p, runner)),
                                ("err", run_negative)):
        tests = sorted((TESTS / folder).glob("*.txt"))
        print(f"\n=== tests/{folder}: {len(tests)} programs ===", flush=True)
        for src in tests:
            try:
                error = test_method(src)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                error = f"test infrastructure error: {exc}"
            if error is None:
                print("PASS", src.name, flush=True)
                passed += 1
            else:
                print("FAIL", src.name, "\n ", error, flush=True)
                failed += 1
    smoke = cli_smoke_check()
    if smoke:
        failed += 1
        print("FAIL CLI smoke test:", smoke, flush=True)
    else:
        passed += 1
        print("PASS CLI smoke test", flush=True)
    print(f"\n{passed} passed, {failed} failed", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
