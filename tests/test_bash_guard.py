import importlib.util
import json
import os
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path
from unittest import mock

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "bash_guard.py"
spec = importlib.util.spec_from_file_location("bash_guard", HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

SHELL, RM, ALLOW = "shell", "rm", "allow"

CASES = [
    # commands that triggered real prompts in bypassPermissions mode
    ("S=/x; cat > $S/a <<'EOF'\nimport x\nEOF\nrm $S/$w/tests/unit/zz.test.ts", RM),
    ("S=/x && pg_ctl -D $S/pgdata stop; rm -rf $S/pgdata $(cat $S/sockdir) $S/data.sql $S/*_0.tsv", RM),
    ('S=/x; W=$S/w; cd $W && git status --short; bash -c "cd $W/backend && go build ./... ; echo RC=\\$?"', SHELL),
    ('AE=/a; nohup zsh -c "$AE/gates.sh rust; $AE/gates.sh android" > $AE/chainA.nohup 2>&1 & echo started', SHELL),
    # shell -c / eval
    ("bash -lc 'echo hi'", SHELL),
    ("/bin/sh -ec 'ls'", SHELL),
    ("bash -o pipefail -c 'ls | head'", SHELL),
    ('eval "$CMD"', SHELL),
    ("find . -name x -exec sh -c 'echo {}' \\;", SHELL),
    # rm / find with unguarded expansions
    ('find "$D" -name "*.tmp" -delete', RM),
    ("find $D -type f -exec rm {} +", RM),
    ('for f in *.log; do rm -f "$f"; done', RM),
    ("rm -rf ${DIR}/build", RM),
    ("rm -rf ${DIR:-/tmp}/build", RM),
    ("sudo rm -rf $X", RM),
    ("FOO=1 rm -f `cat list`", RM),
    ("( cd /tmp && rm -f $X )", RM),
    # cases from the official "Rewrite a flagged command" docs
    ('rm -rf "${HOME:?}"/foo/*', RM),
    ("rm -rf $HOME/.cache/x", RM),
    ('D=$(pwd); rm -rf "${D:?}"', RM),
    ('R=$(git rev-parse --show-toplevel) && rm -rf "${R:?}/build"', RM),
    ('rm -rf "$(pwd)"', RM),
    ("rm -rf logs/*/*", RM),
    ("rm -rf logs/*/", RM),
    ("cd logs && rm -rf a/*", RM),
    # allowed
    ('rm -rf "${S:?}"/pgdata "${S:?}"/*_0.tsv "${S:?}/data.sql"', ALLOW),
    ('D=$(cat "${S:?}/sockdir"); rm -rf "${D:?}"', ALLOW),
    ("rm /tmp/literal/file", ALLOW),
    ("rm -f /tmp/foo.$$", ALLOW),
    ("rm -rf /tmp/build/*", ALLOW),
    ("rm -f *.log", ALLOW),
    ("rm -rf ~/tmp/x", ALLOW),
    ("cd /tmp && rm -f a.log", ALLOW),
    ('grep -rn "bash -c" src/', ALLOW),
    ("git commit -m 'use zsh -c less; rm $X too'", ALLOW),
    ("git rm $X", ALLOW),
    ("echo $X && rm -f /tmp/a", ALLOW),
    ('( cd "$W" && go test ./... )', ALLOW),
    ("cat > /tmp/s.sh <<'EOF'\nzsh -c \"echo $rt\"\nrm -rf $X/*\nEOF\nchmod +x /tmp/s.sh", ALLOW),
    ('cat <<< "bash -c x"', ALLOW),
    ('docker exec c sh -c "ls /app" # allow-shell-c', ALLOW),
    ('ssh host "bash -c ls"', ALLOW),
    ("python3 -c 'import os'", ALLOW),
    ("ls -la", ALLOW),
    ("go -C backend test ./...", ALLOW),
    ('nohup "$AE/gates.sh" rust > "$AE/chainA.nohup" 2>&1 &', ALLOW),
    ("brew list | grep sh", ALLOW),
    ("ssh -c aes128-ctr host ls", ALLOW),
]


def verdict(cmd):
    reason = guard.check(cmd)
    if reason is None:
        return ALLOW
    return SHELL if reason == guard.SHELL_C_REASON else RM


class CheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if guard.parse("true") is None:
            raise RuntimeError("Verified shfmt is required: run dependency bootstrap or set CCU_SHFMT")

    def test_ast_execution_contexts(self):
        cases = [
            ('echo "$(rm -rf $X)"', RM),
            ('cat <(rm -rf $X)', RM),
            ("cat <<EOF\n$(rm -rf $X)\nEOF", RM),
            ("cat <<'EOF'\n$(rm -rf $X)\nEOF", ALLOW),
            ("cat <<EOF\nrm -rf $X\nEOF", ALLOW),
            ("# bash -c x\necho done", ALLOW),
            ("echo bash -c ls", ALLOW),
            ("printf '%s' 'eval rm $X'", ALLOW),
            ("'bash' '-c' ls", SHELL),
            (r"b\ash -c ls", SHELL),
            (r"r\m -f $X", RM),
            ("sudo -u nobody rm -f $X", RM),
            ("env -u OLD FOO=1 command rm -f $X", RM),
            ("command -v rm", ALLOW),
            ("nice -n 5 rm $X", RM),
            ("rm -rf 'logs/*/*'", ALLOW),
            (r"rm -rf logs/\*/file", ALLOW),
            ('rm -rf "${S:?$(echo missing)}"/x', RM),
            ('echo 中文; rm "$X"', RM),
            ('echo "# allow-shell-c"; bash -c ls', SHELL),
            ('bash -c ls # allow-shell-c', ALLOW),
            ('rm $X # allow-shell-c', RM),
            ('setopt nullglob; rm -rf $X **/*.py(.N)', RM),
            ('echo "$(cd /tmp)"; rm -rf a/*', ALLOW),
            ('echo "$(cd /tmp && rm -rf a/*)"', RM),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(verdict(command), expected)

    def test_parser_failure_is_silent(self):
        with mock.patch.object(guard, "parse", return_value=None):
            self.assertIsNone(guard.check("rm -rf $X"))

    def test_cases(self):
        for cmd, want in CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(verdict(cmd), want)


class HookProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if guard.parse("true") is None:
            raise RuntimeError("Verified shfmt is required: run dependency bootstrap or set CCU_SHFMT")

    def run_hook(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            return subprocess.run([sys.executable, str(HOOK)], input=payload,
                                  env={**os.environ, "CCU_CONFIG_DIR": directory},
                                  capture_output=True, text=True, check=True).stdout

    def test_deny_output(self):
        out = self.run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "bash -c ls"}}))
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertTrue(spec["permissionDecisionReason"])

    def test_silent_when_allowed(self):
        self.assertEqual(self.run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})), "")

    def test_ignores_other_tools(self):
        self.assertEqual(self.run_hook(json.dumps({"tool_name": "Edit", "tool_input": {}})), "")

    def test_fails_open_on_garbage(self):
        self.assertEqual(self.run_hook("not json"), "")


if __name__ == "__main__":
    unittest.main()
