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


def _migration_002_projects(connection) -> None:
    """Create the sample workspace and scope pre-project rows to it."""

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "projects" not in tables:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(
                "CREATE TABLE projects ("
                "project_id VARCHAR(64) PRIMARY KEY, "
                "name VARCHAR(255) NOT NULL, slug VARCHAR(255) NOT NULL UNIQUE, "
                "description TEXT, status VARCHAR(32) NOT NULL DEFAULT 'active', "
                "is_sample BOOLEAN NOT NULL DEFAULT FALSE, "
                "created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)"
            )
        else:
            connection.exec_driver_sql(
                "CREATE TABLE projects ("
                "project_id VARCHAR(64) PRIMARY KEY, "
                "name VARCHAR(255) NOT NULL, slug VARCHAR(255) NOT NULL UNIQUE, "
                "description TEXT, status VARCHAR(32) NOT NULL DEFAULT 'active', "
                "is_sample BOOLEAN NOT NULL DEFAULT 0, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
    for values in (
        (
            "sample-project",
            "Sample Project",
            "sample",
            "Bundled CiteBot sources, ready for a first query.",
            True,
        ),
    ):
        connection.execute(
            text(
                "INSERT INTO projects "
                "(project_id, name, slug, description, status, is_sample, created_at, updated_at) "
                "VALUES (:project_id, :name, :slug, :description, 'active', :is_sample, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (project_id) DO NOTHING"
            ),
            {
                "project_id": values[0],
                "name": values[1],
                "slug": values[2],
                "description": values[3],
                "is_sample": values[4],
            },
        )
    for table in ("documents", "ingestion_jobs", "research_sessions", "analysis_runs"):
        if table in tables and "project_id" in {
            column["name"] for column in inspect(connection).get_columns(table)
        }:
            connection.exec_driver_sql(
                f"UPDATE {table} SET project_id = 'sample-project' "
                "WHERE project_id IS NULL"
            )
    if "documents" in tables:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_documents_source_uri")
        else:
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_documents_source_uri")
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_project_source "
            "ON documents(project_id, source_uri)"
        )


def _migration_003_retire_legacy_project(connection) -> None:
    """Move pre-project data into a named import workspace on upgrade."""

    inspector = inspect(connection)
    if "projects" not in inspector.get_table_names():
        return
    legacy = connection.execute(
        text(
            "SELECT name, description, status, is_sample, created_at, updated_at "
            "FROM projects WHERE project_id = 'legacy-project'"
        )
    ).first()
    if legacy is None:
        return

    imported_id = "imported-documents"
    imported_exists = connection.execute(
        text("SELECT 1 FROM projects WHERE project_id = :project_id"),
        {"project_id": imported_id},
    ).first()
    if imported_exists is None:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(project_id, name, slug, description, status, is_sample, created_at, updated_at) "
                "VALUES (:project_id, :name, :slug, :description, :status, FALSE, "
                ":created_at, :updated_at)"
            ),
            {
                "project_id": imported_id,
                "name": "Imported Documents",
                "slug": "imported-documents",
                "description": "Documents imported from before project scoping.",
                "status": legacy.status,
                "created_at": legacy.created_at,
                "updated_at": legacy.updated_at,
            },
        )
    for table in ("documents", "ingestion_jobs", "research_sessions", "analysis_runs"):
        if table in inspector.get_table_names() and "project_id" in {
            column["name"] for column in inspect(connection).get_columns(table)
        }:
            connection.execute(
                text(f"UPDATE {table} SET project_id = :new_id WHERE project_id = :old_id"),
                {"new_id": imported_id, "old_id": "legacy-project"},
            )
    connection.execute(
        text("DELETE FROM projects WHERE project_id = 'legacy-project'")
    )


MIGRATIONS: dict[str, Migration] = {
    "001_foundation": _migration_001_foundation,
    "002_projects": _migration_002_projects,
    "003_retire_legacy_project": _migration_003_retire_legacy_project,
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
