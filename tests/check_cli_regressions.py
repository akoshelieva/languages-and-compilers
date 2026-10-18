
from pathlib import Path
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import check as runner
from src import compiler


class CLIRegressions(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / 'input.txt'
        self.output = self.root / 'output.ll'
        self.source.write_bytes(b'exit 7\n')
        self.output.write_bytes(b'OLD IR\n')

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = compiler.main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_missing_input_removes_stale_ir(self):
        code, stdout, stderr = self.cli(self.root / 'missing.txt', self.output)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, '')
        self.assertIn('compiler I/O error:', stderr)
        self.assertNotIn('Traceback', stderr)
        self.assertFalse(self.output.exists())

    def test_invalid_source_removes_stale_ir(self):
        self.source.write_bytes(b'@\n')
        code, _, stderr = self.cli(self.source, self.output)
        self.assertEqual(code, 1)
        self.assertIn('line 1:1:', stderr)
        self.assertFalse(self.output.exists())

    def test_same_input_and_output_preserves_source(self):
        before = self.source.read_bytes()
        code, _, stderr = self.cli(self.source, self.source)
        self.assertEqual(code, 1)
        self.assertIn('input and output paths must be different', stderr)
        self.assertEqual(self.source.read_bytes(), before)

    def test_ast_and_tokens_are_read_only(self):
        for mode in ('--ast', '--tokens'):
            with self.subTest(mode=mode):
                code, stdout, stderr = self.cli(mode, self.source)
                self.assertEqual(code, 0)
                self.assertTrue(stdout)
                self.assertEqual(stderr, '')
                self.assertEqual(self.source.read_bytes(), b'exit 7\n')
                self.assertEqual(self.output.read_bytes(), b'OLD IR\n')

    def test_missing_backend_reports_failure_without_traceback(self):
        golden = self.source.with_suffix('.expected')
        golden.write_bytes(b'Program exit with result 7\n')
        with patch.object(runner.shutil, 'which', return_value=None):
            passed, detail = runner.test_program(self.source, golden)
        self.assertFalse(passed)
        self.assertIn('neither lli nor clang', detail)
        self.assertNotIn('Traceback', detail)

    def test_clang_build_failure_keeps_binary_stderr(self):
        golden = self.source.with_suffix('.expected')
        golden.write_bytes(b'Program exit with result 7\n')
        failed_build = subprocess.CompletedProcess(['clang'], 1, b'', b'clang failed\n')
        with patch.object(runner.shutil, 'which', side_effect=[None, '/mock/clang']):
            with patch.object(runner, 'run', return_value=failed_build) as build:
                passed, detail = runner.test_program(self.source, golden)
        self.assertFalse(passed)
        self.assertIn('clang failed', detail)
        self.assertTrue(build.call_args.kwargs['binary'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
