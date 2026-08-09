"""Provision the pinned local-model directory used by the offline Compose stack.

This command is intentionally the *only* path that contacts model registries. It
resolves each configured Hugging Face revision to an immutable commit, downloads
the required files into the local model directory, and writes the manifest that
offline runtime validates before it starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_HF_ENDPOINT = "https://huggingface.co"
ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ArtifactSpec:
    """A source repository and the local path where its artifact is stored."""

    name: str
    repository: str
    revision: str
    path: Path


@dataclass(frozen=True)
class ResolvedArtifact:
    """A fully downloaded artifact ready to be placed in the runtime manifest."""

    spec: ArtifactSpec
    commit: str
    license_name: str


class HuggingFaceClient:
    """Small standard-library client so provisioning has no package dependency."""

    def __init__(self, endpoint: str, token: str | None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token

    def model_info(self, repository: str, revision: str) -> dict[str, Any]:
        encoded_repository = quote(repository, safe="/")
        encoded_revision = quote(revision, safe="")
        url = f"{self._endpoint}/api/models/{encoded_repository}/revision/{encoded_revision}"
        payload = self._request(url).read()
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"Unexpected model metadata response for {repository}")
        return value

    def download_file(self, repository: str, commit: str, filename: str, target: Path) -> None:
        encoded_repository = quote(repository, safe="/")
        encoded_commit = quote(commit, safe="")
        encoded_filename = quote(filename, safe="/")
        url = f"{self._endpoint}/{encoded_repository}/resolve/{encoded_commit}/{encoded_filename}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._request(url) as response:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".part",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                while block := response.read(1024 * 1024):
                    temporary.write(block)
        temporary_path.replace(target)

    def _request(self, url: str):
        headers = {"User-Agent": "citebot-model-provisioner/1"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return urlopen(Request(url, headers=headers), timeout=60)  # noqa: S310


def build_specs(environ: dict[str, str]) -> list[ArtifactSpec]:
    """Build the overridable default artifact list from environment variables."""

    return [
        ArtifactSpec(
            name="qwen3-embedding-0.6b",
            repository=environ.get("EMBEDDING_MODEL_REPOSITORY", "Qwen/Qwen3-Embedding-0.6B"),
            revision=environ.get("EMBEDDING_MODEL_REVISION", "main"),
            path=Path("Qwen3-Embedding-0.6B"),
        ),
        ArtifactSpec(
            name="phi-4-mini-instruct-q4",
            repository=environ.get(
                "LLM_MODEL_REPOSITORY", "bartowski/microsoft_Phi-4-mini-instruct-GGUF"
            ),
            revision=environ.get("LLM_MODEL_REVISION", "main"),
            path=Path("phi-4-mini-instruct-q4.gguf"),
        ),
        ArtifactSpec(
            name="paddleocr-detection",
            repository=environ.get(
                "OCR_DETECTION_MODEL_REPOSITORY", "PaddlePaddle/PP-OCRv5_mobile_det"
            ),
            revision=environ.get("OCR_DETECTION_MODEL_REVISION", "main"),
            path=Path("paddleocr/detection"),
        ),
        ArtifactSpec(
            name="paddleocr-recognition",
            repository=environ.get(
                "OCR_RECOGNITION_MODEL_REPOSITORY", "PaddlePaddle/PP-OCRv5_mobile_rec"
            ),
            revision=environ.get("OCR_RECOGNITION_MODEL_REVISION", "main"),
            path=Path("paddleocr/recognition"),
        ),
    ]


def load_provisioning_environment(
    environ: dict[str, str], dotenv_path: Path = Path(".env")
) -> dict[str, str]:
    """Load simple `.env` defaults without letting them override exported variables."""

    values = dict(environ)
    if not dotenv_path.is_file():
        return values
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if not ENVIRONMENT_KEY.fullmatch(key) or key in values:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def provision(
    specs: list[ArtifactSpec], root: Path, client: HuggingFaceClient
) -> list[ResolvedArtifact]:
    """Download every model snapshot and return immutable metadata for the manifest."""

    root.mkdir(parents=True, exist_ok=True)
    resolved: list[ResolvedArtifact] = []
    for spec in specs:
        destination = safe_destination(root, spec.path)
        if destination.exists():
            raise RuntimeError(
                f"Refusing to overwrite existing artifact: {destination}. "
                "Remove it after review, or run with --force."
            )
        metadata = client.model_info(spec.repository, spec.revision)
        commit = metadata.get("sha")
        if not isinstance(commit, str) or not commit:
            raise RuntimeError(
                f"Model metadata for {spec.repository} did not include an immutable revision"
            )
        files = snapshot_files(metadata, spec)
        print(f"Downloading {spec.name} from {spec.repository}@{commit[:12]} ({len(files)} files)")
        for filename in files:
            target = (
                destination
                if spec.name == "phi-4-mini-instruct-q4"
                else safe_destination(destination, filename)
            )
            client.download_file(spec.repository, commit, filename, target)
        card_data = metadata.get("cardData")
        license_name = "unknown"
        if isinstance(card_data, dict) and isinstance(card_data.get("license"), str):
            license_name = card_data["license"]
        resolved.append(ResolvedArtifact(spec=spec, commit=commit, license_name=license_name))
    return resolved


def snapshot_files(metadata: dict[str, Any], spec: ArtifactSpec) -> list[str]:
    """Return a full snapshot or one explicitly selected GGUF."""

    siblings = metadata.get("siblings")
    if not isinstance(siblings, list):
        raise RuntimeError(f"Model metadata for {spec.repository} did not include snapshot files")
    names = [item.get("rfilename") for item in siblings if isinstance(item, dict)]
    files = [name for name in names if isinstance(name, str) and name and not name.endswith("/")]
    if spec.name == "phi-4-mini-instruct-q4":
        expected = os.environ.get("LLM_MODEL_FILENAME", "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf")
        if expected not in files:
            raise RuntimeError(f"{expected} is not available in {spec.repository}@{spec.revision}")
        return [expected]
    if not files:
        raise RuntimeError(f"No downloadable files found for {spec.repository}@{spec.revision}")
    return files


def write_manifest(root: Path, artifacts: list[ResolvedArtifact]) -> Path:
    """Write a verifier-compatible manifest for all provisioned artifacts."""

    manifest_artifacts = []
    for artifact in artifacts:
        destination = safe_destination(root, artifact.spec.path)
        size, digest = digest_path(destination)
        manifest_artifacts.append(
            {
                "name": artifact.spec.name,
                "path": artifact.spec.path.as_posix(),
                "size": size,
                "sha256": digest,
                "revision": artifact.commit,
                "license": artifact.license_name,
                "source": f"https://huggingface.co/{artifact.spec.repository}",
            }
        )
    manifest_path = root / "manifest.lock.json"
    manifest_path.write_text(
        json.dumps(
            {"schema_version": "model-manifest-v1", "artifacts": manifest_artifacts}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def digest_path(path: Path) -> tuple[int, str]:
    """Hash a file or deterministic directory tree without following symlinks."""

    digest = hashlib.sha256()
    total_size = 0
    children = sorted(path.rglob("*") if path.is_dir() else [path])
    for child in children:
        if not child.is_file() or child.is_symlink():
            continue
        relative = child.relative_to(path) if path.is_dir() else child.name
        digest.update(str(relative).encode("utf-8"))
        with child.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                total_size += len(block)
                digest.update(block)
    return total_size, digest.hexdigest()


def safe_destination(root: Path, relative_path: Path | str) -> Path:
    """Keep all downloads inside the configured model root."""

    base = root.resolve()
    destination = (base / relative_path).resolve()
    if destination != base and base not in destination.parents:
        raise ValueError(f"Artifact path escapes MODEL_ARTIFACT_ROOT: {relative_path}")
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-root",
        type=Path,
        help="Directory mounted at /models by Compose (default: MODEL_ARTIFACT_ROOT or models)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace only the known artifact targets and regenerate the manifest",
    )
    return parser.parse_args()


def remove_known_artifacts(root: Path, specs: list[ArtifactSpec]) -> None:
    """Remove only configured targets after explicit --force approval."""

    for spec in specs:
        target = safe_destination(root, spec.path)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    manifest = root / "manifest.lock.json"
    if manifest.exists():
        manifest.unlink()


def main() -> int:
    """Run the one-time networked provisioning flow."""

    args = parse_args()
    environment = load_provisioning_environment(dict(os.environ))
    root = (args.model_root or Path(environment.get("MODEL_ARTIFACT_ROOT", "models"))).expanduser()
    specs = build_specs(environment)
    if args.force:
        remove_known_artifacts(root, specs)
    client = HuggingFaceClient(
        endpoint=environment.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT),
        token=environment.get("HF_TOKEN"),
    )
    artifacts = provision(specs, root, client)
    manifest = write_manifest(root, artifacts)
    print(f"Wrote offline manifest: {manifest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Model provisioning failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
