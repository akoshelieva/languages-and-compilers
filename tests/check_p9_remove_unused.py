"""Practice 9 Task 1: checked-AST cleanup and observable behavior."""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (AssignNode, CodeGen, DeclNode, ExprStmtNode,
                          LambdaNode, PrintNode, Parser, RemoveUnusedVariables,
                          SemanticChecker, lex)


def checked(source, optimize=True):
    tree = Parser(lex(source.encode())).parse_program()
    tree.accept(SemanticChecker())
    if optimize:
        tree.accept(RemoveUnusedVariables())
    return tree


def dump(tree):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        tree.dump()
    return output.getvalue()


def run_cli(source, flags=(), stdin=b""):
    with tempfile.TemporaryDirectory() as temp:
        src = Path(temp) / "source.txt"
        out = Path(temp) / "output.ll"
        src.write_text(source, encoding="utf-8")
        if "--ast" in flags or "--ast-checked" in flags:
            result = subprocess.run([sys.executable, str(ROOT / "compiler.py"),
                                     *flags, str(src)], capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise AssertionError(result.stderr)
            return result.stdout
        tree = checked(source, optimize="--remove-unused" in flags)
        out.write_text(str(tree.accept(CodeGen())), encoding="utf-8")
        executed = subprocess.run([sys.executable, str(ROOT / "tests/jit_run.py"),
                                   str(out)], input=stdin, capture_output=True, timeout=15)
        if executed.returncode != 0:
            raise AssertionError(executed.stderr)
        return executed.stdout


class RemoveUnusedTests(unittest.TestCase):
    def test_warmup_simple_unused_declaration(self):
        source = "i32 used{4}\ni32 unused{7}\nexit used\n"
        tree = checked(source)
        self.assertEqual([s.name for s in tree.statements if isinstance(s, DeclNode)],
                         ["used"])
        self.assertEqual(run_cli(source, ("--remove-unused",)),
                         run_cli(source))

    def test_warmup_cascading_unused_declarations(self):
        source = "i32 a{4}\ni32 b{a + 1}\ni32 c{b + 2}\nexit 0\n"
        self.assertEqual([type(s).__name__ for s in checked(source).statements], [])
        self.assertIn("Decl a", dump(checked(source, optimize=False)))
        self.assertEqual(run_cli(source, ("--remove-unused",)), run_cli(source))

    def test_stores_are_not_reads_and_shadowing_is_by_binding(self):
        source = """\
i32 mut x{7}
if true
{
    i32 mut x{3}
    print(x)
    x := 4
}
x := 8
exit 0
"""
        tree = checked(source)
        self.assertEqual(len([s for s in tree.statements if isinstance(s, DeclNode)]), 0)
        inner = tree.statements[0].body.statements
        self.assertEqual([type(s) for s in inner], [DeclNode, PrintNode, AssignNode])
        self.assertEqual(run_cli(source, ("--remove-unused",)), b"3\nProgram exit with result 0\n")

    def test_read_side_effects_retained_in_initializers_and_writes(self):
        source = """\
i32 mut unused{read() + read()}
unused := read()
exit read()
"""
        tree = checked(source)
        self.assertEqual([type(s) for s in tree.statements],
                         [DeclNode, AssignNode])
        stdin = b"1\n2\n3\n4\n"
        self.assertEqual(run_cli(source, ("--remove-unused",), stdin),
                         b"Program exit with result 4\n")
        self.assertEqual(run_cli(source, ("--remove-unused",), stdin),
                         run_cli(source, stdin=stdin))

    def test_nested_call_side_effect_order(self):
        source = """\
fn trace := (i32 n) -> i32
{
    print(n)
    exit n
}
i32 mut unwanted{trace(4) * 3 + trace(6)}
unwanted := trace(9)
exit 0
"""
        tree = checked(source)
        self.assertEqual([type(s) for s in tree.statements],
                         [DeclNode, AssignNode])
        self.assertEqual(run_cli(source, ("--remove-unused",)),
                         b"4\n6\n9\nProgram exit with result 0\n")

    def test_field_write_to_dead_struct_retains_rhs_effect(self):
        source = """\
struct Pair
{
    i32 mut x
    i32 mut y
}
Pair mut p{1, 2}
p.x := read()
exit read()
"""
        tree = checked(source)
        self.assertEqual([type(s) for s in tree.statements], [DeclNode, AssignNode])
        self.assertEqual(run_cli(source, ("--remove-unused",), b"11\n22\n"),
                         b"Program exit with result 22\n")

    def test_nested_initializers_preserve_read_order(self):
        source = """\
struct Inner
{
    i32 value
}
struct Outer
{
    Inner child
    i32 count
}
Outer ignored{{read()}, read()}
exit read()
"""
        tree = checked(source)
        self.assertEqual([type(s) for s in tree.statements], [DeclNode])
        self.assertEqual(run_cli(source, ("--remove-unused",), b"3\n4\n5\n"),
                         b"Program exit with result 5\n")

    def test_idempotent_and_scope_in_while(self):
        source = """\
i32 mut counter{0}
while counter != 2
{
    i32 transient{counter + 1}
    counter := counter + 1
}
exit counter
"""
        tree = checked(source)
        first = dump(tree)
        tree.accept(RemoveUnusedVariables())
        self.assertEqual(dump(tree), first)
        self.assertNotIn("Decl transient", first)
        self.assertEqual(run_cli(source, ("--remove-unused",)),
                         b"Program exit with result 2\n")

    def test_method_call_receiver_is_live_even_when_result_is_unused(self):
        source = """\
struct Counter
{
    i32 mut n
    fn inc := () -> i32
    {
        n := n + 1
        print(n)
        exit n
    }
}
Counter mut c{0}
i32 ignored{c.inc()}
exit 0
"""
        tree = checked(source)
        self.assertEqual(len([s for s in tree.statements if isinstance(s, DeclNode)]), 2)
        self.assertEqual(run_cli(source, ("--remove-unused",)),
                         b"1\nProgram exit with result 0\n")

    def test_live_capture_and_scoped_shadowing(self):
        source = """\
i32 x{5}
if true
{
    i32 x{9}
    fn show := [x]() -> i32
    {
        exit x
    }
    print(show())
}
exit 0
"""
        tree = checked(source)
        self.assertFalse(any(isinstance(s, DeclNode) for s in tree.statements))
        inner = tree.statements[0].body.statements
        self.assertTrue(any(isinstance(s, LambdaNode) for s in inner))
        self.assertTrue(any(isinstance(s, DeclNode) and s.name == "x" for s in inner))
        self.assertEqual(run_cli(source, ("--remove-unused",)),
                         b"9\nProgram exit with result 0\n")

    def test_unused_lambda_and_its_capture_are_not_pruned(self):
        source = """\
i32 captured{12}
fn unused := [captured]() -> i32
{
    exit captured
}
exit 0
"""
        tree = checked(source)
        self.assertEqual([type(s) for s in tree.statements], [DeclNode, LambdaNode])
        self.assertEqual(run_cli(source, ("--remove-unused",)),
                         b"Program exit with result 0\n")

    def test_unchanged_raw_ast_and_opt_in_checked_ast(self):
        source = "i32 unused{2}\nexit 0\n"
        raw = run_cli(source, ("--ast",))
        self.assertEqual(raw, run_cli(source, ("--remove-unused", "--ast")))
        self.assertIn("Decl unused", raw)
        self.assertIn("Decl unused", run_cli(source, ("--ast-checked",)))
        self.assertNotIn("Decl unused", run_cli(source, ("--remove-unused", "--ast-checked")))
        self.assertNotIn("Decl unused", run_cli(source, ("--ast-checked", "--remove-unused")))

    def test_cli_compile_with_optimization(self):
        source = "i32 unneeded{read()}\nexit read()\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.txt"
            target = Path(directory) / "result.ll"
            path.write_text(source)
            result = subprocess.run([sys.executable, str(ROOT / "compiler.py"),
                                     "--remove-unused", str(path), str(target)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.exists())
            executed = subprocess.run([sys.executable, str(ROOT / "tests/jit_run.py"),
                                       str(target)], input=b"2\n3\n", capture_output=True,
                                      timeout=15)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(executed.stdout, b"Program exit with result 3\n")

    def test_p8_mutable_capture_regression(self):
        source = (ROOT / "tests/ok/p8_mut_pdf.txt").read_text(encoding="utf-8")
        expected = (ROOT / "tests/ok/p8_mut_pdf.expected").read_bytes()
        self.assertEqual(run_cli(source, ("--remove-unused",)), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
