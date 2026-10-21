import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.config = self.base / "persistent config"
        self.config.mkdir()
        self.plugin = self.base / "plugin with spaces $literal"
        shutil.copytree(ROOT / "hooks", self.plugin / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "rules", self.plugin / "rules")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CCU_") and k != "CLAUDE_CONFIG_DIR"}
        self.env.update(CCU_CONFIG_DIR=str(self.config), CCU_MODE="away", CLAUDE_PLUGIN_ROOT=str(self.plugin))
        self.manifest = json.loads((ROOT / "hooks/hooks.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, action, payload):
        result = subprocess.run([sys.executable, str(self.plugin / "hooks/plugin_entry.py"), action],
                                input=json.dumps(payload), env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout else None

    def test_manifest_uses_standard_layout_without_double_hooks(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(plugin["name"], market["plugins"][0]["name"])
        self.assertEqual(market["plugins"][0]["source"], "./")
        self.assertNotIn("hooks", plugin)
        self.assertEqual(set(self.manifest["hooks"]), {"PreToolUse", "PermissionRequest", "Stop", "UserPromptSubmit", "SessionStart"})
        self.assertTrue(any(" health_check" in hook["command"]
                            for entry in self.manifest["hooks"]["SessionStart"] for hook in entry["hooks"]))
        for entries in self.manifest["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    self.assertLessEqual(hook["timeout"], 10)
                    self.assertNotIn("onFailure", hook)
                    words = shlex.split(hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(self.plugin)))
                    self.assertEqual(Path(words[1]), self.plugin / "hooks/plugin_entry.py")

    def test_session_context_all_sources_and_no_user_document_writes(self):
        rules = (ROOT / "rules/CLAUDE.md").read_text()
        for source in ("startup", "resume", "compact", "clear", "fork"):
            out = self.invoke("context", {"hook_event_name": "SessionStart", "source": source})
            self.assertEqual(out, {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": rules}})
        self.assertEqual(list(self.config.iterdir()), [])

    def test_state_and_audit_survive_plugin_removal(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode", "tool_input": {}, "session_id": "plugin-test"}
        out = self.invoke("unattended", payload)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        audit = self.config / "unattended-decisions.jsonl"
        self.assertTrue(audit.exists())
        shutil.rmtree(self.plugin)
        self.assertEqual(json.loads(audit.read_text())["selected"], "deny")
        self.assertEqual(json.loads(audit.read_text())["type"], "plan-deferred")

    def test_managed_registration_suppresses_plugin_actions_and_context(self):
        settings = {"hooks": {"Stop": [{"hooks": [{"command": shlex.join([sys.executable, str(self.config / 'hooks/unattended.py')])}]}]}}
        (self.config / "settings.json").write_text(json.dumps(settings))
        self.assertIsNone(self.invoke("context", {}))
        self.assertIsNone(self.invoke("unattended", {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode", "tool_input": {}}))
        self.assertFalse((self.config / "unattended-decisions.jsonl").exists())

    def test_missing_rules_and_malformed_settings_fail_open(self):
        (self.plugin / "rules/CLAUDE.md").unlink()
        self.assertIsNone(self.invoke("context", {}))
        (self.config / "settings.json").write_text("not JSON")
        self.assertIsNone(self.invoke("unattended", {}))
        self.assertIsNone(self.invoke("unknown", {}))

    def test_managed_migration_to_prepared_plugin_preserves_user_rules(self):
        rules = self.config / "CLAUDE.md"
        original = b"# My workflow\r\nKeep this exactly.  \r\n"
        rules.write_bytes(original)
        source = os.environ["CCU_SHFMT"]
        for options in (("--shfmt", source, "--mode", "away", "--no-push"),
                        ("--uninstall",), ("--prepare-plugin", "--shfmt", source, "--mode", "away")):
            subprocess.run([sys.executable, str(ROOT / "install.py"), "--claude-dir", str(self.config), *options],
                           capture_output=True, text=True, check=True)
        self.assertEqual(rules.read_bytes(), original)
        self.assertIsNotNone(self.invoke("context", {"source": "startup"}))
        self.assertIsNone(self.invoke("health_check", {"source": "startup"}))
        payload = {"hook_event_name": "PreToolUse", "session_id": "plugin-migration", "tool_name": "Bash",
                   "tool_input": {"command": "rm $EMPTY/path"}}
        out = self.invoke("bash_guard", payload)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        payload.update(tool_name="Edit", tool_input={"file_path": str(self.config / "settings.json")})
        out = self.invoke("config_guard", payload)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(rules.read_bytes(), original)
        (self.config / "tools/shfmt/shfmt").unlink()
        warning = self.invoke("health_check", {"source": "compact"})
        self.assertIn("install.py", warning["systemMessage"])
        self.assertIn("Shell safety", warning["hookSpecificOutput"]["additionalContext"])
