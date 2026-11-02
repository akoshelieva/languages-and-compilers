"""Practice 9 Task 3: optimization fixtures and Python AST subtree lifetime."""

import contextlib
import gc
import io
from pathlib import Path
import re
import sys
import unittest
import weakref

from llvmlite import binding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.compiler import (CodeGen, DeclNode, LambdaNode, Parser,
                          RemoveUnusedVariables, SemanticChecker, lex)

FIXTURES = ROOT / "tests" / "opt"


def checked_file(name):
    source = (FIXTURES / f"{name}.txt").read_bytes()
    tree = Parser(lex(source)).parse_program()
    tree.accept(SemanticChecker())
    return tree


def ast_text(tree):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        tree.dump()
    return output.getvalue()


class OptimizationSuiteTests(unittest.TestCase):
    def test_all_fixtures_have_all_three_files(self):
        fixtures = sorted(FIXTURES.glob("*.txt"))
        self.assertGreaterEqual(len(fixtures), 6)
        for source in fixtures:
            with self.subTest(source=source.name):
                self.assertTrue(source.with_suffix(".astc").is_file())
                self.assertTrue(source.with_suffix(".expected").is_file())

    def test_snapshots_and_fixpoint(self):
        for source in sorted(FIXTURES.glob("*.txt")):
            with self.subTest(source=source.name):
                tree = checked_file(source.stem)
                tree.accept(RemoveUnusedVariables())
                expected = source.with_suffix(".astc").read_text(encoding="utf-8")
                self.assertEqual(ast_text(tree), expected)
                tree.accept(RemoveUnusedVariables())
                self.assertEqual(ast_text(tree), expected)

    def test_twice_ir_has_only_twice_and_main(self):
        tree = checked_file("warmup_unused_functions")
        tree.accept(RemoveUnusedVariables())
        ir = str(tree.accept(CodeGen()))
        binding.parse_assembly(ir).verify()
        self.assertEqual(set(re.findall(r'^define .*?@"?([^"(]+)"?\(', ir, re.M)),
                         {"user_twice", "main"})

    def test_scopes_and_shadows(self):
        tree = checked_file("if_scopes")
        tree.accept(RemoveUnusedVariables())
        self.assertFalse(any(isinstance(node, DeclNode) for node in tree.statements))
        self.assertIn("Decl value i32 const", ast_text(tree))
        self.assertNotIn("Decl discarded", ast_text(tree))
        self.assertNotIn("Decl shadow_unused", ast_text(tree))

    def test_lambda_and_callable_keep_reachable_globals(self):
        tree = checked_file("lambda_scopes")
        tree.accept(RemoveUnusedVariables())
        self.assertEqual([fn.name for fn in tree.functions], ["helper"])
        self.assertEqual(len(tree.generated_structs), 1)
        self.assertIn("Lambda show", ast_text(tree))
        self.assertNotIn("Lambda unused", ast_text(tree))
        callable_tree = checked_file("callable_objects")
        callable_tree.accept(RemoveUnusedVariables())
        self.assertEqual([fn.name for fn in callable_tree.functions], ["helper"])
        self.assertIn("CallableCall twice", ast_text(callable_tree))
        self.assertIn("MemberFn call", ast_text(callable_tree))

    def test_no_optimization_and_self_feeding_limitation(self):
        live = checked_file("no_optimization")
        before = ast_text(live)
        live.accept(RemoveUnusedVariables())
        self.assertEqual(ast_text(live), before)
        self_feeding = checked_file("self_feeding")
        self_feeding.accept(RemoveUnusedVariables())
        self.assertIn("Decl x i32 mut", ast_text(self_feeding))
        self.assertIn("Assign x", ast_text(self_feeding))

    def test_removed_declarations_functions_and_subtrees_are_collected(self):
        # The live Program remains reachable; only removed subtrees may die.
        tree = checked_file("warmup_unused_variables")
        unused = weakref.ref(tree.statements[1])
        initializer = weakref.ref(tree.statements[1].initializers[0])
        tree.accept(RemoveUnusedVariables())  # transient visitor leaves no root
        gc.collect()
        self.assertIsNone(unused())
        self.assertIsNone(initializer())
        self.assertEqual(len(tree.statements), 1)

        tree = checked_file("warmup_unused_functions")
        dead_function = weakref.ref(tree.functions[0])
        dead_exit = weakref.ref(tree.functions[0].body[0])
        tree.accept(RemoveUnusedVariables())
        gc.collect()
        self.assertIsNone(dead_function())
        self.assertIsNone(dead_exit())
        self.assertEqual([fn.name for fn in tree.functions], ["twice"])

        tree = checked_file("lambda_scopes")
        body = tree.statements[1].body
        dead_lambda = body.statements[2]
        self.assertIsInstance(dead_lambda, LambdaNode)
        lambda_ref = weakref.ref(dead_lambda)
        generated_ref = weakref.ref(dead_lambda.synthetic_struct)
        del dead_lambda
        tree.accept(RemoveUnusedVariables())
        gc.collect()
        self.assertIsNone(lambda_ref())
        self.assertIsNone(generated_ref())
        self.assertEqual(len(tree.generated_structs), 1)


if __name__ == "__main__":
    unittest.main()
