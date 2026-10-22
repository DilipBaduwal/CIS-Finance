"""Explicit, transactional PostgreSQL SQL-migration runner."""

import hashlib
from pathlib import Path
from typing import List

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


LEGACY_CHECKSUMS = {
    # This exact 0001 revision is recorded in the configured database and its core schema was verified.
    "0001": frozenset({"89ae1aa9d925a923ac02dec72ff147011964cbf71a7532d7f7d6097dc33f660f"}),
}

CORE_SCHEMA_COLUMNS = {
    "companies": {
        "company_id",
        "symbol",
        "company_name",
        "sector",
        "industry",
        "constituent_list_version",
        "constituent_as_of",
        "active_status",
        "created_at",
        "updated_at",
    },
    "researchers": {
        "researcher_id", "user_identifier", "display_name", "role", "active_status", "created_at",
    },
    "company_assignments": {
        "assignment_id",
        "researcher_id",
        "company_id",
        "assignment_status",
        "assigned_at",
        "completed_at",
        "notes",
    },
    "documents": {
        "document_id",
        "company_id",
        "document_title",
        "document_type",
        "fiscal_year",
        "publication_date",
        "source_url",
        "source_classification",
        "object_storage_key",
        "checksum",
        "file_size_bytes",
        "mime_type",
        "page_count",
        "extraction_status",
        "processing_status",
        "created_by",
        "created_at",
        "updated_at",
        "extraction_engine",
        "extraction_engine_version",
    },
    "chunks": {
        "chunk_id",
        "document_id",
        "company_id",
        "page_start",
        "page_end",
        "chunk_index",
        "chunk_text",
        "chunk_hash",
        "extraction_version",
        "researcher_id",
        "review_status",
        "created_at",
    },
    "questions": {
        "question_id",
        "question_text",
        "normalized_question_hash",
        "framework",
        "dimension",
        "taxonomy_version",
        "generation_model",
        "prompt_version",
        "generated_at",
        "validation_status",
        "review_status",
        "reviewer_id",
        "reviewed_at",
        "reviewer_notes",
    },
    "chunk_question_evidence": {
        "evidence_id",
        "chunk_id",
        "question_id",
        "evidence_quote",
        "evidence_type",
        "ai_specific_evidence",
        "semantic_support_status",
        "reviewer_notes",
        "created_at",
    },
    "embedding_registry": {
        "embedding_id",
        "chunk_id",
        "vector_id",
        "collection_name",
        "embedding_model",
        "embedding_version",
        "vector_dimensions",
        "indexing_status",
        "created_at",
        "updated_at",
    },
    "processing_jobs": {
        "job_id",
        "document_id",
        "researcher_id",
        "stage",
        "status",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "lease_owner",
        "lease_expires_at",
        "checkpoint",
        "error_code",
        "error_message",
        "started_at",
        "completed_at",
        "updated_at",
    },
    "audit_events": {
        "audit_id",
        "actor_id",
        "action",
        "entity_type",
        "entity_id",
        "occurred_at",
        "details",
    },
}


class MigrationError(Exception):
    """A migration could not be inspected or applied safely."""


def _checksum_mismatch_message(
    migration_name: str,
    recorded_checksum: str,
    local_checksum: str,
) -> str:
    return (
        "Migration checksum mismatch for {}. The database records {}, but the local file calculates {}. "
        "No migration was applied and no schema changes were made. Stop uploads for now. "
        "Ask the database owner to restore the exact applied migration file, or have a PostgreSQL "
        "administrator compare the live schema with the migration history and prepare a reviewed "
        "reconciliation migration. Do not edit schema_migrations or bypass this check."
    ).format(migration_name, recorded_checksum, local_checksum)


def _verify_core_schema(cursor) -> None:
    cursor.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    )
    actual = {}
    for table_name, column_name in cursor.fetchall():
        actual.setdefault(table_name, set()).add(column_name)

    mismatches = []
    for table, expected_columns in CORE_SCHEMA_COLUMNS.items():
        missing = sorted(expected_columns - actual.get(table, set()))
        if missing:
            mismatches.append("{} (missing columns: {})".format(table, ", ".join(missing)))
    if mismatches:
        raise MigrationError(
            "The recorded historical 0001 checksum is recognized, but the live core schema does not match "
            "the expected baseline: {}. No migration was applied; ask the database owner to review the schema.".format(
                "; ".join(mismatches)
            )
        )


def _migration_files(migration_dir: Path) -> List[Path]:
    files = sorted(migration_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not files:
        raise MigrationError("No numbered SQL migrations found in {}.".format(migration_dir))
    versions = [path.name.split("_", 1)[0] for path in files]
    if len(versions) != len(set(versions)):
        raise MigrationError("Migration filenames contain duplicate version numbers.")
    return files


def run_migrations(settings: Settings, migration_dir: Path, apply: bool) -> List[str]:
    missing = settings.missing_for("database")
    if missing:
        raise MigrationError("Set {} in .env before using migrations.".format(", ".join(missing)))
    try:
        import psycopg
    except ImportError as exc:
        raise MigrationError("PostgreSQL driver missing; install requirements.txt before running migrations.") from exc

    files = _migration_files(migration_dir)
    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url, timeout_seconds=5)) as connection:
            with connection.cursor() as cursor:
                if apply:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS public.schema_migrations (
                            version text PRIMARY KEY,
                            checksum text NOT NULL,
                            applied_at timestamptz NOT NULL DEFAULT now()
                        )
                        """
                    )
                    cursor.execute("SELECT pg_advisory_xact_lock(198734, 1)")
                    cursor.execute("SELECT version, checksum FROM public.schema_migrations")
                    applied = dict(cursor.fetchall())
                else:
                    cursor.execute("SELECT to_regclass('public.schema_migrations')")
                    exists = cursor.fetchone()[0] is not None
                    if exists:
                        cursor.execute("SELECT version, checksum FROM public.schema_migrations")
                        applied = dict(cursor.fetchall())
                    else:
                        applied = {}

                pending = []
                for path in files:
                    version = path.name.split("_", 1)[0]
                    contents = path.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(contents.encode("utf-8")).hexdigest()
                    if version in applied:
                        if applied[version] != checksum:
                            if applied[version] in LEGACY_CHECKSUMS.get(version, frozenset()):
                                _verify_core_schema(cursor)
                                continue
                            raise MigrationError(
                                _checksum_mismatch_message(
                                    path.name,
                                    applied[version],
                                    checksum,
                                )
                            )
                        continue
                    pending.append((path, version, checksum, contents))

                if apply:
                    for path, version, checksum, contents in pending:
                        cursor.execute(contents)
                        cursor.execute(
                            "INSERT INTO public.schema_migrations (version, checksum) VALUES (%s, %s)",
                            (version, checksum),
                        )
                return [path.name for path, _, _, _ in pending]
    except MigrationError:
        raise
    except psycopg.Error as exc:
        raise MigrationError(
            "PostgreSQL migration operation failed ({}). Verify TLS, credentials, and database permissions; no partial migration was committed.".format(
                type(exc).__name__
            )
        ) from exc
