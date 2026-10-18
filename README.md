# languages-and-compilers — Practice 7 (Tasks 1–4 + Additional)

Incremental compiler for Practices 1–7. `src/compiler.py` contains the byte lexer,
parser, AST, semantic checker and LLVM code generator; `compiler.py` is the
compatible root CLI. Earlier practices remain supported. The original Git
history is retained in the `practice-7` branch.

## Setup (venv)

Python 3.13 and `llvmlite` 0.47.0 were used for the final checks. `clang 17` was
available; `lli` was **not** installed in the validation environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install 'llvmlite>=0.46,<0.48'
python compiler.py --ast tests/ok/p7_task1_pdf.txt
```

A compatible native LLVM toolchain is needed to execute IR via `lli` or `clang`.
The included `tests/jit_run.py` executes it through `llvmlite.binding` MCJIT
without requiring either executable (libc is used for `scanf` and `printf`).

## Compile and execute

```bash
python compiler.py tests/ok/p7_task1_pdf.txt output.ll
lli output.ll
clang output.ll -o program && ./program
python tests/jit_run.py output.ll
```

stdin belongs in a sidecar `NAME.in` next to `NAME.txt`. For example:

```bash
python compiler.py tests/ok/p7_read_print_pdf.txt output.ll
python tests/jit_run.py output.ll < tests/ok/p7_read_print_pdf.in
lli output.ll < tests/ok/p7_read_print_pdf.in
```

`--ast` and `--tokens` are read-only CLI modes. `--ast` shows `Struct`, ordered
fields, `MemberFn`, `Param`, `Block`, and `MemberCall` with its receiver subtree.

## Language and implementation

Practice 7 adds struct member functions after at least one field, calls such as
`c.center.d2()`, receiver resolution to locals/parameters before fields, hidden
`this` pointers, recursion and forward calls. A member method is mutating when it
writes a receiver field, or transitively invokes another mutating member on its
own receiver or a receiver field. Effects are propagated to a fixed point, so
cycles work. Mutating calls through a normal receiver require `mut` at **every**
link; nonmutating calls are legal on const objects. Local copies do not mutate
`this`.

`read()` is an `i32`-valued factor reading one integer from stdin via `scanf`;
`print(arg)` is a statement. It prints signed `i32`, `i64`, bools (`true`/
`false`), raw-byte string literals and recursively formatted structs, then a
newline. Literal `%` is data, not a format. `read`/`print` are reserved words.
The existing `Program exit with result N` line is unchanged.

**Additional (+1):** expression chains may continue after struct-valued
function/member calls: `origin().moved(7).x`, `c.grown(1).center.d2()`, and
`p.moved(1).shift(2)` all work. Call results are independent temporary copies;
mutating methods on a fresh temporary do not modify the original object.
Assignments to a call result or one of its fields are rejected. A dot after a
non-struct call result produces a positional error at that dot. A call is not a
statement, and grouping parentheses and unary minus are not part of the language.

**LLVM implementation:** the IR is built **only** with `llvmlite.ir`; `str(module)`
serializes it. `Point_shift` takes `Point* this`; fields use GEP/load/store.
Fields read from a temporary aggregate use `extractvalue`. Invoking a method on
a temporary places the returned aggregate in an `alloca` in the current
function's `entry`, then passes its address. All storage `alloca` instructions
are placed in their owning function's `entry`. Builtin `scanf` and shared
`printf` are declared once.

## Tests

```bash
python check.py
python run_tests.py
python tests/check_p7_parser_ast.py
python tests/check_p7_member_sem_ir.py
python tests/check_p7_io.py
python tests/check_p7_additional.py
python tests/check_struct_ir.py
python tests/check_functions_ir.py
python tests/check_task3_ir.py
python tests/check_nested_init_ir.py
```

The two main runners choose `lli` if installed, otherwise `clang`; both pass
`NAME.in` to stdin and compare **all** stdout bytes to `NAME.expected`. The
Additional checker executes its fixtures using **MCJIT** and every available
external execution backend (`lli`, `clang`), also checking the LLVM verifier,
`extractvalue`, `Point* this`, and each `alloca` placement. Byte-heavy fixtures
not readable as UTF-8 are checked by `tests/check_p7_io.py` separately.

See `SUBMISSION.md` for exact demo commands, observed results, limitations and a
traceability table to tests. The PDF's submission instructions for push/PR and
`ai_usage.txt` are intentionally **not** followed here: per user restrictions,
no push or PR was performed, and `ai_usage.txt` was not modified or committed.
