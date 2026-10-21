#!/usr/bin/env python3
"""Deny prompt-prone Bash shapes, using shfmt rather than handwritten syntax.

Only project-specific rewrite policy lives here. A silent result is not an
approval; Claude Code still applies its own checks. Internal errors fail open.
"""
import json
import sys
from pathlib import Path

# Also support importlib-based tests and external tooling loading this file.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_ast import commands, literal_word, parse, walk, word_text

ESCAPE_MARKER = "# allow-shell-c"
SHELLS = {"bash", "zsh", "sh", "dash", "ksh"}
WRAPPERS = {"nohup", "sudo", "command", "builtin", "exec", "nice", "env"}
WRAPPER_VALUES = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-T", "-R", "-D"},
                  "env": {"-u", "--unset", "-C", "--chdir"}, "nice": {"-n"}}

SHELL_C_REASON = (
    "已被 bash_guard hook 拦截（命令未执行）：命令里用了 `bash/zsh/sh -c \"...\"` 或 `eval`，"
    "Claude Code 内置安全检查看不透字符串里的脚本，bypass 模式下也会弹窗等用户确认，而用户经常不在电脑边。"
    "请改写后重试：直接写命令；切换目录用 `( cd 目录 && 命令 )` 或 `git -C` / `go -C`；"
    "后台任务用 Bash 工具的 run_in_background，或先把命令写进脚本文件再直接执行该脚本。"
    "只有确实无法避免时（例如 `docker exec 容器 sh -c ...`），才在命令末尾加注释 `"
    + ESCAPE_MARKER
    + "` 放行（这种情况仍可能弹窗）。"
)
RM_PREFIX = (
    "已被 bash_guard hook 拦截（命令未执行）：`{cmd}` 里的 `{hit}` 会触发 Claude Code 内置的删除安全检查，"
    "bypass 模式下也会弹窗等用户确认。请改写后重试："
)
# Rewrite hints follow https://code.claude.com/docs/en/permission-modes#rewrite-a-flagged-command
RM_HINTS = {
    "var": "每个变量写成 \"${VAR:?}\"，例如 `rm -rf \"${S:?}\"/pgdata \"${S:?}\"/*_0.tsv`；或者直接写字面绝对路径。",
    "subst": "删除目标里不要放 $(...) 或反引号。先单独运行那条命令拿到结果，再用打印出来的字面路径删除。",
    "home": "$HOME 这类总是有值的变量，加 :? 也过不了检查，改用字面绝对路径。",
    "dirsubst": "变量来自 $(pwd) / $(git rev-parse --show-toplevel) 时，加 :? 也过不了检查，改用字面绝对路径。",
    "glob": "多级通配（如 a/*/*、a/*/）或 cd 之后的相对通配删除，检查无法预判范围。先 ls 列出具体路径，再用字面路径删除。",
}



def _base(value):
    return value.rsplit("/", 1)[-1] if isinstance(value, str) else None


def _assignment(value):
    return isinstance(value, str) and "=" in value and value.split("=", 1)[0].isidentifier()


def command_args(call):
    """Remove only known execution wrappers; assignments are separate AST nodes."""
    args = call.get("Args", [])
    while args and _base(literal_word(args[0])) in WRAPPERS:
        wrapper = _base(literal_word(args[0]))
        # These command modes query names rather than executing their argv.
        if wrapper == "command" and any(literal_word(a) in {"-v", "-V"} for a in args[1:2]):
            return []
        index = 1
        while index < len(args):
            value = literal_word(args[index])
            if value == "--":
                index += 1
                break
            if value in WRAPPER_VALUES.get(wrapper, set()):
                index += 2
            elif value and value.startswith("-"):
                index += 1
            elif wrapper == "env" and _assignment(value):
                index += 1
            else:
                break
        args = args[index:]
    return args


def _shell_c(args):
    values = [literal_word(word) for word in args]
    head = _base(values[0]) if values else None
    if head == "eval":
        return len(args) > 1
    if head not in SHELLS:
        return False
    index = 1
    while index < len(values):
        value = values[index]
        if value in {"-o", "+o", "--rcfile", "--init-file"}:
            index += 2
            continue
        if value is None or value == "--" or not value.startswith(("-", "+")):
            break
        if value.startswith("-") and not value.startswith("--") and "c" in value[1:]:
            return True
        index += 1
    return False


def shell_rewrite(args):
    if _shell_c(args):
        return True
    values = [literal_word(word) for word in args]
    head = _base(values[0]) if values else None
    if head == "find":
        for index, value in enumerate(values):
            if value in {"-exec", "-execdir", "-ok", "-okdir"} and _shell_c(args[index + 1:]):
                return True
    # Container exec passes the following argv directly, unlike ssh's remote
    # command text, which remains data for this local rewrite policy.
    if head in {"docker", "podman"} and len(values) > 2 and values[1] == "exec":
        for index in range(3, len(args)):
            if _base(values[index]) in SHELLS and _shell_c(args[index:]):
                return True
    return False


def is_delete(args):
    values = [literal_word(word) for word in args]
    head = _base(values[0]) if values else None
    if head == "rm":
        return True
    if head == "find":
        if "-delete" in values:
            return True
        return any(a in {"-exec", "-execdir"} and _base(b) == "rm"
                   for a, b in zip(values, values[1:]))
    return False


def directory_vars(ast):
    names = set()
    for call in commands(ast):
        for assignment in call.get("Assigns", []):
            substitutions = [node for node in walk(assignment.get("Value")) if node.get("Type") == "CmdSubst"]
            for substitution in substitutions:
                for nested in commands(substitution):
                    values = [literal_word(word) for word in command_args(nested)]
                    if values and (_base(values[0]) == "pwd" or
                                   (_base(values[0]) == "git" and values[1:3] == ["rev-parse", "--show-toplevel"])):
                        names.add(assignment.get("Name", {}).get("Value"))
    return names


def risky_expansion(args, dir_vars, command):
    for node in walk(args):
        kind = node.get("Type")
        if kind in {"CmdSubst", "ProcSubst", "ArithmExp"}:
            return "subst", word_text(node, command)
        if kind != "ParamExp":
            continue
        name = node.get("Param", {}).get("Value", "")
        if name == "HOME":
            return "home", word_text(node, command)
        if name in dir_vars:
            return "dirsubst", word_text(node, command)
        # Stable shell bookkeeping expansions are not possibly-empty paths.
        if name in {"$", "?", "!", "#", "-"}:
            continue
        if name.isidentifier() and node.get("Exp", {}).get("Op") == ":?":
            continue
        return "var", word_text(node, command)
    return None


def _glob_parts(word):
    # Only unquoted AST literals can glob. Escaped stars remain literal.
    result = []
    for part in word.get("Parts", []):
        if part.get("Type") == "Lit":
            value = part.get("Value", "")
            index = 0
            while index < len(value):
                if value[index] == "\\" and index + 1 < len(value):
                    result.append("_")
                    index += 2
                else:
                    result.append(value[index])
                    index += 1
        else:
            result.append("_")
    return "".join(result)


def risky_glob(args, after_cd, command):
    values = [literal_word(word) for word in args]
    recursive = any(value == "--recursive" or (value and value.startswith("-") and
                    not value.startswith("--") and "r" in value.lower()) for value in values[1:])
    for word in args[1:]:
        value = literal_word(word)
        if value and value.startswith("-"):
            continue
        pattern = _glob_parts(word)
        star = pattern.find("*")
        if star >= 0 and ("/" in pattern[star:] or (after_cd and recursive and not pattern.startswith("/"))):
            return word_text(word, command)
    return None


def scoped_commands(node, scope=0):
    """A cd inside a subshell/substitution cannot change its parent's cwd."""
    if isinstance(node, dict):
        if node.get("Type") in {"Subshell", "CmdSubst", "ProcSubst", "FuncDecl"}:
            scope = id(node)
        if node.get("Type") == "CallExpr":
            yield node, scope
        for child in node.values():
            if isinstance(child, (dict, list)):
                yield from scoped_commands(child, scope)
    elif isinstance(node, list):
        for child in node:
            yield from scoped_commands(child, scope)


def check(command):
    ast = parse(command)
    if ast is None:
        return None
    calls = list(scoped_commands(ast))
    # The escape marker must be a real shell comment, not quoted data.
    escape = any(node.get("Text", "").strip() == "allow-shell-c" for node in walk(ast))
    if not escape and any(shell_rewrite(command_args(call)) for call, _ in calls):
        return SHELL_C_REASON
    dir_vars = directory_vars(ast)
    cd_scopes = set()
    for call, scope in calls:
        args = command_args(call)
        if is_delete(args):
            found = risky_expansion(args, dir_vars, command)
            if not found and _base(literal_word(args[0])) == "rm":
                glob = risky_glob(args, scope in cd_scopes, command)
                found = ("glob", glob) if glob else None
            if found:
                kind, hit = found
                snippet = word_text(call, command)
                short = snippet if len(snippet) <= 80 else snippet[:77] + "..."
                return RM_PREFIX.format(cmd=short, hit=hit[:24]) + RM_HINTS[kind]
        if args and literal_word(args[0]) == "cd":
            cd_scopes.add(scope)
    return None


def main():
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return
        cmd = (data.get("tool_input") or {}).get("command") or ""
        reason = check(cmd)
    except Exception:
        return
    if reason:
        try:
            from unattended import record_decision
            record_decision(data, "bash-rewrite", "Flagged shell/delete syntax; rewrite requested",
                            "deny_rewrite", cmd)
        except Exception:
            pass  # legacy rewrite guard stays effective if best-effort audit fails
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
