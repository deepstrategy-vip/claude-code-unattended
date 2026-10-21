import concurrent.futures
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "unattended.py"
spec = importlib.util.spec_from_file_location("unattended", HOOK)
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def question(**extra):
    return {"hook_event_name": "PreToolUse", "session_id": "test-session",
            "tool_name": "AskUserQuestion", "tool_input": {"questions": [{
                "question": "Which format?", "header": "Format", "multiSelect": False,
                "options": [{"label": "Plain", "description": "Simple"},
                            {"label": "Markdown (Recommended)", "description": "Readable"}],
            }], **extra}}


def stop(message="要我继续完成测试吗？", **extra):
    return {"hook_event_name": "Stop", "session_id": "test-session",
            "stop_hook_active": False, "last_assistant_message": message, **extra}


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, "CCU_CONFIG_DIR": str(self.root), "CCU_MODE": "away"}
        # Isolate tests even on a developer machine with custom overrides.
        self.env.pop("CCU_IDLE_SECONDS", None)
        self.env.pop("CCU_MAX_STOP_CONTINUATIONS", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, payload, **env):
        result = subprocess.run([sys.executable, str(HOOK)],
                                input=payload if isinstance(payload, str) else json.dumps(payload),
                                env={**self.env, **env}, text=True, capture_output=True, check=True)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout else None

    def records(self):
        path = self.root / "unattended-decisions.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_answer_recommended_and_preserve_full_input(self):
        payload = question(metadata={"keep": True})
        result = self.run_hook(payload)["hookSpecificOutput"]
        self.assertEqual(result["permissionDecision"], "allow")
        self.assertEqual(result["updatedInput"]["answers"], {"Which format?": "Markdown (Recommended)"})
        self.assertEqual(result["updatedInput"]["questions"], payload["tool_input"]["questions"])
        self.assertEqual(result["updatedInput"]["metadata"], {"keep": True})
        self.assertEqual(self.records()[0]["selected"][0]["option_index"], 2)

    def test_first_option_and_multiselect(self):
        payload = question()
        q = payload["tool_input"]["questions"][0]
        q["multiSelect"] = True
        q["options"][1]["label"] = "Markdown"
        self.assertEqual(self.run_hook(payload)["hookSpecificOutput"]["updatedInput"]["answers"],
                         {"Which format?": "Plain"})

    def test_preserve_existing_answers_and_fill_remaining(self):
        payload = question(answers={"Another?": "Already selected"})
        payload["tool_input"]["questions"].append({"question": "Another?", "options": [{"label": "Other"}]})
        answers = self.run_hook(payload)["hookSpecificOutput"]["updatedInput"]["answers"]
        self.assertEqual(answers["Another?"], "Already selected")
        self.assertEqual(len(self.records()[0]["selected"]), 1)

    def test_all_already_answered_is_untouched_even_risky(self):
        payload = question(answers={"Deploy?": "No"})
        payload["tool_input"]["questions"][0]["question"] = "Deploy?"
        self.assertIsNone(self.run_hook(payload))
        self.assertEqual(self.records(), [])

    def test_risky_question_deferred_never_approved(self):
        for text in ("Deploy to production?", "删除数据吗？", "Pay now?", "Grant permission?",
                     "Remove all backups?", "Drop all tables?", "Erase the disk?", "Reset the repository?"):
            payload = question()
            payload["tool_input"]["questions"][0]["question"] = text
            self.assertEqual(self.run_hook(payload)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertTrue(all(row["type"] == "question-deferred" for row in self.records()))

    def test_generic_action_approval_is_deferred(self):
        payload = question()
        payload["tool_input"]["questions"][0]["options"] = [
            {"label": "Yes (Recommended)"}, {"label": "No"}]
        result = self.run_hook(payload)["hookSpecificOutput"]
        self.assertEqual(result["permissionDecision"], "deny")

    def test_permission_fallback_deny_without_interrupt(self):
        for tool in ("Bash", "Write", "mcp__example__action"):
            result = self.run_hook({"hook_event_name": "PermissionRequest", "tool_name": tool,
                                    "session_id": "test-session", "tool_input": {"command": "rm -rf /"}})
            decision = result["hookSpecificOutput"]["decision"]
            self.assertEqual(decision["behavior"], "deny")
            self.assertIs(decision["interrupt"], False)
            self.assertIn("禁止绕过", decision["message"])

    def test_exit_plan_is_deferred_read_only(self):
        payload = question()
        payload["tool_name"] = "ExitPlanMode"
        result = self.run_hook(payload)["hookSpecificOutput"]
        self.assertEqual(result["permissionDecision"], "deny")
        self.assertIn("只读", result["permissionDecisionReason"])

    def test_off_means_no_intervention_for_any_new_event(self):
        for payload in (question(), stop(), {"hook_event_name": "PermissionRequest"}):
            self.assertIsNone(self.run_hook(payload, CCU_MODE="off"))
        self.assertEqual(self.records(), [])

    def test_present_means_no_intervention(self):
        with patch.dict(os.environ, self.env), patch.object(u, "idle_seconds", return_value=12):
            cfg = {**u.DEFAULTS, "mode": "auto"}
            for payload in (question(), stop(), {"hook_event_name": "PermissionRequest"}):
                self.assertIsNone(u.decide(payload, cfg))
        self.assertEqual(self.records(), [])

    def test_unknown_presence_fails_open(self):
        with patch.dict(os.environ, self.env), patch.object(u, "idle_seconds", return_value=None):
            self.assertIsNone(u.decide(question(), {**u.DEFAULTS, "mode": "auto"}))

    def test_idle_threshold_boundary(self):
        cfg = {**u.DEFAULTS, "mode": "auto"}
        for idle, expected in ((299.99, False), (300, True), (301, True), (float("nan"), False)):
            with patch.object(u, "idle_seconds", return_value=idle):
                self.assertEqual(u.is_away(cfg), expected)

    def test_ioreg_command_parses_most_recent_and_is_bounded(self):
        result = subprocess.CompletedProcess([], 0, '"HIDIdleTime" = 900000000000\n"HIDIdleTime" = 310000000000')
        with patch.object(u.sys, "platform", "darwin"), patch.object(u.subprocess, "run", return_value=result) as run:
            self.assertEqual(u.idle_seconds(), 310)
            self.assertIn("-r", run.call_args[0][0])
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
        with patch.object(u.sys, "platform", "linux"):
            self.assertIsNone(u.idle_seconds())

    def test_empty_ioreg_is_unknown(self):
        with patch.object(u.sys, "platform", "darwin"), patch.object(u.subprocess, "run", return_value=
                subprocess.CompletedProcess([], 0, "")):
            self.assertIsNone(u.idle_seconds())

    def test_stop_offer_and_active_guard(self):
        self.assertEqual(self.run_hook(stop())["decision"], "block")
        self.assertIsNone(self.run_hook(stop(stop_hook_active=True)))
        payload = stop()
        del payload["stop_hook_active"]
        self.assertIsNone(self.run_hook(payload))
        self.assertEqual(len(self.records()), 1)

    def test_stop_dedup_survives_process_and_budget(self):
        self.assertIsNotNone(self.run_hook(stop()))
        self.assertIsNone(self.run_hook(stop()))
        self.assertIsNotNone(self.run_hook(stop("Would you like me to finish the tests?")))
        self.assertIsNotNone(self.run_hook(stop("需要我补全说明吗？")))
        self.assertIsNone(self.run_hook(stop("Shall I check the remaining files?")))
        self.assertEqual(len(self.records()), 3)

    def test_stop_session_isolation(self):
        self.assertIsNotNone(self.run_hook(stop()))
        self.assertIsNotNone(self.run_hook(stop(session_id="different-session")))
        self.assertIsNone(self.run_hook(stop(session_id="")))

    def test_stop_no_false_continuation_for_completion_or_owner(self):
        for message in ("工作已经完成。", "All done.", "需要我部署吗？", "Shall I delete the data?",
                        "待你决定：是否发送。\n需要我继续吗？", "> 要我继续吗？", "```\n要我继续吗？",
                        "Example:\n    Shall I continue?", "Shall I continue? This is an example.",
                        "问题示例：`要我继续吗？`", "Question?\nCompleted."):
            with self.subTest(message=message):
                self.assertIsNone(self.run_hook(stop(message)))

    def test_stop_transcript_fallback_only_assistant_text(self):
        path = self.root / "transcript.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Would you like me to finish the checks?"}]}}) + "\n")
        payload = stop(transcript_path=str(path))
        del payload["last_assistant_message"]
        self.assertIsNotNone(self.run_hook(payload))
        path.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "Shall I continue?"}]}}))
        self.assertIsNone(self.run_hook(payload))

    def test_transcript_does_not_cross_new_human_turn_or_session(self):
        path = self.root / "transcript.jsonl"
        assistant = {"type": "assistant", "sessionId": "test-session", "message": {"content": [
            {"type": "text", "text": "Shall I finish the checks?"}]}}
        path.write_text(json.dumps(assistant) + "\n" + json.dumps(
            {"type": "user", "message": {"content": "Please stop."}}) + "\n")
        payload = stop(transcript_path=str(path))
        del payload["last_assistant_message"]
        self.assertIsNone(self.run_hook(payload))
        assistant["sessionId"] = "another-session"
        path.write_text(json.dumps(assistant) + "\n")
        self.assertIsNone(self.run_hook(payload))

    def test_log_is_structural_private_and_not_raw_text(self):
        payload = question()
        self.run_hook(payload)
        text = (self.root / "unattended-decisions.jsonl").read_text()
        self.assertNotIn("Which format?", text)
        self.assertNotIn("Markdown", text)
        self.assertEqual((self.root / "unattended-decisions.jsonl").stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(self.records()[0]), {"time", "session", "type", "summary", "selected", "content_sha256"})

    def test_audit_failure_fails_open_without_action(self):
        (self.root / "unattended-decisions.jsonl").mkdir()
        for payload in (question(), stop(), {"hook_event_name": "PermissionRequest", "session_id": "test-session",
                                            "tool_name": "Bash", "tool_input": {"command": "ls"}}):
            self.assertIsNone(self.run_hook(payload))

    def test_symlink_audit_not_followed(self):
        target = self.root / "unrelated"
        target.write_text("keep")
        (self.root / "unattended-decisions.jsonl").symlink_to(target)
        self.assertIsNone(self.run_hook(question()))
        self.assertEqual(target.read_text(), "keep")

    def test_corrupt_config_and_payload_fail_open(self):
        for payload in ("not json", "[]", "null", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"},
                        {"hook_event_name": "PermissionRequest"}):
            self.assertIsNone(self.run_hook(payload))
        (self.root / "unattended-config.json").write_text("{")
        self.assertIsNone(self.run_hook(question()))

    def test_invalid_environment_fails_open(self):
        for env in ({"CCU_MODE": "bad"}, {"CCU_IDLE_SECONDS": "nan"},
                    {"CCU_IDLE_SECONDS": "0"}, {"CCU_MAX_STOP_CONTINUATIONS": "100"}):
            self.assertIsNone(self.run_hook(question(), **env))

    def test_probe_timeout_and_error_fail_open_at_entrypoint(self):
        for error in (subprocess.TimeoutExpired("ioreg", 2), OSError("probe unavailable")):
            with patch.dict(os.environ, {**self.env, "CCU_MODE": "auto"}), \
                    patch.object(u, "idle_seconds", side_effect=error), \
                    patch.object(u.sys, "stdin", io.StringIO(json.dumps(question()))), \
                    patch.object(u.sys, "stdout", io.StringIO()) as out:
                u.main()
                self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.records(), [])

    def test_duplicate_or_ambiguous_questions_fail_open(self):
        payload = question()
        payload["tool_input"]["questions"] *= 2
        self.assertIsNone(self.run_hook(payload))
        payload = question()
        payload["tool_input"]["questions"][0]["options"][0]["label"] += " (Recommended)"
        self.assertIsNone(self.run_hook(payload))
        self.assertEqual(self.records(), [])

    def test_corrupt_stop_state_fails_open(self):
        state_dir = self.root / "unattended-state"
        state_dir.mkdir()
        (state_dir / (u.fingerprint("test-session") + ".json")).write_text("{")
        self.assertIsNone(self.run_hook(stop()))
        self.assertEqual(self.records(), [])

    def test_legacy_hooks_audit_best_effort_without_losing_guards(self):
        for script, payload, kind in (("bash_guard.py", {"tool_name": "Bash", "tool_input":
                {"command": "bash -c ls"}}, "bash-rewrite"),
                ("spawn_task_redirect.py", {}, "task-redirected")):
            result = subprocess.run([sys.executable, str(HOOK.parent / script)], input=json.dumps(payload),
                                    env=self.env, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(self.records()[-1]["type"], kind)
        (self.root / "unattended-decisions.jsonl").unlink()
        (self.root / "unattended-decisions.jsonl").mkdir()
        for script in ("bash_guard.py", "spawn_task_redirect.py"):
            result = subprocess.run([sys.executable, str(HOOK.parent / script)], input=json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": "bash -c ls"}}), env=self.env,
                text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        result = subprocess.run([sys.executable, str(HOOK.parent / "spawn_task_redirect.py")], input="not json",
                                env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_stop_budget_resets_on_each_human_turn_without_presence_probe(self):
        for _ in range(5):
            self.assertIsNone(self.run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "test-session"},
                                          CCU_MODE="invalid-config-is-not-read"))
            self.assertIsNotNone(self.run_hook(stop()))
            self.assertIsNone(self.run_hook(stop()))  # same fingerprint in this turn
        self.assertEqual(len(self.records()), 5)
        with patch.dict(os.environ, self.env), patch.object(u, "idle_seconds", side_effect=AssertionError), \
                patch.object(u.sys, "stdin", io.StringIO(json.dumps(
                    {"hook_event_name": "UserPromptSubmit", "session_id": "test-session"}))), \
                patch.object(u.sys, "stdout", io.StringIO()) as out:
            u.main()
            self.assertEqual(out.getvalue(), "")

    def test_active_stop_uses_same_turn_budget_and_dedup(self):
        self.assertIsNotNone(self.run_hook(stop()))
        self.assertIsNone(self.run_hook(stop(stop_hook_active=True)))
        for message in ("Shall I finish the checks?", "需要我补全说明吗？"):
            self.assertIsNotNone(self.run_hook(stop(message, stop_hook_active=True)))
        self.assertIsNone(self.run_hook(stop("Can I finish the remaining tests?", stop_hook_active=True)))
        self.assertEqual(len(self.records()), 3)

    def test_stop_risk_scope_is_only_offer_and_immediate_previous_line(self):
        report = "我把规则改好了，旧的配置已经删除并合并进新文件，权限设置没动。\n\n要我把这条也写进 CLAUDE.md 吗？"
        self.assertIsNotNone(self.run_hook(stop(report)))
        self.assertIsNone(self.run_hook(stop("要我推送到线上吗？")))
        self.assertIsNone(self.run_hook(stop("下一步推送到线上。\n要我这样做吗？")))

    def test_concurrent_stop_respects_session_budget(self):
        def invoke(index):
            return self.run_hook(stop(f"Shall I finish check {index}?"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(invoke, range(8)))
        self.assertEqual(sum(result is not None for result in results), 3)
        self.assertEqual(len(self.records()), 3)


if __name__ == "__main__":
    unittest.main()
