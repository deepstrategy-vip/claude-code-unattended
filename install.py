#!/usr/bin/env python3
"""Install, update or uninstall claude-code-unattended in ~/.claude.

    python3 install.py              # install or update
    python3 install.py --uninstall  # remove everything this script added
    python3 install.py --no-push    # leave mobile push for waiting prompts untouched

Re-running is safe: hook entries are matched by script name and replaced in place,
and the CLAUDE.md rules live between marker comments that are rewritten, not appended.
Every file that changes gets a timestamped .bak copy first.
"""
import argparse
import datetime
import json
import os
import shutil
import shlex
import sys
from pathlib import Path

from dependencies import backup, install_shfmt

REPO = Path(__file__).resolve().parent
HOOKS = [
    ("SessionStart", None, "health_check.py"),
    ("PreToolUse", "Bash|Edit|MultiEdit|Write", "config_guard.py"),
    ("PreToolUse", "Bash", "bash_guard.py"),
    ("PreToolUse", "mcp__ccd_session__spawn_task", "spawn_task_redirect.py"),
    ("PreToolUse", "AskUserQuestion|ExitPlanMode", "unattended.py"),
    ("PermissionRequest", "*", "unattended.py"),
    ("Stop", None, "unattended.py"),
    ("UserPromptSubmit", None, "unattended.py"),
]
SCRIPTS = sorted({script for _, _, script in HOOKS})
FILES = SCRIPTS + ["shell_ast.py"]
EVENTS = sorted({event for event, _, _ in HOOKS})
BEGIN = "<!-- claude-code-unattended:begin -->"
END = "<!-- claude-code-unattended:end -->"
PUSH_KEY = "inputNeededNotifEnabled"


def pick_python():
    for p in ("/usr/bin/python3", sys.executable):
        if p and os.access(p, os.X_OK):
            return p
    sys.exit("python3 not found")


def write_if_changed(path, text):
    old = path.read_bytes().decode("utf-8") if path.exists() else None
    if old == text:
        return False
    if old is not None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    path.write_bytes(text.encode("utf-8"))
    return True


def load_settings(path):
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"{path} is not valid JSON ({e}). Fix it first; nothing was changed.")


def is_ours(hook, script, hooks_dir):
    try:
        paths = {hooks_dir / script}
        if script in ("bash_guard.py", "spawn_task_redirect.py"):
            paths.add(Path("~/.claude/hooks").expanduser() / script)  # original install command
        return any(Path(token).expanduser() in paths for token in shlex.split(hook.get("command") or ""))
    except ValueError:
        return False


def strip_ours(pre, hooks_dir):
    """Remove our hook commands from PreToolUse entries, dropping entries left empty."""
    kept = []
    for entry in pre:
        hooks = [h for h in entry.get("hooks", []) if not any(is_ours(h, s, hooks_dir) for s in SCRIPTS)]
        if hooks:
            kept.append({**entry, "hooks": hooks})
        elif not entry.get("hooks"):
            kept.append(entry)
    return kept


def install_settings(settings, python, hooks_dir, push):
    all_hooks = settings.setdefault("hooks", {})
    for event in EVENTS:
        entries = strip_ours(all_hooks.get(event, []), hooks_dir)
        for hook_event, matcher, script in HOOKS:
            if hook_event != event:
                continue
            entry = {"hooks": [{"type": "command", "command":
                      f'{shlex.quote(python)} {shlex.quote(str(hooks_dir / script))}', "timeout": 10}]}
            if matcher is not None:
                entry["matcher"] = matcher
            entries.append(entry)
        all_hooks[event] = entries
    if push:
        settings[PUSH_KEY] = True


def uninstall_settings(settings, hooks_dir):
    all_hooks = settings.get("hooks", {})
    for event in EVENTS:
        entries = strip_ours(all_hooks.get(event, []), hooks_dir)
        if entries:
            all_hooks[event] = entries
        else:
            all_hooks.pop(event, None)
    if not all_hooks:
        settings.pop("hooks", None)


def plugin_enabled(settings):
    return any(name.split("@", 1)[0] == "claude-code-unattended" and enabled
               for name, enabled in settings.get("enabledPlugins", {}).items())


def remove_with_backup(path):
    if path.exists():
        backup(path)
        path.unlink()


def install_notices(cdir):
    for source, destination in (
        (REPO / "third_party/shfmt/LICENSE", cdir / "tools/shfmt/LICENSE"),
        (REPO / "third_party/config-guard/LICENSE", cdir / "hooks/config-guard.LICENSE"),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_if_changed(destination, source.read_text(encoding="utf-8"))


def split_block(text):
    if BEGIN in text and END in text:
        before, rest = text.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        return before, after
    return None


def with_rules(text, rules):
    block = f"{BEGIN}\n{rules.strip()}\n{END}\n"
    parts = split_block(text)
    if parts:
        before, after = parts
        return before + block.rstrip("\n") + after
    # Add no separator outside the managed block: even trailing whitespace or
    # a missing final newline belongs to the user and must survive uninstall.
    return text + block


def without_rules(text):
    parts = split_block(text)
    if not parts:
        return text
    before, after = parts
    # Only the newline emitted as part of our block belongs to us. Legacy
    # separators before BEGIN are outside the block and must remain untouched.
    return before + (after[1:] if after.startswith("\n") else after)


def main():
    # Canonical flags only: abbreviations must not evade owner-control matching.
    ap = argparse.ArgumentParser(description="Install claude-code-unattended into the Claude Code config dir.",
                                 allow_abbrev=False)
    ap.add_argument("--uninstall", action="store_true", help="remove the hooks, settings entries and CLAUDE.md rules")
    ap.add_argument("--no-push", action="store_true", help=f"do not set {PUSH_KEY} (mobile push for waiting prompts)")
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude",
                    help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    ap.add_argument("--idle-seconds", type=int, help="away threshold, 1..86400 (default: 300 seconds)")
    ap.add_argument("--mode", choices=("auto", "off", "away"),
                    help="auto=macOS HID idle; off=disable new interventions; away=explicit always-away")
    ap.add_argument("--max-stop-continuations", type=int, help="Stop budget per human turn, 1..20 (default: 3)")
    ap.add_argument("--question-timeout", choices=("60s", "5m", "10m", "never"),
                    help="opt-in global askUserQuestionTimeout; inactive during Remote Control")
    ap.add_argument("--agent-push", action="store_true", help="opt-in agentPushNotifEnabled")
    ap.add_argument("--proactive", action="store_true", help="opt-in global outputStyle=Proactive")
    ap.add_argument("--config-guard", choices=("on", "off"),
                    help="owner-controlled configuration protection (default: on; no environment override)")
    ap.add_argument("--shfmt", default=os.environ.get("CCU_SHFMT"),
                    help="offline source of the pinned official shfmt binary (SHA256 verified)")
    ap.add_argument("--prepare-plugin", action="store_true",
                    help="prepare parser/licenses/config only; do not register hooks or write CLAUDE.md")
    args = ap.parse_args()
    if args.uninstall and args.prepare_plugin:
        ap.error("--uninstall and --prepare-plugin are mutually exclusive")
    if args.idle_seconds is not None and not 1 <= args.idle_seconds <= 86400:
        ap.error("--idle-seconds must be 1..86400")
    if args.max_stop_continuations is not None and not 1 <= args.max_stop_continuations <= 20:
        ap.error("--max-stop-continuations must be 1..20")

    cdir = Path(args.claude_dir).expanduser().absolute()
    hooks_dir = cdir / "hooks"
    settings_path = cdir / "settings.json"
    md_path = cdir / "CLAUDE.md"
    settings = load_settings(settings_path)
    md = md_path.read_bytes().decode("utf-8") if md_path.exists() else ""
    config_path = cdir / "unattended-config.json"
    config = load_settings(config_path)
    if not isinstance(settings, dict) or not isinstance(config, dict):
        sys.exit("Settings and unattended config must be JSON objects; nothing was changed.")
    if config.get("config_guard", "on") not in ("on", "off"):
        sys.exit("config_guard must be on or off; nothing was changed.")

    if args.uninstall:
        uninstall_settings(settings, hooks_dir)
        if settings_path.exists():
            write_if_changed(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        if md_path.exists():
            write_if_changed(md_path, without_rules(md))
        for script in FILES:
            remove_with_backup(hooks_dir / script)
        for path in (hooks_dir / "config-guard.LICENSE", cdir / "tools/shfmt/shfmt", cdir / "tools/shfmt/LICENSE"):
            remove_with_backup(path)
        if config_path.exists():
            # Preserve a backup, and never remove the audit log or session budgets.
            write_if_changed(config_path, "{}\n")
            config_path.unlink()
        print(f"Uninstalled from {cdir}. Notification/timeout settings and audit history were left as is.")
        return

    if not args.prepare_plugin and plugin_enabled(settings):
        sys.exit("The plugin is enabled; disable it before managed installation to avoid duplicate hooks.")

    try:
        install_shfmt(cdir, args.shfmt)
    except Exception as error:
        sys.exit("shfmt preparation failed (" + type(error).__name__ + "); settings/rules were not changed.")

    cdir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(exist_ok=True)
    install_notices(cdir)
    for key, default in (("mode", "auto"), ("idle_seconds", 300), ("max_stop_continuations", 3), ("config_guard", "on")):
        value = getattr(args, key)
        config[key] = value if value is not None else config.get(key, default)
    write_if_changed(config_path, json.dumps(config, indent=2) + "\n")
    if args.prepare_plugin:
        print("Plugin dependencies prepared. Enable only one distribution entry point.")
        return
    for script in FILES:
        write_if_changed(hooks_dir / script, (REPO / "hooks" / script).read_text(encoding="utf-8"))
        (hooks_dir / script).chmod(0o755)
    install_settings(settings, pick_python(), hooks_dir, push=not args.no_push)
    if args.question_timeout is not None:
        settings["askUserQuestionTimeout"] = args.question_timeout
    if args.agent_push:
        settings["agentPushNotifEnabled"] = True
    if args.proactive:
        settings["outputStyle"] = "Proactive"
    write_if_changed(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    write_if_changed(md_path, with_rules(md, (REPO / "rules" / "CLAUDE.md").read_text(encoding="utf-8")))
    print(f"Installed into {cdir}:")
    print(f"  hooks     {hooks_dir}/{{{', '.join(SCRIPTS)}}}")
    print(f"  settings  {settings_path}")
    print(f"  rules     {md_path}")
    print("Start a new session to reliably load hooks and rules; existing Desktop sessions may cache them.")


if __name__ == "__main__":
    main()
