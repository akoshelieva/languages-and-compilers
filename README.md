# languages-and-compilers — practice 6

This is an extension of the existing language compiler. The implementation is in
`src/compiler.py`; `compiler.py` at the repository root is a compatibility entry point.

Implemented: structs, functions, field chains, calls, `mut` checks, and nested
brace initialisation with LLVM `insertvalue`. Generated LLVM IR is built using
`llvmlite.ir` and emitted by `str(module)`.

## Requirements

- Python 3 with `llvmlite`
- LLVM `lli` or `clang` for execution

## Run

```bash
python3 compiler.py tests/ok/p6_full_program_28.txt output.ll
lli output.ll
python3 compiler.py tests/ok/p6_nested_initializer_ring.txt output.ll
lli output.ll
```

If `lli` is unavailable, compile generated IR with
`clang output.ll -o program && ./program`.

## Tests

```bash
python3 run_tests.py
python3 tests/check_struct_ir.py
python3 tests/check_functions_ir.py
python3 tests/check_task3_ir.py
python3 tests/check_nested_init_ir.py
```

`run_tests.py` verifies positive and negative cases, LLVM syntax and execution,
and the command-line interface. The optional nested initialisation example
`tests/ok/p6_nested_initializer_ring.txt` produces `Program exit with result 36`.

The assignment requires the source code, tests, and `ai_usage.txt` to be included
in the `practice-6` branch and submitted in a pull request into `main`.
