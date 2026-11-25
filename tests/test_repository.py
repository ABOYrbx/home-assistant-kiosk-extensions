#!/usr/bin/env python3
"""Repository-level checks for the signed extension catalog scaffold."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class RepositoryTest(unittest.TestCase):
    def test_required_catalog_files_exist(self) -> None:
        required = (
            "README.md",
            "index.json",
            "index.json.minisig",
            "catalog.pub",
            "schema/index.schema.json",
            "schema/manifest.schema.json",
            "extensions/README.md",
            "scripts/validate.py",
            "scripts/validate.sh",
            "scripts/package.sh",
            ".github/CODEOWNERS",
            ".github/workflows/validate.yml",
        )
        missing = [path for path in required if not (ROOT / path).is_file()]
        self.assertEqual(missing, [])

    def test_index_has_schema_version_one_and_an_extension_list(self) -> None:
        value = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(value["schema_version"], 1)
        self.assertIsInstance(value["extensions"], list)

    def test_signature_verifies_and_rejects_tampering(self) -> None:
        minisign = shutil.which("minisign")
        self.assertIsNotNone(minisign, "minisign must be installed to run the repository tests")
        verified = subprocess.run(
            [minisign, "-V", "-p", str(ROOT / "catalog.pub"), "-m", str(ROOT / "index.json")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        with tempfile.TemporaryDirectory() as directory:
            tampered = Path(directory) / "index.json"
            value = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
            value["generated_at"] = "2026-11-26T00:00:00Z"
            tampered.write_text(json.dumps(value) + "\n", encoding="utf-8")
            rejected = subprocess.run(
                [minisign, "-V", "-p", str(ROOT / "catalog.pub"), "-m", str(tampered)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)

    def test_no_private_key_is_tracked(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.splitlines()
        forbidden = [
            path
            for path in tracked
            if path.endswith((".key", ".minisign.key", ".pem"))
            or "__pycache__" in path
        ]
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
