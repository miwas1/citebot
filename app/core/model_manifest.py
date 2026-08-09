"""Offline model-artifact manifest verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def verify_model_manifest(manifest_path: Path) -> None:
    """Verify every declared model artifact before an offline service starts."""

    if not manifest_path.exists():
        raise RuntimeError(f"Offline model manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("Offline model manifest must declare at least one artifact")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RuntimeError("Offline model manifest contains an invalid artifact entry")
        raw_path = Path(str(artifact.get("path", "")))
        path = raw_path if raw_path.is_absolute() else manifest_path.parent / raw_path
        if not path.exists():
            raise RuntimeError(f"Offline model artifact is missing: {path}")
        actual_size, actual_sha = _digest_path(path)
        expected_size = artifact.get("size")
        if expected_size is not None and actual_size != int(expected_size):
            raise RuntimeError(f"Offline model artifact size mismatch: {path}")
        expected_sha = str(artifact.get("sha256", ""))
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError(f"Offline model artifact checksum mismatch: {path}")


def _digest_path(path: Path) -> tuple[int, str]:
    """Hash files or deterministic directory trees without following symlinks."""

    digest = hashlib.sha256()
    total_size = 0
    paths = sorted(path.rglob("*") if path.is_dir() else [path])
    for child in paths:
        if not child.is_file():
            continue
        relative = child.relative_to(path) if path.is_dir() else child.name
        digest.update(str(relative).encode("utf-8"))
        with child.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                total_size += len(block)
                digest.update(block)
    return total_size, digest.hexdigest()
