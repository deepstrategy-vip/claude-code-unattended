import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
spec = importlib.util.spec_from_file_location("health_check", ROOT / "hooks/health_check.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTest(unittest.TestCase):
    def test_pinned_version_matches_dependency_installer(self):
        dependency_spec = importlib.util.spec_from_file_location("dependencies", ROOT / "dependencies.py")
        dependencies = importlib.util.module_from_spec(dependency_spec)
        dependency_spec.loader.exec_module(dependencies)
        self.assertEqual(health.PINNED_VERSION, dependencies.VERSION)

    def test_healthy_is_silent(self):
        with patch.object(health.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v3.14.1\n", "")) as run:
            self.assertIsNone(health.check())
        args, kwargs = run.call_args
        self.assertEqual(args[0][-1], "--version")
        self.assertEqual(kwargs["timeout"], 2)
        self.assertTrue(kwargs["check"])

    def test_missing_timeout_corrupt_and_wrong_version_are_nonblocking_advisories(self):
        cases = [FileNotFoundError(), subprocess.TimeoutExpired("shfmt", 2),
                 subprocess.CalledProcessError(1, "shfmt"),
                 subprocess.CompletedProcess([], 0, "v0.0.0", "")]
        for result in cases:
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with self.subTest(result=type(result).__name__), patch.object(health.subprocess, "run", **kwargs):
                output = health.check()
                self.assertIn("install.py", output["systemMessage"])
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
                self.assertIn("Shell safety", output["hookSpecificOutput"]["additionalContext"])
                self.assertNotIn("decision", output)
                self.assertNotIn("permissionDecision", output["hookSpecificOutput"])
