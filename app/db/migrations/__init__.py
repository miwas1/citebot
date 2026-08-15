"""Small forward-only migration registry for local and test databases."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import inspect, text

Migration = Callable[[object], None]


def _migration_001_foundation(connection) -> None:
    """Record the foundation schema created by SQLAlchemy metadata."""

    # New installations receive the tables through Base.metadata.create_all.
    # This migration exists so upgrades have an explicit, inspectable checkpoint.
    inspector = inspect(connection)
    required = {
        "document_versions",
        "source_anchors",
        "analysis_runs",
        "claims",
        "claim_evidence",
        "work_products",
        "review_events",
        "review_checkpoints",
        "calculation_runs",
        "document_diffs",
        "element_diffs",
    }
    missing = sorted(required - set(inspector.get_table_names()))
    if missing:
        raise RuntimeError(
            "Foundation schema is incomplete; metadata creation did not create: "
            + ", ".join(missing)
        )


MIGRATIONS: dict[str, Migration] = {
    "001_foundation": _migration_001_foundation,
}


def run_migrations(connection) -> None:
    """Run each registered migration once on the active database connection."""

    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version VARCHAR(128) PRIMARY KEY, applied_at DATETIME NOT NULL)"
    )
    applied = {
        row[0]
        for row in connection.execute(text("SELECT version FROM schema_migrations"))
    }
    for version, migration in MIGRATIONS.items():
        if version in applied:
            continue
        migration(connection)
        connection.execute(
            text(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (:version, CURRENT_TIMESTAMP)"
            ),
            {"version": version},
        )
