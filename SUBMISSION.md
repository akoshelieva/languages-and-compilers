# Practice 7 — local submission and verification record

Source requirements: `w07-practice(1).pdf`, pages 1–5. Current branch:
`practice-7`, based on the completed Practice 6 commit
`959279832b533002da7679bf5500d73e7a6d49f7`. Full Git history remains.

## Environment and validated result

- Validation runtime: Python 3.13, `llvmlite` 0.47.0, `clang` 17,
  native llvmlite MCJIT (`tests/jit_run.py`).
- `lli`: **not installed here**. The required demonstration using `lli`
  still needs to be run on a machine where `lli` is installed.
- LLVM IR generated exclusively with `llvmlite.ir`; text serialization
  occurs only in `str(module)`. No string-built instructions.
- Test sidecar `NAME.in` feeds stdin; the runners compare *all* stdout bytes,
  including print lines and the final `Program exit with result N` line.
- All LLVM `alloca` instructions are checked to be in `entry` of the
  function to which they belong.

### Demonstration: Task 1 (83)

```bash
python compiler.py --ast tests/ok/p7_task1_pdf.txt
python compiler.py tests/ok/p7_task1_pdf.txt output.ll
python tests/jit_run.py output.ll
lli output.ll
```

Observed MCJIT stdout (exact golden match):

```text
Program exit with result 83
```

`output.ll` defines `Point_shift` with pointer first parameter, with
`getelementptr`, `load`, `add`, `store` to update the original receiver.

### Demonstration: Additional (+1), 46 and unchanged p.x

```bash
python compiler.py tests/ok/p7_additional_pdf.txt output.ll
python tests/jit_run.py output.ll
```

Observed MCJIT stdout (exact golden match):

```text
Program exit with result 46
```

In the source, `p` is const, starts at `x=3` and its original `p.x`
participates in the final sum as `3`, not the temporary shifted value.
The generated IR contains `extractvalue` for reading call-result fields, a
fresh `alloca` for a struct returned by a call, and `Point* this` for member
invocation on that temporary. A separate test
`temporary_mutation_keeps_const_original.txt` prints `p.x` as `3`.

### Demonstration: Task 3 (exact stdout)

```bash
python compiler.py tests/ok/p7_read_print_pdf.txt output.ll
python tests/jit_run.py output.ll < tests/ok/p7_read_print_pdf.in
```

Input (`NAME.in`): `3 4`.

Observed MCJIT stdout (exact golden match):

```text
p and c:
Point{x: 3, y: 4}
Circle{center: Point{x: 3, y: 4}, radius: 2}
7
6000000000
true
Program exit with result 2
```

### Two obligatory semantic/syntax errors with exact positions

```bash
python compiler.py tests/p7_parser/err/field_after_method.src output.ll
python compiler.py tests/err/p7_const_object.txt output.ll
```

Exact diagnostics are recorded in
`tests/p7_parser/err/field_after_method.err` and
`tests/err/p7_const_object.expected`, including `line:column`.

## Requirement-to-code/test traceability

| PDF requirement | Implementation | Verification |
|---|---|---|
| T1: fields before methods; at least one field | `Parser.parse_struct`, `StructDeclNode` | `tests/p7_parser/{ok,err}`, parser/AST checker |
| T1: no duplicate or field/method name collision | `Parser.parse_struct`, `SemanticChecker.visit_structdecl` | `method_duplicate`, `field_method_collision` |
| T1: member call, nested arguments, `exit`, `--ast` | `Parser.parse_factor`, `MemberCallNode`, `MemberFnNode`, AST `children` | `task1_pdf.src`, `mixed_calls.src`, root `p7_task1_pdf.txt` |
| T1: reject call statement/type receiver; grouping and unary minus | `Parser.parse_assign`, `parse_factor` | `tests/p7_parser/err` |
| T2: signatures, forward/recursive/cyclic calls | `visit_program` prototypes and checks | `tests/p7_task2/ok/forward_mutual_and_effects.txt` |
| T2: params/locals shadow fields; top-level invisible | `lookup`, `visit_function` | `local_and_param_shadow_fields`, `inaccessible_top_level` |
| T2: const field assignments, wrong args/exit, unknown methods | `visit_assign`, `check_call_arguments`, `visit_exit`, `visit_membercall` | `tests/p7_task2/err`, root `tests/err/p7_*` |
| T2: direct + transitive mutating until fixed point, cycles | `member_edges`, `visit_program`, `check_mutating_receiver` | `forward_mutual_and_effects`, `cyclic_transitive_const`, `local_copy_does_not_mutate_this` |
| T2: `this` pointer / GEP / global collisions | `CodeGen.visit_memberfn`, `field_decl_ptr`, `visit_membercall`, member mangling | `tests/check_p7_member_sem_ir.py` (`pdf_task1`, `underscore_name_collision`) |
| T3: `read()` i32 anywhere as factor, scanf/entry slot | `parse_factor`, `visit_call` | `tests/ok/p7_read_print_pdf.*`, `p7_exit_read`, `p7_read_nested_arguments` |
| T3: `print` statement all scopes/1 arg | `parse_print`, `visit_print` | `p7_print_control_flow`, `p7_print_member_read`, `p7_print_zero/two/as_value` |
| T3: i32/i64/bool/string/recursive struct/struct call | `_print_primitive`, `_print_struct`, `_printf_text`, `visit_print` | `p7_print_every_kind`, `p7_read_print_pdf`, `p7_print_recursive_nested` |
| T3: raw string bytes/%, reserved words/errors | Lexer `STRING`, `KEYWORDS`, `parse_factor` | `tests/check_p7_io.py`, `tests/p7_io/ok/p7_print_raw_bytes.txt`, root `p7_reserved_*` errors |
| T3: shared printf for print/exit | `CodeGen.__init__`, `visit_exit`, `visit_print` | `tests/check_p7_io.py`: one declaration and LLVM verifier |
| T4: stdin `.in`, whole stdout | `check.py`, `run_tests.py` | `tests/ok/p7_read_print_pdf.in` and golden `*.expected` |
| +1: postfix after struct-returning call | `Parser.parse_factor`, `visit_membercall`, `visit_fieldaccess` | `tests/ok/p7_additional_pdf.txt`, `tests/p7_additional/ok` |
| +1: temporary copy mutable despite const origin | `check_mutating_receiver`, `CodeGen.field_ptr` | `temporary_mutation_keeps_const_original`, `nonmut_member_calls_mut_on_temporary` |
| +1: `extractvalue`, `alloca`/pointer in entry | `CodeGen.visit_fieldaccess`, `field_ptr`, `entry_alloca` | `tests/check_p7_additional.py` (IR pattern and alloca placement) |
| +1: no assign to call or derived fields; nonstruct dot position | `Parser.parse_assign`, `SemanticChecker.fail_dot` | `tests/p7_additional/err` exact error goldens and dot-column checks |
| Earlier practices preserved | existing implementations, AST labels unchanged | `check.py`, `run_tests.py`, 4 legacy IR checkers, 7 old AST goldens |

### Task 4: all seven positive and seven negative categories

| Positive category (PDF) | Program |
|---|---|
| Member call with 0 args | `tests/ok/p7_task1_pdf.txt`: `p.d2()` |
| Member call with 2 args | `tests/ok/p7_two_args.txt`: `c.sum(...)` |
| Mutation followed by field read | `tests/ok/p7_task1_pdf.txt`, `tests/p7_task2/ok/nested_mutation.txt` |
| One member calling another | `tests/ok/p7_member_nested_mutation.txt` |
| Two-level chain `c.center.d2()` | `tests/ok/p7_task1_pdf.txt` |
| Program using `read()` | `tests/ok/p7_read_print_pdf.txt` plus `.in` |
| Program printing every type | `tests/ok/p7_print_every_kind.txt`, `p7_read_print_pdf.txt` |

| Negative category (PDF) | Program |
|---|---|
| Field after member function | `tests/p7_parser/err/field_after_method.src` |
| Mutating call on const object | `tests/err/p7_const_object.txt` |
| Mutating call through const field | `tests/err/p7_const_field.txt` |
| Unknown member function | `tests/err/p7_unknown_method.txt` |
| Wrong number of arguments | `tests/err/p7_wrong_member_args.txt` |
| String literal outside print | `tests/err/p7_string_outside_print.txt` |
| Print used as a value | `tests/err/p7_print_as_value.txt` |

Additional negative scenarios: member before first field, duplicates, unknown
names, wrong argument types, wrong return type, read with args, print with 0
or 2 args, `p.print()`, reserved read/print names, unclosed string at opening
quote, primitive member call, assignment to const field, recursive transitive
mutation on const receivers, assignment to struct temporaries and their fields,
and primitive call-result `.` error **at the dot**.

## Final verification commands and results

Executed from the Practice 7 working tree before any local commit:

| Command | Observed outcome |
|---|---|
| `python check.py` | **255 passed, 0 failed** |
| `python run_tests.py` | **189 passed, 0 failed** (`clang` backend) |
| `python tests/check_p7_parser_ast.py` | **23 passed, 0 failed** |
| `python tests/check_p7_member_sem_ir.py` | **38 passed, 0 failed** |
| `python tests/check_p7_io.py` | **41 passed, 0 failed** (`clang` backend) |
| `python tests/check_p7_additional.py` | **20 passed, 0 failed** (`MCJIT`, `clang`) |
| `python tests/check_struct_ir.py` | PASS |
| `python tests/check_functions_ir.py` | PASS |
| `python tests/check_task3_ir.py` | PASS |
| `python tests/check_nested_init_ir.py` | PASS |
| `git diff --check` | PASS |

The main test scripts use `lli` if installed, otherwise `clang`. Separate
Additional tests and demos executed using JIT as required when `lli` is absent.
The seven original Practice 1–6 AST golden snapshots were unchanged. The two
obsolete Task 1-only *negative* AST cases were promoted to positives because
Additional now legalizes their syntax.

## Repository policy / outstanding external demonstration

**Local commits only. No push or PR.** `ai_usage.txt` is unchanged relative to
the Practice 6 base, excluded from staging and new commits. Note the PDF
requires updating this file and a PR; the current user's explicit restrictions
supersede these actions. A final real-`lli` demonstration to the instructor
remains necessary, since this environment has no `lli` executable.
