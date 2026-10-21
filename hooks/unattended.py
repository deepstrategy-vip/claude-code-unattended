#!/usr/bin/env python3
"""Presence-gated, deny-only permission fallback and bounded continuation.

No model/network calls. Any malformed input, presence/config/audit failure is a
silent pass-through. The adjacent install directory owns config and audit data.
"""
import datetime
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

DEFAULTS = {"mode": "auto", "idle_seconds": 300, "max_stop_continuations": 3}
DEFER = (
    "无人值守：此项需要负责人决定，暂不执行，也不要换工具或放宽权限绕过。"
    "把原因及需要的决定记入最终汇报的「待你决定」，继续其他已授权且可逆的工作。"
)
CONTINUE = (
    "负责人离开了电脑。不要以可自行决定的征询结束本轮；按最稳妥、可逆、"
    "已授权的方案继续完成尚未完成的工作，记录假设。不要新增任务、重复已完成的工作，"
    "不要扩大权限、执行不可逆/对外/付费操作或绕过拒绝。"
    "真正需要负责人决定的事写入最终汇报的「待你决定」，其余做完即可结束。"
    "汇报可引用配置目录的 unattended-decisions.jsonl 中本会话的决策索引。"
)
# This is a conservative heuristic, not semantic authorization. Rules remain
# responsible for respecting the user's actual scope, including prior approval.
OWNER_RISK = re.compile(
    r"不可逆|删除|移除|清空|销毁|抹除|重置|格式化|撤销|覆盖|生产|线上|部署|发布|推送|合并|付款|付费|花钱|购买|"
    r"账单|授权|权限|凭据|密码|密钥|令牌|客户|发邮件|发送消息|对外|负责人|拍板|"
    r"\b(?:delet\w*|remov\w*|eras\w*|drop\w*|truncat\w*|reset\w*|revok\w*|"
    r"destroy\w*|wipe|irreversible|overwrit\w*|production|deploy\w*|"
    r"publish\w*|push|merg\w*|pay\w*|purchas\w*|billing|spend\w*|cost|budget|"
    r"permission\w*|authoriz\w*|credential\w*|password\w*|secret\w*|token\w*|"
    r"customer\w*|send|email|external|owner|approv\w*|execute|proceed)\b", re.I
)
APPROVAL_LABEL = re.compile(
    r"^(?:yes\b|no\b|ok\b|allow\b|go ahead\b|do it\b|confirm\b|"
    r"是(?:的)?(?:[，,。.!！\s（(]|$)|否(?:[，,。.!！\s（(]|$)|同意|允许|确认|批准|执行|继续)", re.I
)
OFFER = re.compile(
    r"(?:要(?:不要|我)|需要我|需不需要我|是否(?:需要|要)(?:我)?|想让我|让我).{0,160}[吗么？?]"
    r"|(?:would you like me to|do you want me to|shall i|should i|can i|may i)\b.{0,180}\?",
    re.I,
)


def config_dir():
    return Path(os.environ.get("CCU_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
                or Path(__file__).resolve().parent.parent).expanduser()


def configuration():
    values = dict(DEFAULTS)
    path = config_dir() / "unattended-config.json"
    if path.exists():
        values.update(json.loads(path.read_text(encoding="utf-8")))
    for key in DEFAULTS:
        override = os.environ.get("CCU_" + key.upper())
        if override is not None:
            values[key] = override
    if values["mode"] not in ("auto", "off", "away"):
        raise ValueError("invalid mode")
    for key, maximum in (("idle_seconds", 86400), ("max_stop_continuations", 20)):
        number = float(values[key])
        if not math.isfinite(number) or number != int(number) or not 1 <= number <= maximum:
            raise ValueError("invalid numeric config")
        values[key] = int(number)
    return values


def idle_seconds():
    if sys.platform != "darwin":
        return None
    result = subprocess.run(
        ["/usr/sbin/ioreg", "-r", "-c", "IOHIDSystem", "-d", "1"],
        capture_output=True, text=True, timeout=2, check=True,
    )
    samples = re.findall(r'"HIDIdleTime"\s*=\s*(\d+)\b', result.stdout)
    # Most recent activity wins if the registry exposes multiple records.
    return min(int(value) for value in samples) / 1_000_000_000 if samples else None


def is_away(config):
    if config["mode"] == "off":
        return False
    if config["mode"] == "away":
        return True  # explicit opt-in for a dedicated unattended host
    idle = idle_seconds()
    return idle is not None and math.isfinite(idle) and idle >= config["idle_seconds"]


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def private_file(path):
    flags = os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a+", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise


def record_decision(payload, kind, summary, selected, content="", stop_limit=None):
    """Persist BEFORE intervening. No raw prompts, labels, commands or paths.

    Log never rotated/deleted here or by uninstall (retained >=186 days).
    A locked per-session budget survives process restarts and concurrent Stops.
    """
    import fcntl
    root = config_dir()
    root.mkdir(parents=True, exist_ok=True)
    session = payload.get("session_id", "unknown")
    if not isinstance(session, str):
        raise ValueError("invalid session")
    # UUID-like identifiers are useful for matching a transcript, arbitrary
    # attacker-supplied strings may contain private text and are hashed instead.
    session_key = session if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", session) else fingerprint(session)
    digest = fingerprint(content)
    row = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "session": session_key, "type": kind, "summary": summary,
           "selected": selected, "content_sha256": digest}
    with private_file(root / "unattended-decisions.jsonl") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        if stop_limit is not None:
            state_dir = root / "unattended-state"
            state_dir.mkdir(mode=0o700, exist_ok=True)
            if state_dir.is_symlink():
                raise ValueError("symlink state directory")
            with private_file(state_dir / (fingerprint(session) + ".json")) as state:
                state.seek(0)
                raw = state.read(8192)
                value = json.loads(raw) if raw else {"count": 0, "seen": []}
                if value["count"] >= stop_limit or digest in value["seen"]:
                    return False
                value["count"] += 1
                value["seen"].append(digest)
                state.seek(0)
                state.truncate()
                state.write(json.dumps(value))
                state.flush()
                os.fsync(state.fileno())
        log.write(json.dumps(row, ensure_ascii=False) + "\n")
        log.flush()
        os.fsync(log.fileno())
    return True


def pre_deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": reason}}


def answer_questions(payload):
    tool_input = payload["tool_input"]
    questions = tool_input["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
        return None
    answers = dict(tool_input.get("answers", {}))
    choices = []
    text = json.dumps(questions, ensure_ascii=False)
    names = [question["question"] for question in questions]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        return None
    for question in questions:
        options = question["options"]
        if not isinstance(options, list) or not options:
            return None
        labels = [option["label"] for option in options]
        if any(not isinstance(label, str) or not label for label in labels) or len(set(labels)) != len(labels):
            return None
        if sum(bool(re.search(r"\(recommended\)|（推荐）|\(推荐\)", label, re.I)) for label in labels) > 1:
            return None
    if all(isinstance(question, dict) and question.get("question") in answers for question in questions):
        return None
    if OWNER_RISK.search(text) or any(APPROVAL_LABEL.search(option["label"].strip())
            for question in questions for option in question["options"]):
        record_decision(payload, "question-deferred", "Authority-sensitive question deferred",
                        "defer_owner", text)
        return pre_deny(DEFER)
    for question in questions:
        name, options = question["question"], question["options"]
        if name in answers:
            continue  # preserve answers the person already selected
        labels = [option["label"] for option in options]
        recommended = next((i for i, label in enumerate(labels)
                            if re.search(r"\(recommended\)|（推荐）|\(推荐\)", label, re.I)), None)
        index = recommended if recommended is not None else 0
        answers[name] = labels[index]
        # A single best option is valid for multiSelect too; don't select every
        # option merely because multiple answers are possible.
        choices.append({"question_sha256": fingerprint(name), "option_index": index + 1,
                        "option_sha256": fingerprint(labels[index]),
                        "recommended": recommended is not None})
    if not choices:
        return None
    record_decision(payload, "question-answered", "Selected recommended/first options",
                    choices, text)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "allow", "permissionDecisionReason":
            "离席自动选择：优先推荐项，否则第一项。此回答不扩大原任务授权；记录假设并继续。",
            "updatedInput": {**tool_input, "answers": answers}}}


def assistant_message(payload):
    if "last_assistant_message" in payload:
        value = payload["last_assistant_message"]
        return value if isinstance(value, str) else ""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return ""
    # Old runtimes: bounded read of assistant text only, never user/tool output.
    with open(path, "rb") as transcript:
        transcript.seek(0, os.SEEK_END)
        size = transcript.tell()
        transcript.seek(max(0, size - 262144))
        data = transcript.read(262144).decode("utf-8", errors="replace")
    for line in reversed(data.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("sessionId") and row["sessionId"] != payload.get("session_id"):
            return ""
        if row.get("type") == "user":
            content = row.get("message", {}).get("content", [])
            if not (isinstance(content, list) and content and all(isinstance(part, dict)
                    and part.get("type") == "tool_result" for part in content)):
                return ""  # never replay an offer from before a new human turn
            continue
        if row.get("type") != "assistant":
            continue
        message = row.get("message", {})
        content = message.get("content", [])
        if isinstance(content, str):
            return content
        # The latest assistant record must be natural language, not a tool call.
        if any(part.get("type") == "tool_use" for part in content if isinstance(part, dict)):
            return ""
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return ""


def optional_offer(message):
    lines = [line for line in message.splitlines() if line.strip()]
    if not lines or len(lines[-1]) > 250:
        return False
    raw_last = lines[-1]
    last = raw_last.strip()
    # Don't reinterpret quoted examples, code, or an explicitly deferred decision.
    if raw_last.startswith(("    ", "\t")) or last.startswith((">", "`")) or message.count("```") % 2:
        return False
    if "待你决定" in message or OWNER_RISK.search(message[-2000:]):
        return False
    return bool(OFFER.fullmatch(last.lstrip("*- ")))


def decide(payload, config):
    event = payload.get("hook_event_name")
    if event not in ("PreToolUse", "PermissionRequest", "Stop"):
        return None
    if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        return None
    if event in ("PreToolUse", "PermissionRequest") and (
            not isinstance(payload.get("tool_name"), str) or not payload["tool_name"]
            or not isinstance(payload.get("tool_input"), dict)):
        return None
    if event == "Stop" and payload.get("stop_hook_active") is not False:
        return None  # missing/invalid flag is also uncertain
    if not is_away(config):
        return None
    if event == "PermissionRequest":
        record_decision(payload, "permission-denied", "Permission request deferred; never auto allow",
                        "deny", json.dumps(payload.get("tool_input", {}), sort_keys=True))
        return {"hookSpecificOutput": {"hookEventName": event, "decision": {
            "behavior": "deny", "message": DEFER +
            "危险删除先只读核对范围；仅在已授权范围内改写为字面路径或跳过，禁止绕过保护。",
            "interrupt": False}}}
    if event == "PreToolUse":
        if payload.get("tool_name") == "AskUserQuestion":
            return answer_questions(payload)
        if payload.get("tool_name") == "ExitPlanMode":
            record_decision(payload, "plan-deferred", "Plan approval deferred; plan mode remains read-only",
                            "deny", json.dumps(payload.get("tool_input", {}), sort_keys=True))
            return pre_deny(DEFER + "仍处于计划模式，只继续该模式允许的只读工作；不要循环请求退出计划模式。")
        return None
    message = assistant_message(payload)
    if optional_offer(message) and record_decision(
            payload, "stop-continued", "Optional closing offer: continue authorized reversible work",
            "continue", message, stop_limit=config["max_stop_continuations"]):
        return {"decision": "block", "reason": CONTINUE}
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        result = decide(payload, configuration())
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass  # fail open; never print payload/config/exception with private text


if __name__ == "__main__":
    main()
