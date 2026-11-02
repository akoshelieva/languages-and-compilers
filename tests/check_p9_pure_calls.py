"""Practice 9 Additional Task: conservative effects and dead pure calls."""

import contextlib
import io
from pathlib import Path
import re
import subprocess
import sys
import unittest

from llvmlite import binding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (Parser, RemoveUnusedVariables, SemanticChecker,
                          _CallEffects, ExprStmtNode, CallNode, MemberCallNode,
                          lex, CodeGen)

FIXTURES = ROOT / "tests" / "opt"


def parse_fixture(name):
    data = (FIXTURES / f"{name}.txt").read_bytes()
    tree = Parser(lex(data)).parse_program()
    tree.accept(SemanticChecker())
    return tree


def ast_text(tree):
    with io.StringIO() as stream, contextlib.redirect_stdout(stream):
        tree.dump()
        return stream.getvalue()


def optimized(name):
    tree = parse_fixture(name)
    tree.accept(RemoveUnusedVariables())
    return tree


def ir_text(tree):
    ir = str(tree.accept(CodeGen()))
    binding.parse_assembly(ir).verify()
    return ir


def user_definitions(ir):
    return set(re.findall(r'^define .*?@"?(user_[a-zA-Z0-9_]+)"?\(', ir, re.M))


class PureCallOptimizationTests(unittest.TestCase):
    def test_effect_fixpoint_and_indirect_print(self):
        tree = parse_fixture("indirect_print")
        effects = _CallEffects(tree)
        self.assertEqual({fn.name for fn in effects.effectful}, {"report", "wrapper"})
        after = optimized("indirect_print")
        self.assertEqual([fn.name for fn in after.functions], ["report", "wrapper"])
        self.assertIn("ExprStmt\n    Call wrapper", ast_text(after))
        self.assertEqual(user_definitions(ir_text(after)), {"user_report", "user_wrapper"})

    def test_pure_chain_cascades_to_no_global_functions(self):
        tree = parse_fixture("pure_call_chain")
        self.assertEqual(_CallEffects(tree).effectful, set())
        tree.accept(RemoveUnusedVariables())
        self.assertFalse(tree.functions)
        self.assertEqual(len(tree.statements), 0)
        self.assertFalse(user_definitions(ir_text(tree)))
        first = ast_text(tree)
        tree.accept(RemoveUnusedVariables())
        self.assertEqual(first, ast_text(tree))

    def test_pure_calls_preserve_nested_read_order(self):
        tree = optimized("pure_nested_read")
        self.assertFalse(tree.functions)
        self.assertEqual(len(tree.statements), 2)
        self.assertTrue(all(isinstance(stmt, ExprStmtNode) for stmt in tree.statements))
        self.assertEqual([stmt.expression.name for stmt in tree.statements], ["read", "read"])
        self.assertEqual([type(stmt.expression) for stmt in tree.statements], [CallNode]*2)
        self.assertFalse(user_definitions(ir_text(tree)))

    def test_pure_member_call_elided_not_method_definition(self):
        tree = optimized("pure_callable")
        self.assertFalse(tree.statements)
        self.assertFalse(tree.functions)
        ir = ir_text(tree)
        self.assertIn('Adder_call', ir)
        main_body = ir.split('define i32 @"main"()', 1)[1]
        self.assertNotIn('call.member.call', main_body)

    def test_pure_lambda_and_capture_pruned(self):
        tree = optimized("pure_lambda")
        self.assertFalse(tree.statements)
        self.assertFalse(tree.generated_structs)
        self.assertNotIn('lambda.calc', ir_text(tree))

    def test_mutating_capture_keeps_lambda_call(self):
        tree = optimized("mut_capture_call")
        self.assertEqual(len(tree.generated_structs), 1)
        self.assertEqual(len(tree.statements), 4)
        for statement in tree.statements[-2:]:
            self.assertIsInstance(statement, ExprStmtNode)
            self.assertIsInstance(statement.expression, MemberCallNode)
            self.assertTrue(statement.expression.function.mutating)
        self.assertIn('CallableCall bump', ast_text(tree))
        ir_text(tree)

    def test_while_and_recursion_preserve_unused_calls(self):
        tree = parse_fixture("while_and_recursion")
        self.assertEqual({fn.name for fn in _CallEffects(tree).effectful}, {"counting", "loop"})
        tree.accept(RemoveUnusedVariables())
        self.assertEqual(len(tree.statements), 2)
        self.assertEqual(len(tree.functions), 2)
        self.assertEqual(user_definitions(ir_text(tree)), {"user_counting", "user_loop"})

    def test_pure_callable_preserves_effectful_arguments(self):
        tree = optimized("member_nested_read")
        self.assertEqual(len(tree.statements), 2)
        self.assertEqual([stmt.expression.name for stmt in tree.statements], ["read", "read"])
        self.assertNotIn('call.member.call', ir_text(tree).split('define i32 @"main"()', 1)[1])

    def test_transitive_mutating_member_chain_is_preserved(self):
        tree = parse_fixture("mutating_member_chain")
        effects = _CallEffects(tree)
        members = {fn.name: fn for fn in tree.structs[0].methods}
        self.assertIn(members['change'], effects.effectful)
        self.assertIn(members['proxy'], effects.effectful)
        tree.accept(RemoveUnusedVariables())
        self.assertEqual(len(tree.statements), 2)
        self.assertIsInstance(tree.statements[-1], ExprStmtNode)
        self.assertIn('call.member.proxy', ir_text(tree))

    def test_effect_propagates_from_nested_lambda_to_global(self):
        tree = parse_fixture("nested_lambda_indirect_print")
        effects = _CallEffects(tree)
        self.assertIn(tree.functions[0], effects.effectful)
        tree.accept(RemoveUnusedVariables())
        self.assertEqual([fn.name for fn in tree.functions], ['outer'])
        self.assertEqual(len(tree.generated_structs), 1)
        self.assertIn('call.outer', ir_text(tree))

    def test_mutual_recursion_is_not_assumed_pure(self):
        source = b"""fn left := (i32 n) -> i32
{
    if n == 0
    {
        exit 1
    }
    exit right(n - 1)
}
fn right := (i32 n) -> i32
{
    if n == 0
    {
        exit 2
    }
    exit left(n - 1)
}
i32 unused{left(2)}
exit 4
"""
        tree = Parser(lex(source)).parse_program()
        tree.accept(SemanticChecker())
        self.assertEqual({fn.name for fn in _CallEffects(tree).effectful},
                         {'left', 'right'})
        tree.accept(RemoveUnusedVariables())
        self.assertEqual([fn.name for fn in tree.functions], ['left', 'right'])
        self.assertEqual(user_definitions(ir_text(tree)), {'user_left', 'user_right'})

    def test_semantic_errors_rejected_before_optimization(self):
        for name in ("p9_discarded_call_wrong_type", "p9_unused_mutating_const_object"):
            src = ROOT / "tests" / "err" / f"{name}.txt"
            expected = src.with_suffix('.expected').read_text().strip()
            for flags in ([], ["--remove-unused"]):
                with self.subTest(name=name, flags=flags):
                    result = subprocess.run([sys.executable, str(ROOT / 'compiler.py'),
                                             *flags, '--ast-checked', str(src)],
                                            cwd=ROOT, capture_output=True, text=True,
                                            timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stderr.strip(), expected)

    def test_checked_ast_golden_and_raw_ast_unchanged(self):
        for name in ('pure_call_chain', 'pure_nested_read', 'indirect_print',
                     'pure_callable', 'pure_lambda', 'mut_capture_call',
                     'while_and_recursion', 'member_nested_read',
                     'mutating_member_chain', 'nested_lambda_indirect_print'):
            src = FIXTURES / f'{name}.txt'
            with self.subTest(name=name):
                tree = optimized(name)
                self.assertEqual(ast_text(tree), src.with_suffix('.astc').read_text())
                # Optimization does not alter the parser's raw AST.
                raw = Parser(lex(src.read_bytes())).parse_program()
                self.assertNotIn('ExprStmt', ast_text(raw))


if __name__ == '__main__':
    unittest.main()
