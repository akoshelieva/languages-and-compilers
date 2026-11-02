"""Practice 9 Task 2: reachable global functions after unused-variable removal."""

import contextlib
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from llvmlite import binding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (Parser, RemoveUnusedVariables, SemanticChecker,
                          _GlobalFunctionCalls, lex)


def tree_for(source, optimize=True):
    tree = Parser(lex(source.encode())).parse_program()
    tree.accept(SemanticChecker())
    if optimize:
        tree.accept(RemoveUnusedVariables())
    return tree


def ast_text(tree):
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        tree.dump()
        return output.getvalue()


def compile_cli(source, flags=("--remove-unused",)):
    with tempfile.TemporaryDirectory() as directory:
        src = Path(directory) / "code.txt"
        ir_file = Path(directory) / "code.ll"
        src.write_text(source, encoding="utf-8")
        process = subprocess.run([sys.executable, str(ROOT / "compiler.py"),
                                  *flags, str(src), str(ir_file)],
                                 capture_output=True, text=True, timeout=30)
        if process.returncode:
            raise AssertionError(process.stderr)
        ir = ir_file.read_text(encoding="utf-8")
        module = binding.parse_assembly(ir)
        module.verify()
        run = subprocess.run([sys.executable, str(ROOT / "tests/jit_run.py"),
                              str(ir_file)], capture_output=True, timeout=30)
        if run.returncode:
            raise AssertionError(run.stderr)
        return ir, run.stdout


class RemoveUnusedFunctionsTests(unittest.TestCase):
    def test_twice_main_only_ir_and_exit_four(self):
        source = """\
fn unused := () -> i32
{
    exit 9
}
fn twice := (i32 x) -> i32
{
    i32 doubled{x * 2}
    exit doubled
}
exit twice(2)
"""
        self.assertEqual([f.name for f in tree_for(source).functions], ["twice"])
        ir, output = compile_cli(source)
        self.assertEqual(set(re.findall(r'^define .*?@"?([^"(]+)"?\(', ir, re.M)),
                         {"user_twice", "main"})
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_reachable_chain_and_unused_chain(self):
        source = """\
fn live_a := () -> i32
{
    exit live_b()
}
fn live_b := () -> i32
{
    exit 4
}
fn dead_a := () -> i32
{
    exit dead_b()
}
fn dead_b := () -> i32
{
    exit 2
}
exit live_a()
"""
        self.assertEqual([f.name for f in tree_for(source).functions], ["live_a", "live_b"])
        ir, output = compile_cli(source)
        self.assertNotIn('user_dead_', ir)
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_unreachable_self_recursion(self):
        source = """\
fn spin := (i32 n) -> i32
{
    exit spin(n)
}
exit 4
"""
        self.assertFalse(tree_for(source).functions)
        ir, output = compile_cli(source)
        self.assertNotIn('user_spin', ir)
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_unreachable_mutual_recursion(self):
        source = """\
fn even := (i32 n) -> bool
{
    if n == 0
    {
        exit true
    }
    exit odd(n - 1)
}
fn odd := (i32 n) -> bool
{
    if n == 0
    {
        exit false
    }
    exit even(n - 1)
}
exit 4
"""
        self.assertFalse(tree_for(source).functions)
        ir, output = compile_cli(source)
        self.assertNotIn('user_even', ir)
        self.assertNotIn('user_odd', ir)
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_live_recursion_is_retained(self):
        source = (ROOT / "tests/ok/p6_fib_recursive.txt").read_text()
        self.assertEqual([f.name for f in tree_for(source).functions], ["fib"])
        ir, output = compile_cli(source)
        self.assertIn('user_fib', ir)
        self.assertEqual(output, b"Program exit with result 55\n")

    def test_mutual_recursion_reachable_from_main(self):
        source = """\
fn even := (i32 n) -> bool
{
    if n == 0
    {
        exit true
    }
    exit odd(n - 1)
}
fn odd := (i32 n) -> bool
{
    if n == 0
    {
        exit false
    }
    exit even(n - 1)
}
exit even(4)
"""
        self.assertEqual([f.name for f in tree_for(source).functions], ["even", "odd"])
        _, output = compile_cli(source)
        self.assertEqual(output, b"Program exit with result true\n")

    def test_calls_from_member_and_lambda_scopes(self):
        source = """\
struct Holder
{
    i32 number
    fn value := () -> i32
    {
        exit member_helper()
    }
}
fn member_helper := () -> i32
{
    exit 2
}
fn lambda_helper := () -> i32
{
    exit 2
}
fn dead := () -> i32
{
    exit 8
}
Holder object{3}
fn get := []() -> i32
{
    exit lambda_helper()
}
i32 result{object.value() + get()}
exit result
"""
        tree = tree_for(source)
        self.assertEqual([f.name for f in tree.functions],
                         ["member_helper", "lambda_helper"])
        counter = _GlobalFunctionCalls(tree.functions)
        tree.accept(counter)
        self.assertEqual({fn.name: counter.calls[fn] for fn in tree.functions},
                         {"member_helper": 1, "lambda_helper": 1})
        ir, output = compile_cli(source)
        self.assertIn('user_member_helper', ir)
        self.assertIn('user_lambda_helper', ir)
        self.assertNotIn('user_dead', ir)
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_call_in_nested_while_if_reaches_global(self):
        source = """\
fn helper := () -> i32
{
    exit 4
}
i32 mut n{0}
while n != 4
{
    if true
    {
        n := helper()
    }
}
exit n
"""
        self.assertEqual([f.name for f in tree_for(source).functions], ["helper"])
        ir, output = compile_cli(source)
        self.assertIn('user_helper', ir)
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_calls_in_unchanged_uninvoked_member_method_keep_global(self):
        source = """\
struct Holder
{
    i32 n
    fn calculate := () -> i32
    {
        exit helper()
    }
}
fn helper := () -> i32
{
    exit 4
}
exit 0
"""
        self.assertEqual([f.name for f in tree_for(source).functions], ["helper"])
        ir, output = compile_cli(source)
        self.assertIn('user_helper', ir)
        self.assertIn('Holder_calculate', ir)
        self.assertEqual(output, b"Program exit with result 0\n")

    def test_lambda_pruned_by_variables_does_not_keep_global_alive(self):
        source = """\
fn helper := () -> i32
{
    exit 4
}
fn unused := []() -> i32
{
    exit helper()
}
exit 0
"""
        tree = tree_for(source)
        self.assertFalse(tree.functions)
        self.assertFalse(tree.statements)
        self.assertFalse(tree.generated_structs)
        ir, output = compile_cli(source)
        self.assertNotIn('user_helper', ir)
        self.assertNotIn('lambda.unused', ir)
        self.assertEqual(output, b"Program exit with result 0\n")

    def test_nested_lambda_in_dead_function_prunes_dependencies(self):
        source = """\
fn helper := () -> i32
{
    exit 8
}
fn dead := () -> i32
{
    fn callback := []() -> i32
    {
        exit helper()
    }
    exit callback()
}
exit 4
"""
        tree = tree_for(source)
        self.assertFalse(tree.functions)
        self.assertFalse(tree.generated_structs)
        ir, output = compile_cli(source)
        self.assertNotIn('user_helper', ir)
        self.assertNotIn('lambda.callback', ir)
        self.assertEqual(output, b"Program exit with result 4\n")

    def test_side_effecting_unused_variable_initializer_keeps_global(self):
        source = """\
fn show := () -> i32
{
    print(3)
    exit 9
}
i32 dropped{show()}
exit 4
"""
        tree = tree_for(source)
        self.assertEqual([f.name for f in tree.functions], ["show"])
        self.assertNotIn('Decl dropped', ast_text(tree))
        ir, output = compile_cli(source)
        self.assertIn('user_show', ir)
        self.assertEqual(output, b"3\nProgram exit with result 4\n")

    def test_removed_assignment_call_keeps_global(self):
        source = """\
fn show := () -> i32
{
    print(2)
    exit 3
}
i32 mut unused{1}
unused := show()
exit 4
"""
        tree = tree_for(source)
        self.assertEqual([f.name for f in tree.functions], ["show"])
        self.assertNotIn('Decl unused', ast_text(tree))
        ir, output = compile_cli(source)
        self.assertIn('user_show', ir)
        self.assertEqual(output, b"2\nProgram exit with result 4\n")

    def test_raw_and_checked_ast_flags_are_distinct(self):
        source = """\
fn unused := () -> i32
{
    exit 1
}
exit 4
"""
        with tempfile.TemporaryDirectory() as directory:
            input_file = Path(directory) / "input.txt"
            input_file.write_text(source)
            def ast(*args):
                result = subprocess.run([sys.executable, str(ROOT / 'compiler.py'),
                                         *args, str(input_file)], capture_output=True,
                                        text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            self.assertEqual(ast('--ast'), ast('--remove-unused', '--ast'))
            self.assertIn('Function unused', ast('--ast-checked'))
            self.assertNotIn('Function unused', ast('--remove-unused', '--ast-checked'))
            self.assertNotIn('Function unused', ast('--ast-checked', '--remove-unused'))

    def test_fixpoint_idempotence(self):
        source = """\
fn alone := () -> i32
{
    fn nested := []() -> i32
    {
        exit 4
    }
    exit nested()
}
i32 unused{3}
exit 4
"""
        tree = tree_for(source)
        before = ast_text(tree)
        tree.accept(RemoveUnusedVariables())
        self.assertEqual(before, ast_text(tree))
        self.assertFalse(tree.generated_structs)
        self.assertEqual([f.name for f in tree.functions], [])


if __name__ == '__main__':
    unittest.main()
