"""Practice 9 PDF examples: exact AST, fixpoint counts and conservative effects."""

import contextlib
import io
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from llvmlite import binding

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src.compiler as compiler

FIXTURES = ROOT / "tests" / "opt"


def checked(name):
    tree = compiler.Parser(compiler.lex((FIXTURES / f"{name}.txt").read_bytes())).parse_program()
    tree.accept(compiler.SemanticChecker())
    return tree


def ast(tree):
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        tree.dump()
        return output.getvalue()


class PDFAlignmentTests(unittest.TestCase):
    def test_exact_pdf_examples_and_round_counts(self):
        expected_rounds = {
            "pdf_warmup_cascade": 3,
            "pdf_warmup_shadowing": 2,
            "pdf_three_functions": 3,
            "pdf_additional_pure_vs_loud": 4,
        }
        original_counter = compiler._UnusedReadCounter

        class Counting(original_counter):
            rounds = 0

            def __init__(self):
                Counting.rounds += 1
                super().__init__()

        for name, rounds in expected_rounds.items():
            with self.subTest(example=name), patch.object(compiler, "_UnusedReadCounter", Counting):
                tree = checked(name)
                Counting.rounds = 0
                tree.accept(compiler.RemoveUnusedVariables())
                self.assertEqual(Counting.rounds, rounds)
                self.assertEqual(ast(tree), (FIXTURES / f"{name}.astc").read_text())

    def test_pdf_warmup_shadow_is_binding_specific(self):
        tree = checked("pdf_warmup_shadowing")
        outer_x = tree.statements[1]
        inner_x = tree.statements[2].body.statements[0]
        read_x = tree.statements[2].body.statements[1].value
        self.assertIs(read_x.decl, inner_x)
        self.assertIsNot(read_x.decl, outer_x)
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertNotIn(outer_x, tree.statements)
        self.assertIn(inner_x, tree.statements[1].body.statements)

    def test_pdf_three_functions_llvm_definitions_and_exit_four(self):
        tree = checked("pdf_three_functions")
        tree.accept(compiler.RemoveUnusedVariables())
        ir = str(tree.accept(compiler.CodeGen()))
        binding.parse_assembly(ir).verify()
        self.assertEqual(set(re.findall(r'^define .*?@"?([^"(]+)"?\(', ir, re.M)),
                         {"main", "user_twice"})
        self.assertIn('call i32 @"user_twice"(i32 2)', ir)

    def test_pdf_additional_loud_and_effectful_declaration_remain(self):
        tree = checked("pdf_additional_pure_vs_loud")
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertEqual([fn.name for fn in tree.functions], ["loud"])
        self.assertEqual([stmt.name for stmt in tree.statements], ["c"])
        self.assertEqual(tree.statements[0].initializers[0].name, "loud")
        binding.parse_assembly(str(tree.accept(compiler.CodeGen()))).verify()

    def test_pdf_counter_keeps_bump_but_not_peek(self):
        tree = checked("pdf_additional_counter")
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertIn("Decl t i32 const", ast(tree))
        self.assertNotIn("Decl u i32 const", ast(tree))
        self.assertIn("MemberCall bump", ast(tree))
        self.assertIn("MemberFn peek", ast(tree))
        binding.parse_assembly(str(tree.accept(compiler.CodeGen()))).verify()

    def test_unreachable_cycles_stay_due_to_syntactic_calls(self):
        tree = checked("function_chain")
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertIn("dead_recursive", [fn.name for fn in tree.functions])
        tree = checked("mutual_recursion_effects")
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertEqual([fn.name for fn in tree.functions], ["left", "right"])

    def test_lambdas_not_pruned_and_pure_call_is_removed(self):
        tree = checked("pure_lambda")
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertIn("Lambda calc", ast(tree))
        self.assertNotIn("Decl ignored", ast(tree))
        self.assertEqual(len(tree.generated_structs), 1)
        binding.parse_assembly(str(tree.accept(compiler.CodeGen()))).verify()

    def test_effectful_assignment_keeps_declaration(self):
        source = b"""fn show := () -> i32
{
    print(3)
    exit 4
}
i32 mut value{0}
value := show()
exit 4
"""
        tree = compiler.Parser(compiler.lex(source)).parse_program()
        tree.accept(compiler.SemanticChecker())
        tree.accept(compiler.RemoveUnusedVariables())
        self.assertEqual([type(stmt) for stmt in tree.statements],
                         [compiler.DeclNode, compiler.AssignNode])
        binding.parse_assembly(str(tree.accept(compiler.CodeGen()))).verify()


if __name__ == "__main__":
    unittest.main()
