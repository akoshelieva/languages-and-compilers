"""Final Practice 9 regressions: conditional effects, call chains, and GC."""

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
from src.compiler import (CodeGen, DeclNode, Parser,
                          RemoveUnusedVariables, SemanticChecker, lex)

FIXTURES = ROOT / "tests" / "opt"


def optimized(name):
    tree = Parser(lex((FIXTURES / f"{name}.txt").read_bytes())).parse_program()
    tree.accept(SemanticChecker())
    tree.accept(RemoveUnusedVariables())
    return tree


def ir_of(tree):
    result = str(tree.accept(CodeGen()))
    binding.parse_assembly(result).verify()
    return result


def definitions(ir):
    return set(re.findall(r'^define .*?@"?([^"(]+)"?\(', ir, re.M))


class FinalOptimizationAudit(unittest.TestCase):
    def test_condition_local_effects_are_not_hoisted(self):
        tree = optimized("branch_read_effects")
        self.assertEqual([fn.name for fn in tree.functions], ["identity"])
        branches = (tree.statements[0].body, tree.statements[0].else_block)
        self.assertEqual([len(block.statements) for block in branches], [1, 1])
        for block in branches:
            self.assertIsInstance(block.statements[0], DeclNode)
            self.assertEqual(block.statements[0].initializers[0].name, "identity")
            self.assertEqual(block.statements[0].initializers[0].args[0].name, "read")
        self.assertEqual(ir_of(tree).count('call i32 (i8*, ...) @"scanf"'), 3)

    def test_pure_wrapper_removed_but_effectful_argument_kept(self):
        tree = optimized("effectful_arg_in_pure_call")
        self.assertEqual([fn.name for fn in tree.functions], ["emit", "passthrough"])
        self.assertEqual(len(tree.statements), 1)
        self.assertIsInstance(tree.statements[0], DeclNode)
        self.assertEqual(tree.statements[0].initializers[0].name, "passthrough")
        self.assertEqual(tree.statements[0].initializers[0].args[0].name, "emit")
        self.assertEqual(definitions(ir_of(tree)), {"main", "user_emit", "user_passthrough"})

    def test_mutually_recursive_functions_are_conservatively_retained(self):
        tree = optimized("mutual_recursion_effects")
        self.assertEqual([fn.name for fn in tree.functions], ["left", "right"])
        self.assertIsInstance(tree.statements[0], DeclNode)
        self.assertEqual(definitions(ir_of(tree)),
                         {"main", "user_left", "user_right"})

    def test_new_snapshots_are_stable_across_optimizer_runs(self):
        for name in ("branch_read_effects", "effectful_arg_in_pure_call",
                     "mutual_recursion_effects"):
            with self.subTest(name=name):
                tree = optimized(name)
                with io.StringIO() as out, contextlib.redirect_stdout(out):
                    tree.dump()
                    first = out.getvalue()
                self.assertEqual(first, (FIXTURES / f"{name}.astc").read_text())
                tree.accept(RemoveUnusedVariables())
                with io.StringIO() as out, contextlib.redirect_stdout(out):
                    tree.dump()
                    self.assertEqual(out.getvalue(), first)

    def test_unused_pure_functions_are_released_with_root_alive(self):
        tree = Parser(lex((FIXTURES / "pdf_additional_pure_vs_loud.txt").read_bytes())).parse_program()
        tree.accept(SemanticChecker())
        square = weakref.ref(tree.functions[0])
        quiet = weakref.ref(tree.functions[1])
        tree.accept(RemoveUnusedVariables())
        gc.collect()
        self.assertIsNone(square())
        self.assertIsNone(quiet())
        self.assertEqual([fn.name for fn in tree.functions], ["loud"])


if __name__ == "__main__":
    unittest.main()
