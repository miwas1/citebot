"""Tests for local-only policy and structured document provenance."""

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.agents.generation import LlamaCppAnswerGenerator
from app.agents.schemas import ResearchGenerationRequest, ResearchMemory
from app.core.config import Settings
from app.core.lifecycle import build_container
from app.core.model_manifest import verify_model_manifest
from app.ingestion.embedder import LocalHttpEmbedder
from app.ingestion.loaders import LocalCorpusLoader

_PROVISIONER_PATH = Path(__file__).parents[1] / "scripts" / "provision_local_models.py"
_PROVISIONER_SPEC = importlib.util.spec_from_file_location(
    "citebot_model_provisioner", _PROVISIONER_PATH
)
assert _PROVISIONER_SPEC is not None and _PROVISIONER_SPEC.loader is not None
_PROVISIONER = importlib.util.module_from_spec(_PROVISIONER_SPEC)
sys.modules[_PROVISIONER_SPEC.name] = _PROVISIONER
_PROVISIONER_SPEC.loader.exec_module(_PROVISIONER)

ArtifactSpec = _PROVISIONER.ArtifactSpec
ResolvedArtifact = _PROVISIONER.ResolvedArtifact
build_specs = _PROVISIONER.build_specs
load_provisioning_environment = _PROVISIONER.load_provisioning_environment
provision = _PROVISIONER.provision
safe_destination = _PROVISIONER.safe_destination
write_manifest = _PROVISIONER.write_manifest


def test_offline_production_rejects_hosted_providers() -> None:
    """Offline production cannot select a hosted inference or evaluation provider."""

    with pytest.raises(ValueError, match="Hosted embedding providers"):
        Settings(
            APP_ENV="production",
            RUNTIME_MODE="offline",
            EMBEDDING_PROVIDER="openai",
            OPENAI_API_KEY="test-key",
            ANSWER_PROVIDER="llama-cpp",
            EVALUATION_EVALUATOR_PROVIDER="local",
            RESEARCH_API_KEY="research-key",
            ADMIN_API_KEY="admin-key",
            _env_file=None,
        )


def test_offline_production_rejects_public_local_service_url() -> None:
    """Model and vector endpoints must resolve through the configured local allowlist."""

    with pytest.raises(ValueError, match="not allowed by LOCAL_SERVICE_HOSTS"):
        Settings(
            APP_ENV="production",
            RUNTIME_MODE="offline",
            QDRANT_URL="https://example.invalid/collections",
            EMBEDDING_PROVIDER="local-http",
            ANSWER_PROVIDER="llama-cpp",
            EVALUATION_EVALUATOR_PROVIDER="local",
            RESEARCH_API_KEY="research-key",
            ADMIN_API_KEY="admin-key",
            _env_file=None,
        )


def test_pdf_loader_preserves_native_page_elements(tmp_path: Path) -> None:
    """Native PDF extraction produces page and element provenance without OCR."""

    fitz = pytest.importorskip("fitz")
    path = tmp_path / "native.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Citation traceability")
    document.save(path)
    document.close()

    settings = Settings(
        DOCUMENT_PARSER="native",
        OCR_PROVIDER="none",
        _env_file=None,
    )
    loaded = LocalCorpusLoader(settings).load(path)[0]

    assert loaded.structured is not None
    assert loaded.structured.pages[0].page_number == 1
    assert loaded.structured.pages[0].elements[0].source_engine == "pymupdf"
    assert "Citation traceability" in loaded.text


@pytest.mark.asyncio
async def test_sqlite_worker_queue_claims_and_completes_job(tmp_path: Path) -> None:
    """Queued ingestion survives submission until a worker claims it."""

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "notes.md").write_text("local queue retrieval", encoding="utf-8")
    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'citebot.db'}",
        OBJECT_STORAGE_PATH=tmp_path / "raw",
        STRUCTURED_DOCUMENT_PATH=tmp_path / "structured",
        SPARSE_INDEX_PATH=tmp_path / "sparse.json",
        ENABLE_QDRANT=False,
        ENABLE_PGVECTOR=False,
        EMBEDDING_PROVIDER="local",
        ANSWER_PROVIDER="local",
        INGESTION_EXECUTION_MODE="queued",
        _env_file=None,
    )
    container = build_container(settings)
    await container.initialize()
    try:
        queued = await container.ingestion_service.enqueue_path(corpus)
        completed = await container.ingestion_service.run_next_job("test-worker")
    finally:
        await container.close()

    assert queued.status == "queued"
    assert completed is not None
    assert completed.status == "completed"
    assert completed.documents_indexed == 1


@pytest.mark.asyncio
async def test_local_http_model_adapters_use_private_contracts() -> None:
    """Embedding and generation adapters validate the local service payloads."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={"data": [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"direct_answer":"grounded","supporting_evidence":["e"]}'
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    embedder = LocalHttpEmbedder(
        base_url="http://embedding:8081",
        model_name="test-model",
        dimensions=2,
        transport=transport,
    )
    assert await embedder.embed_texts(["one", "two"]) == [[1.0, 0.0], [0.0, 1.0]]

    generator = LlamaCppAnswerGenerator(
        base_url="http://llm:8082/v1",
        model="test-model",
        transport=transport,
    )
    answer = await generator.generate(
        ResearchGenerationRequest(
            query="What?",
            trace_id="trace",
            memory=ResearchMemory(),
        )
    )
    assert answer.direct_answer == "grounded"


def test_model_provisioner_writes_verifiable_local_manifest(tmp_path: Path) -> None:
    """Provisioning records a fixed source revision and local artifact checksum."""

    artifact_path = tmp_path / "paddleocr"
    (artifact_path / "detection").mkdir(parents=True)
    (artifact_path / "detection" / "config.yml").write_text("local", encoding="utf-8")
    spec = ArtifactSpec(
        name="paddleocr-models",
        repository="PaddlePaddle/PP-OCRv6_tiny_det",
        revision="main",
        path=Path("paddleocr"),
    )

    manifest = write_manifest(
        tmp_path,
        [ResolvedArtifact(spec=spec, commit="a" * 40, license_name="apache-2.0")],
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["artifacts"][0]["revision"] == "a" * 40
    assert payload["artifacts"][0]["size"] == len("local")
    assert safe_destination(tmp_path, "paddleocr/detection").is_dir()
    with pytest.raises(ValueError, match="escapes"):
        safe_destination(tmp_path, "../outside")


def test_model_provisioner_downloads_into_compose_paths(tmp_path: Path) -> None:
    """The provisioner creates the exact local paths mounted by Compose services."""

    class FakeHuggingFaceClient:
        def model_info(self, repository: str, revision: str) -> dict[str, object]:
            filename = (
                "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf"
                if "Phi-4" in repository
                else "config.json"
            )
            return {
                "sha": "b" * 40,
                "cardData": {"license": "apache-2.0"},
                "siblings": [{"rfilename": filename}],
            }

        def download_file(
            self, repository: str, commit: str, filename: str, target: Path
        ) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{repository}@{commit}:{filename}", encoding="utf-8")

    artifacts = provision(build_specs({}), tmp_path, FakeHuggingFaceClient())
    manifest = write_manifest(tmp_path, artifacts)

    assert (tmp_path / "Qwen3-Embedding-0.6B" / "config.json").is_file()
    assert (tmp_path / "phi-4-mini-instruct-q4.gguf").is_file()
    assert (tmp_path / "paddleocr" / "detection" / "config.json").is_file()
    assert (tmp_path / "paddleocr" / "recognition" / "config.json").is_file()
    verify_model_manifest(manifest)


def test_model_provisioner_reads_dotenv_without_overriding_shell(tmp_path: Path) -> None:
    """Provisioning defaults are configurable from `.env` and shell values win."""

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "EMBEDDING_MODEL_REPOSITORY=example/embedding\nLLM_MODEL_REVISION='commit'\n",
        encoding="utf-8",
    )

    values = load_provisioning_environment(
        {"EMBEDDING_MODEL_REPOSITORY": "shell/embedding"}, dotenv
    )

    assert values["EMBEDDING_MODEL_REPOSITORY"] == "shell/embedding"
    assert values["LLM_MODEL_REVISION"] == "commit"
