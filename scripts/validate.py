#!/usr/bin/env python3
"""Validate the signed extension catalog and its source manifests."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"
MAX_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 20 * 1024 * 1024
MATCHED_MANIFEST_FIELDS = (
    "id",
    "name",
    "version",
    "api_version",
    "min_app_version",
    "description",
    "author",
    "license",
    "homepage",
    "provides",
    "permissions",
    "config_schema",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class ValidationError(Exception):
    """A catalog or manifest failed validation."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValidationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except ValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read {path}: {exc}") from exc


def load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: manifest must be a TOML table")
    return value


def schema_validators() -> tuple[Draft202012Validator, Draft202012Validator, Registry]:
    index_schema = load_json(SCHEMA_DIR / "index.schema.json")
    manifest_schema = load_json(SCHEMA_DIR / "manifest.schema.json")
    registry = Registry().with_resource(
        index_schema["$id"], Resource.from_contents(index_schema)
    )
    checker = FormatChecker()
    return (
        Draft202012Validator(index_schema, registry=registry, format_checker=checker),
        Draft202012Validator(manifest_schema, registry=registry, format_checker=checker),
        registry,
    )


def schema_errors(validator: Draft202012Validator, value: Any, label: str) -> list[str]:
    errors: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(part) for part in error.absolute_path)
        prefix = f"{label}.{location}" if location else label
        errors.append(f"{prefix}: {error.message}")
    return errors


def is_safe_relative_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    return bool(path.parts) and all(part not in {"", ".", ".."} for part in path.parts)


def valid_semver(value: str) -> bool:
    pattern = re.compile(
        r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
    )
    match = pattern.fullmatch(value)
    if match is None:
        return False
    prerelease = match.group(4)
    if prerelease is None:
        return True
    return all(not part.isdigit() or part == "0" or not part.startswith("0") for part in prerelease.split("."))


def config_default_error(field: dict[str, Any], value: Any) -> str | None:
    kind = field["type"]
    if kind in {"string", "secret"}:
        return None if isinstance(value, str) else "default must be a string"
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return "default must be an integer"
        if "min" in field and value < field["min"]:
            return "default is below min"
        if "max" in field and value > field["max"]:
            return "default is above max"
        return None
    if kind == "boolean":
        return None if isinstance(value, bool) else "default must be a boolean"
    if kind in {"url", "entity_id", "select"}:
        if not isinstance(value, str):
            return "default must be a string"
        if kind == "url":
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return "default must be an HTTP(S) URL"
        elif kind == "entity_id":
            if re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", value) is None:
                return "default must be an entity ID"
        elif value not in field.get("options", []):
            return "default must be one of options"
    return None


def semantic_errors(value: dict[str, Any], label: str, *, manifest: bool) -> list[str]:
    errors: list[str] = []
    for key in ("name", "author", "license", "description"):
        if not value.get(key, "").strip():
            errors.append(f"{label}.{key}: must not be empty")
    if len(value.get("description", "").encode("utf-8")) > 2000:
        errors.append(f"{label}.description: exceeds 2000 bytes")
    for key in ("version", "min_app_version"):
        if not valid_semver(value.get(key, "")):
            errors.append(f"{label}.{key}: must be valid semver")
    if manifest:
        entry = value.get("entry", "")
        if not is_safe_relative_path(entry) or not entry.endswith(".rhai"):
            errors.append(f"{label}.entry: must be a safe relative path ending in .rhai")
    else:
        parsed = urlsplit(value.get("package_url", ""))
        if parsed.scheme != "https" or not parsed.hostname:
            errors.append(f"{label}.package_url: must use HTTPS and include a host")
        if parsed.username is not None or parsed.password is not None:
            errors.append(f"{label}.package_url: must not contain credentials")

    permissions = value.get("permissions", {})
    for host in permissions.get("http_hosts", []):
        port = host.rsplit(":", 1)[1] if ":" in host else None
        if port is not None and not 1 <= int(port) <= 65535:
            errors.append(f"{label}.permissions.http_hosts: invalid port in {host}")
    for entity in permissions.get("ha_entities", []):
        if re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entity) is None:
            errors.append(f"{label}.permissions.ha_entities: invalid entity ID {entity}")

    fields = value.get("config_schema", [])
    keys: set[str] = set()
    for field in fields:
        key = field.get("key", "")
        if key in keys:
            errors.append(f"{label}.config_schema: duplicate config key {key}")
        keys.add(key)
        if not field.get("label", "").strip():
            errors.append(f"{label}.config_schema.{key}: label must not be empty")
        if field.get("type") == "select" and not field.get("options"):
            errors.append(f"{label}.config_schema.{key}: select needs options")
        if "min" in field and "max" in field and field["min"] > field["max"]:
            errors.append(f"{label}.config_schema.{key}: min is greater than max")
        if field.get("type") == "secret" and "default" in field:
            errors.append(f"{label}.config_schema.{key}: secret fields must not have defaults")
        if "default" in field:
            default_error = config_default_error(field, field["default"])
            if default_error:
                errors.append(f"{label}.config_schema.{key}: {default_error}")
    return errors


def normalized_manifest(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    normalized.setdefault("homepage", None)
    normalized.setdefault("permissions", {"http_hosts": [], "ha_entities": []})
    normalized.setdefault("config_schema", [])
    return normalized


def scan_source(source: Path, errors: list[str]) -> None:
    if source.is_symlink() or not source.is_dir():
        errors.append(f"extension source is not a real directory: {source}")
        return
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if path.is_symlink():
            errors.append(f"{relative}: symlinks are not allowed")
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in {".key", ".pem", ".env"} or path.name == ".env":
            errors.append(f"{relative}: secret-key files are not allowed")
        if path.stat().st_size > MAX_DECOMPRESSED_BYTES:
            errors.append(f"{relative}: source file exceeds 20 MiB")
        if path.suffix.lower() in {".toml", ".rhai", ".md", ".txt"} or path.name == "LICENSE":
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    errors.append(f"{relative}: possible secret material")


def validate(root: Path) -> list[str]:
    index_path = root / "index.json"
    if not index_path.is_file():
        return [f"missing {index_path}"]
    index = load_json(index_path)
    index_validator, manifest_validator, _ = schema_validators()
    errors = schema_errors(index_validator, index, "index.json")
    if errors:
        return errors

    generated = datetime.fromisoformat(index["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None or generated.utcoffset() != timedelta(0):
        errors.append("index.json.generated_at: must be UTC")

    entries = index["extensions"]
    seen: set[str] = set()
    for position, entry in enumerate(entries):
        label = f"index.json.extensions[{position}]"
        errors.extend(semantic_errors(entry, label, manifest=False))
        extension_id = entry["id"]
        if extension_id in seen:
            errors.append(f"duplicate extension id: {extension_id}")
        seen.add(extension_id)

        source = root / "extensions" / extension_id
        manifest_path = source / "manifest.toml"
        if not manifest_path.is_file():
            errors.append(f"{extension_id}: missing extensions/{extension_id}/manifest.toml")
            continue
        source_errors: list[str] = []
        scan_source(source, source_errors)
        errors.extend(f"{extension_id}: {item}" for item in source_errors)
        if source_errors:
            continue
        manifest = load_toml(manifest_path)
        manifest_errors = schema_errors(manifest_validator, manifest, f"{extension_id}/manifest.toml")
        errors.extend(manifest_errors)
        if manifest_errors:
            continue
        errors.extend(semantic_errors(manifest, f"{extension_id}/manifest.toml", manifest=True))
        normalized = normalized_manifest(manifest)
        normalized_entry = normalized_manifest(entry)
        for field in MATCHED_MANIFEST_FIELDS:
            if normalized.get(field) != normalized_entry.get(field):
                errors.append(f"{extension_id}: manifest/index mismatch for {field}")
        for required in ("README.md", "LICENSE"):
            path = source / required
            if not path.is_file() or path.is_symlink():
                errors.append(f"{extension_id}: missing regular file {required}")
        entry_path = source / manifest["entry"]
        if not entry_path.is_file() or entry_path.is_symlink():
            errors.append(f"{extension_id}: missing regular entry script {manifest['entry']}")
        try:
            entry_path.resolve().relative_to(source.resolve())
        except ValueError:
            errors.append(f"{extension_id}: entry script escapes the source directory")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        errors = validate(args.root.resolve())
    except (ValidationError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"validation failed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("catalog validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
