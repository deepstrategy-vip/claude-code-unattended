#!/usr/bin/env python3
"""Acquire only the pinned official shfmt release; never install on PATH.

Hashes are the GitHub release asset digests for mvdan/sh v3.14.1. Runtime
hooks never download anything. This module is used only by the installer/CI.
"""
import argparse
import datetime
import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

VERSION = "v3.14.1"
BASE = "https://github.com/mvdan/sh/releases/download/" + VERSION + "/"
DIGESTS = {
    ("darwin", "arm64"): "b7c872db63553ccffc7253aba3ed7d4885a27d83f1ba567b1138c6315a5847e5",
    ("darwin", "amd64"): "d33eee0da0f92835b3562e9767a05cee7e4eaeef47daa03bfd09da17b4b590a6",
    ("linux", "arm64"): "5f2db09dae91fca848f7adbdd014632e921a383863a2ad7e0450ad3aba0c6489",
    ("linux", "amd64"): "76e77641faa025814b77f153b29796b8e6fa2fca03e0c76a691608b86c7ea7bf",
}
MAX_BINARY = 16 * 1024 * 1024


def target():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(machine, machine)
    key = (system, arch)
    if key not in DIGESTS:
        raise ValueError("shfmt is supported here only on macOS/Linux arm64/amd64")
    return key


def digest(data):
    return hashlib.sha256(data).hexdigest()


def backup(path):
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, path.with_name(path.name + ".bak-" + stamp))


def install_shfmt(config_dir, source=None):
    """Verify before executing/replacing. Returns the installed absolute path."""
    system, arch = target()
    expected = DIGESTS[(system, arch)]
    destination = Path(config_dir) / "tools" / "shfmt" / "shfmt"
    if destination.is_symlink():
        raise ValueError("refusing a symlink shfmt destination")
    if destination.is_file() and digest(destination.read_bytes()) == expected:
        destination.chmod(0o755)
        return destination
    if source:
        with Path(source).expanduser().open("rb") as stream:
            data = stream.read(MAX_BINARY + 1)
    else:
        url = BASE + "shfmt_" + VERSION + "_" + system + "_" + arch
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(MAX_BINARY + 1)
    if len(data) > MAX_BINARY or digest(data) != expected:
        raise ValueError("shfmt SHA256 mismatch; no unverified binary was installed or executed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".shfmt-", delete=False) as stream:
            staging = Path(stream.name)
            stream.write(data)
        staging.chmod(0o755)
        result = subprocess.run([str(staging), "--version"], capture_output=True, text=True,
                                check=True, timeout=5)
        if result.stdout.strip() != VERSION:
            raise ValueError("unexpected shfmt version")
        backup(destination)
        os.replace(staging, destination)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-dir", required=True)
    parser.add_argument("--shfmt", default=os.environ.get("CCU_SHFMT"))
    args = parser.parse_args()
    try:
        path = install_shfmt(Path(args.claude_dir).expanduser().absolute(), args.shfmt)
    except Exception as error:
        parser.exit(1, "shfmt preparation failed (" + type(error).__name__ + ").\n")
    print(path)


if __name__ == "__main__":
    main()
