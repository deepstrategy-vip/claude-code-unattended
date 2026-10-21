#!/usr/bin/env python3
"""Native plugin adapter; state outlives plugin upgrades and uninstall.

The managed installer and native plugin share code, never live registrations.
No dependency download is performed inside a hook.
"""
import json
import os
import runpy
import shlex
import sys
from pathlib import Path

SCRIPTS = ("unattended.py", "bash_guard.py", "spawn_task_redirect.py", "config_guard.py", "health_check.py")


def managed(settings, root):
    paths = {str(root / "hooks" / script) for script in SCRIPTS}
    for entries in settings.get("hooks", {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if any(str(Path(token).expanduser()) in paths
                       for token in shlex.split(hook.get("command", ""))):
                    return True
    return False


def main():
    try:
        root = Path(os.environ.get("CCU_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
                    or "~/.claude").expanduser().absolute()
        os.environ["CCU_CONFIG_DIR"] = str(root)
        settings_file = root / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
        if managed(settings, root):
            return
        action = sys.argv[1]
        if action == "context":
            rules = (Path(__file__).resolve().parent.parent / "rules/CLAUDE.md").read_text(encoding="utf-8")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                    "additionalContext": rules}}, ensure_ascii=False))
        elif action + ".py" in SCRIPTS:
            runpy.run_path(str(Path(__file__).with_name(action + ".py")), run_name="__main__")
    except Exception:
        return  # missing rules/settings/script and runtime failures never block


if __name__ == "__main__":
    main()
