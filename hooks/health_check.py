#!/usr/bin/env python3
"""SessionStart dependency health: silent when healthy, advisory on failure.

This never blocks a session or repairs/downloads anything inside a hook.
"""
import json
import subprocess

PINNED_VERSION = "v3.14.1"
MESSAGE = ("claude-code-unattended: shfmt 健康检查失败，shell/config 守卫可能不可用。"
           "请由负责人运行 install.py 修复；会话仍可继续。")
CONTEXT = ("bash_guard / config_guard 当前不可用或依赖异常；严格按规则里的 Shell safety 写命令。"
           "不要把缺少守卫意见视为批准，不要自行禁用或绕过保护；"
           "把修复交给负责人，继续其他已授权工作。")


def check():
    try:
        from shell_ast import parser_path
        result = subprocess.run([str(parser_path()), "--version"], capture_output=True,
                                text=True, timeout=2, check=True)
        if result.stdout.strip() == PINNED_VERSION:
            return None
    except Exception:
        pass
    return {"systemMessage": MESSAGE, "hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": CONTEXT}}


def main():
    try:
        result = check()
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass


if __name__ == "__main__":
    main()
