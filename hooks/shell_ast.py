"""Bounded shfmt JSON-AST adapter; no shell code is evaluated.

The parser executable is supplied by the verified dependency installer. Parsing
failures produce None, leaving Claude Code's normal checks in control.
"""
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import time

MAX_COMMAND_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
PARSE_TIMEOUT = 1.5


def parser_path():
    override = os.environ.get("CCU_SHFMT")
    if override:
        return Path(override)
    root = (os.environ.get("CCU_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR"))
    return (Path(root) if root else Path(__file__).resolve().parent.parent) / "tools" / "shfmt" / "shfmt"


def _run_parser(command, dialect):
    """Read pipes incrementally so input, output and elapsed time are bounded."""
    process = subprocess.Popen(
        [str(parser_path()), "-ln", dialect, "--to-json"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    output = bytearray()
    sent = 0
    deadline = time.monotonic() + PARSE_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("parser deadline")
                events = selector.select(remaining)
                if not events:
                    raise TimeoutError("parser deadline")
                for key, _ in events:
                    if key.fileobj is process.stdin:
                        if sent < len(command):
                            try:
                                sent += os.write(key.fd, command[sent:sent + 4096])
                            except BlockingIOError:
                                continue
                        if sent == len(command):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(process.stdout)
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_OUTPUT_BYTES:
                                raise ValueError("parser output limit")
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if process.returncode:
            return None
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def parse(command):
    """Return a complete typed File AST or None on any parser/input error."""
    try:
        if not isinstance(command, str):
            return None
        raw = command.encode("utf-8")
        if len(raw) > MAX_COMMAND_BYTES or b"\x00" in raw:
            return None
        # The tool commonly receives Bash-compatible input on macOS. Zsh has
        # extra syntax; retry only a rejected Bash parse, never execute either.
        output = _run_parser(raw, "bash")
        if output is None:
            output = _run_parser(raw, "zsh")
        if output is None:
            return None
        ast = json.loads(output)
        if not isinstance(ast, dict) or ast.get("Type") != "File":
            return None
        if "Stmts" in ast and not isinstance(ast["Stmts"], list):
            return None
        return ast
    except Exception:
        return None


def walk(node):
    """Yield dictionary nodes, including executable substitutions in heredocs.

    Literal strings and comments are never parsed as code. An unquoted heredoc
    may contain CmdSubst children, which really execute and must be visited.
    """
    if isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def commands(ast):
    return (node for node in walk(ast) if node.get("Type") == "CallExpr")


def _literal_part(part, quoted=False):
    kind = part.get("Type")
    if kind == "Lit":
        value = part.get("Value", "")
        if "\\" not in value:
            return value
        # shfmt has already determined boundaries and quote context. shlex only
        # decodes one literal part's escapes; it never segments shell commands.
        source = '"' + value + '"' if quoted else value
        pieces = shlex.split(source)
        if len(pieces) != 1:
            return "" if not pieces else None
        value = pieces[0]
        # POSIX shlex intentionally retains these two double-quote escapes;
        # the shell removes them, and shfmt has marked them literal already.
        return value.replace("\\$", "$").replace("\\`", "`") if quoted else value
    if kind == "SglQuoted" and not part.get("Dollar"):
        return part.get("Value", "")
    if kind == "DblQuoted" and not part.get("Dollar"):
        values = [_literal_part(child, True) for child in part.get("Parts", [])]
        return None if None in values else "".join(values)
    return None


def literal_word(word):
    """Static quote-removed value, or None if evaluation would be necessary."""
    if not isinstance(word, dict) or not isinstance(word.get("Parts"), list):
        return None
    try:
        values = [_literal_part(part) for part in word["Parts"]]
        return None if None in values else "".join(values)
    except (TypeError, ValueError, AttributeError):
        return None


def word_text(word, command):
    """Read shfmt's UTF-8 byte span, never Python Unicode-character offsets."""
    raw = command.encode("utf-8")
    start, end = word["Pos"]["Offset"], word["End"]["Offset"]
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(raw)):
        raise ValueError("invalid source span")
    return raw[start:end].decode("utf-8")
