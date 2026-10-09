import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.py"
REDIRECT = ROOT / "hooks" / "spawn_task_redirect.py"

OTHER_HOOK = {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "prettier --write"}]}
USER_MD = "# My own rules\n- keep answers short\n"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "settings.json").write_text(json.dumps({
            "theme": "dark",
            "hooks": {"PreToolUse": [OTHER_HOOK]},
        }))
        (self.dir / "CLAUDE.md").write_text(USER_MD)

    def tearDown(self):
        self.tmp.cleanup()

    def run_install(self, *extra):
        subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(self.dir), *extra],
                       capture_output=True, text=True, check=True)

    def settings(self):
        return json.loads((self.dir / "settings.json").read_text())

    def test_install_is_idempotent_and_preserves_user_config(self):
        self.run_install()
        self.run_install()
        s = self.settings()
        self.assertEqual(s["theme"], "dark")
        self.assertTrue(s["inputNeededNotifEnabled"])
        pre = s["hooks"]["PreToolUse"]
        self.assertIn(OTHER_HOOK, pre)
        self.assertEqual(sorted(e["matcher"] for e in pre),
                         sorted(["Write|Edit", "Bash", "mcp__ccd_session__spawn_task"]))
        md = (self.dir / "CLAUDE.md").read_text()
        self.assertTrue(md.startswith(USER_MD.rstrip()))
        self.assertEqual(md.count("claude-code-unattended:begin"), 1)
        self.assertTrue((self.dir / "hooks" / "bash_guard.py").exists())

    def test_install_replaces_legacy_unmarked_entries(self):
        s = self.settings()
        s["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/usr/bin/python3 ~/.claude/hooks/bash_guard.py"}]})
        (self.dir / "settings.json").write_text(json.dumps(s))
        self.run_install()
        bash_entries = [e for e in self.settings()["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
        self.assertEqual(len(bash_entries), 1)

    def test_no_push_flag(self):
        self.run_install("--no-push")
        self.assertNotIn("inputNeededNotifEnabled", self.settings())

    def test_uninstall_restores_user_config(self):
        self.run_install()
        self.run_install("--uninstall")
        s = self.settings()
        self.assertEqual(s["hooks"]["PreToolUse"], [OTHER_HOOK])
        self.assertEqual((self.dir / "CLAUDE.md").read_text(), USER_MD)
        self.assertFalse((self.dir / "hooks" / "bash_guard.py").exists())

    def test_refuses_invalid_settings(self):
        (self.dir / "settings.json").write_text("{ not json")
        r = subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(self.dir)],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.dir / "settings.json").read_text(), "{ not json")


class RedirectHookTest(unittest.TestCase):
    def test_denies_spawn_task(self):
        out = subprocess.run([sys.executable, str(REDIRECT)], input="{}",
                             capture_output=True, text=True, check=True).stdout
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertIn("run_in_background", spec["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
