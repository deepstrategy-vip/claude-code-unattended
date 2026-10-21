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
                         sorted(["Write|Edit", "Bash|Edit|MultiEdit|Write", "Bash", "mcp__ccd_session__spawn_task", "AskUserQuestion|ExitPlanMode"]))
        self.assertEqual(len(s["hooks"]["Stop"]), 1)
        self.assertNotIn("matcher", s["hooks"]["Stop"][0])
        self.assertEqual(len(s["hooks"]["PermissionRequest"]), 1)
        self.assertEqual(len(s["hooks"]["UserPromptSubmit"]), 1)
        self.assertNotIn("matcher", s["hooks"]["UserPromptSubmit"][0])
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
        self.assertFalse((self.dir / "hooks" / "shell_ast.py").exists())
        self.assertFalse((self.dir / "hooks" / "config_guard.py").exists())
        self.assertFalse((self.dir / "tools" / "shfmt" / "shfmt").exists())
        self.assertNotIn("UserPromptSubmit", s["hooks"])

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

    def test_dependency_failure_does_not_change_settings_or_rules(self):
        bad = self.dir / "fake-shfmt"
        bad.write_text("this must never execute")
        settings = (self.dir / "settings.json").read_bytes()
        rules = (self.dir / "CLAUDE.md").read_bytes()
        result = subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(self.dir),
                                 "--shfmt", str(bad)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dir / "settings.json").read_bytes(), settings)
        self.assertEqual((self.dir / "CLAUDE.md").read_bytes(), rules)
        self.assertFalse((self.dir / "hooks").exists())

    def test_prepare_plugin_only_and_idempotency(self):
        settings = (self.dir / "settings.json").read_bytes()
        rules = (self.dir / "CLAUDE.md").read_bytes()
        self.run_install("--prepare-plugin", "--mode", "away")
        snapshot = {p.relative_to(self.dir): p.read_bytes() for p in self.dir.rglob("*") if p.is_file()}
        self.run_install("--prepare-plugin")
        self.assertEqual(snapshot, {p.relative_to(self.dir): p.read_bytes() for p in self.dir.rglob("*") if p.is_file()})
        self.assertEqual((self.dir / "settings.json").read_bytes(), settings)
        self.assertEqual((self.dir / "CLAUDE.md").read_bytes(), rules)
        self.assertFalse((self.dir / "hooks" / "unattended.py").exists())
        self.assertTrue((self.dir / "tools" / "shfmt" / "shfmt").exists())

    def test_enabled_plugin_refuses_duplicate_managed_install(self):
        s = self.settings()
        s["enabledPlugins"] = {"claude-code-unattended@claude-code-unattended": True}
        (self.dir / "settings.json").write_text(json.dumps(s))
        before = (self.dir / "settings.json").read_bytes()
        result = subprocess.run([sys.executable, str(INSTALL), "--claude-dir", str(self.dir)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dir / "settings.json").read_bytes(), before)
        self.assertFalse((self.dir / "tools").exists())

    def test_reinstall_without_changes_creates_no_new_backups(self):
        self.run_install()
        before = {p.relative_to(self.dir): p.read_bytes() for p in self.dir.rglob("*") if p.is_file()}
        self.run_install()
        self.assertEqual(before, {p.relative_to(self.dir): p.read_bytes() for p in self.dir.rglob("*") if p.is_file()})

    def test_user_rules_bytes_survive_install_update_and_uninstall(self):
        path = self.dir / "CLAUDE.md"
        for original in (b"# User\r\n- keep this  \r\n\r\n", b"# No final newline  ",
                         b"# Mixed\r\n\n- spaces \t\r\n\n\n", b" \r\n  \n"):
            with self.subTest(original=original):
                path.write_bytes(original)
                self.run_install()
                installed = path.read_bytes()
                self.assertEqual(installed.split(b"<!-- claude-code-unattended:begin -->", 1)[0], original)
                suffix = b"\r\n\r\n## Custom workflow\r\nExact spaces  \r\n"
                path.write_bytes(installed + suffix)
                self.run_install()
                self.assertEqual(path.read_bytes(), installed + suffix)
                self.run_install("--uninstall")
                self.assertEqual(path.read_bytes(), original + suffix)

    def test_legacy_marker_update_preserves_outside_bytes(self):
        path = self.dir / "CLAUDE.md"
        prefix = b"# Existing\r\n  \r\n"
        suffix = b"\n\r\n## User suffix\r\n  end  "
        path.write_bytes(prefix + b"<!-- claude-code-unattended:begin -->\nold\n<!-- claude-code-unattended:end -->" + suffix)
        self.run_install()
        updated = path.read_bytes()
        self.assertTrue(updated.startswith(prefix))
        self.assertTrue(updated.endswith(suffix))

    def test_installed_guards_use_private_parser_without_environment_override(self):
        self.run_install()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCU_") and k != "CLAUDE_CONFIG_DIR"}
        cases = [
            ("Bash", {"tool_name": "Bash", "tool_input": {"command": "rm $EMPTY/path"}}),
            ("Bash|Edit|MultiEdit|Write", {"tool_name": "Edit", "tool_input": {
                "file_path": str(self.dir / "settings.json"), "new_string": "private-value"}}),
        ]
        for matcher, payload in cases:
            entry = next(e for e in self.settings()["hooks"]["PreToolUse"] if e["matcher"] == matcher)
            result = subprocess.run(shlex.split(entry["hooks"][0]["command"]), env=env,
                                    input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "installed-guards", **payload}),
                                    capture_output=True, text=True, check=True, timeout=5)
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("private-value", (self.dir / "unattended-decisions.jsonl").read_text())


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
