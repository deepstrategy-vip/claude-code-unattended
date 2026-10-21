"""MIT upstream scenarios adapted from config-guard's pinned test suite.

Copyright (c) 2026 Karan Bansal. See third_party/config-guard/LICENSE.
All commands are parsed as data; none of the classified commands are executed.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))
spec = importlib.util.spec_from_file_location("config_guard", HOOKS / "config_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ConfigGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cdir = self.root / "custom config"
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.cdir.mkdir()
        self.env = patch.dict(os.environ, {"CCU_CONFIG_DIR": str(self.cdir)}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def payload(self, name="Edit", **inputs):
        return {"hook_event_name": "PreToolUse", "session_id": "test-session",
                "cwd": str(self.cwd), "tool_name": name, "tool_input": inputs}

    def assertBlocked(self, path, pattern=None, **extra):
        found = guard.check_file_path(path, str(self.cwd), **extra)
        self.assertTrue(found, path)
        if pattern:
            self.assertEqual(found, pattern)

    def test_upstream_path_taxonomy(self):
        cases = [
            (".claude", "config-directory"),
            (".claude/settings.json", "settings-file"),
            (".claude/settings.local.json", "settings-file"),
            (".Claude/Settings.JSON", "settings-file"),
            ("~/.claude/settings.json", "settings-file"),
            ("policy/managed-settings.json", "managed-settings"),
            (".claude/hooks/guard.py", "hook-script"),
            (".claude/hooks/new/deep/guard.py", "hook-script"),
            ("plugins/x/hooks/hooks.json", "hooks-manifest"),
            ("hooks.json", "hooks-manifest"),
            (".mcp.json", "mcp-config"),
            (".claude-plugin/marketplace.json", "plugin-manifest"),
            ("plugins/x/.claude-plugin/plugin.json", "plugin-manifest"),
        ]
        for path, pattern in cases:
            with self.subTest(path=path):
                self.assertBlocked(path, pattern)

    def test_upstream_unrelated_files_and_default_high(self):
        for path in ["src/app.py", "package.json", "config/settings.json", "src/webhooks.json",
                     "app/hooks.js", "src/hooks/useAuth.ts", ".claude/README.md",
                     "CLAUDE.md", ".claude/rules/style.md", ""]:
            with self.subTest(path=path):
                self.assertIsNone(guard.check_file_path(path, str(self.cwd)))
        self.assertIsNone(guard.check_file_path(".mcp.json", str(self.cwd), "critical"))
        self.assertBlocked("CLAUDE.md", "claude-md", safety_level="strict")

    def test_actual_config_files_and_runtime(self):
        for path in [self.cdir, self.cdir / "settings.json", self.cdir / "settings.local.json",
                     self.cdir / "CLAUDE.md", self.cdir / "unattended-config.json",
                     self.cdir / "unattended-decisions.jsonl", self.cdir / "hooks/new.py",
                     self.cdir / "tools/shfmt/shfmt", self.cdir / "unattended-state/session.json"]:
            with self.subTest(path=path):
                self.assertBlocked(str(path))
        self.assertIsNone(guard.check_file_path(str(self.cdir / "notes.txt")))
        self.assertBlocked(str(self.cdir / "plugins/cache/plugin/hooks/guard.py"))
        self.assertBlocked(str(self.cdir / "rules/unattended.md"))

    def test_symlink_and_missing_nested_parents(self):
        alias = self.cwd / "alias"
        alias.symlink_to(self.cdir, target_is_directory=True)
        (self.cdir / "settings.json").write_text("{}")
        self.assertBlocked("alias/settings.json")
        self.assertBlocked("alias/hooks/does/not/exist.py")
        self.assertIsNone(guard.check_file_path("alias/notes.txt", str(self.cwd)))
        self.assertBlocked("../custom config/settings.json")

    def test_actual_plugin_is_protected(self):
        plugin = self.root / "plugin cache" / "version"
        plugin.mkdir(parents=True)
        with patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": str(plugin)}):
            self.assertBlocked(str(plugin / "hooks/guard.py"), "installed-plugin")
            self.assertBlocked(str(plugin), "installed-plugin")
            self.assertIsNone(guard.check_file_path(str(plugin.parent / "other/README.md")))

    def test_tool_routing_and_read_pass(self):
        for name in ("Edit", "MultiEdit", "Write"):
            with self.subTest(name=name):
                self.assertTrue(guard.check_tool(name, {"file_path": ".claude/settings.json"}, str(self.cwd)))
        for name in ("Read", "Grep", "Unknown"):
            self.assertIsNone(guard.check_tool(name, {"file_path": ".claude/settings.json"}, str(self.cwd)))
        self.assertIsNone(guard.check_tool("Edit", None))
        self.assertIsNone(guard.check_tool("Edit", {"file_path": 42}))

    def test_successful_audit_precedes_deny_and_has_no_original_text(self):
        secret_path = str(self.cdir / "settings.json")
        result = guard.handle(self.payload(file_path=secret_path, new_string="never-log-this-value"))
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        audit = self.cdir / "unattended-decisions.jsonl"
        text = audit.read_text()
        row = json.loads(text)
        self.assertNotIn(secret_path, text)
        self.assertNotIn("never-log-this-value", text)
        self.assertEqual(row["type"], "config-protected")
        self.assertEqual(audit.stat().st_mode & 0o777, 0o600)

    def test_audit_false_returns_no_decision(self):
        with patch.object(guard, "record_decision", return_value=False):
            self.assertIsNone(guard.handle(self.payload(file_path=".claude/settings.json")))

    def run_process(self, payload, **env):
        result = subprocess.run([sys.executable, str(HOOKS / "config_guard.py")],
                                input=payload if isinstance(payload, str) else json.dumps(payload),
                                text=True, capture_output=True, env={**os.environ, **env}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout else None

    def test_audit_error_fails_open_without_output(self):
        (self.cdir / "unattended-decisions.jsonl").mkdir()
        self.assertIsNone(self.run_process(self.payload(file_path=".claude/settings.json")))

    def test_invalid_payloads_fail_open(self):
        for payload in ["not json", "null", "[]", "{}", self.payload(file_path=24),
                        {**self.payload(file_path=".claude/settings.json"), "hook_event_name": "Stop"}]:
            with self.subTest(payload=payload):
                self.assertIsNone(self.run_process(payload))

    def test_no_ask_allow_override_or_presence_dependency(self):
        result = self.run_process(self.payload(file_path=".claude/settings.json"),
                                  CONFIG_GUARD_ALLOW="true", HOOK_ASK_CRITICAL="true",
                                  HOOK_SAFETY_LEVEL="critical", CCU_MODE="off")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("updatedInput", result["hookSpecificOutput"])

    def bash(self, command):
        return guard.check_bash_command(command, str(self.cwd))

    def test_bash_mutation_families(self):
        commands = [
            "echo '{}' > .claude/settings.json", "echo x >> .claude/settings.json",
            "echo x >| .claude/settings.json", "cat payload > .claude/settings.local.json",
            "printf '%s' value > .mcp.json", "tee .claude/settings.json < payload",
            "tee -a .claude/settings.json", "truncate -s 0 .claude/settings.json",
            "dd if=payload of=.claude/settings.json", "mv evil .claude/settings.json",
            "cp evil .claude/settings.json", "rsync evil .claude/settings.json",
            "ln -sf evil .claude/settings.json", "install evil .claude/hooks/new.py",
            "rm .claude/settings.json", "rm -rf .claude/hooks", "git rm .claude/settings.json",
            "shred -u .claude/settings.json", "unlink .claude/settings.json",
            "sed -i 's/deny/allow/' .claude/settings.json", "sed -i '' 's/x/y/' .claude/settings.json",
            "perl -pi -e 's/x/y/' .claude/settings.json", "gawk -i inplace '{}' .claude/settings.json",
            "RM .claude/settings.json", "SED -I 's/x/y/' .claude/settings.json",
            "chmod 000 .claude/settings.json", "git restore .claude/settings.json",
            "env MODE=x /bin/rm .claude/settings.json", "command rm .claude/settings.json",
            "echo $(rm .claude/settings.json)", "rm \"$HOME/.claude/settings.json\"",
            "rm -rf .claude", "mv .claude/settings.json old.json",
            "cp -t .claude/settings.json source", "install -m 600 source .claude/settings.json",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(self.bash(command), command)

    def test_bash_cli_writes_and_read_commands(self):
        for command in ["claude config set mode value", "claude config add x y",
                        "claude mcp add evil -- program", "claude mcp remove github",
                        "claude plugin install x", "claude plugin uninstall x",
                        "claude plugin disable x", "claude plugin enable x",
                        "claude plugin update x", "claude plugin marketplace add example/repo",
                        "CLAUDE MCP ADD example"]:
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), "claude-cli-config")
        for command in ["claude config get theme", "claude mcp list", "claude plugin list",
                        "claude plugin marketplace list"]:
            with self.subTest(command=command):
                self.assertIsNone(self.bash(command))

    def test_bash_data_and_unrelated_commands_pass(self):
        commands = [
            "cat .claude/settings.json", "jq '.hooks' .claude/settings.json",
            "grep -n permissions .claude/settings.json", "ls -la .claude/hooks/",
            "diff .claude/settings.json backup", "cat .claude/settings.json > copy.json",
            "echo hello > notes.txt", "rm scratch.txt", "mv src/a.py src/b.py",
            "cp .claude/settings.json backup.json", "cp -a .claude/settings.json backup.json",
            'cp "$HOME/.claude/settings.json" /tmp/backup.json',
            'cp "$CCU_CONFIG_DIR/settings.json" /tmp/backup.json',
            "install -m 600 .claude/settings.json backup.json",
            "ln -s .claude/settings.json alias", "rsync -a .claude/settings.json backup.json",
            "dd if=.claude/settings.json of=backup.json", "command -v claude config set",
            "command -V claude plugin disable", "command -v rm .claude/settings.json",
            "command -pv claude config set",
            "sed -i 's/x/y/' src/app.py", "printf '%s' 'rm .claude/settings.json'",
            "git commit -m 'rm .claude/settings.json'", "echo rm; cat .claude/settings.json",
            "cat <<'EOF'\nrm .claude/settings.json\nEOF\n",
            "cat <<EOF\nrm .claude/settings.json\nEOF\n",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.bash(command), command)

    def test_bash_actual_config_symlink_and_cd(self):
        alias = self.cwd / "alias"
        alias.symlink_to(self.cdir, target_is_directory=True)
        for command in ["echo x > alias/settings.json", "rm alias/hooks/new.py",
                        "cd '../custom config'; rm settings.json",
                        "echo x > '../custom config/unattended-config.json'",
                        'echo x > "$CCU_CONFIG_DIR/settings.json"',
                        'rm "${CCU_CONFIG_DIR}/hooks/guard.py"']:
            with self.subTest(command=command):
                self.assertTrue(self.bash(command))

    def test_parser_failure_returns_no_decision(self):
        self.assertIsNone(self.bash("echo 'unterminated"))
        self.assertIsNone(self.run_process(self.payload("Bash", command="rm .claude/settings.json"),
                                           CCU_SHFMT=str(self.root / "missing-parser")))

    def test_documented_dynamic_and_interpreter_gaps(self):
        # These are known noncoverage, not commands executed during the test.
        # Native permissions / external guards still apply after no decision.
        self.assertIsNone(self.bash('rm "$D/settings.json"'))
        self.assertIsNone(self.bash("python3 -c 'open(\".claude/settings.json\",\"w\").write(\"{}\")'"))


if __name__ == "__main__":
    unittest.main()
