"""Build and validate evidence-backed business work products."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from app.agents.schemas import ResearchResponse
from app.workflows.registry import get_manifest
from app.workflows.schemas import (
    EvidenceLink,
    WorkProduct,
    WorkProductFinding,
)
from app.workflows.validators import schema_hash, validate_payload


class WorkflowService:
    """Turn a grounded research response into a reviewable workflow product."""

    def build_product(
        self,
        workflow_id: str,
        response: ResearchResponse,
    ) -> WorkProduct:
        """Create a conservative finding for every verified answer claim."""

        manifest = get_manifest(workflow_id)
        findings: list[WorkProductFinding] = []
        citation_by_id = {citation.citation_id: citation for citation in response.answer.citations}
        for index, claim in enumerate(response.claims or response.verification.claims, start=1):
            citation = citation_by_id.get(claim.citation_id)
            evidence = []
            if citation is not None:
                evidence.append(
                    EvidenceLink(
                        claim_id=claim.claim_id,
                        chunk_id=citation.chunk_id,
                        document_id=citation.document_id,
                        source_uri=citation.source_uri,
                        location_marker=citation.location_marker,
                        page=citation.page,
                        quoted_support=citation.quoted_support,
                        anchor_ids=list(citation.source_anchor_ids or citation.element_ids),
                        relation=(
                            "contradicts"
                            if claim.verdict == "contradicted"
                            else "supports"
                        ),
                    )
                )
            status = {
                "supported": "supported",
                "partially_supported": "needs_review",
                "contradicted": "contradicted",
                "unsupported": "insufficient",
                "insufficient": "insufficient",
                "uncertain": "uncertain",
                "stale": "needs_review",
            }.get(claim.verdict, "needs_review")
            findings.append(
                WorkProductFinding(
                    finding_id=f"finding-{index}",
                    field=_map_claim_to_field(claim.claim_text, manifest.required_fields),
                    value=claim.claim_text,
                    status=status,
                    importance="material",
                    confidence=claim.confidence,
                    evidence=evidence,
                )
            )
        present_fields = {finding.field for finding in findings}
        for field in manifest.required_fields:
            if field in present_fields:
                continue
            findings.append(
                WorkProductFinding(
                    finding_id=f"missing-{field}",
                    field=field,
                    value=None,
                    status="missing",
                    importance=(
                        "critical" if field in manifest.critical_fields else "material"
                    ),
                    confidence=0.0,
                )
            )
        needs_review = manifest.always_review or any(
            finding.status != "supported" for finding in findings
        )
        source_document_ids = sorted(
            {
                citation.document_id
                for citation in response.answer.citations
                if citation.document_id
            }
        )
        product = WorkProduct(
            work_product_id=uuid4().hex,
            analysis_run_id=response.trace_id,
            workflow_id=manifest.workflow_id,
            workflow_version=manifest.version,
            schema_version=manifest.schema_version,
            title=manifest.title,
            status="needs_review" if needs_review else "draft",
            findings=findings,
            summary=response.answer.direct_answer,
            limitations=[
                response.answer.limitations
                or "This product is evidence support, not a professional determination."
            ],
            source_document_ids=source_document_ids,
            metadata={
                "manifest_required_fields": manifest.required_fields,
                "prohibited_uses": manifest.prohibited_uses,
                "retrieval_filters": manifest.retrieval_filters,
                "verification_policy": manifest.verification_policy,
                "evaluation_dataset": manifest.evaluation_dataset,
                "verification_version": response.verification_version,
            },
        )
        self.validate_product(product)
        return product

    def validate_product(self, product: WorkProduct) -> None:
        """Apply workflow-independent invariants before persistence or export."""

        payload = product.model_dump(mode="json", exclude={"metadata"})
        if not payload["analysis_run_id"]:
            raise ValueError("Work product requires an analysis run ID")
        if any(
            finding.status == "supported" and not finding.evidence
            for finding in product.findings
        ):
            raise ValueError("Supported findings must include evidence")
        validate_payload(product.workflow_id, payload, product.schema_version)
        product.metadata["schema_hash"] = schema_hash(
            product.workflow_id, product.schema_version
        )
        product.metadata["content_hash"] = sha256(
            product.model_dump_json(exclude={"metadata"}).encode("utf-8")
        ).hexdigest()


def _map_claim_to_field(claim_text: str, required_fields: list[str]) -> str:
    """Project a claim onto the closest workflow field using conservative keywords."""

    lowered = claim_text.lower()
    for field in required_fields:
        tokens = [token for token in field.replace("_", " ").split() if len(token) > 3]
        if tokens and any(token in lowered for token in tokens):
            return field
    return "claim"
