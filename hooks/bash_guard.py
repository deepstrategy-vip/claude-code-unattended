#!/usr/bin/env python3
"""PreToolUse(Bash) guard.

Claude Code still prompts in bypassPermissions mode for two command shapes:
  1. `bash/zsh/sh -c "..."` or `eval` (the checker cannot see inside the string)
  2. `rm` / `find -delete` whose paths contain unguarded variables ($X, ${X}, $(...))

This hook denies those shapes up front with a rewrite hint, so Claude fixes the
command itself instead of waiting on a human to click "Allow once".
Any internal error lets the command through unchanged.
"""
import json
import re
import shlex
import sys

ESCAPE_MARKER = "# allow-shell-c"

SHELL_C = re.compile(
    r"(?:^|[\s;&|(`/])(?:bash|zsh|sh|dash|ksh)\s+"
    r"(?:--?[A-Za-z][A-Za-z-]*\s+|[+-]o\s+\S+\s+)*?"
    r"-(?!-)[A-Za-z]*c[A-Za-z]*(?=\s|$)"
)
EVAL = re.compile(r"(?:^|[\s;&|(])eval\s")
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GUARDED = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:\?[^}]*\}")
VAR_REF = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\})")
DIR_ASSIGN = re.compile(
    r"(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=\"?\$\(\s*(?:pwd|git\s+rev-parse\s+--show-toplevel)\b"
)
PREFIX_WORDS = {
    "!", "{", "then", "do", "else", "elif", "if", "while", "until",
    "time", "nohup", "sudo", "command", "builtin", "exec", "nice", "env",
}

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


def strip_heredocs(cmd):
    out, pending = [], []
    for line in cmd.split("\n"):
        if pending:
            delim, dash = pending[0]
            if (line.lstrip("\t") if dash else line) == delim:
                pending.pop(0)
            continue
        out.append(line)
        for m in HEREDOC.finditer(line):
            pending.append((m.group(3), m.group(1) == "-"))
    return "\n".join(out)


def mask_quotes(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            out.append(s[i:i + 2])
            i += 2
        elif c == "'":
            j = s.find("'", i + 1)
            i = n if j == -1 else j + 1
            out.append("Q")
        elif c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            i = j + 1
            out.append("Q")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_segments(cmd):
    segs, cur, i, n = [], [], 0, len(cmd)
    sq = dq = False
    depth = 0
    while i < n:
        c = cmd[i]
        if sq:
            cur.append(c)
            sq = c != "'"
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            cur.append(cmd[i:i + 2])
            i += 2
            continue
        if c == "'" and not dq:
            sq = True
        elif c == '"':
            dq = not dq
        elif not dq and c == "$" and cmd[i + 1:i + 2] == "(":
            depth += 1
            cur.append("$(")
            i += 2
            continue
        elif not dq and c == ")" and depth > 0:
            depth -= 1
        elif not dq and depth == 0 and c in ";&|\n()":
            segs.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()]


def words(seg):
    try:
        return shlex.split(seg, comments=True)
    except ValueError:
        return seg.split()


def command_words(seg):
    ws = words(seg)
    k = 0
    while k < len(ws) and (ws[k] in PREFIX_WORDS or ASSIGN.match(ws[k])
                           or (k > 0 and ws[k - 1] in PREFIX_WORDS and ws[k].startswith("-"))):
        k += 1
    return ws[k:]


def risky_expansion(seg, dir_vars):
    """Return (kind, snippet) for the first expansion the built-in rm check flags."""
    i, n = 0, len(seg)
    sq = dq = False
    while i < n:
        c = seg[i]
        if sq:
            sq = c != "'"
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "'" and not dq:
            sq = True
        elif c == '"':
            dq = not dq
        elif c == "`":
            return "subst", seg[i:i + 24]
        elif c == "$":
            m = VAR_REF.match(seg, i)
            name = m and (m.group(1) or m.group(2))
            if name == "HOME":
                return "home", m.group(0)
            if name in dir_vars:
                return "dirsubst", m.group(0)
            g = GUARDED.match(seg, i)
            if g:
                i = g.end()
                continue
            nxt = seg[i + 1:i + 2]
            if nxt == "(":
                return "subst", seg[i:i + 24]
            if nxt and (nxt.isalnum() or nxt in "_{@*"):
                return "var", seg[i:i + 24]
        i += 1
    return None


def risky_glob(cw, after_cd):
    targets = [w for w in cw[1:] if not w.startswith("-")]
    recursive = any(w.startswith("-") and not w.startswith("--") and "r" in w.lower() for w in cw[1:]) \
        or "--recursive" in cw
    for t in targets:
        if re.search(r"\*[^/]*/", t):
            return t
        if after_cd and recursive and "*" in t and not t.startswith("/"):
            return t
    return None


def is_delete(cw):
    if not cw:
        return False
    head = cw[0].rsplit("/", 1)[-1]
    if head == "rm":
        return True
    if head == "find":
        if "-delete" in cw:
            return True
        for a, b in zip(cw, cw[1:]):
            if a in ("-exec", "-execdir") and b.rsplit("/", 1)[-1] == "rm":
                return True
    return False


def check(cmd):
    body = strip_heredocs(cmd)
    if ESCAPE_MARKER not in cmd:
        masked = mask_quotes(body)
        if SHELL_C.search(masked) or EVAL.search(masked):
            return SHELL_C_REASON
    dir_vars = set(DIR_ASSIGN.findall(body))
    after_cd = False
    for seg in split_segments(body):
        cw = command_words(seg)
        if is_delete(cw):
            found = risky_expansion(seg, dir_vars)
            if not found and cw[0].rsplit("/", 1)[-1] == "rm":
                g = risky_glob(cw, after_cd)
                found = g and ("glob", g)
            if found:
                kind, hit = found
                short = seg if len(seg) <= 80 else seg[:77] + "..."
                hit = hit.split()[0] if hit.split() else hit
                return RM_PREFIX.format(cmd=short, hit=hit) + RM_HINTS[kind]
        if cw and cw[0] == "cd":
            after_cd = True
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
            return
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
