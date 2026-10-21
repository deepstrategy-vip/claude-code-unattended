import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import shell_ast as ast


class ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ast.parse("true") is None:
            raise RuntimeError("Verified shfmt is required: run dependency bootstrap or set CCU_SHFMT")

    def test_execution_is_distinct_from_data(self):
        command = "cat <<'EOF'\nrm $X\nEOF\necho \"$(rm $Y)\" # rm $Z"
        tree = ast.parse(command)
        self.assertEqual([ast.literal_word(call["Args"][0]) for call in ast.commands(tree)],
                         ["cat", "echo", "rm"])

    def test_unquoted_heredoc_substitution_executes(self):
        tree = ast.parse("cat <<EOF\n$(rm $X)\nEOF")
        self.assertEqual([ast.literal_word(call["Args"][0]) for call in ast.commands(tree)], ["cat", "rm"])

    def test_literals_and_dynamic_parts(self):
        command = r'''echo 'single space' "double space" r\m "x\$X" "$X" ${S:?}'''
        args = next(ast.commands(ast.parse(command)))["Args"]
        self.assertEqual([ast.literal_word(word) for word in args],
                         ["echo", "single space", "double space", "rm", "x$X", None, None])

    def test_utf8_source_offsets(self):
        command = 'echo 中文; rm "$X"'
        call = list(ast.commands(ast.parse(command)))[1]
        self.assertEqual(ast.word_text(call, command), 'rm "$X"')
        self.assertEqual(ast.word_text(call["Args"][1], command), '"$X"')
        with self.assertRaises(ValueError):
            ast.word_text({"Pos": {"Offset": -1}, "End": {"Offset": 3}}, command)

    def test_assignment_and_guard_are_structured(self):
        tree = ast.parse('D=$(pwd); rm "${D:?}"')
        calls = list(ast.commands(tree))
        self.assertEqual(calls[0]["Assigns"][0]["Name"]["Value"], "D")
        expansions = [n for n in ast.walk(tree) if n.get("Type") == "ParamExp"]
        self.assertEqual(expansions[0]["Exp"]["Op"], ":?")

    def test_zsh_only_syntax(self):
        tree = ast.parse("print -l **/*.py(.N)")
        self.assertEqual(ast.literal_word(next(ast.commands(tree))["Args"][0]), "print")

    def test_syntax_and_input_errors_fail_open(self):
        self.assertIsNone(ast.parse("echo 'unterminated"))
        self.assertIsNone(ast.parse(None))
        self.assertIsNone(ast.parse("echo \x00"))
        with mock.patch.object(ast, "MAX_COMMAND_BYTES", 3):
            self.assertIsNone(ast.parse("echo too long"))


class FailureTest(unittest.TestCase):
    def fake_parser(self, source):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "parser"
        path.write_text("#!/usr/bin/env python3\n" + source)
        path.chmod(0o700)
        return str(path)

    def test_missing_dependency_fails_open(self):
        with mock.patch.dict(os.environ, {"CCU_SHFMT": "/nonexistent/verified-parser"}):
            self.assertIsNone(ast.parse("rm $X"))

    def test_invalid_output_fails_open(self):
        for output in [b"not json", b"[]", b'{"Type":"CallExpr"}', b'{"Type":"File","Stmts":false}']:
            with self.subTest(output=output), mock.patch.object(ast, "_run_parser", return_value=output):
                self.assertIsNone(ast.parse("rm $X"))

    def test_output_bound(self):
        parser = self.fake_parser("import sys\nsys.stdout.write('x' * 65536)\n")
        with mock.patch.dict(os.environ, {"CCU_SHFMT": parser}), mock.patch.object(ast, "MAX_OUTPUT_BYTES", 4096):
            self.assertIsNone(ast.parse("rm $X"))

    def test_timeout_bound(self):
        parser = self.fake_parser("import time\ntime.sleep(5)\n")
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"CCU_SHFMT": parser}), mock.patch.object(ast, "PARSE_TIMEOUT", 0.05):
            self.assertIsNone(ast.parse("rm $X"))
        self.assertLess(time.monotonic() - started, 2)

    def test_timeout_when_parser_does_not_consume_input(self):
        parser = self.fake_parser("import time\ntime.sleep(5)\n")
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"CCU_SHFMT": parser}), mock.patch.object(ast, "PARSE_TIMEOUT", 0.05):
            self.assertIsNone(ast.parse("x" * ast.MAX_COMMAND_BYTES))
        self.assertLess(time.monotonic() - started, 2)

    def test_nonzero_exit_fails_open(self):
        parser = self.fake_parser("raise SystemExit(1)\n")
        with mock.patch.dict(os.environ, {"CCU_SHFMT": parser}):
            self.assertIsNone(ast.parse("rm $X"))

    def test_default_config_directory_discovery(self):
        with mock.patch.dict(os.environ, {"CCU_CONFIG_DIR": "/example/config"}, clear=True):
            self.assertEqual(ast.parser_path(), Path("/example/config/tools/shfmt/shfmt"))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/example/claude"}, clear=True):
            self.assertEqual(ast.parser_path(), Path("/example/claude/tools/shfmt/shfmt"))


if __name__ == "__main__":
    unittest.main()
