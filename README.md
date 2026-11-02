# languages-and-compilers — Practices 1–8

Incremental compiler for Practices 1–8. `src/compiler.py` contains the byte lexer,
parser, AST, semantic checker and LLVM code generator; `compiler.py` is the
compatible root CLI. Earlier practices remain supported. The original Git
history is retained in the `practice-7` branch.

## Setup (venv)

The corrected archive was checked with Python 3.12.14, `llvmlite` 0.50.0
(LLVM 22.1.0) and MCJIT. Neither native `lli` nor `clang` was installed in
this recheck environment. The original implementation had also been checked
with Python 3.13, `llvmlite` 0.47.0 and `clang` 17.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install 'llvmlite==0.50.0'
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
python tests/check_cli_regressions.py
python tests/check_struct_ir.py
python tests/check_functions_ir.py
python tests/check_task3_ir.py
python tests/check_nested_init_ir.py
```

The two main runners require native `lli` or `clang` for normal execution;
`check.py` reports a regular failure if neither exists. Both pass
`NAME.in` to stdin and compare **all** stdout bytes to `NAME.expected`. The
Additional checker executes its fixtures using **MCJIT** and every available
external execution backend (`lli`, `clang`), also checking the LLVM verifier,
`extractvalue`, `Point* this`, and each `alloca` placement. Byte-heavy fixtures
not readable as UTF-8 are checked by `tests/check_p7_io.py` separately.

See `SUBMISSION.md` for exact demo commands, observed results, limitations and a
traceability table to tests. The PDF's submission instructions for push/PR and
`ai_usage.txt` are intentionally **not** followed here: per user restrictions,
no push or PR was performed, and `ai_usage.txt` was not modified or committed.

The CLI removes an earlier output IR file before opening a new source in
compile mode, including when that source is missing. `--ast` and `--tokens`
remain read-only, and an identical input/output path is rejected before any
file is removed. Runner diagnostics preserve binary stdout/stderr consistently,
including a failed clang build. All seven Task 4 negative categories now have
`.txt`/`.expected` fixtures directly under `tests/err`.

## Practice 8: callable objects and lambdas

A struct whose member function is named `call` can be invoked as `object(args)`;
`object.call(args)` remains legal. The semantic checker rewrites the first
form into the existing member-call AST, including callable fields. Calls
validate visible-variable/function name collisions, argument types/counts,
and transitive member mutability, with `line:column` diagnostics.

Lambdas use `fn name := [captures](params) -> type` followed by a block.
A capture is copied at declaration time; `[name]` stores a const copy and
`[mut name]` stores an independently mutable copy, even if the source was
const. Each lambda is lowered by `SemanticChecker` into a uniquely named
`lambda.<name>` struct, a `call` member method, and a declaration initialized
from captures. Empty, nested, scoped, and loop-local lambdas are supported,
as is capturing another lambda. The closure variable is `mut` if any capture
is `mut`; existing mutating member-call rules apply transitively. The original
variables are never modified by mutations of the closure's copies.

The lambda body sees only its parameters, locals, captures, and global
functions; the synthetic `call` method is not implicitly visible by name.
A capture itself may be named `call` without colliding with the synthetic
method. Lambda types have no source-language names and lambda objects cannot
be passed or used as ordinary values outside permitted captures.

`--ast` prints the original parse tree; `--ast-checked` prints the lowered
AST, including synthetic structures and `CallableCall` nodes. All LLVM IR is
created through `llvmlite.ir` (`str(module)` only serializes the module).

PDF demonstration programs are `tests/ok/p8_callable_pdf_task1.txt`,
`tests/ok/p8_lambda_pdf.txt`, and `tests/ok/p8_mut_pdf.txt`. All three have
matching `.expected` outputs. For input-consuming tests, a same-stem `.in`
file supplies standard input.

## Practice 8: callable objects and lambdas

A struct whose member function is named `call` can be invoked as `object(args)`;
`object.call(args)` remains legal. The semantic checker rewrites the first
form into the existing member-call AST, including callable fields. Calls
validate visible-variable/function name collisions, argument types/counts,
and transitive member mutability, with `line:column` diagnostics.

Lambdas use `fn name := [captures](params) -> type` followed by a block.
A capture is copied at declaration time; `[name]` stores a const copy and
`[mut name]` stores an independently mutable copy, even if the source was
const. Each lambda is lowered by `SemanticChecker` into a uniquely named
`lambda.<name>` struct, a `call` member method, and a declaration initialized
from captures. Empty, nested, scoped, and loop-local lambdas are supported,
as is capturing another lambda. The closure variable is `mut` if any capture
is `mut`; existing mutating member-call rules apply transitively. The original
variables are never modified by mutations of the closure's copies.

The lambda body sees only its parameters, locals, captures, and global
functions; the synthetic `call` method is not implicitly visible by name.
A capture itself may be named `call` without colliding with the synthetic
method. Lambda types have no source-language names and lambda objects cannot
be passed or used as ordinary values outside permitted captures.

`--ast` prints the original parse tree; `--ast-checked` prints the lowered
AST, including synthetic structures and `CallableCall` nodes. All LLVM IR is
created through `llvmlite.ir` (`str(module)` only serializes the module).

PDF demonstration programs are `tests/ok/p8_callable_pdf_task1.txt`,
`tests/ok/p8_lambda_pdf.txt`, and `tests/ok/p8_mut_pdf.txt`. All three have
matching `.expected` outputs. For input-consuming tests, a same-stem `.in`
file supplies standard input.


## Practice 9 — Task 1: remove unused variables

`--remove-unused` optionally runs `RemoveUnusedVariables` as an AST visitor
**after** semantic checking and **before** LLVM generation. It removes unread
local declarations (including unused lambdas) and assignments to them, using
resolved declaration identities for shadowing, lexical scopes and captures.
Reads are recounted until a fixed point. `read()` and function/member calls
from removed initializers and assignments are retained as internal expression
statements, preserving their possible side effects and evaluation order.

```bash
python compiler.py --remove-unused tests/ok/p8_mut_pdf.txt output.ll
python compiler.py --remove-unused --ast-checked tests/ok/p8_mut_pdf.txt
python tests/check_p9_remove_unused.py
```

Plain `--ast` remains the original parser AST; without `--remove-unused` the
existing checked AST, LLVM generation and CLI behavior are unchanged.

## Practice 9 — Task 2: remove unused global functions

The same `--remove-unused` checked-AST visitor also removes unreachable **global**
functions, including their bodies. It follows semantically resolved calls from
`main` and retained member/lambda methods, then transitively through called
global functions. Unreachable self-recursion and mutually recursive groups are
removed; member functions and live lambdas are not targets of this pass.
Generated lambda methods orphaned by Task 1 or a removed global function are
excluded from LLVM emission. Function reachability and unused-variable removal
repeat to a fixed point, preserving calls with possible side effects.

```bash
python compiler.py --remove-unused --ast-checked input.txt
python compiler.py --remove-unused input.txt output.ll
python tests/check_p9_remove_unused_functions.py
```

Without `--remove-unused`, compilation and both AST modes retain their prior
behavior. `--ast` always displays the original parsed tree.

## Optimizations (Practice 9 — Task 3: tests and documentation)

Optimization is **opt-in**: `--remove-unused` runs the existing
`RemoveUnusedVariables` AST visitor after `SemanticChecker` has resolved
bindings and lowered lambdas, and before `CodeGen`. The default CLI, plain
`--ast`, and unoptimized `--ast-checked` are unchanged. To inspect the actual
optimized AST use **both** `--remove-unused --ast-checked`.

**Algorithm and fixpoint:** `_UnusedReadCounter` identifies variable *reads* by
`VarNode.decl` identity, rather than text name, so shadowed locals, nested
blocks and lambda captures are distinct. An assignment target is a write, not
an implicit read. Unread declarations, stores to them, and unused lambdas are
removed. Initializers/RHS expressions with potentially observable effects
(`read()`, global calls, member/callable calls) are retained in evaluation
order as `ExprStmtNode`s. `_GlobalFunctionCalls` follows resolved function
calls starting at the program statements/exit and retained member/lambda
methods, recursively marking reachable global functions. Unreachable globals
(including dead self-recursion and mutual-recursion cycles) are removed with
their bodies; member functions and live lambdas remain. Orphaned synthetic
lambda structs are pruned. The visitor **recounts and repeats** both passes
until no more declarations/functions are removed. This handles cascades in
which removal makes another binding or function unreachable.

**Additional Task — removing unused pure calls:** `_CallEffects` uses the
SemanticChecker-resolved function/member targets (including callable objects
and generated `lambda.call` methods) to build a call graph. It begins with
direct effects: `print`, `read()`, any `while` (conservatively, it may not
terminate), and mutating member methods, including writes to mutable lambda
captures. Cycles of calls are conservatively effectful because recursion may
not terminate. These summaries propagate backwards through callers to a
**fixpoint**, so wrappers around effects remain effectful. Unknown calls are
never presumed pure. A pure call whose return value is unused is eliminated;
its receiver/arguments are processed left-to-right and any nested effectful
calls are kept as `ExprStmt` nodes. A call with effects is preserved in full,
including its receiver and arguments. The existing variable/function removal
and lambda-structure cleanup repeat until stable. No LLVM-level optimization
was introduced.

The conservatism is intentional: a bounded recursive call, a terminating
`while`, or a method that mutates only an unreachable object may still be
retained. This pass does **not** prove termination or interprocedural value
liveness. Positive and negative checks live in `tests/check_p9_pure_calls.py`,
`tests/opt/{pure_call_chain,pure_nested_read,indirect_print,pure_callable,
pure_lambda,mut_capture_call,while_and_recursion,member_nested_read,
mutating_member_chain,nested_lambda_indirect_print}.*`, and
`tests/err/p9_*`. The full runner already runs both optimized and unoptimized
versions of every `tests/ok`, `tests/err`, and `tests/opt` program.

**Known self-feeding limitation:** the analysis counts *syntactic reads* rather
than proving variable liveness. In `i32 mut x{1}; x := x + 1; exit 0`, the RHS
reads `x`, so the declaration and store remain, although the value is never
observed. Eliminating this case would need a more precise liveness/data-flow
analysis and is not part of this practice. See `tests/opt/self_feeding.txt`.

**AST subtree ownership in Python:** removing an item from `ProgramNode`'s
`functions`/`statements` (or a block's `statements`) drops the parent's strong
reference to that subtree. We **do not manually free nodes**: CPython's
reference counting and cycle garbage collector reclaim them once there are no
remaining references, including those from temporary semantic visitors or
optimizer bookkeeping. The live AST still owns all retained children. The
`weakref.ref` checks in `tests/check_p9_optimization_suite.py` retain the
*live root* while verifying that discarded declarations, their initializer
nodes, removed function bodies and a dead lambda's generated struct become
unreachable after `gc.collect()`. This tests actual object release, not just
that the optimized tree's printed shape changed.

**Golden optimization fixtures:** each `tests/opt/NAME.txt` has a checked-AST
snapshot `NAME.astc` (the exact stdout of `--remove-unused --ast-checked`)
and a runtime output `NAME.expected`; `NAME.in` is optional stdin. The two
warm-up reproductions demonstrate unread variables and unused functions
(`twice(2)` produces only `user_twice` and `main` in LLVM IR and prints exit
4). Further cases cover transitive function chains, `if`/shadowing, `read()`
and calls with side effects, no-change optimization, scoped lambda captures,
callable objects, member calls and the self-feeding limitation.

```bash
python run_tests.py                         # tests/ok + tests/err in both modes; tests/opt
python tests/check_p9_optimization_suite.py # AST fixpoint, LLVM verifier, weakref/GC
python tests/check_p9_remove_unused.py      # Task 1 regressions
python tests/check_p9_remove_unused_functions.py # Task 2 regressions
python compiler.py --remove-unused --ast-checked tests/opt/warmup_unused_functions.txt
python compiler.py --remove-unused tests/opt/warmup_unused_functions.txt output.ll
```

`run_tests.py` compiles **every** `tests/ok` and `tests/err` fixture both
without and with `--remove-unused`, comparing the existing `.expected`
outputs/errors verbatim. It also checks `tests/opt` output in *both* modes,
LLVM IR validity, the optimized `.astc` golden, and that `--remove-unused`
does not alter plain `--ast`. `no_optimization` additionally checks that
checked AST output is identical with and without optimization. The old
fixtures and test files were not changed. No additional optimization task is
implemented here.
