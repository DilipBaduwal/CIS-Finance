"""Read-only company lookup for the local ingestion interface."""

from typing import List, Tuple
import uuid

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


class CompanyLookupError(Exception):
    """The shared company list could not be retrieved."""


def list_recent_documents(settings: Settings, limit: int = 50) -> List[Tuple[str, str, str, str, str]]:
    if not settings.database_url:
        raise CompanyLookupError("Set DATABASE_URL in the local .env file.")
    if not 1 <= limit <= 100:
        raise CompanyLookupError("Document-list limit must be between 1 and 100.")
    try:
        import psycopg
    except ImportError as exc:
        raise CompanyLookupError("PostgreSQL driver is missing; install requirements.txt.") from exc
    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT d.document_id::text, d.document_title, c.symbol,
                           COALESCE(d.fiscal_year, ''), d.processing_status
                    FROM documents d
                    JOIN companies c ON c.company_id = d.company_id
                    ORDER BY d.updated_at DESC, d.created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return list(cursor.fetchall())
    except psycopg.Error as exc:
        raise CompanyLookupError(
            "Could not load recent documents ({}). Verify database read permissions.".format(type(exc).__name__)
        ) from exc


def list_active_companies(settings: Settings) -> List[Tuple[str, str, str, str, str]]:
    if not settings.database_url:
        raise CompanyLookupError("Set DATABASE_URL in the local .env file.")
    try:
        import psycopg
    except ImportError as exc:
        raise CompanyLookupError("PostgreSQL driver is missing; install requirements.txt.") from exc

    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT company_id::text, symbol, company_name, sector, industry
                    FROM companies
                    WHERE active_status
                    ORDER BY symbol
                    """
                )
                return list(cursor.fetchall())
    except psycopg.Error as exc:
        raise CompanyLookupError(
            "Could not load companies ({}). Verify the database migration and read permissions.".format(
                type(exc).__name__
            )
        ) from exc


def export_document_chunks(settings: Settings, document_id: uuid.UUID) -> List[dict]:
    if not settings.database_url:
        raise CompanyLookupError("Set DATABASE_URL in the local .env file.")
    try:
        import psycopg
    except ImportError as exc:
        raise CompanyLookupError("PostgreSQL driver is missing; install requirements.txt.") from exc
    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        c.symbol, c.company_name, c.sector, c.industry,
                        d.document_title, d.document_type, d.fiscal_year,
                        d.source_url, d.source_classification, d.checksum,
                        d.page_count, d.selected_page_start, d.selected_page_end,
                        ch.chunk_id::text, ch.chunk_index, ch.page_start, ch.page_end,
                        ch.chunk_hash, ch.chunk_text
                    FROM chunks AS ch
                    JOIN documents AS d ON d.document_id = ch.document_id
                    JOIN companies AS c ON c.company_id = ch.company_id
                    WHERE d.document_id = %s
                    ORDER BY ch.chunk_index, ch.page_start
                    """,
                    (document_id,),
                )
                rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT
                        ch.chunk_id::text, q.question_id::text, q.question_text,
                        q.framework, q.dimension, q.generation_model,
                        q.validation_status, q.review_status, ev.evidence_quote,
                        ev.ai_specific_evidence, ev.semantic_support_status
                    FROM chunks AS ch
                    JOIN chunk_question_evidence AS ev ON ev.chunk_id = ch.chunk_id
                    JOIN questions AS q ON q.question_id = ev.question_id
                    WHERE ch.document_id = %s
                    ORDER BY ch.chunk_index, q.generated_at, q.question_id
                    """,
                    (document_id,),
                )
                question_rows = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT checkpoint.chunk_id::text, checkpoint.generation_model, checkpoint.status
                    FROM question_generation_checkpoints AS checkpoint
                    JOIN chunks AS ch ON ch.chunk_id = checkpoint.chunk_id
                    WHERE ch.document_id = %s
                    ORDER BY checkpoint.attempted_at
                    """,
                    (document_id,),
                )
                checkpoint_rows = cursor.fetchall()
    except psycopg.Error as exc:
        raise CompanyLookupError(
            "Could not export stored chunks ({}). Verify database read permissions.".format(type(exc).__name__)
        ) from exc

    fields = (
        "symbol",
        "company_name",
        "sector",
        "industry",
        "document_title",
        "document_type",
        "fiscal_year",
        "source_url",
        "source_classification",
        "document_checksum",
        "document_page_count",
        "selected_page_start",
        "selected_page_end",
        "chunk_id",
        "chunk_index",
        "page_start",
        "page_end",
        "chunk_hash",
        "chunk_text",
    )
    candidates_by_chunk = {}
    generation_status_by_chunk = {}
    for question_row in question_rows:
        (
            chunk_id,
            question_id,
            question_text,
            framework,
            dimension,
            generation_model,
            validation_status,
            review_status,
            evidence_quote,
            ai_specific_evidence,
            semantic_support_status,
        ) = question_row
        candidates_by_chunk.setdefault(chunk_id, []).append(
            {
                "question_id": question_id,
                "question": question_text,
                "framework": framework,
                "dimension": dimension,
                "generation_model": generation_model,
                "validation_status": validation_status,
                "review_status": review_status,
                "evidence_quote": evidence_quote,
                "ai_specific": ai_specific_evidence,
                "semantic_support_status": semantic_support_status,
            }
        )
    for chunk_id, generation_model, status in checkpoint_rows:
        generation_status_by_chunk.setdefault(chunk_id, []).append(
            {"model": generation_model, "status": status}
        )

    return [
        {
            **dict(zip(fields, row)),
            "question_candidates": candidates_by_chunk.get(row[13], []),
            "question_generation_status": "generated_pending_review"
            if candidates_by_chunk.get(row[13])
            else (
                "abstained"
                if generation_status_by_chunk.get(row[13])
                else "not_generated"
            ),
            "question_generation_attempts": generation_status_by_chunk.get(row[13], []),
        }
        for row in rows
    ]
