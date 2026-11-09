import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import llvmlite.binding as llvm

from compiler import (CodeGen, CompileError, Parser, RemoveUnusedVariables,
                      SemanticChecker, lex)

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
TIMEOUT = 15


def test_cases(sources, check, jobs):
    """Execute independent fixtures concurrently; report in stable filename order."""
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        yield from zip(sources, executor.map(check, sources))


def checked(check, source):
    try:
        return check(source)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"test infrastructure error: {exc}"


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          timeout=TIMEOUT)


def run_binary(cmd, stdin=None):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, input=stdin,
                          timeout=TIMEOUT)


def compile_source(source, generate_ir, remove_unused=False):
    try:
        tree = Parser(lex(source.read_bytes())).parse_program()
        tree.accept(SemanticChecker())
        if remove_unused:
            tree.accept(RemoveUnusedVariables())
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


def run_positive(source, runner, remove_unused=False):
    expected = source.with_suffix(".expected").read_bytes()
    ir_text, error = compile_source(source, generate_ir=True,
                                    remove_unused=remove_unused)
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


def run_negative(source, remove_unused=False):
    expected = source.with_suffix(".expected").read_text(encoding="utf-8").strip()
    ir_text, error = compile_source(source, generate_ir=False,
                                    remove_unused=remove_unused)
    if error is None:
        return "compiler accepted invalid program"
    if error != expected:
        return f"expected {expected!r}, got {error!r}"
    return None



def run_native_program(source, remove_unused=False, native_backend="llc",
                       c_source=None):
    """Build one .txt into a native binary and compare each input session."""
    sessions = sorted(source.parent.glob(source.stem + ".*.in"))
    if not sessions:
        return [(source.name, "no input sessions found")]
    if c_source is not None and not c_source.is_file():
        return [(source.name, f"missing C source: {c_source.name}")]
    flags = ["--remove-unused"] if remove_unused else []
    with tempfile.TemporaryDirectory() as directory:
        tmpdir = Path(directory)
        ir_path = tmpdir / (source.stem + ".ll")
        obj_path = tmpdir / (source.stem + ".o")
        executable = tmpdir / source.stem
        compile_result = run([sys.executable, str(ROOT / "compiler.py"),
                              *flags, str(source), str(ir_path)])
        if compile_result.returncode != 0:
            return [(source.name, "compiler failed: " + compile_result.stderr.strip())]
        try:
            llvm.parse_assembly(ir_path.read_text(encoding="utf-8")).verify()
        except Exception as exc:
            return [(source.name, f"invalid LLVM IR: {exc}")]

        if native_backend == "llc":
            object_cmd = ["llc", "-filetype=obj", "-relocation-model=pic",
                          str(ir_path), "-o", str(obj_path)]
        else:
            # Explicit alternative for installations without the llc executable.
            object_cmd = ["clang", "-c", "-fPIC", str(ir_path), "-o", str(obj_path)]
        object_result = run(object_cmd)
        if object_result.returncode != 0:
            return [(source.name, "object generation failed: " + object_result.stderr.strip())]
        link_cmd = ["clang", "-fPIE", str(obj_path)]
        if c_source is not None:
            link_cmd.append(str(c_source))
        link_result = run([*link_cmd, "-o", str(executable)])
        if link_result.returncode != 0:
            return [(source.name, "link failed: " + link_result.stderr.strip())]

        results = []
        for session in sessions:
            expected_path = session.with_suffix(".expected")
            if not expected_path.is_file():
                results.append((session.name, f"missing {expected_path.name}"))
                continue
            result = run_binary([str(executable)], stdin=session.read_bytes())
            if result.returncode != 0:
                results.append((session.name, f"binary exited with {result.returncode}: "
                                + result.stderr.decode(errors="replace")))
                continue
            expected = expected_path.read_bytes()
            if result.stdout != expected:
                results.append((session.name,
                                f"expected {expected!r}, got {result.stdout!r}"))
            else:
                results.append((session.name, None))
    return results


def run_io_program(source, remove_unused=False, native_backend="llc"):
    return run_native_program(source, remove_unused=remove_unused,
                              native_backend=native_backend)


def run_ffi_program(source, remove_unused=False, native_backend="llc"):
    return run_native_program(source, remove_unused=remove_unused,
                              native_backend=native_backend,
                              c_source=source.with_suffix(".c"))


def run_optimized_fixture(source, runner):
    """Check execution in both modes and the optimized checked-AST golden."""
    for suffix in (".expected", ".astc"):
        if not source.with_suffix(suffix).exists():
            return f"missing {suffix} golden"
    for optimized in (False, True):
        error = run_positive(source, runner, remove_unused=optimized)
        if error is not None:
            return f"{'optimized' if optimized else 'original'}: {error}"

    result = run([sys.executable, str(ROOT / "compiler.py"), "--remove-unused",
                  "--ast-checked", str(source)])
    if result.returncode != 0:
        return "optimized checked AST failed: " + result.stderr
    expected_ast = source.with_suffix(".astc").read_text(encoding="utf-8")
    if result.stdout != expected_ast:
        return f"optimized checked AST differs from {source.with_suffix('.astc').name}"

    # --ast is deliberately the original parse tree, regardless of optimization.
    raw = run([sys.executable, str(ROOT / "compiler.py"), "--ast", str(source)])
    raw_opt = run([sys.executable, str(ROOT / "compiler.py"), "--remove-unused",
                   "--ast", str(source)])
    if raw.returncode or raw_opt.returncode or raw.stdout != raw_opt.stdout:
        return "--remove-unused changed plain --ast"

    if source.stem == "no_optimization":
        checked = run([sys.executable, str(ROOT / "compiler.py"), "--ast-checked",
                       str(source)])
        if checked.returncode or checked.stdout != expected_ast:
            return "no_optimization changed checked AST"
    return None


def cli_smoke_check(remove_unused=False):
    flags = ["--remove-unused"] if remove_unused else []
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'output.ll'
        src_ok = TESTS / 'ok' / 'p6_full_program_28.txt'
        good = run([sys.executable, str(ROOT / 'compiler.py'), *flags,
                    str(src_ok), str(output)])
        if good.returncode != 0 or not output.exists():
            return f'CLI positive smoke test failed: {good.stderr}'
        llvm.parse_assembly(output.read_text(encoding='utf-8')).verify()
        src_err = TESTS / 'err' / 't3_unknown_field_after_chain.txt'
        bad = run([sys.executable, str(ROOT / 'compiler.py'), *flags,
                   str(src_err), str(output)])
        if bad.returncode == 0 or output.exists():
            return 'CLI negative smoke test failed'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run compiler regression and native I/O tests")
    parser.add_argument("--remove-unused", action="store_true",
                        help="run the optimized mode only (default: both modes)")
    parser.add_argument("--native-backend", choices=("llc", "clang"), default="llc",
                        help="object-file generator for tests/io and tests/ffi (default: llc)")
    parser.add_argument("--jobs", type=int, default=4,
                        help="number of independent tests to execute concurrently (default: 4)")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if not shutil.which(args.native_backend):
        print(f"Missing {args.native_backend}; install it or select an available "
              "native backend explicitly with --native-backend", file=sys.stderr)
        return 1
    if not shutil.which("clang"):
        print("clang is required for native linking", file=sys.stderr)
        return 1
    if shutil.which("lli"):
        runner = "lli"
    elif shutil.which("clang"):
        runner = "clang"
    else:
        print("Neither lli nor clang installed; cannot execute positive tests", file=sys.stderr)
        return 1
    print(f"LLVM execution backend: {runner}; native object backend: {args.native_backend}",
          flush=True)
    passed = failed = 0
    for optimized in ((True,) if args.remove_unused else (False, True)):
        mode = "--remove-unused" if optimized else "without optimization"
        for folder, test_method in (("ok", run_positive), ("err", run_negative)):
            tests = sorted((TESTS / folder).glob("*.txt"))
            print(f"\n=== tests/{folder} ({mode}): {len(tests)} programs ===", flush=True)
            def check_source(src):
                return checked(lambda source: (
                    test_method(source, runner, remove_unused=optimized)
                    if folder == "ok" else test_method(source, remove_unused=optimized)), src)

            for src, error in test_cases(tests, check_source, args.jobs):
                if error is None:
                    print("PASS", src.name, flush=True)
                    passed += 1
                else:
                    print("FAIL", src.name, "\n ", error, flush=True)
                    failed += 1
        for folder, native_test in (("io", run_io_program),
                                    ("ffi", run_ffi_program)):
            native_tests = sorted((TESTS / folder).glob("*.txt"))
            print(f"\n=== tests/{folder} ({mode}): {len(native_tests)} programs ===",
                  flush=True)

            def check_native(src):
                try:
                    return native_test(src, remove_unused=optimized,
                                       native_backend=args.native_backend)
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    return [(src.name, f"test infrastructure error: {exc}")]

            for src, results in test_cases(native_tests, check_native, args.jobs):
                for session_name, error in results:
                    label = f"{src.name}: {session_name}"
                    if error is None:
                        print("PASS", label, flush=True)
                        passed += 1
                    else:
                        print("FAIL", label, "\n ", error, flush=True)
                        failed += 1
        smoke = cli_smoke_check(remove_unused=optimized)
        if smoke:
            failed += 1
            print(f"FAIL CLI smoke test ({mode}): {smoke}", flush=True)
        else:
            passed += 1
            print(f"PASS CLI smoke test ({mode})", flush=True)

    opt_tests = sorted((TESTS / "opt").glob("*.txt"))
    print(f"\n=== tests/opt: {len(opt_tests)} programs (both modes + .astc) ===",
          flush=True)
    for src, error in test_cases(opt_tests,
                                 lambda src: checked(lambda s: run_optimized_fixture(s, runner), src),
                                 args.jobs):
        if error is None:
            print("PASS", src.name, flush=True)
            passed += 1
        else:
            print("FAIL", src.name, "\n ", error, flush=True)
            failed += 1
    print(f"\n{passed} passed, {failed} failed", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
