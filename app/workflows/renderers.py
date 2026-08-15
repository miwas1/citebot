"""Deterministic work-product renderers and evidence-bundle assembly."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from app.workflows.schemas import WorkProduct


def render_json(product: WorkProduct) -> str:
    """Render the exact validated product payload with stable key ordering."""

    return json.dumps(product.model_dump(mode="json"), sort_keys=True, indent=2)


def render_markdown(product: WorkProduct) -> str:
    """Render a human-readable report that keeps every finding review-visible."""

    lines = [
        f"# {product.title}",
        "",
        f"- Status: **{product.status}**",
        f"- Workflow: `{product.workflow_id}@{product.workflow_version}`",
        f"- Analysis run: `{product.analysis_run_id}`",
        "",
        product.summary,
        "",
        "## Findings",
        "",
    ]
    for finding in product.findings:
        lines.extend(
            [
                f"### {finding.field} — {finding.status}",
                f"- Value: {finding.value}",
                f"- Importance: {finding.importance}",
                f"- Confidence: {finding.confidence:.2f}",
            ]
        )
        for evidence in finding.evidence:
            location = evidence.location_marker or (
                f"page {evidence.page}" if evidence.page else "source"
            )
            lines.append(
                f"- Evidence: `{evidence.document_id or evidence.source_uri}` ({location})"
            )
        lines.append("")
    if product.limitations:
        lines.extend(["## Limitations", "", *[f"- {item}" for item in product.limitations], ""])
    return "\n".join(lines)


def evidence_bundle(
    product: WorkProduct,
    review_events: list[dict[str, Any]] | None = None,
) -> bytes:
    """Return a ZIP bundle with portable, source-linked audit artifacts."""

    manifest = {
        "bundle_version": "v1",
        "work_product_id": product.work_product_id,
        "workflow_id": product.workflow_id,
        "schema_version": product.schema_version,
        "status": product.status,
        "generated_at": datetime.now(UTC).isoformat(),
        "contains_source_documents": False,
    }
    claims = [finding.model_dump(mode="json") for finding in product.findings]
    provenance = [
        evidence.model_dump(mode="json")
        for finding in product.findings
        for evidence in finding.evidence
    ]
    files = {
        "manifest.json": manifest,
        "work-product.json": product.model_dump(mode="json"),
        "claims.json": claims,
        "provenance.json": provenance,
        "review-history.json": review_events or [],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", render_markdown(product))
        for name, payload in files.items():
            archive.writestr(name, json.dumps(payload, sort_keys=True, indent=2))
    return output.getvalue()
