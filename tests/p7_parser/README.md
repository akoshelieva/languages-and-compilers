# Practice 7 — parser/AST fixtures (including Additional)

Run `python tests/check_p7_parser_ast.py` from the project root.

- `ok/*.src` + `*.ast`: exact `--ast` stdout, including Task 1 PDF, multiple
  arguments, calls after `exit`, a global struct-returning call followed by a
  member call, and a member-returned struct followed by field access.
- `err/*.src` + `*.err`: exact `line:column` parsing diagnostics for ordering,
  duplicate names, type receivers, standalone calls, grouping, unary minus.
- All seven **pre-existing** AST golden files are compared byte-for-byte.

Two Task 1-only negative fixtures (`function_result_member`, `middle_of_chain`)
were intentionally moved to `ok` and given AST snapshots because Additional
Task requires those forms to be valid. Their previous expected rejections
are obsolete. No pre-existing Practice 1–6 AST fixtures were modified.

The new AST nodes are `MemberFn` (with explicit `Block`) and `MemberCall`
(with receiver subtree, such as `Field .center` → `Var c`).
