"""Persistence for reviewable workflow products and append-only decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.db.models import ReviewCheckpointRecord, ReviewEventRecord, WorkProductRecord
from app.db.session import DatabaseSessionManager
from app.workflows.review import transition
from app.workflows.schemas import WorkProduct
from app.workflows.validators import validate_payload


class WorkflowRepository:
    """Store materialized products and their review history."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self._session_manager = session_manager

    async def save(self, product: WorkProduct) -> WorkProduct:
        """Insert or update a product before it enters review."""

        payload = product.model_dump(mode="json")
        async with self._session_manager.session() as session:
            record = await session.get(WorkProductRecord, product.work_product_id)
            if record is None:
                session.add(
                    WorkProductRecord(
                        work_product_id=product.work_product_id,
                        analysis_run_id=product.analysis_run_id,
                        workflow_id=product.workflow_id,
                        schema_version=product.schema_version,
                        schema_hash=str(
                            product.metadata.get("schema_hash") or _product_hash(product)
                        ),
                        title=product.title,
                        status=product.status,
                        payload_json=payload,
                    )
                )
            else:
                record.payload_json = payload
                record.status = product.status
                record.updated_at = datetime.now(tz=UTC)
            await self._upsert_checkpoint(session, product)
        return product

    async def get(self, work_product_id: str) -> WorkProduct | None:
        """Load one product by stable ID."""

        async with self._session_manager.session() as session:
            record = await session.get(WorkProductRecord, work_product_id)
        return WorkProduct.model_validate(record.payload_json) if record else None

    async def list_pending(self, limit: int = 100) -> list[WorkProduct]:
        """Return products waiting for human review."""

        async with self._session_manager.session() as session:
            records = (
                await session.scalars(
                    select(WorkProductRecord)
                    .where(WorkProductRecord.status == "needs_review")
                    .order_by(WorkProductRecord.created_at)
                    .limit(limit)
                )
            ).all()
        return [WorkProduct.model_validate(record.payload_json) for record in records]

    async def decide(
        self,
        work_product_id: str,
        actor_id: str,
        action: str,
        comment: str | None,
        expected_hash: str | None,
        edits: dict[str, object] | None = None,
    ) -> WorkProduct:
        """Apply a review decision with optimistic concurrency."""

        allowed = {"approve", "edit", "reject", "request_reanalysis", "waive_with_reason"}
        if action not in allowed:
            raise ValueError(f"Unsupported review action: {action}")
        async with self._session_manager.session() as session:
            record = await session.get(WorkProductRecord, work_product_id)
            if record is None:
                raise LookupError(f"Work product not found: {work_product_id}")
            current = WorkProduct.model_validate(record.payload_json)
            current_hash = _product_hash(current)
            if expected_hash and expected_hash != current_hash:
                raise RuntimeError("Work product changed since it was loaded")
            if action == "waive_with_reason" and not comment:
                raise ValueError("waive_with_reason requires a reviewer comment")
            if action == "approve" and current.status == "approved":
                return current
            if action == "reject" and current.status == "rejected":
                return current
            policy_action = "waive_with_reason" if action == "waive_with_reason" else action
            next_status = transition(current.status, policy_action).after
            updated = current.model_copy(update={"status": next_status})
            if action == "edit":
                updated = _apply_finding_edits(updated, edits or {})
            validate_payload(
                updated.workflow_id,
                updated.model_dump(mode="json", exclude={"metadata"}),
                updated.schema_version,
            )
            updated_hash = _product_hash(updated)
            record.status = next_status
            record.payload_json = updated.model_dump(mode="json")
            record.reviewed_by = actor_id
            record.approved_by = actor_id if next_status == "approved" else None
            await self._upsert_checkpoint(session, updated)
            session.add(
                ReviewEventRecord(
                    event_id=uuid4().hex,
                    work_product_id=work_product_id,
                    actor_id=actor_id,
                    action=action,
                    target_type="work_product",
                    target_id=work_product_id,
                    before_hash=current_hash,
                    after_hash=updated_hash,
                    comment=comment,
                )
            )
        return updated

    async def resume(
        self,
        work_product_id: str,
        actor_id: str,
        comment: str | None,
        expected_hash: str | None,
    ) -> WorkProduct:
        """Move a review product back to draft with an append-only resume event."""

        return await self.decide(
            work_product_id,
            actor_id,
            "request_reanalysis",
            comment,
            expected_hash,
        )

    async def checkpoint(self, work_product_id: str) -> dict[str, object] | None:
        """Return the durable review checkpoint for one work product."""

        async with self._session_manager.session() as session:
            record = await session.scalar(
                select(ReviewCheckpointRecord).where(
                    ReviewCheckpointRecord.work_product_id == work_product_id
                )
            )
        if record is None:
            return None
        return {
            "checkpoint_id": record.checkpoint_id,
            "thread_id": record.thread_id,
            "work_product_id": record.work_product_id,
            "state": record.state_json,
            "status": record.status,
            "state_hash": record.state_hash,
            "version": record.version,
            "updated_at": record.updated_at,
        }

    async def _upsert_checkpoint(self, session, product: WorkProduct) -> None:
        """Materialize a restart-safe checkpoint in the same transaction as product state."""

        state = {
            "work_product_id": product.work_product_id,
            "analysis_run_id": product.analysis_run_id,
            "workflow_id": product.workflow_id,
            "status": product.status,
            "product_hash": _product_hash(product),
        }
        state_hash = _canonical_hash(state)
        record = await session.scalar(
            select(ReviewCheckpointRecord).where(
                ReviewCheckpointRecord.work_product_id == product.work_product_id
            )
        )
        if record is None:
            session.add(
                ReviewCheckpointRecord(
                    checkpoint_id=uuid4().hex,
                    thread_id=product.analysis_run_id,
                    work_product_id=product.work_product_id,
                    state_json=state,
                    status=product.status,
                    state_hash=state_hash,
                )
            )
            return
        if record.state_hash == state_hash:
            return
        record.state_json = state
        record.status = product.status
        record.state_hash = state_hash
        record.version += 1

    async def events(self, work_product_id: str) -> list[dict[str, object]]:
        """Return append-only review history."""

        async with self._session_manager.session() as session:
            records = (
                await session.scalars(
                    select(ReviewEventRecord)
                    .where(ReviewEventRecord.work_product_id == work_product_id)
                    .order_by(ReviewEventRecord.created_at)
                )
            ).all()
        return [
            {
                "event_id": record.event_id,
                "actor_id": record.actor_id,
                "action": record.action,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "before_hash": record.before_hash,
                "after_hash": record.after_hash,
                "comment": record.comment,
                "created_at": record.created_at,
            }
            for record in records
        ]


def _product_hash(product: WorkProduct) -> str:
    """Hash product content excluding mutable metadata."""

    payload = product.model_dump(mode="json", exclude={"metadata"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_hash(payload: dict[str, object]) -> str:
    """Hash checkpoint state with stable JSON serialization."""

    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _apply_finding_edits(product: WorkProduct, edits: dict[str, object]) -> WorkProduct:
    """Apply only field-local edits supplied by a reviewer."""

    findings = []
    for finding in product.findings:
        patch = edits.get(finding.finding_id)
        if not isinstance(patch, dict):
            findings.append(finding)
            continue
        allowed = {
            key: value
            for key, value in patch.items()
            if key in {"value", "reviewer_note"}
        }
        findings.append(finding.model_copy(update=allowed))
    return product.model_copy(update={"findings": findings})
