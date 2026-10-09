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
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOOKS = [
    ("Bash", "bash_guard.py"),
    ("mcp__ccd_session__spawn_task", "spawn_task_redirect.py"),
]
BEGIN = "<!-- claude-code-unattended:begin -->"
END = "<!-- claude-code-unattended:end -->"
PUSH_KEY = "inputNeededNotifEnabled"


def pick_python():
    for p in ("/usr/bin/python3", sys.executable):
        if p and os.access(p, os.X_OK):
            return p
    sys.exit("python3 not found")


def write_if_changed(path, text):
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == text:
        return False
    if old is not None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    path.write_text(text, encoding="utf-8")
    return True


def load_settings(path):
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"{path} is not valid JSON ({e}). Fix it first; nothing was changed.")


def is_ours(hook, script):
    return script in (hook.get("command") or "")


def strip_ours(pre):
    """Remove our hook commands from PreToolUse entries, dropping entries left empty."""
    kept = []
    for entry in pre:
        hooks = [h for h in entry.get("hooks", []) if not any(is_ours(h, s) for _, s in HOOKS)]
        if hooks:
            kept.append({**entry, "hooks": hooks})
        elif not entry.get("hooks"):
            kept.append(entry)
    return kept


def install_settings(settings, python, hooks_dir, push):
    all_hooks = settings.setdefault("hooks", {})
    pre = strip_ours(all_hooks.get("PreToolUse", []))
    for matcher, script in HOOKS:
        pre.append({
            "matcher": matcher,
            "hooks": [{"type": "command", "command": f'"{python}" "{hooks_dir / script}"', "timeout": 10}],
        })
    all_hooks["PreToolUse"] = pre
    if push:
        settings[PUSH_KEY] = True


def uninstall_settings(settings):
    all_hooks = settings.get("hooks", {})
    pre = strip_ours(all_hooks.get("PreToolUse", []))
    if pre:
        all_hooks["PreToolUse"] = pre
    else:
        all_hooks.pop("PreToolUse", None)
    if not all_hooks:
        settings.pop("hooks", None)


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
        return before + block + after.lstrip("\n")
    return (text.rstrip() + "\n\n" if text.strip() else "") + block


def without_rules(text):
    parts = split_block(text)
    if not parts:
        return text
    before, after = parts
    rest = "\n\n".join(p for p in (before.strip(), after.strip()) if p)
    return rest + "\n" if rest else ""


def main():
    ap = argparse.ArgumentParser(description="Install claude-code-unattended into the Claude Code config dir.")
    ap.add_argument("--uninstall", action="store_true", help="remove the hooks, settings entries and CLAUDE.md rules")
    ap.add_argument("--no-push", action="store_true", help=f"do not set {PUSH_KEY} (mobile push for waiting prompts)")
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude",
                    help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    args = ap.parse_args()

    cdir = Path(args.claude_dir).expanduser()
    hooks_dir = cdir / "hooks"
    settings_path = cdir / "settings.json"
    md_path = cdir / "CLAUDE.md"
    settings = load_settings(settings_path)
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    if args.uninstall:
        uninstall_settings(settings)
        if settings_path.exists():
            write_if_changed(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        if md_path.exists():
            write_if_changed(md_path, without_rules(md))
        for _, script in HOOKS:
            (hooks_dir / script).unlink(missing_ok=True)
        print(f"Uninstalled from {cdir}. {PUSH_KEY} was left as is.")
        return

    cdir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(exist_ok=True)
    for _, script in HOOKS:
        shutil.copy2(REPO / "hooks" / script, hooks_dir / script)
        (hooks_dir / script).chmod(0o755)
    install_settings(settings, pick_python(), hooks_dir, push=not args.no_push)
    write_if_changed(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    write_if_changed(md_path, with_rules(md, (REPO / "rules" / "CLAUDE.md").read_text(encoding="utf-8")))
    print(f"Installed into {cdir}:")
    print(f"  hooks     {hooks_dir}/{{{', '.join(s for _, s in HOOKS)}}}")
    print(f"  settings  {settings_path}")
    print(f"  rules     {md_path}")
    print("Running sessions pick up the hooks; the CLAUDE.md rules apply to new sessions.")


if __name__ == "__main__":
    main()
