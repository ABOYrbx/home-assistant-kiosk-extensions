#!/usr/bin/env python3
"""End-to-end tests for the extension catalog validator."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VALIDATOR = REPO / "scripts" / "validate.py"
ID = "org.example.bus-departures"
GENERATED_AT = "2026-11-25T00:00:00Z"


def manifest() -> dict:
    return {
        "id": ID,
        "name": "Bus Departures",
        "version": "1.0.0",
        "api_version": 1,
        "min_app_version": "0.6.0",
        "entry": "main.rhai",
        "provides": "departures",
        "author": "Example Author",
        "license": "MIT",
        "homepage": "https://example.com/bus-departures",
        "description": "Shows upcoming bus departures for a configured stop.",
        "permissions": {"http_hosts": ["api.example.com"], "ha_entities": []},
        "config_schema": [
            {"key": "stop_id", "label": "Stop ID", "type": "string", "required": True},
            {"key": "limit", "label": "Maximum departures", "type": "integer", "default": 6, "min": 1, "max": 20},
        ],
    }


def entry() -> dict:
    value = manifest()
    value.pop("entry")
    value.update(
        {
            "package_url": "https://example.com/org.example.bus-departures-1.0.0.hkext",
            "sha256": "0" * 64,
            "size_bytes": 123,
        }
    )
    return value


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)


def manifest_toml(value: dict) -> str:
    lines: list[str] = []
    for key, item in value.items():
        if key in {"permissions", "config_schema"}:
            continue
        lines.append(f"{key} = {toml_value(item)}")
    permissions = value["permissions"]
    lines.extend(["", "[permissions]"])
    for key, items in permissions.items():
        rendered = "[" + ", ".join(toml_value(item) for item in items) + "]"
        lines.append(f"{key} = {rendered}")
    for field in value["config_schema"]:
        lines.extend(["", "[[config_schema]]"])
        for key, item in field.items():
            lines.append(f"{key} = {toml_value(item)}")
    return "\n".join(lines) + "\n"


class ValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "extensions").mkdir()

    def write_index(self, value: dict | None = None, raw: str | None = None) -> None:
        if raw is None:
            value = value or {"schema_version": 1, "generated_at": GENERATED_AT, "extensions": []}
            raw = json.dumps(value, indent=2) + "\n"
        (self.root / "index.json").write_text(raw, encoding="utf-8")

    def write_source(self, value: dict | None = None) -> None:
        value = copy.deepcopy(value or manifest())
        source = self.root / "extensions" / value["id"]
        source.mkdir()
        (source / "manifest.toml").write_text(manifest_toml(value), encoding="utf-8")
        (source / "README.md").write_text("# Bus Departures\n", encoding="utf-8")
        (source / "LICENSE").write_text("MIT\n", encoding="utf-8")
        entry_path = source / value["entry"]
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text('fn refresh(config) { #{ kind: "departures", items: [] } }\n', encoding="utf-8")

    def run_validator(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO)
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.root), *args],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_invalid(self, fragment: str) -> None:
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(fragment.lower(), (result.stdout + result.stderr).lower())

    def test_empty_index_is_valid(self) -> None:
        self.write_index()
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        self.write_index(raw='{"schema_version":1,"schema_version":1,"generated_at":"2026-11-25T00:00:00Z","extensions":[]}\n')
        self.assert_invalid("duplicate")

    def test_unknown_index_fields_are_rejected(self) -> None:
        value = {"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [], "unexpected": True}
        self.write_index(value)
        self.assert_invalid("additional")

    def test_duplicate_extension_ids_are_rejected(self) -> None:
        first = entry()
        second = copy.deepcopy(first)
        second["version"] = "1.1.0"
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [first, second]})
        self.write_source()
        self.assert_invalid("duplicate extension id")

    def test_http_package_urls_are_rejected(self) -> None:
        value = entry()
        value["package_url"] = "http://example.com/package.hkext"
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        self.write_source()
        self.assert_invalid("https")

    def test_manifest_permissions_must_match_the_index(self) -> None:
        value = entry()
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        source = manifest()
        source["permissions"]["http_hosts"] = ["other.example.com"]
        self.write_source(source)
        self.assert_invalid("permissions")

    def test_unsafe_entry_paths_are_rejected(self) -> None:
        value = entry()
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        source = manifest()
        source["entry"] = "../main.rhai"
        self.write_source(source)
        self.assert_invalid("entry")

    def test_secret_fields_cannot_have_defaults(self) -> None:
        value = entry()
        value["config_schema"].append({"key": "token", "label": "Token", "type": "secret", "default": "unsafe"})
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        source = manifest()
        source["config_schema"].append({"key": "token", "label": "Token", "type": "secret", "default": "unsafe"})
        self.write_source(source)
        self.assert_invalid("secret")

    def test_wildcard_hosts_are_rejected(self) -> None:
        value = entry()
        value["permissions"]["http_hosts"] = ["*.example.com"]
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        source = manifest()
        source["permissions"]["http_hosts"] = ["*.example.com"]
        self.write_source(source)
        self.assert_invalid("host")

    def test_missing_license_is_rejected(self) -> None:
        value = entry()
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        self.write_source()
        (self.root / "extensions" / ID / "LICENSE").unlink()
        self.assert_invalid("license")

    def test_generated_at_must_be_utc(self) -> None:
        self.write_index({"schema_version": 1, "generated_at": "2026-11-25T01:00:00+01:00", "extensions": []})
        self.assert_invalid("utc")

    def test_malformed_manifest_types_are_reported_without_traceback(self) -> None:
        value = entry()
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        self.write_source()
        path = self.root / "extensions" / ID / "manifest.toml"
        text = path.read_text(encoding="utf-8")
        text = text.replace("[permissions]\n", 'permissions = "not-a-table"\n\n[unused]\n')
        text = text.replace('http_hosts = ["api.example.com"]\nha_entities = []\n', "")
        path.write_text(text, encoding="utf-8")
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertIn("permissions", result.stdout + result.stderr)

    def test_native_and_arbitrary_ui_files_are_rejected(self) -> None:
        value = entry()
        self.write_index({"schema_version": 1, "generated_at": GENERATED_AT, "extensions": [value]})
        self.write_source()
        source = self.root / "extensions" / ID
        for name in ("plugin.so", "plugin.wasm", "widget.slint", "widget.html"):
            with self.subTest(name=name):
                (source / name).write_bytes(b"forbidden")
                self.assert_invalid("forbidden")
                (source / name).unlink()


if __name__ == "__main__":
    unittest.main()
