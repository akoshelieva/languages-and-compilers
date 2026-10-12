# languages-and-compilers — practice 6

Compiler for the language developed across Practices 1–6. The implementation is
in `src/compiler.py`; the root `compiler.py` is a compatibility entry point.

## Requirements

Python 3, `llvmlite`, and either LLVM `lli` or `clang` to execute generated IR.
Code generation uses `llvmlite.ir`; the LLVM text is emitted with `str(module)`.

## Commands

```bash
python3 compiler.py tests/ok/p6_full_program_28.txt output.ll
lli output.ll

python3 compiler.py tests/ok/p6_nested_initializer_ring.txt output.ll
lli output.ll

python3 compiler.py --ast tests/ok/if_else_nested.txt
python3 compiler.py --tokens tests/cli/tokens_input.txt
```

If `lli` is unavailable, run `clang output.ll -o program && ./program`.
The nested `Ring` sample prints `Program exit with result 36`.

## Language

The language supports `i32`, `i64`, `bool`, variable declarations and
assignments, arithmetic, comparisons and `!`. Statements can use nested `if`,
`else` and `while` blocks with lexical scopes and shadowing. Blocks may end in
`exit`; all local storage slots are allocated in LLVM function entry blocks.

Practice 6 adds structs with `mut` fields, functions with typed parameters and
results, struct-valued arguments and returns, field chains, and nested struct
initialisers such as `Circle c{{10, 20}, 5}`. Struct field access uses resolved
indexes with `getelementptr`, and nested aggregate initialisation uses
`insertvalue`. Function calls, recursion and forward calls are supported.

Use `--ast` to print the parsed tree without a semantic type check. Use
`--tokens` to print tokens without modifying the input file.

## Tests

```bash
python3 check.py
python3 run_tests.py
python3 tests/check_struct_ir.py
python3 tests/check_functions_ir.py
python3 tests/check_task3_ir.py
python3 tests/check_nested_init_ir.py
```

`check.py` verifies golden-output, diagnostic, CLI token and AST fixtures.
`run_tests.py` covers valid programs, rejected programs and LLVM verification.
Both use `lli` when present and fall back to `clang` for execution.

Submit `src/`, `tests/` and `ai_usage.txt` on branch `practice-6`; open a pull
request into `main` with `vovaskochko` as reviewer.
