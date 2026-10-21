#!/usr/bin/env python3
"""Deny known guardrail configuration mutations, with best-effort audit.

Adapted from Karan Bansal's MIT config-guard, pinned at
57a28c7687305e4a9b9dd50490e9b3c552c71ca4. Copyright (c) 2026 Karan Bansal.
See third_party/config-guard for the license, source mapping and limitations.

The upstream path taxonomy and CLI grammar are retained; shfmt identifies
executing commands and redirection targets instead of whole-command regexes.
This is defense in depth, not an OS boundary: dynamic paths, interpreter code,
external scripts and unrecognized mutators can evade classification. Input,
configuration and parser errors return no decision; audit errors keep a known
protective denial. Only the owner-controlled JSON file enables/disables this
guard, and its switch remains protected when normal guarding is off.
"""
import json
import os
import re
import sys
from pathlib import Path

from unattended import config_dir, pre_deny, record_decision

# Upstream PROTECTED_PATHS/LEVELS, ported to Python; default high. The strict
# layer remains explicit for classifier compatibility, not an environment knob.
LEVELS = {"critical": 1, "high": 2, "strict": 3}
PROTECTED_PATHS = [
    ("critical", "config-directory", r"(^|/)\.claude/?$"),
    ("critical", "settings-file", r"(^|/)\.claude/settings(\.local)?\.json$"),
    ("critical", "managed-settings", r"(^|/)managed-settings\.json$"),
    ("critical", "hook-script", r"(^|/)\.claude/hooks(/|$)"),
    ("critical", "hooks-manifest", r"(^|/)hooks/hooks\.json$"),
    ("high", "mcp-config", r"(^|/)\.mcp\.json$"),
    ("high", "plugin-manifest", r"(^|/)\.claude-plugin(/|$)"),
    ("strict", "claude-md", r"(^|/)CLAUDE(\.local)?\.md$"),
    ("strict", "rules-dir", r"(^|/)\.claude/(rules|agents|commands)(/|$)"),
]
PATTERNS = [(level, name, re.compile(pattern, re.I))
            for level, name, pattern in PROTECTED_PATHS]
CLI_WRITES = {
    "config": {"set", "add", "remove", "rm"},
    "mcp": {"add", "add-json", "add-from-claude-desktop", "remove", "rm"},
    "plugin": {"install", "uninstall", "enable", "disable", "update"},
}
MARKETPLACE_WRITES = {"add", "remove", "rm", "update"}
DELETE_VERBS = {"rm", "unlink", "shred", "trash"}
MOVE_COPY_VERBS = {"mv", "cp", "rsync", "install", "ln"}
WRITE_VERBS = {"tee", "truncate", "dd"}
OUTPUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
CONFIG_FILES = {"settings.json", "settings.local.json",
                "unattended-config.json", "unattended-decisions.jsonl"}
CONFIG_DIRS = {"hooks", "tools", "unattended-state", "plugins", "rules"}


def resolve_candidates(file_path, cwd=None):
    """Typed and symlink-resolved paths, including missing nested targets.

    Path.resolve(strict=False) resolves the nearest existing ancestor without
    requiring the direct parent to exist (stronger than upstream's fallback).
    """
    if not isinstance(file_path, str) or not file_path or "\x00" in file_path:
        return []
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    absolute = Path(os.path.abspath(path))
    return [file_path.replace("\\", "/"), str(absolute), str(absolute.resolve())]


def under(path, root):
    return path == root or root in path.parents


def check_file_path(file_path, cwd=None, safety_level="high", switch_only=False):
    """Return a fixed pattern id, or None. Never return private path text."""
    threshold = LEVELS.get(safety_level, LEVELS["high"])
    candidates = resolve_candidates(file_path, cwd)
    root = config_dir().resolve()
    switch = root / "unattended-config.json"
    for candidate in candidates[1:]:
        # Removing the configuration root also removes its owner-only switch.
        if Path(candidate) in (switch, root):
            return "config-guard-switch" if Path(candidate) == switch else "config-root"
    if switch_only:
        return None
    for candidate in candidates:
        for level, pattern_id, pattern in PATTERNS:
            if LEVELS[level] <= threshold and pattern.search(candidate):
                return pattern_id
    plugin = os.environ.get("CLAUDE_PLUGIN_ROOT")
    plugin_root = Path(plugin).expanduser().resolve() if plugin else None
    for candidate in candidates[1:]:
        path = Path(candidate)
        if plugin_root and under(path, plugin_root):
            return "installed-plugin"
        if path == root:
            return "config-root"
        if path.parent == root and path.name.lower() in {s.lower() for s in CONFIG_FILES}:
            return "unattended-config"
        if any(under(path, root / directory) for directory in CONFIG_DIRS):
            return "unattended-runtime"
    return None


def executable_words(words):
    """Strip common static execution prefixes; never evaluate shell content."""
    words = list(words)
    while words:
        name = Path(words[0]).name.lower() if isinstance(words[0], str) else ""
        if name not in {"env", "command", "builtin", "sudo", "nohup"}:
            break
        words.pop(0)
        if name == "command" and words and isinstance(words[0], str) and (
                words[0].startswith("-") and set(words[0][1:]) <= set("pvV")
                and any(flag in words[0] for flag in "vV")):
            return []  # Queries the shell; it does not execute the following argv.
        while words and isinstance(words[0], str) and (
                words[0].startswith("-") or (name == "env" and "=" in words[0])):
            option = words.pop(0)
            if option in {"-u", "--unset", "-C", "--chdir", "-u", "-g"} and words:
                words.pop(0)
    return words


def is_cli_write(words):
    if not words or not isinstance(words[0], str) or Path(words[0]).name.lower() != "claude":
        return False
    args = [word.lower() if isinstance(word, str) else None for word in words[1:]]
    if len(args) < 2:
        return False
    if args[1] in CLI_WRITES.get(args[0], set()):
        return True
    return len(args) >= 3 and args[:2] == ["plugin", "marketplace"] and args[2] in MARKETPLACE_WRITES


def is_switch_command(words):
    """Recognize owner-only installer controls, without executing the script."""
    if not words or not isinstance(words[0], str):
        return False
    executable = Path(words[0]).name.lower()
    args = words[1:]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        # Only recognize a script invocation; -c/-m bodies are not script paths.
        if "-c" in args or "-m" in args:
            return False
        script = next((word for word in args if isinstance(word, str) and not word.startswith("-")), None)
        if script is None or Path(script).name != "install.py":
            return False
        args = args[args.index(script) + 1:]
    elif executable != "install.py":
        return False
    if "--help" in args or "-h" in args:
        return False
    controls = {"--uninstall", "--config-guard", "--mode", "--prepare-plugin"}
    return any(isinstance(arg, str) and arg.split("=", 1)[0] in controls for arg in args)


def mutation_targets(words):
    """Retain upstream conservative mutation families, per parsed CallExpr."""
    if not words or not isinstance(words[0], str):
        return []
    name = Path(words[0]).name.lower()
    args = words[1:]
    if name == "dd":
        return [arg[3:] for arg in args if isinstance(arg, str) and arg.startswith("of=")]
    if name in {"cp", "install", "ln", "rsync"}:
        # Copy/link sources are reads. Resolve destinations only for common,
        # unambiguous argv grammar; retain conservative checks for unknown flags.
        if name == "rsync" and "--remove-source-files" in args:
            return args
        operands, destinations = [], []
        options = True
        index = 0
        while index < len(args):
            arg = args[index]
            if not isinstance(arg, str):
                return args
            if options and arg == "--":
                options = False
            elif options and arg in {"-t", "--target-directory"}:
                if index + 1 >= len(args):
                    return args
                index += 1
                destinations.append(args[index])
            elif options and arg.startswith("--target-directory="):
                destinations.append(arg.split("=", 1)[1])
            elif options and name == "install" and arg in {"-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix"}:
                index += 1
                if index >= len(args):
                    return args
            elif options and arg.startswith("-"):
                if name == "install" and arg in {"-d", "--directory"}:
                    return args
                known_flags = {
                    "cp": "aRrpfPHiLnsvfxT",
                    "ln": "sfnivPrLFT",
                    "install": "bCcDpsSv",
                    "rsync": "aRrltpogDHzuvnqPLcW",
                }[name]
                long_flags = {"--recursive", "--force", "--verbose", "--archive", "--no-target-directory"}
                if arg not in long_flags and (arg.startswith("--") or any(c not in known_flags for c in arg[1:])):
                    return args
            else:
                operands.append(arg)
            index += 1
        if destinations:
            return destinations
        return operands[-1:] if len(operands) >= 2 else args
    if name == "git" and args and args[0] in {"rm", "checkout", "restore"}:
        return args[1:]
    mutate = name in DELETE_VERBS | MOVE_COPY_VERBS | WRITE_VERBS | {"chmod", "chown"}
    if name in {"sed", "gsed", "perl"}:
        mutate = any(isinstance(arg, str) and re.match(r"^-\w*i", arg, re.I) for arg in args)
    if name in {"gawk", "awk"}:
        mutate = "inplace" in args
    if not mutate:
        return []
    return args


def known_word(word, shell_ast):
    """Resolve only the hook's known location aliases, without running a shell.

    Runtime assignments can change these values, so this adds conservative
    candidates; it is not general shell expansion or a guarantee of coverage.
    """
    literal = shell_ast.literal_word(word)
    if literal is not None:
        return literal

    def parts_value(parts):
        values = []
        for part in parts:
            kind = part.get("Type")
            if kind == "Lit":
                value = shell_ast.literal_word({"Parts": [part]})
            elif kind == "DblQuoted":
                value = parts_value(part.get("Parts", []))
            elif kind == "ParamExp" and not any(key in part for key in (
                    "Index", "Slice", "Repl", "Exp", "Excl", "Length", "Width", "Names")):
                name = part.get("Param", {}).get("Value")
                value = os.environ.get(name) if name in {
                    "HOME", "CCU_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT"} else None
            else:
                return None
            if value is None:
                return None
            values.append(value)
        return "".join(values)

    return parts_value(word.get("Parts", []))


def check_bash_command(command, cwd=None, switch_only=False):
    import shell_ast
    ast = shell_ast.parse(command)
    if ast is None:
        return None
    bases = [cwd or os.getcwd()]
    # Static cd targets add conservative alternate bases. No shell evaluation
    # or claim to model branch/subshell control flow; dynamic cd is a gap.
    for call in shell_ast.commands(ast):
        raw_args = call.get("Args", [])
        words = executable_words([known_word(word, shell_ast) for word in raw_args])
        if not words:
            continue
        if is_switch_command(words):
            return "config-guard-switch"
        if not switch_only and is_cli_write(words):
            return "claude-cli-config"
        if words[0] == "cd" and len(words) >= 2 and isinstance(words[1], str):
            # Bounded work even for adversarial scripts with many cd calls.
            alternatives = [str((Path(base) / Path(words[1]).expanduser()).resolve())
                            for base in bases[-16:]]
            bases = list(dict.fromkeys(bases + alternatives))[-32:]
        for target in mutation_targets(words):
            if isinstance(target, str):
                for base in bases:
                    found = check_file_path(target, base, switch_only=switch_only)
                    if found:
                        return found
        # Nonliteral arguments with a literal protected suffix are identifiable
        # without expanding attacker-controlled env values. Arbitrary variable
        # paths such as "$DIR/settings.json" remain intentionally unknown.
        if not switch_only and mutation_targets(words):
            for word in raw_args[1:]:
                if known_word(word, shell_ast) is None:
                    raw = shell_ast.word_text(word, command)
                    for level, pattern_id, pattern in PATTERNS:
                        if LEVELS[level] <= LEVELS["high"] and pattern.search(raw.strip("\"'")):
                            return pattern_id
    for node in shell_ast.walk(ast):
        if node.get("Op") not in OUTPUT_REDIRECTS or "Word" not in node:
            continue
        word = node["Word"]
        target = known_word(word, shell_ast)
        if target is not None:
            for base in bases:
                found = check_file_path(target, base, switch_only=switch_only)
                if found:
                    return found
        elif not switch_only:
            raw = shell_ast.word_text(word, command).strip("\"'")
            for level, pattern_id, pattern in PATTERNS:
                if LEVELS[level] <= LEVELS["high"] and pattern.search(raw):
                    return pattern_id
    return None


def check_tool(tool_name, tool_input, cwd=None, switch_only=False):
    if not isinstance(tool_input, dict):
        return None
    if tool_name in {"Edit", "MultiEdit", "Write"}:
        return check_file_path(tool_input.get("file_path"), cwd, switch_only=switch_only)
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            return check_bash_command(command, cwd, switch_only=switch_only)
    return None


def config_guard_enabled():
    """The on/off choice is owned by JSON configuration, never by an env knob."""
    path = config_dir() / "unattended-config.json"
    if not path.exists():
        return True
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("config_guard", "on") not in {"on", "off"}:
        raise ValueError("invalid config guard configuration")
    return value.get("config_guard", "on") == "on"


def handle(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get("hook_event_name") not in (None, "PreToolUse"):
        return None
    found = check_tool(payload.get("tool_name"), payload.get("tool_input"), payload.get("cwd"),
                       switch_only=not config_guard_enabled())
    if found:
        try:
            record_decision(payload, "config-protected", "Guardrail configuration mutation denied",
                            {"pattern": found}, json.dumps(payload.get("tool_input"), sort_keys=True))
        except Exception:
            pass  # A broken audit must not disable a known protective denial.
        return pre_deny("Guardrail configuration is protected (" + found + "). "
                        "The owner may edit it, delegate to Codex, or run install.py --config-guard off. "
                        "Claude must not run that command, disable protections, or bypass this denial. "
                        "Continue other independently authorized work.")
    return None


def main():
    try:
        result = handle(json.load(sys.stdin))
        if result:
            print(json.dumps(result))
    except Exception:
        pass  # Fail open without printing command/path/exception content.


if __name__ == "__main__":
    main()
