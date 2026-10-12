## Compiler interface

The documented entry point is:

```bash
python3 src/compiler.py input.txt output.ll
python3 src/compiler.py --ast input.txt
python3 src/compiler.py --tokens input.txt
```

`compiler.py` at the repository root is retained as a compatibility wrapper for earlier practices, so the same three commands also work with `python3 compiler.py ...`.

The compiler uses `llvmlite.ir` to build LLVM IR. It does not assemble IR from strings; `str(module)` is used only when writing the finished module.

## Practice 5 language

A block starts with `{` on its own line and ends with `}` on its own line. `if` and `while` keep their condition on the keyword line. `else` is optional and also stands on its own line. Blocks may nest and may finish with `exit` as their last line.

`!` binds to the factor immediately after it. Conditions of `if` and `while`, and operands of `!`, must be `bool`. Each block creates a semantic scope frame: declarations are checked only against the innermost frame, lookup walks outward, and shadowing may change the type.

Code generation creates LLVM basic blocks for branches and loops. Every variable slot is allocated in the function entry block, including declarations found in nested source blocks. Branch arms and loop bodies receive only the terminators they need, and `exit` terminates the current LLVM block with `ret`.

## Tests

Run the complete regression suite from the repository root:

```bash
python3 check.py
```

The runner discovers all source/golden-file pairs under `tests/`, compiles valid programs, rejects invalid programs, and diffs exact output. It also checks every committed `.ast` golden file and the `--tokens` regression that verifies the input file is not modified or deleted. For generated IR, the runner uses `lli` when available and falls back to `clang` when `lli` is not installed.

Practice 5 tests cover, among other cases: `if` without `else`, nested `if`, cross-type shadowing, `exit` inside an arm, `!` in conditions, values assigned in both arms and read afterward, invalid conditions, invalid `!`, scope leaks, same-scope redeclaration, braces on the `if` line, empty blocks, and `while`.

## LLVM inspection commands

```bash
python3 src/compiler.py tests/ok/if_else_nested.txt output.ll
lli output.ll

python3 src/compiler.py tests/ok/both_arms_assign.txt output.ll
opt -passes=mem2reg -S output.ll

python3 src/compiler.py tests/ok/while_sum.txt output.ll
lli output.ll
