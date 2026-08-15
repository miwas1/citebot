"""Versioned JSON Schema validation for persisted work products."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_ROOT = Path(__file__).with_name("schemas")


def schema_path(workflow_id: str, schema_version: str = "v1") -> Path:
    """Return the checked-in schema path for one workflow output contract."""

    return _SCHEMA_ROOT / f"{workflow_id}.{schema_version}.json"


def load_schema(workflow_id: str, schema_version: str = "v1") -> dict[str, Any]:
    """Load and validate a checked-in Draft 2020-12 schema document."""

    path = schema_path(workflow_id, schema_version)
    if not path.exists():
        raise ValueError(f"No output schema registered for workflow: {workflow_id}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    if schema.get("allOf"):
        base = json.loads(
            (_SCHEMA_ROOT / "contract_review.v1.json").read_text(encoding="utf-8")
        )
        base["$id"] = schema["$id"]
        base["title"] = schema["title"]
        workflow_id = workflow_id
        base["properties"]["workflow_id"] = {"const": workflow_id}
        schema = base
    Draft202012Validator.check_schema(schema)
    return schema


def schema_hash(workflow_id: str, schema_version: str = "v1") -> str:
    """Return a stable hash of the canonical output schema."""

    schema = load_schema(workflow_id, schema_version)
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def validate_payload(
    workflow_id: str,
    payload: dict[str, Any],
    schema_version: str = "v1",
) -> None:
    """Raise a useful validation error when a generated payload violates its contract."""

    validator = Draft202012Validator(load_schema(workflow_id, schema_version))
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or "payload"
        raise ValueError(f"{location}: {errors[0].message}")
