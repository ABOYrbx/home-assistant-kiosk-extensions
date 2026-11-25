#!/usr/bin/env python3
"""Build a deterministic, safe .hkext package from an extension source tree."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 developer fallback
    import toml as tomllib  # type: ignore[no-redef]

from validate import (  # type: ignore[import-not-found]
    MAX_DECOMPRESSED_BYTES,
    MAX_PACKAGE_BYTES,
    ValidationError,
    is_safe_relative_path,
    load_toml,
    scan_source,
    schema_errors,
    schema_validators,
    semantic_errors,
    valid_semver,
)

ROOT = Path(__file__).resolve().parent.parent
EXTENSION_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file, not a symlink")
    data = path.read_bytes()
    if len(data) > MAX_DECOMPRESSED_BYTES:
        raise ValidationError(f"{label} exceeds the 20 MiB decompressed-size limit")
    return data


def build(root: Path, dist: Path, extension_id: str, version: str) -> dict[str, object]:
    if EXTENSION_ID.fullmatch(extension_id) is None:
        raise ValidationError("extension id must be reverse-DNS")
    if not valid_semver(version):
        raise ValidationError("version must be valid semver")
    source = root / "extensions" / extension_id
    if source.is_symlink() or not source.is_dir():
        raise ValidationError(f"missing extension source directory: {source}")
    manifest_path = source / "manifest.toml"
    manifest = load_toml(manifest_path)
    _, manifest_validator, _ = schema_validators(root)
    errors = schema_errors(manifest_validator, manifest, "manifest.toml")
    if not errors:
        errors = semantic_errors(manifest, "manifest.toml", manifest=True)
    source_errors: list[str] = []
    scan_source(source, source_errors)
    errors.extend(source_errors)
    if errors:
        raise ValidationError("manifest validation failed: " + "; ".join(errors))
    if manifest.get("id") != extension_id:
        raise ValidationError("manifest id does not match the requested id")
    if manifest.get("version") != version:
        raise ValidationError("manifest version does not match the requested version")
    entry = manifest.get("entry", "")
    if not isinstance(entry, str) or not is_safe_relative_path(entry) or not entry.endswith(".rhai"):
        raise ValidationError("manifest entry must be a safe relative path ending in .rhai")

    names = sorted({"manifest.toml", "README.md", "LICENSE", entry})
    files: dict[str, bytes] = {}
    for name in names:
        if not is_safe_relative_path(name):
            raise ValidationError(f"unsafe package path: {name}")
        candidate = source / name
        current = source
        for part in Path(name).parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValidationError(f"{name}: symlinked parent directories are not allowed")
        try:
            candidate.resolve().relative_to(source.resolve())
        except ValueError as exc:
            raise ValidationError(f"{name}: path escapes the extension source directory") from exc
        files[name] = regular_file(candidate, name)
    if sum(len(data) for data in files.values()) > MAX_DECOMPRESSED_BYTES:
        raise ValidationError("package exceeds the 20 MiB decompressed-size limit")

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.pax_headers = {}
            archive.addfile(info, io.BytesIO(data))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=9, mtime=0) as encoder:
        encoder.write(tar_buffer.getvalue())
    package = compressed.getvalue()
    if len(package) > MAX_PACKAGE_BYTES:
        raise ValidationError("package exceeds the 5 MiB compressed-size limit")

    dist.mkdir(parents=True, exist_ok=True)
    output = dist / f"{extension_id}-{version}.hkext"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=dist)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(package)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return {
        "id": extension_id,
        "version": version,
        "path": str(output),
        "sha256": hashlib.sha256(package).hexdigest(),
        "size_bytes": len(package),
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return fail("usage: scripts/package.sh <extension-id> <version>")
    root = Path(os.environ.get("REPO_ROOT", ROOT)).resolve()
    dist = Path(os.environ.get("DIST_DIR", root / "dist")).resolve()
    try:
        metadata = build(root, dist, args[0], args[1])
    except (ValidationError, OSError) as exc:
        return fail(str(exc))
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
