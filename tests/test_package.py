#!/usr/bin/env python3
"""End-to-end tests for deterministic .hkext packaging."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGER = REPO / "scripts" / "package.sh"
ID = "org.example.bus-departures"
VERSION = "1.0.0"

MANIFEST = f'''id = "{ID}"
name = "Bus Departures"
version = "{VERSION}"
api_version = 1
min_app_version = "0.6.0"
entry = "main.rhai"
provides = "departures"
author = "Example Author"
license = "MIT"
description = "Shows upcoming bus departures."

[permissions]
http_hosts = ["api.example.com"]
ha_entities = []
'''


class PackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "extensions" / ID
        self.source.mkdir(parents=True)
        (self.source / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
        (self.source / "main.rhai").write_text('fn refresh(config) { #{ kind: "departures", items: [] } }\n', encoding="utf-8")
        (self.source / "README.md").write_text("# Bus Departures\n", encoding="utf-8")
        (self.source / "LICENSE").write_text("MIT\n", encoding="utf-8")
        self.dist = self.root / "dist"

    def run_packager(self, extension_id: str = ID, version: str = VERSION) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update({"REPO_ROOT": str(self.root), "DIST_DIR": str(self.dist), "SOURCE_DATE_EPOCH": "0"})
        return subprocess.run(
            [str(PACKAGER), extension_id, version],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def package_path(self) -> Path:
        return self.dist / f"{ID}-{VERSION}.hkext"

    def test_valid_package_has_safe_root_files_and_checksum(self) -> None:
        result = self.run_packager()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        package = self.package_path()
        self.assertTrue(package.is_file())
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata["id"], ID)
        self.assertEqual(metadata["version"], VERSION)
        self.assertEqual(metadata["size_bytes"], package.stat().st_size)
        self.assertEqual(metadata["sha256"], hashlib.sha256(package.read_bytes()).hexdigest())
        with tarfile.open(package, "r:gz") as archive:
            self.assertEqual(archive.getnames(), ["LICENSE", "README.md", "main.rhai", "manifest.toml"])
            for member in archive.getmembers():
                self.assertTrue(member.isfile())
                self.assertEqual(member.mtime, 0)
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.gid, 0)

    def test_package_is_reproducible(self) -> None:
        first = self.run_packager()
        self.assertEqual(first.returncode, 0, first.stderr)
        original = self.package_path().read_bytes()
        second = self.run_packager()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.package_path().read_bytes(), original)

    def test_missing_required_file_is_rejected(self) -> None:
        (self.source / "LICENSE").unlink()
        result = self.run_packager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LICENSE", result.stderr)

    def test_version_mismatch_is_rejected(self) -> None:
        result = self.run_packager(version="1.0.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version", result.stderr.lower())

    def test_symlink_is_rejected(self) -> None:
        (self.source / "main.rhai").unlink()
        (self.source / "main.rhai").symlink_to("README.md")
        result = self.run_packager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())

    def test_unsafe_entry_path_is_rejected(self) -> None:
        path = self.source / "manifest.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('entry = "main.rhai"', 'entry = "../main.rhai"'), encoding="utf-8")
        result = self.run_packager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("entry", result.stderr.lower())

    def test_symlinked_parent_directory_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "main.rhai").write_text("outside", encoding="utf-8")
        (self.source / "nested").symlink_to(outside, target_is_directory=True)
        path = self.source / "manifest.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('entry = "main.rhai"', 'entry = "nested/main.rhai"'), encoding="utf-8")
        result = self.run_packager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
