import json
import os
import shlex
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
                         sorted(["Write|Edit", "Bash", "mcp__ccd_session__spawn_task", "AskUserQuestion|ExitPlanMode"]))
        self.assertEqual(len(s["hooks"]["Stop"]), 1)
        self.assertNotIn("matcher", s["hooks"]["Stop"][0])
        self.assertEqual(len(s["hooks"]["PermissionRequest"]), 1)
        self.assertNotIn("askUserQuestionTimeout", s)
        self.assertNotIn("agentPushNotifEnabled", s)
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

    def test_update_preserves_suffix_byte_for_byte(self):
        self.run_install()
        path = self.dir / "CLAUDE.md"
        suffix = "\n\n## My custom workflow\n  Exact whitespace.\n\n"
        path.write_text(path.read_text() + suffix)
        before = path.read_text().split("<!-- claude-code-unattended:end -->", 1)[1]
        self.run_install()
        after = path.read_text().split("<!-- claude-code-unattended:end -->", 1)[1]
        self.assertEqual(before, after)

    def test_custom_config_and_preferences_survive_update(self):
        self.run_install("--idle-seconds", "600", "--mode", "off", "--max-stop-continuations", "2",
                         "--question-timeout", "60s", "--agent-push", "--proactive")
        self.run_install()
        config = json.loads((self.dir / "unattended-config.json").read_text())
        self.assertEqual(config, {"idle_seconds": 600, "mode": "off", "max_stop_continuations": 2})
        s = self.settings()
        self.assertEqual(s["askUserQuestionTimeout"], "60s")
        self.assertTrue(s["agentPushNotifEnabled"])
        self.assertEqual(s["outputStyle"], "Proactive")
        self.run_install("--uninstall")
        self.assertEqual(self.settings()["outputStyle"], "Proactive")

    def test_uninstall_preserves_audit_and_unrelated_event_hooks(self):
        s = self.settings()
        s["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "custom-stop"}]}]
        s["hooks"]["PermissionRequest"] = [OTHER_HOOK]
        (self.dir / "settings.json").write_text(json.dumps(s))
        self.run_install()
        audit = self.dir / "unattended-decisions.jsonl"
        audit.write_text('{"keep": "history"}\n')
        self.run_install("--uninstall")
        self.assertEqual(self.settings()["hooks"]["Stop"], s["hooks"]["Stop"])
        self.assertEqual(self.settings()["hooks"]["PermissionRequest"], [OTHER_HOOK])
        self.assertEqual(audit.read_text(), '{"keep": "history"}\n')
        self.assertFalse((self.dir / "hooks" / "unattended.py").exists())

    def test_invalid_parameters_and_config_do_not_mutate_settings(self):
        initial = (self.dir / "settings.json").read_bytes()
        for extra in (("--idle-seconds", "0"), ("--max-stop-continuations", "21")):
            result = subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(self.dir), *extra],
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        (self.dir / "unattended-config.json").write_text("[1]")
        result = subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(self.dir)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dir / "settings.json").read_bytes(), initial)
        self.assertFalse((self.dir / "hooks").exists())

    def test_installed_command_quoted_path_and_actual_protocol(self):
        custom_dir = self.dir / "config with spaces $literal"
        subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(custom_dir), "--mode", "away"],
                       capture_output=True, text=True, check=True)
        settings = json.loads((custom_dir / "settings.json").read_text())
        entry = next(e for e in settings["hooks"]["PreToolUse"] if e["matcher"] == "AskUserQuestion|ExitPlanMode")
        command = shlex.split(entry["hooks"][0]["command"])
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCU_") and k != "CLAUDE_CONFIG_DIR"}
        payload = {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode", "tool_input": {},
                   "session_id": "installed-test"}
        result = subprocess.run(command, input=json.dumps(payload), env=env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertTrue((custom_dir / "unattended-decisions.jsonl").exists())

    def test_backup_of_hook_is_kept_on_update(self):
        self.run_install()
        script = self.dir / "hooks" / "unattended.py"
        script.write_text("# old version\n")
        self.run_install()
        self.assertTrue(any(p.read_text() == "# old version\n" for p in script.parent.glob("unattended.py.bak-*")))

    def test_other_directory_same_script_name_is_preserved(self):
        s = self.settings()
        other = {"hooks": [{"type": "command", "command": "python3 /opt/other-tool/unattended.py"}]}
        s["hooks"]["Stop"] = [other]
        (self.dir / "settings.json").write_text(json.dumps(s))
        self.run_install()
        self.assertIn(other, self.settings()["hooks"]["Stop"])
        self.run_install("--uninstall")
        self.assertEqual(self.settings()["hooks"]["Stop"], [other])


class RedirectHookTest(unittest.TestCase):
    def test_denies_spawn_task(self):
        with tempfile.TemporaryDirectory() as directory:
            out = subprocess.run([sys.executable, str(REDIRECT)], input="{}",
                                 env={**os.environ, "CCU_CONFIG_DIR": directory},
                                 capture_output=True, text=True, check=True).stdout
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertIn("run_in_background", spec["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
