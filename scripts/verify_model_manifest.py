"""Verify local model artifacts against a pinned offline manifest."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

REQUIRED_RUNTIME_PATHS = (
    Path("bge-small-en-v1.5"),
    Path("phi-4-mini-instruct-q4.gguf"),
    Path("paddleocr/detection"),
    Path("paddleocr/recognition"),
)


def main() -> int:
    """Validate every artifact declared by MODEL_MANIFEST_PATH."""

    manifest_path = Path(
        os.environ.get("MODEL_MANIFEST_PATH", "models/manifest.lock.json")
    )
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        print("manifest must contain a non-empty artifacts list", file=sys.stderr)
        return 2
    failures: list[str] = []
    declared_paths: set[Path] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            failures.append("artifact entry is not an object")
            continue
        raw_path = Path(str(artifact.get("path", "")))
        path = raw_path if raw_path.is_absolute() else manifest_path.parent / raw_path
        declared_paths.add(raw_path)
        if not path.exists():
            failures.append(f"missing: {path}")
            continue
        actual_size, actual_sha = _digest_path(path)
        expected_size = artifact.get("size")
        if expected_size is not None and actual_size != int(expected_size):
            failures.append(f"size mismatch: {path}")
        expected_sha = str(artifact.get("sha256", ""))
        if expected_sha and actual_sha != expected_sha:
            failures.append(f"sha256 mismatch: {path}")
    for required_path in REQUIRED_RUNTIME_PATHS:
        if required_path not in declared_paths:
            failures.append(f"runtime artifact is not declared: {required_path}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"verified {len(artifacts)} offline model artifacts")
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
