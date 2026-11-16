"""Resumable automatic Ollama scoring for generated evidence questions."""

import json
import uuid
from pathlib import Path

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.qualitative_scoring import score_qualitative_evidence


def score_pending_evidence(settings, document_id=None, progress_callback=None):
    """Score every generated question/evidence pair and persist it immediately.

    Existing saved traces are skipped, so this operation is safe to resume.
    """
    if not settings.database_url:
        raise RuntimeError("Set DATABASE_URL before automatic scoring.")
    import psycopg

    query = """
        SELECT d.document_id, d.company_id, d.checksum, c.symbol,
               q.question_id, q.question_text, q.dimension,
               ch.chunk_id, ch.chunk_text, ch.page_start, ch.page_end
        FROM chunk_question_evidence ev
        JOIN questions q ON q.question_id = ev.question_id
        JOIN chunks ch ON ch.chunk_id = ev.chunk_id
        JOIN documents d ON d.document_id = ch.document_id
        JOIN companies c ON c.company_id = d.company_id
        WHERE d.processing_status = 'completed'
    """
    params = []
    if document_id:
        query += " AND d.document_id = %s"
        params.append(uuid.UUID(str(document_id)))
    query += " ORDER BY c.symbol, d.document_id, ch.chunk_index, q.generated_at, q.question_id"

    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        total = len(rows)
        completed = skipped = failed = 0
        trace_dir = Path("artifacts/automatic_score_traces")
        trace_dir.mkdir(parents=True, exist_ok=True)
        for index, row in enumerate(rows, 1):
            document_uuid, company_id, checksum, symbol, question_id, question, dimension, chunk_id, text, page_start, page_end = row
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT trace_id FROM score_traces
                       WHERE document_id=%s AND question_id=%s
                         AND trace_metadata->>'chunk_id'=%s
                       ORDER BY created_at DESC LIMIT 1""",
                    (document_uuid, question_id, str(chunk_id)),
                )
                existing = cursor.fetchone()
            if existing:
                skipped += 1
                continue
            try:
                result = score_qualitative_evidence(settings, question, text, page_start, dimension)
                trace_id = uuid.uuid4()
                metadata = {
                    "chunk_id": str(chunk_id), "question_id": str(question_id),
                    "page_start": page_start, "page_end": page_end,
                    "ollama_score": result.get("score"),
                    "ollama_rationale": result.get("rationale", ""),
                    "ollama_confidence": result.get("confidence"),
                    "evidence_quote": result.get("evidence_quote", ""),
                    "source": "automatic_post_question_generation",
                }
                trace_path = trace_dir / "{}_{}_{}.md".format(symbol, str(chunk_id)[:8], str(question_id)[:8])
                trace_path.write_text(
                    "# Automatic Ollama score\n\n"
                    "- Company: {}\n- Question: {}\n- Dimension: {}\n- Pages: {}–{}\n"
                    "- Chunk ID: {}\n- Score: {}\n- Confidence: {}\n\n{}\n\n## Evidence\n\n{}\n".format(
                        symbol, question, dimension, page_start, page_end, chunk_id,
                        result.get("score"), result.get("confidence"), result.get("rationale", ""), text
                    ), encoding="utf-8",
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO score_traces
                           (trace_id, company_id, document_id, question_id, trace_path,
                            source_checksum, model, prompt_version, score, trace_metadata)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (trace_id, company_id, document_uuid, question_id, str(trace_path),
                         checksum, result.get("model", settings.ollama_model), "automatic-score-v1",
                         int(result["score"]), json.dumps(metadata)),
                    )
                connection.commit()
                completed += 1
            except Exception:
                connection.rollback()
                failed += 1
            if progress_callback:
                progress_callback(index, total, completed, skipped, failed)
    return {"total": total, "scored": completed, "skipped": skipped, "failed": failed}
