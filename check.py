#!/usr/bin/env python3
from pathlib import Path
import contextlib
import difflib
import io
import shutil
import subprocess
import sys
import tempfile

from src import compiler as compiler_module


ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"


def run(command, cwd=ROOT):
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )



def run_compiler(args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        returncode = compiler_module.main([str(arg) for arg in args])
    return subprocess.CompletedProcess(
        args=[str(arg) for arg in args],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )

def diff_text(expected, actual, expected_name, actual_name):
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=expected_name,
            tofile=actual_name,
        )
    )


def execute_ir(ir_path):
    lli = shutil.which("lli")
    if lli:
        return run([lli, ir_path])

    clang = shutil.which("clang")
    if clang:
        executable = ir_path.with_suffix("")
        compiled = run([clang, ir_path, "-o", executable])
        if compiled.returncode != 0:
            return compiled
        try:
            return run([executable])
        finally:
            try:
                executable.unlink()
            except FileNotFoundError:
                pass

    return subprocess.CompletedProcess(
        args=["lli"],
        returncode=127,
        stdout="",
        stderr="neither lli nor clang is available to execute LLVM IR\n",
    )


def golden_for_source(source):
    stem = source.with_suffix("")
    for suffix in (".expected", ".out", ".err"):
        candidate = Path(str(stem) + suffix)
        if candidate.exists():
            return candidate
    return None


def test_program(source, golden):
    expected = golden.read_text(encoding="utf-8")
    expects_error = golden.suffix == ".err" or expected.startswith("compilation error:")

    with tempfile.TemporaryDirectory(prefix="practice5-") as temp_dir:
        output = Path(temp_dir) / "output.ll"
        compiled = run_compiler([source, output])

        if expects_error:
            if compiled.returncode == 0:
                return False, "compiler accepted an invalid program\n"
            if output.exists():
                return False, "compiler left LLVM IR for an invalid program\n"
            actual = compiled.stderr
        else:
            if compiled.returncode != 0:
                return False, "compiler rejected a valid program:\n" + compiled.stderr
            if compiled.stderr:
                return False, "compiler wrote unexpected stderr:\n" + compiled.stderr
            if not output.exists():
                return False, "compiler did not create LLVM IR\n"
            executed = execute_ir(output)
            if executed.returncode != 0:
                return False, "LLVM execution failed:\n" + executed.stderr
            actual = executed.stdout

    if actual != expected:
        return False, diff_text(expected, actual, str(golden), "actual")
    return True, ""


def test_ast(source, golden):
    expected = golden.read_text(encoding="utf-8")
    result = run_compiler(["--ast", source])
    if result.returncode != 0:
        return False, "--ast failed:\n" + result.stderr
    if result.stderr:
        return False, "--ast wrote unexpected stderr:\n" + result.stderr
    if result.stdout != expected:
        return False, diff_text(expected, result.stdout, str(golden), "actual AST")
    return True, ""


def test_tokens(source, golden):
    expected = golden.read_text(encoding="utf-8")
    before = source.read_bytes()
    result = run([sys.executable, ROOT / "compiler.py", "--tokens", source])
    after = source.read_bytes() if source.exists() else None

    if result.returncode != 0:
        return False, "--tokens failed:\n" + result.stderr
    if after != before:
        return False, "--tokens modified or deleted its input file\n"
    if result.stderr:
        return False, "--tokens wrote unexpected stderr:\n" + result.stderr
    if result.stdout != expected:
        return False, diff_text(expected, result.stdout, str(golden), "actual tokens")
    return True, ""


def collect_cases():
    cases = []
    for source in sorted(TESTS.rglob("*.txt")):
        golden = golden_for_source(source)
        if golden is not None:
            cases.append(("program", source, golden))

        ast = source.with_suffix(".ast")
        if ast.exists():
            cases.append(("ast", source, ast))

        tokens = source.with_suffix(".tokens")
        if tokens.exists():
            cases.append(("tokens", source, tokens))
    return cases


def main():
    cases = collect_cases()
    failures = 0

    print(f"{'KIND':8} {'RESULT':6} TEST")
    print("-" * 72)
    for kind, source, golden in cases:
        if kind == "program":
            passed, details = test_program(source, golden)
        elif kind == "ast":
            passed, details = test_ast(source, golden)
        else:
            passed, details = test_tokens(source, golden)

        relative = source.relative_to(ROOT)
        print(f"{kind:8} {'PASS' if passed else 'FAIL':6} {relative}")
        if not passed:
            failures += 1
            if details:
                print(details.rstrip())

    print("-" * 72)
    print(f"{len(cases) - failures} passed, {failures} failed, {len(cases)} total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
