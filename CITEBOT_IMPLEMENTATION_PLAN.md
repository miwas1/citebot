# CiteBot Agentic RAG Research Assistant - Production Implementation Plan

## Objective

Build a production-ready agentic RAG research assistant that can ingest and retrieve from a 500,000-document corpus, answer research questions with traceable citations, use tools safely, continuously evaluate answer quality, and operate reliably on AWS.

## Target Outcomes

- End-to-end RAG pipeline orchestrated with LangGraph and LangChain retrieval chains.
- Dual vector-store support using PostgreSQL with pgvector and Qdrant.
- Hybrid retrieval combining dense OpenAI embeddings, sparse BM25, and cross-encoder re-ranking.
- Query latency target of sub-200 ms for retrieval-stage responses under realistic cached/indexed conditions.
- Agentic tools for Tavily web search, Python sandbox execution, and citation verification.
- Automated RAGAS evaluation loop with quality thresholds and re-indexing triggers.
- Dynamic context-window compression that reduces token usage while preserving citation traceability.
- Production deployment on FastAPI, Docker, AWS ECS, RDS PostgreSQL, Qdrant, and managed observability.

## Guiding Production Principles

- Treat citation traceability as a core product invariant, not a UI feature.
- Keep retrieval, generation, verification, and tool execution as separately observable stages.
- Make every agent state transition explicit through LangGraph state schemas.
- Prefer deterministic quality gates before adding agent autonomy.
- Design for backfills, re-indexing, partial failures, and corpus growth from day one.
- Do not expose code execution or web tools without policy, resource, timeout, and audit controls.

## Proposed System Architecture

```text
Client
  -> FastAPI Gateway
    -> Conversation Service
      -> LangGraph Research Agent
        -> Query Planner
        -> Hybrid Retrieval Service
          -> BM25 Index
          -> pgvector Store
          -> Qdrant Store
          -> Cross-Encoder Re-ranker
        -> Answer Generator
        -> Citation Verifier
        -> Optional Tools
          -> Tavily Web Search
          -> Python Sandbox
    -> Evaluation Service
      -> RAGAS Metrics
      -> Quality Threshold Monitor
      -> Re-index Trigger

Ingestion Pipeline
  -> Document Loader
  -> Parser and Normalizer
  -> Chunker
  -> Embedding Worker
  -> pgvector Index Writer
  -> Qdrant Index Writer
  -> BM25 Index Writer

Infrastructure
  -> AWS ECS Services
  -> AWS RDS PostgreSQL with pgvector
  -> Qdrant Cluster or Managed Qdrant
  -> Object Storage for Raw Documents
  -> Queue for Ingestion and Re-index Jobs
  -> Metrics, Logs, Traces, Alerts
```

## Phase 0 - Product, Risk, and Architecture Definition

### Goals

- Define non-functional requirements for latency, quality, safety, security, and operations.
- Establish architecture boundaries before implementation starts.

### Scope

- Define user workflows:
  - Ask a research question over the internal corpus.
  - Receive cited answers with source snippets.
  - Ask follow-up questions in a multi-turn session.
  - Request web-augmented research when internal sources are insufficient.
  - Run lightweight Python analysis on retrieved data when allowed.
- Define primary service boundaries:
  - API gateway.
  - ingestion service.
  - retrieval service.
  - agent orchestration service.
  - evaluation service.
  - admin/re-index service.
- Define quality thresholds:
  - minimum RAGAS faithfulness score.
  - minimum context precision score.
  - minimum answer relevance score.
  - maximum allowed citation verification failure rate.
- Define latency budgets:
  - query normalization.
  - dense retrieval.
  - sparse retrieval.
  - re-ranking.
  - generation.
  - citation verification.

### Deliverables

- `docs/architecture/requirements.md`
- `docs/architecture/system-design.md`
- `docs/architecture/quality-thresholds.md`
- `docs/architecture/security-model.md`
- Initial ADRs for vector-store strategy, orchestration model, and evaluation strategy.

### Acceptance Criteria

- All production targets have measurable definitions.
- Agent tool permissions and failure modes are documented.
- Latency targets are separated into retrieval latency and full answer latency.
- Stakeholders agree on what triggers re-indexing and quality investigation.

## Phase 1 - Repository, Runtime, and Development Foundation

### Goals

- Establish a maintainable Python service foundation.
- Add local development, testing, linting, and containerized dependencies.

### Scope

- Create project layout:
  - `app/api`
  - `app/core`
  - `app/agents`
  - `app/retrieval`
  - `app/ingestion`
  - `app/evaluation`
  - `app/tools`
  - `app/observability`
  - `tests`
- Add FastAPI application bootstrap.
- Add typed configuration using environment variables and secrets.
- Add local Docker Compose services:
  - PostgreSQL with pgvector.
  - Qdrant.
  - Redis or queue backend if selected.
  - optional OpenSearch/Elasticsearch if BM25 is not implemented in PostgreSQL.
- Add dependency management, formatting, linting, and test commands.
- Add health, readiness, and version endpoints.

### Deliverables

- Running FastAPI service.
- Docker Compose for local dependencies.
- CI-ready test and lint commands.
- `.env.example` with required configuration.
- Basic architecture docs in the repository.

### Acceptance Criteria

- A developer can run the API and dependencies locally with one documented command.
- Health checks validate database and vector-store connectivity.
- Unit test framework is running in CI.
- Configuration fails fast when required production values are missing.

## Phase 2 - Document Ingestion and Corpus Management

### Goals

- Build an ingestion pipeline that can process, chunk, embed, store, and reprocess a 500,000-document corpus.
- Preserve source metadata required for citation traceability.

### Scope

- Define canonical document model:
  - document ID.
  - source URI.
  - title.
  - author or publisher if available.
  - publication date if available.
  - ingestion timestamp.
  - content hash.
  - access policy metadata.
- Define chunk model:
  - chunk ID.
  - document ID.
  - chunk text.
  - token count.
  - character offsets.
  - section heading.
  - page number or location marker when available.
  - embedding version.
  - index version.
- Implement loaders for the initial corpus format.
- Implement normalization and deduplication.
- Implement chunking strategy with overlap tuned for citation precision.
- Implement embedding workers with batching, retry, idempotency, and rate-limit handling.
- Store raw documents or extracted text in durable object storage.
- Store metadata in PostgreSQL.
- Write embeddings to pgvector and Qdrant.
- Write sparse index entries for BM25.
- Add ingestion job state tracking.

### Deliverables

- Ingestion CLI or admin API.
- Document and chunk database schema.
- Embedding pipeline.
- pgvector writer.
- Qdrant writer.
- BM25 writer.
- Backfill and re-index job runner.
- Ingestion metrics dashboard.

### Acceptance Criteria

- Pipeline can resume safely after worker failure.
- Re-ingesting unchanged documents is idempotent.
- Every indexed chunk can be traced back to a source document and location.
- Embedding version changes can trigger selective re-indexing.
- A sample corpus can be fully ingested locally and queried.

## Phase 3 - Dense Retrieval with pgvector and Qdrant

### Goals

- Implement dense vector retrieval against both pgvector and Qdrant.
- Create an abstraction that allows controlled routing, fallback, and benchmarking.

### Scope

- Define retrieval interface:
  - `search(query, filters, top_k, index_target)`.
  - `batch_search`.
  - `health_check`.
  - `explain`.
- Implement OpenAI embedding generation for queries.
- Implement pgvector retrieval with appropriate indexes.
- Implement Qdrant retrieval with collection configuration and payload filters.
- Add metadata filters for document type, date, source, access policy, and index version.
- Implement fallback behavior:
  - Qdrant primary with pgvector fallback, or vice versa based on benchmark results.
  - degraded retrieval response if one store is unavailable.
- Add retrieval benchmarking harness.

### Deliverables

- Dense retrieval service.
- pgvector schema and index migrations.
- Qdrant collection setup script.
- Benchmark report comparing pgvector and Qdrant on sample workloads.
- Retrieval unit and integration tests.

### Acceptance Criteria

- Dense retrieval returns stable chunk IDs with metadata and scores.
- Both vector stores can be queried through the same application interface.
- Retrieval respects access filters.
- Benchmarking captures p50, p95, and p99 latency.
- Store failure is observable and handled without corrupting agent state.

## Phase 4 - Hybrid Search and Cross-Encoder Re-ranking

### Goals

- Combine dense semantic retrieval with sparse keyword retrieval.
- Improve answer relevance with cross-encoder re-ranking.

### Scope

- Implement BM25 retrieval using the selected sparse backend.
- Add hybrid retrieval strategy:
  - dense top-k.
  - sparse top-k.
  - score normalization.
  - reciprocal rank fusion or weighted blending.
  - duplicate removal by chunk ID.
- Add cross-encoder re-ranking:
  - candidate window size.
  - model selection.
  - batching.
  - timeout and fallback.
  - CPU/GPU deployment decision.
- Add retrieval explainability payload:
  - dense score.
  - sparse score.
  - fused score.
  - re-ranker score.
  - selected reason.
- Tune chunking, top-k, and re-ranker candidate size against evaluation sets.

### Deliverables

- Hybrid retrieval implementation.
- Re-ranker service or in-process adapter.
- Evaluation notebook or script for relevance comparison.
- Retrieval tuning report.
- Tests for score fusion, deduplication, filtering, and fallback.

### Acceptance Criteria

- Hybrid retrieval outperforms dense-only retrieval on answer relevance benchmarks.
- Re-ranking can be disabled safely through configuration.
- Retrieval explain output is available for debugging.
- Re-ranker timeouts do not break the response path.
- Quality improvement target is validated against a fixed evaluation set before launch.

## Phase 5 - LangGraph Agent Orchestration

### Goals

- Implement an explicit LangGraph state machine for research workflows.
- Keep retrieval, tool use, generation, and verification independently testable.

### Scope

- Define LangGraph state schema:
  - user query.
  - conversation history.
  - compressed memory.
  - retrieval plan.
  - retrieved contexts.
  - tool calls.
  - draft answer.
  - citation verification results.
  - final answer.
  - error state.
- Implement graph nodes:
  - input validation.
  - query classification.
  - query rewriting.
  - retrieval planning.
  - hybrid retrieval.
  - answer generation.
  - citation verification.
  - web search escalation.
  - Python analysis escalation.
  - final response formatting.
  - error handling.
- Implement graph edges:
  - retrieval required.
  - insufficient context.
  - web search allowed.
  - computation required.
  - verification failed.
  - retry or return guarded response.
- Add structured state persistence for multi-turn sessions.
- Add trace IDs across graph execution.

### Deliverables

- LangGraph research agent.
- Typed state schema.
- Node-level tests.
- End-to-end graph tests.
- Agent trace logging.
- Error and retry policy documentation.

### Acceptance Criteria

- Every agent decision is represented as a state transition.
- Agent failures return controlled responses with trace IDs.
- Multi-turn conversations preserve citations and source context.
- Tool use is policy-gated and auditable.
- Graph execution can be replayed in lower environments for debugging.

## Phase 6 - Agentic Tools

### Goals

- Add safe, production-grade tools for web search, Python execution, and citation verification.
- Ensure tools are bounded, observable, and recoverable.

### Scope

#### Tavily Web Search

- Implement Tavily client with API timeout, retries, and rate-limit handling.
- Restrict web use to explicit states:
  - internal corpus insufficient.
  - freshness required.
  - user requests web research.
- Normalize web results into citation-compatible context chunks.
- Mark web citations separately from internal corpus citations.

#### Python Code Execution Sandbox

- Implement isolated execution environment.
- Enforce:
  - CPU timeout.
  - memory limit.
  - no filesystem access except approved scratch space.
  - no network access unless explicitly allowed.
  - output size limit.
- Support common analysis libraries only if product scope requires them.
- Log code, inputs, outputs, runtime, and termination reason.

#### Citation Verifier Agent

- Verify that each factual claim maps to retrieved source context.
- Detect unsupported claims, citation/source mismatch, and stale web results.
- Return structured verification results:
  - citation ID.
  - claim text.
  - supporting chunk IDs.
  - verdict.
  - confidence.
  - failure reason.

### Deliverables

- Tavily tool adapter.
- Python sandbox adapter.
- Citation verifier node.
- Tool policy configuration.
- Tool audit logs.
- Tool failure test suite.

### Acceptance Criteria

- Tool failures do not crash graph execution.
- Python code execution cannot access secrets or host resources.
- Citation verifier can block or revise unsupported answers.
- Web results are clearly attributed and time-stamped.
- Tool calls have metrics, logs, and trace correlation.

## Phase 7 - Answer Generation, Citations, and Context Compression

### Goals

- Produce high-quality answers with precise, auditable citations.
- Reduce token usage through compression without losing source traceability.

### Scope

- Define answer format:
  - direct answer.
  - supporting evidence.
  - citations.
  - limitations or uncertainty.
  - follow-up suggestions only when useful.
- Implement prompt templates for:
  - answer synthesis.
  - citation-grounded generation.
  - uncertainty handling.
  - refusal or insufficient context.
- Implement citation model:
  - citation IDs.
  - chunk IDs.
  - document metadata.
  - source location.
  - quoted or paraphrased support span.
- Implement sliding-window compression:
  - preserve recent turns.
  - summarize older turns.
  - retain citation graph.
  - keep unresolved user constraints.
  - store compressed memory with provenance.
- Add token accounting by graph stage.
- Add regression tests for citation preservation after compression.

### Deliverables

- Prompt library.
- Citation formatter.
- Context compression module.
- Token usage metrics.
- Citation traceability tests.

### Acceptance Criteria

- Generated answers never cite documents that were not retrieved or verified.
- Compressed conversation memory preserves source IDs needed for follow-up questions.
- Token usage reduction is measured against baseline conversations.
- Unsupported claims are removed, qualified, or flagged.
- Answers remain usable when context is insufficient.

## Phase 8 - Automated Evaluation and Quality Monitoring

### Goals

- Continuously measure retrieval and generation quality.
- Trigger investigation or re-indexing when quality degrades.

### Scope

- Build evaluation dataset:
  - golden questions.
  - expected answer characteristics.
  - relevant source documents.
  - adversarial questions.
  - freshness-sensitive questions.
  - multi-turn citation questions.
- Implement RAGAS metrics:
  - faithfulness.
  - context precision.
  - answer relevance.
  - context recall if ground truth is available.
- Add retrieval-specific metrics:
  - hit rate at k.
  - MRR.
  - nDCG.
  - citation verification pass rate.
- Add scheduled evaluation jobs.
- Add threshold monitor.
- Add re-index trigger workflow:
  - alert.
  - isolate affected source/index version.
  - run validation subset.
  - re-index if confirmed.
  - compare before/after results.
- Store eval runs and artifacts.

### Deliverables

- Evaluation service.
- RAGAS integration.
- Golden dataset format.
- Evaluation dashboards.
- Threshold alerting.
- Re-index trigger workflow.

### Acceptance Criteria

- Evaluation can run locally and in CI against a small fixture set.
- Scheduled production evaluation records trend history.
- Quality threshold breaches create actionable alerts.
- Re-indexing is not triggered by single noisy samples.
- Releases are blocked if core evaluation scores regress beyond agreed tolerance.

## Phase 9 - API, UX Contracts, and External Integration

### Goals

- Expose stable APIs for research conversations, citations, admin ingestion, and evaluation.
- Define contracts that allow a frontend or external client to integrate safely.

### Scope

- Implement API endpoints:
  - create conversation.
  - send message.
  - stream answer.
  - fetch citations.
  - fetch conversation trace summary.
  - submit documents for ingestion.
  - check ingestion job status.
  - run evaluation.
  - get evaluation results.
- Add streaming response support.
- Add structured error responses.
- Add authentication and authorization.
- Add request validation and payload limits.
- Add rate limiting.
- Add API documentation.

### Deliverables

- Versioned FastAPI routes.
- OpenAPI schema.
- API contract tests.
- Auth middleware.
- Rate-limit middleware.
- Streaming response implementation.

### Acceptance Criteria

- API clients can distinguish model errors, retrieval errors, policy errors, and validation errors.
- Conversation and citation endpoints are stable and documented.
- Admin endpoints are protected separately from user endpoints.
- Streaming responses include final citation metadata.
- Rate limits protect expensive graph and tool paths.

## Phase 10 - Observability, Reliability, and Security Hardening

### Goals

- Make the system operable under production load.
- Add controls for secrets, access policy, abuse prevention, and incident response.

### Scope

- Add structured logging.
- Add metrics:
  - request latency.
  - retrieval latency by backend.
  - re-ranker latency.
  - model latency.
  - tool latency.
  - token usage.
  - citation verification pass rate.
  - RAGAS scores.
  - ingestion throughput.
  - queue depth.
  - vector-store error rates.
- Add distributed tracing across API, agent graph, retrieval, tools, and evaluation.
- Add alerting:
  - elevated p95 latency.
  - vector-store failure.
  - RAGAS degradation.
  - citation verification failure spike.
  - ingestion backlog.
  - OpenAI/Tavily rate-limit exhaustion.
- Add security:
  - secret management.
  - network isolation.
  - least-privilege IAM.
  - audit logging.
  - prompt-injection handling for retrieved and web content.
  - sandbox escape tests.
  - PII handling if corpus contains sensitive data.

### Deliverables

- Observability dashboards.
- Alert definitions.
- Threat model.
- Security checklist.
- Incident runbooks.
- Load-test plan.

### Acceptance Criteria

- Every production request has a trace ID.
- Operators can diagnose slow answers by stage.
- Secrets are never exposed to agent tools or logs.
- Prompt-injection test cases are part of CI or release validation.
- Sandbox policy is validated before production enablement.

## Phase 11 - Deployment and Infrastructure

### Goals

- Deploy the system to AWS in a repeatable, secure, and scalable way.

### Scope

- Containerize services:
  - API/agent service.
  - ingestion worker.
  - evaluation worker.
  - optional re-ranker service.
- Define AWS infrastructure:
  - ECS service definitions.
  - RDS PostgreSQL with pgvector.
  - Qdrant deployment or managed Qdrant.
  - object storage for raw documents.
  - queue for ingestion/evaluation jobs.
  - load balancer.
  - secrets manager.
  - logging and monitoring.
- Add migration workflow.
- Add blue/green or rolling deployment strategy.
- Add backup and restore plan:
  - PostgreSQL.
  - vector indexes.
  - raw documents.
  - evaluation artifacts.
- Add environment promotion:
  - local.
  - staging.
  - production.

### Deliverables

- Dockerfiles.
- Infrastructure-as-code.
- ECS task definitions.
- Deployment pipeline.
- Migration pipeline.
- Backup and restore runbook.

### Acceptance Criteria

- Staging can be rebuilt from infrastructure definitions.
- Production deploys are repeatable and auditable.
- Database migrations are backward-compatible for rolling deploys.
- Restore procedure is tested before launch.
- Resource sizing is documented with expected throughput and cost assumptions.

## Phase 12 - Performance, Scale, and Launch Readiness

### Goals

- Validate the system against target corpus size, realistic query patterns, and production SLOs.
- Prepare the launch checklist and operational ownership model.

### Scope

- Load test:
  - retrieval-only.
  - full answer generation.
  - streaming conversations.
  - ingestion backfills.
  - evaluation jobs.
- Benchmark:
  - 500,000-document corpus.
  - representative chunk count.
  - realistic metadata filters.
  - concurrent users.
  - cold and warm cache behavior.
- Optimize:
  - pgvector indexes.
  - Qdrant collection parameters.
  - BM25 backend.
  - re-ranker batch size.
  - embedding cache.
  - prompt/context size.
- Run release validation:
  - security tests.
  - prompt-injection tests.
  - tool failure tests.
  - RAGAS regression suite.
  - backup restore drill.
  - incident runbook drill.

### Deliverables

- Performance benchmark report.
- Launch readiness checklist.
- Production SLO document.
- Operational runbooks.
- Final go/no-go review.

### Acceptance Criteria

- Retrieval-stage latency meets the agreed p95 target under load.
- Full answer latency meets the agreed user-facing SLO or is transparently streamed.
- Quality metrics meet launch thresholds.
- All critical alerts have owners and runbooks.
- Rollback and restore procedures are tested.

## Cross-Cutting Data Contracts

### Document

```json
{
  "document_id": "string",
  "source_uri": "string",
  "title": "string",
  "publisher": "string",
  "published_at": "datetime|null",
  "ingested_at": "datetime",
  "content_hash": "string",
  "access_policy": "string",
  "metadata": {}
}
```

### Chunk

```json
{
  "chunk_id": "string",
  "document_id": "string",
  "text": "string",
  "token_count": 512,
  "char_start": 0,
  "char_end": 2048,
  "section": "string|null",
  "page": "integer|null",
  "embedding_model": "string",
  "embedding_version": "string",
  "index_version": "string"
}
```

### Retrieval Result

```json
{
  "chunk_id": "string",
  "document_id": "string",
  "text": "string",
  "metadata": {},
  "dense_score": 0.0,
  "sparse_score": 0.0,
  "fused_score": 0.0,
  "rerank_score": 0.0,
  "source_backend": "pgvector|qdrant|bm25|hybrid"
}
```

### Citation

```json
{
  "citation_id": "string",
  "document_id": "string",
  "chunk_id": "string",
  "source_uri": "string",
  "title": "string",
  "location": "string",
  "supporting_text": "string",
  "verification_status": "supported|partial|unsupported",
  "confidence": 0.0
}
```

## Initial Milestone Plan

### Milestone 1 - Foundation and Local RAG

- Complete phases 0 through 3.
- Demonstrate local ingestion and dense retrieval over a small sample corpus.
- Prove citation metadata survives ingestion and retrieval.

### Milestone 2 - Quality Retrieval and Agent Graph

- Complete phases 4 through 7.
- Demonstrate hybrid retrieval, re-ranking, LangGraph orchestration, citations, and context compression.
- Validate answer quality on an initial golden dataset.

### Milestone 3 - Evaluation and Production API

- Complete phases 8 through 10.
- Add RAGAS evaluation, protected APIs, observability, security controls, and tool hardening.

### Milestone 4 - AWS Deployment and Launch

- Complete phases 11 and 12.
- Deploy to staging, run corpus-scale benchmarks, complete security checks, and launch behind controlled access.

## Key Technical Decisions to Make Early

- Whether Qdrant or pgvector is the primary dense retrieval backend.
- Whether BM25 is implemented with PostgreSQL full-text search, OpenSearch, Elasticsearch, or another search component.
- Whether the cross-encoder runs in-process, as a separate service, or through a managed inference endpoint.
- Which OpenAI embedding model and chat model are production defaults.
- Whether Python execution is enabled for all users, trusted users only, or admin workflows only.
- How document-level access controls are represented and enforced during retrieval.
- Whether evaluation runs on production samples, synthetic sets, or curated golden sets only.

## Production Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Retrieval latency exceeds target | Slow user experience | Benchmark pgvector and Qdrant early, tune indexes, cache embeddings, cap candidate windows |
| Cross-encoder becomes bottleneck | Increased p95/p99 latency | Batch requests, add timeout fallback, deploy separately, tune candidate size |
| Citations are inaccurate | Loss of trust | Enforce citation verifier before final answer, keep source offsets, test traceability |
| Prompt injection from retrieved/web content | Unsafe or manipulated output | Treat retrieved content as untrusted, isolate instructions, add injection tests |
| Python sandbox escape or abuse | Security incident | Use isolated runtime, strict resource limits, no secret access, audit logs |
| Evaluation scores are noisy | False re-indexing or missed regressions | Use rolling windows, minimum sample sizes, and human-reviewed golden sets |
| Dual vector stores drift | Inconsistent results | Track index versions, reconcile counts, run consistency checks |
| Re-indexing overloads production | Service degradation | Use queue rate limits, off-peak scheduling, shadow indexes, staged swaps |

## Definition of Done for Production Launch

- The 500,000-document corpus is ingested with verified document and chunk counts.
- Dense, sparse, hybrid, and re-ranked retrieval are benchmarked and documented.
- LangGraph agent workflows pass end-to-end tests for normal, degraded, and failure paths.
- Citations are verified before final answer delivery.
- RAGAS and retrieval evaluation suites run automatically.
- Alerts exist for latency, quality, tool failures, and ingestion failures.
- Python sandbox and Tavily tool use are policy-controlled and audited.
- AWS staging and production environments are reproducible from infrastructure definitions.
- Backup, restore, rollback, and incident runbooks are tested.
- Launch SLOs and operational ownership are documented.
