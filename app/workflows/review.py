"""Pure review state-machine policy shared by API and checkpoint persistence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ReviewStatus = Literal["draft", "needs_review", "approved", "rejected", "superseded"]
ReviewAction = Literal["approve", "edit", "reject", "request_reanalysis", "waive_with_reason"]


class ReviewTransition(BaseModel):
    """Validated state transition before it is materialized in SQL."""

    before: ReviewStatus
    action: ReviewAction
    after: ReviewStatus


def transition(status: ReviewStatus, action: ReviewAction) -> ReviewTransition:
    """Apply the review policy and fail closed for invalid transitions."""

    if action == "approve":
        if status not in {"needs_review", "draft"}:
            raise ValueError(f"Cannot approve product in {status} state")
        after: ReviewStatus = "approved"
    elif action == "reject":
        if status not in {"needs_review", "draft"}:
            raise ValueError(f"Cannot reject product in {status} state")
        after = "rejected"
    elif action == "request_reanalysis":
        if status not in {"needs_review", "rejected"}:
            raise ValueError(f"Cannot request reanalysis from {status} state")
        after = "draft"
    elif action == "edit":
        if status not in {"needs_review", "draft"}:
            raise ValueError(f"Cannot edit product in {status} state")
        after = "needs_review"
    else:
        if status != "needs_review":
            raise ValueError(f"Cannot waive product in {status} state")
        after = "approved"
    return ReviewTransition(before=status, action=action, after=after)
