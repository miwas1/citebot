"""Built-in workflow manifests kept small enough for the offline runtime."""

from __future__ import annotations

from app.workflows.schemas import WorkflowManifest

_MANIFESTS = [
    WorkflowManifest(
        workflow_id="contract_review",
        version="v1",
        title="Contract review",
        description="Extract material contractual terms, gaps, and risks with evidence.",
        prohibited_uses=["legal advice", "automatic signing", "final legal conclusion"],
        required_fields=["parties", "term", "termination", "payment", "liability"],
        critical_fields=["parties", "term", "termination", "liability"],
        always_review=True,
        output_schema="contract_review.v1.json",
        evaluation_dataset="contract_review/v1.json",
        verification_policy={"critical_fields": ["term", "termination", "liability"]},
    ),
    WorkflowManifest(
        workflow_id="compliance_mapping",
        version="v1",
        title="Compliance evidence map",
        description="Map requirements to implementation evidence and identify gaps.",
        prohibited_uses=["certifying legal compliance", "replacing an auditor"],
        required_fields=["requirement", "evidence", "gap", "owner"],
        critical_fields=["requirement", "gap"],
        always_review=True,
        output_schema="compliance_mapping.v1.json",
        evaluation_dataset="compliance_mapping/v1.json",
    ),
    WorkflowManifest(
        workflow_id="vendor_comparison",
        version="v1",
        title="Vendor comparison",
        description="Compare vendor claims and requirements using cited findings.",
        prohibited_uses=["automatic purchasing decision"],
        required_fields=["criterion", "vendor_value", "evidence", "exception"],
        critical_fields=["vendor_value", "exception"],
        output_schema="vendor_comparison.v1.json",
        evaluation_dataset="vendor_comparison/v1.json",
    ),
    WorkflowManifest(
        workflow_id="due_diligence",
        version="v1",
        title="Due-diligence register",
        description="Produce a reviewable risk and missing-information register.",
        prohibited_uses=["investment advice", "automatic transaction approval"],
        required_fields=["finding", "category", "impact", "evidence", "missing_information"],
        critical_fields=["finding", "impact"],
        always_review=True,
        output_schema="due_diligence.v1.json",
        evaluation_dataset="due_diligence/v1.json",
    ),
    WorkflowManifest(
        workflow_id="investigation_timeline",
        version="v1",
        title="Investigation timeline",
        description="Organize dated events, entities, conflicts, and evidence.",
        prohibited_uses=["final disciplinary or legal decision"],
        required_fields=["timestamp", "event", "entity", "evidence", "certainty"],
        critical_fields=["timestamp", "event"],
        always_review=True,
        output_schema="investigation_timeline.v1.json",
        evaluation_dataset="investigation_timeline/v1.json",
    ),
]

MANIFESTS = {manifest.workflow_id: manifest for manifest in _MANIFESTS}


def get_manifest(workflow_id: str) -> WorkflowManifest:
    """Return a built-in manifest or fail closed for unknown workflows."""

    try:
        return MANIFESTS[workflow_id]
    except KeyError as error:
        raise ValueError(f"Unknown workflow: {workflow_id}") from error


def list_manifests() -> list[WorkflowManifest]:
    """Return available workflow packs in stable order."""

    return list(MANIFESTS.values())
