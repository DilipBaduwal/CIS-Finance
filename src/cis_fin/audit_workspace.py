"""Persistence and metrics for the human-vs-Ollama evidence audit workspace."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cis_fin.postgres import secure_postgres_dsn


class AuditWorkspaceError(Exception):
    pass


def save_evaluation(settings, document_id, company, question_id, question, dimension, record, ollama, human_score, human_rationale, evaluator, status):
    if not settings.database_url:
        raise AuditWorkspaceError("Set DATABASE_URL before saving an evaluation.")
    try:
        import psycopg
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT company_id, checksum FROM documents WHERE document_id = %s", (uuid.UUID(str(document_id)),))
                row = cursor.fetchone()
                if not row:
                    raise AuditWorkspaceError("The source document was not found.")
                company_id, checksum = row
                trace_id = uuid.uuid4()
                now = datetime.now(timezone.utc)
                metadata = {
                    "ollama_score": ollama.get("score"),
                    "ollama_rationale": ollama.get("rationale", ""),
                    "ollama_confidence": ollama.get("confidence"),
                    "human_score": human_score,
                    "human_rationale": human_rationale,
                    "review_status": status,
                    "evaluator": evaluator,
                    "question_id": str(question_id),
                    "page_start": record.get("page_start"),
                    "page_end": record.get("page_end"),
                    "chunk_id": record.get("chunk_id"),
                }
                trace_dir = Path("artifacts/audit_traces")
                trace_dir.mkdir(parents=True, exist_ok=True)
                trace_path = trace_dir / "{}_{}_{}.md".format(now.strftime("%Y%m%dT%H%M%SZ"), company, str(question_id)[:8])
                difference = None if human_score is None or ollama.get("score") is None else human_score - int(ollama["score"])
                trace_path.write_text(
                    "# Evidence evaluation\n\n"
                    "- Timestamp: {}\n- Company: {}\n- Document ID: {}\n- Source checksum: {}\n"
                    "- Question: {}\n- TOE dimension: {}\n- Pages: {}–{}\n- Chunk ID: {}\n"
                    "- Ollama score: {}\n- Ollama confidence: {}\n- Ollama rationale: {}\n"
                    "- Human score: {}\n- Human rationale: {}\n- Difference: {}\n- Review status: {}\n- Evaluator: {}\n\n"
                    "## Source evidence\n\n{}\n".format(
                        now.isoformat(), company, document_id, checksum, question, dimension,
                        record.get("page_start"), record.get("page_end"), record.get("chunk_id"),
                        ollama.get("score"), ollama.get("confidence"), ollama.get("rationale", ""),
                        human_score, human_rationale, difference, status, evaluator, record.get("chunk_text", "")
                    ), encoding="utf-8"
                )
                cursor.execute(
                    "INSERT INTO audit_rows (audit_row_id, text_evidence, expected_score, given_score, company, question, dimension, source_document, page_number, chunk_id, auditor, comments) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (trace_id, record.get("chunk_text", ""), int(human_score), int(ollama.get("score", 0)), company, question, dimension, str(document_id), record.get("page_start"), uuid.UUID(str(record["chunk_id"])), evaluator, json.dumps(metadata)),
                )
                cursor.execute(
                    "INSERT INTO score_traces (trace_id, company_id, document_id, question_id, trace_path, source_checksum, model, prompt_version, score, trace_metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (trace_id, company_id, uuid.UUID(str(document_id)), uuid.UUID(str(question_id)), str(trace_path), checksum, ollama.get("model", "ollama"), "audit-v1", int(ollama.get("score", 0)), json.dumps(metadata)),
                )
        return trace_path
    except AuditWorkspaceError:
        raise
    except Exception as exc:
        raise AuditWorkspaceError("Could not save evaluation: {}".format(exc)) from exc


def audit_metrics(settings):
    if not settings.database_url:
        raise AuditWorkspaceError("Set DATABASE_URL before loading audit metrics.")
    try:
        import psycopg
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*),
                           AVG(CASE WHEN expected_score = given_score THEN 1.0 ELSE 0.0 END),
                           AVG(CASE WHEN ABS(expected_score - given_score) <= 1 THEN 1.0 ELSE 0.0 END),
                           AVG(ABS(expected_score - given_score))
                    FROM audit_rows
                    """
                )
                total, exact, within_one, mae = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COALESCE(dimension, 'unknown'), COUNT(*), AVG(ABS(expected_score - given_score))
                    FROM audit_rows
                    GROUP BY COALESCE(dimension, 'unknown')
                    ORDER BY COALESCE(dimension, 'unknown')
                    """
                )
                dimension_rows = cursor.fetchall()
        return {
            "total": total,
            "exact": exact,
            "within_one": within_one,
            "mae": mae,
            "by_dimension": {
                dimension: {"count": count, "mae": dimension_mae}
                for dimension, count, dimension_mae in dimension_rows
            },
        }
    except Exception as exc:
        raise AuditWorkspaceError("Could not load audit metrics: {}".format(exc)) from exc
