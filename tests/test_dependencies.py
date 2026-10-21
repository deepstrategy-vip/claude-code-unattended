import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dependencies", ROOT / "dependencies.py")
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)


class DependencyTest(unittest.TestCase):
    def test_pins_all_supported_assets(self):
        self.assertEqual(len(dependencies.DIGESTS), 4)
        self.assertTrue(all(len(value) == 64 for value in dependencies.DIGESTS.values()))
        self.assertEqual(dependencies.VERSION, "v3.14.1")
        self.assertEqual(dependencies.BASE, "https://github.com/mvdan/sh/releases/download/v3.14.1/")

    def test_bad_digest_never_executes_or_replaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(b"untrusted")
            dest = root / "tools/shfmt/shfmt"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"old")
            with mock.patch.object(dependencies.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    dependencies.install_shfmt(root, source)
                run.assert_not_called()
            self.assertEqual(dest.read_bytes(), b"old")
            self.assertFalse(list(dest.parent.glob("*.bak-*")))

    def test_verified_install_is_atomic_idempotent_and_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(b"verified fixture")
            checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            key = dependencies.target()
            with mock.patch.dict(dependencies.DIGESTS, {key: checksum}), \
                 mock.patch.object(dependencies.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v3.14.1\n", "")) as run, \
                 mock.patch.object(dependencies.urllib.request, "urlopen") as network:
                dest = dependencies.install_shfmt(root, source)
                snapshot = dest.stat().st_mtime_ns
                dependencies.install_shfmt(root)
                self.assertEqual(dest.stat().st_mtime_ns, snapshot)
                self.assertEqual(run.call_count, 1)
                network.assert_not_called()
                self.assertEqual(dest.read_bytes(), source.read_bytes())
                self.assertTrue(dest.stat().st_mode & 0o100)

    def test_wrong_version_or_execution_failure_leaves_old_binary(self):
        for result in (subprocess.CompletedProcess([], 0, "v9\n", ""), subprocess.TimeoutExpired("shfmt", 5)):
            with self.subTest(result=type(result).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                source.write_bytes(b"fixture")
                dest = root / "tools/shfmt/shfmt"
                dest.parent.mkdir(parents=True)
                dest.write_bytes(b"old")
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with mock.patch.dict(dependencies.DIGESTS, {dependencies.target(): hashlib.sha256(b"fixture").hexdigest()}), \
                     mock.patch.object(dependencies.subprocess, "run", **kwargs):
                    with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                        dependencies.install_shfmt(root, source)
                self.assertEqual(dest.read_bytes(), b"old")
                self.assertFalse(list(dest.parent.glob(".shfmt-*")))

    def test_symlink_destination_and_unsupported_platform_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest = root / "tools/shfmt/shfmt"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(root / "elsewhere")
            with self.assertRaises(ValueError):
                dependencies.install_shfmt(root)
        with mock.patch.object(dependencies.platform, "system", return_value="Windows"):
            with self.assertRaises(ValueError):
                dependencies.target()
