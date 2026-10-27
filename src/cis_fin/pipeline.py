"""Retry-safe question generation and vector indexing after PDF extraction."""

import os
import socket
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from cis_fin.ingestion import IngestionError, PostgresIngestionStore
from cis_fin.questions import QuestionGenerationError, generate_document_questions
from cis_fin.settings import Settings
from cis_fin.vector_index import VectorIndexError, index_document_chunks


class PipelineProcessingError(Exception):
    """Automatic post-extraction processing failed and was checkpointed for recovery."""


@dataclass(frozen=True)
class PipelineResult:
    chunks_processed_for_questions: int
    question_candidates_created: int
    candidates_needing_review: int
    invalid_candidates: int
    newly_indexed_chunks: int


@dataclass(frozen=True)
class DocumentProgress:
    job_id: uuid.UUID
    job_status: str
    stage: str
    attempt_count: int
    max_attempts: int
    chunk_count: int
    question_checkpoints: int
    candidate_count: int
    invalid_candidate_count: int
    indexed_chunk_count: int
    document_status: str
    lease_expires_at: Optional[object]


class PipelineJobStore:
    def __init__(self, settings: Settings):
        self.store = PostgresIngestionStore(settings)

    def claim(self, job_id: uuid.UUID) -> uuid.UUID:
        owner = "{}:{}:pipeline".format(socket.gethostname(), os.getpid())
        try:
            with self.store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(198734, 2)")
                    cursor.execute(
                        """
                        SELECT document_id, status, stage, attempt_count, max_attempts,
                               next_attempt_at, lease_expires_at
                        FROM processing_jobs WHERE job_id = %s FOR UPDATE
                        """,
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise PipelineProcessingError("Processing job {} does not exist.".format(job_id))
                    document_id, status, stage, attempts, maximum, next_at, lease_until = row
                    if stage not in ("chunked", "questions", "embedded", "indexed"):
                        raise PipelineProcessingError(
                            "Job stage is '{}'; post-extraction processing requires a saved chunk checkpoint.".format(stage)
                        )
                    if status == "completed":
                        raise PipelineProcessingError("This processing job is already complete.")
                    if status == "failed" or attempts >= maximum:
                        raise PipelineProcessingError(
                            "This job reached its maximum of {} attempts. Ask the database owner to review it before reprocessing.".format(
                                maximum
                            )
                        )
                    if status == "running" and lease_until:
                        from datetime import datetime, timezone

                        if lease_until > datetime.now(timezone.utc):
                            raise PipelineProcessingError("Another worker currently holds this job lease.")
                    if status == "retry_wait" and next_at:
                        from datetime import datetime, timezone

                        if next_at > datetime.now(timezone.utc):
                            raise PipelineProcessingError(
                                "This job is waiting for its automatic retry backoff until {}.".format(next_at.isoformat())
                            )
                    cursor.execute(
                        """
                        SELECT count(*) FROM processing_jobs
                        WHERE status = 'running' AND lease_expires_at > now() AND job_id <> %s
                        """,
                        (job_id,),
                    )
                    active_jobs = cursor.fetchone()[0]
                    if active_jobs >= self.store.max_concurrent_ingestions:
                        raise PipelineProcessingError(
                            "The configured processing concurrency limit ({}) is reached; retry after another job finishes.".format(
                                self.store.max_concurrent_ingestions
                            )
                        )
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET status = 'running', attempt_count = attempt_count + 1,
                            started_at = COALESCE(started_at, now()), next_attempt_at = NULL,
                            lease_owner = %s, lease_expires_at = now() + interval '30 minutes',
                            error_code = NULL, error_message = NULL, updated_at = now()
                        WHERE job_id = %s
                        """,
                        (owner, job_id),
                    )
                    cursor.execute(
                        "UPDATE documents SET processing_status = 'processing', updated_at = now() WHERE document_id = %s",
                        (document_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO audit_events (audit_id, action, entity_type, entity_id, details)
                        VALUES (%s, 'document.pipeline_started', 'document', %s, %s::jsonb)
                        """,
                        (
                            uuid.uuid4(),
                            document_id,
                            '{"job_id":"%s","attempt":%s,"stage":"%s"}'
                            % (job_id, attempts + 1, stage),
                        ),
                    )
                    return document_id
        except PipelineProcessingError:
            raise
        except self.store.psycopg.Error as exc:
            raise PipelineProcessingError(
                "Could not claim the downstream job ({}). Verify PostgreSQL access and retry.".format(type(exc).__name__)
            ) from exc

    def cancellation_requested(self, job_id: uuid.UUID) -> bool:
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM processing_jobs WHERE job_id = %s", (job_id,))
                row = cursor.fetchone()
                if not row:
                    raise PipelineProcessingError("Processing job does not exist.")
                return row[0] == "cancelled"

    def cancel(self, job_id: uuid.UUID) -> None:
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE processing_jobs
                    SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL,
                        next_attempt_at = NULL, updated_at = now()
                    WHERE job_id = %s AND status IN ('queued', 'running', 'retry_wait')
                    """,
                    (job_id,),
                )
                if cursor.rowcount != 1:
                    raise PipelineProcessingError("Job is already complete or cannot be stopped.")
                cursor.execute(
                    """
                    UPDATE documents SET processing_status = 'queued', updated_at = now()
                    WHERE document_id = (SELECT document_id FROM processing_jobs WHERE job_id = %s)
                    """,
                    (job_id,),
                )


    def checkpoint(self, job_id: uuid.UUID, stage: str) -> None:
        try:
            with self.store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET stage = %s, lease_expires_at = now() + interval '30 minutes', updated_at = now()
                        WHERE job_id = %s AND status = 'running'
                        """,
                        (stage, job_id),
                    )
                    if cursor.rowcount != 1:
                        raise PipelineProcessingError("Job lease was lost while saving the {} checkpoint.".format(stage))
        except PipelineProcessingError:
            raise
        except self.store.psycopg.Error as exc:
            raise PipelineProcessingError(
                "Could not persist the {} stage checkpoint ({}).".format(stage, type(exc).__name__)
            ) from exc

    def complete(self, job_id: uuid.UUID, document_id: uuid.UUID) -> None:
        try:
            with self.store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET stage = 'complete', status = 'completed', completed_at = now(),
                            lease_owner = NULL, lease_expires_at = NULL, next_attempt_at = NULL,
                            error_code = NULL, error_message = NULL, updated_at = now()
                        WHERE job_id = %s AND document_id = %s AND status = 'running'
                        """,
                        (job_id, document_id),
                    )
                    if cursor.rowcount != 1:
                        raise PipelineProcessingError("Job status could not be marked complete; no success was reported.")
                    cursor.execute(
                        "UPDATE documents SET processing_status = 'completed', updated_at = now() WHERE document_id = %s",
                        (document_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO audit_events (audit_id, action, entity_type, entity_id, details)
                        VALUES (%s, 'document.pipeline_completed', 'document', %s, %s::jsonb)
                        """,
                        (uuid.uuid4(), document_id, '{"job_id":"%s"}' % job_id),
                    )
        except PipelineProcessingError:
            raise
        except self.store.psycopg.Error as exc:
            raise PipelineProcessingError(
                "All downstream stages finished, but the completion status did not save ({}). "
                "Retry the job; embeddings and candidates are idempotent.".format(type(exc).__name__)
            ) from exc

    def fail(self, job_id: uuid.UUID, document_id: uuid.UUID, exc: Exception) -> str:
        error_code, message = _safe_pipeline_failure(exc)
        try:
            with self.store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT attempt_count, max_attempts FROM processing_jobs WHERE job_id = %s FOR UPDATE",
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise PipelineProcessingError("Processing job disappeared while recording failure.")
                    attempts, maximum = row
                    status = "retry_wait" if attempts < maximum else "failed"
                    delay_seconds = min(15 * (2 ** max(0, attempts - 1)), 3600)
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET status = %s, error_code = %s, error_message = %s,
                            next_attempt_at = CASE WHEN %s = 'retry_wait'
                                THEN now() + (%s * interval '1 second') ELSE NULL END,
                            lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                        WHERE job_id = %s
                        RETURNING next_attempt_at
                        """,
                        (status, error_code, message, status, delay_seconds, job_id),
                    )
                    next_attempt_at = cursor.fetchone()[0]
                    cursor.execute(
                        "UPDATE documents SET processing_status = 'failed', updated_at = now() WHERE document_id = %s",
                        (document_id,),
                    )
                    return status, next_attempt_at
        except PipelineProcessingError:
            raise
        except self.store.psycopg.Error as update_exc:
            raise PipelineProcessingError(
                "Processing failed and its retry status could not be saved ({}). "
                "Do not re-upload repeatedly; ask the database owner to inspect the job.".format(type(update_exc).__name__)
            ) from exc


def get_document_progress(settings: Settings, document_id: uuid.UUID) -> DocumentProgress:
    store = PostgresIngestionStore(settings)
    try:
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT j.job_id, j.status, j.stage, j.attempt_count, j.max_attempts, d.processing_status,
                        j.lease_expires_at,
                        (SELECT count(*) FROM chunks ch WHERE ch.document_id = d.document_id),
                        (SELECT count(*) FROM question_generation_checkpoints cp
                         JOIN chunks ch ON ch.chunk_id = cp.chunk_id
                         WHERE ch.document_id = d.document_id),
                        (SELECT count(DISTINCT q.question_id) FROM questions q
                         JOIN chunk_question_evidence ev ON ev.question_id = q.question_id
                         JOIN chunks ch ON ch.chunk_id = ev.chunk_id
                         WHERE ch.document_id = d.document_id),
                        (SELECT count(DISTINCT q.question_id) FROM questions q
                         JOIN chunk_question_evidence ev ON ev.question_id = q.question_id
                         JOIN chunks ch ON ch.chunk_id = ev.chunk_id
                         WHERE ch.document_id = d.document_id AND q.validation_status = 'invalid'),
                        (SELECT count(*) FROM embedding_registry er
                         JOIN chunks ch ON ch.chunk_id = er.chunk_id
                         WHERE ch.document_id = d.document_id
                           AND er.collection_name = %s AND er.embedding_model = %s
                           AND er.embedding_version = %s AND er.indexing_status = 'indexed')
                    FROM processing_jobs j
                    JOIN documents d ON d.document_id = j.document_id
                    WHERE d.document_id = %s
                    ORDER BY j.updated_at DESC
                    LIMIT 1
                    """,
                    (
                        settings.qdrant_collection,
                        settings.ollama_embedding_model,
                        settings.qdrant_embedding_version,
                        document_id,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise PipelineProcessingError(
                        "No processing job was found for document {}.".format(document_id)
                    )
                return DocumentProgress(
                    job_id=row[0],
                    job_status=row[1],
                    stage=row[2],
                    attempt_count=row[3],
                    max_attempts=row[4],
                    document_status=row[5],
                    lease_expires_at=row[6],
                    chunk_count=row[7],
                    question_checkpoints=row[8],
                    candidate_count=row[9],
                    invalid_candidate_count=row[10],
                    indexed_chunk_count=row[11],
                )
    except PipelineProcessingError:
        raise
    except store.psycopg.Error as exc:
        raise PipelineProcessingError(
            "Could not refresh processing progress ({}). Check PostgreSQL connectivity.".format(type(exc).__name__)
        ) from exc


def _safe_pipeline_failure(exc: Exception):
    if isinstance(exc, QuestionGenerationError):
        return "question_generation_error", str(exc)[:500]
    if isinstance(exc, VectorIndexError):
        return "vector_index_error", str(exc)[:500]
    return (
        "pipeline_error_{}".format(type(exc).__name__.lower()),
        "Post-extraction processing failed ({}). Retry the saved job; source PDF and completed checkpoints are retained.".format(
            type(exc).__name__
        ),
    )


def process_document_pipeline(
    settings: Settings,
    job_id: uuid.UUID,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> PipelineResult:
    """Run all bounded post-extraction stages; each stage is idempotent and checkpointed."""
    job_store = PipelineJobStore(settings)
    document_id = job_store.claim(job_id)
    try:
        initial_progress = get_document_progress(settings, document_id)
    except IngestionError:
        # Test/dry-run job stores may intentionally not provide a database.
        # A real PipelineJobStore rejects missing DATABASE_URL before reaching
        # this branch, so configured production runs retain normal progress.
        if settings.database_url:
            raise
        initial_progress = DocumentProgress(
            job_id=job_id,
            job_status="running",
            stage="extracted",
            attempt_count=0,
            max_attempts=5,
            document_status="extracted",
            lease_expires_at=None,
            chunk_count=0,
            question_checkpoints=0,
            candidate_count=0,
            invalid_candidate_count=0,
            indexed_chunk_count=0,
        )
    checkpoint_baseline = initial_progress.question_checkpoints
    cancellation_requested = getattr(job_store, "cancellation_requested", lambda _job_id: False)
    total_chunks = initial_progress.chunk_count
    questions_processed = 0
    candidates_created = 0
    candidates_needing_review = 0
    invalid_candidates = 0
    newly_indexed = 0
    try:
        if progress_callback:
            progress_callback("Generating grounded questions in batches of 10…")
            progress_callback("QUESTION_PROGRESS|{}|{}".format(checkpoint_baseline, total_chunks))
        while True:
            if cancellation_requested(job_id):
                raise PipelineProcessingError("Job stopped by the researcher. Upload the same PDF and page range to resume it.")
            result = generate_document_questions(
                settings,
                document_id,
                limit=10,
                progress_callback=(
                    lambda completed, _batch_size: progress_callback(
                        "QUESTION_PROGRESS|{}|{}".format(
                            min(total_chunks, checkpoint_baseline + questions_processed + completed),
                            total_chunks,
                        )
                    )
                    if progress_callback
                    else None
                ),
            )
            questions_processed += result.chunks_processed
            candidates_created += result.candidates_created
            candidates_needing_review += result.candidates_needing_review
            invalid_candidates += result.invalid_candidates
            if result.chunks_processed == 0:
                break
            job_store.checkpoint(job_id, "questions")
            if progress_callback:
                progress_callback(
                    "Generated candidates for {} chunks ({} need review; {} have unmatched quotations)…".format(
                        questions_processed, candidates_needing_review, invalid_candidates
                    )
                )
        job_store.checkpoint(job_id, "questions")
        if progress_callback:
            progress_callback("Embedding chunks locally and indexing them in Qdrant…")
        newly_indexed = index_document_chunks(
            settings,
            document_id,
            progress_callback=(
                lambda count: progress_callback("Indexed {} additional chunks in Qdrant…".format(count))
                if progress_callback
                else None
            ),
        )
        if cancellation_requested(job_id):
            raise PipelineProcessingError("Job stopped by the researcher. Upload the same PDF and page range to resume it.")
        job_store.checkpoint(job_id, "indexed")
        from cis_fin.benchmark_auto_update import auto_update_after_index
        benchmark_refresh = auto_update_after_index(settings, progress_callback)
        job_store.complete(job_id, document_id)
        if progress_callback:
            progress_callback("Questions saved and all chunks indexed.")
        return PipelineResult(
            chunks_processed_for_questions=questions_processed,
            question_candidates_created=candidates_created,
            candidates_needing_review=candidates_needing_review,
            invalid_candidates=invalid_candidates,
            newly_indexed_chunks=newly_indexed,
        )
    except Exception as exc:
        if cancellation_requested(job_id):
            raise PipelineProcessingError(
                "Job stopped by the researcher. Upload the same PDF and page range to resume it."
            ) from exc
        state, next_attempt_at = job_store.fail(job_id, document_id, exc)
        if state == "retry_wait":
            raise PipelineProcessingError(
                "{} Retry this saved job after {}. Completed extraction, questions, and vectors are retained.".format(
                    _safe_pipeline_failure(exc)[1],
                    next_attempt_at.isoformat() if next_attempt_at else "the configured backoff",
                )
            ) from exc
        raise PipelineProcessingError(
            "{} Job reached its retry limit and needs owner review.".format(_safe_pipeline_failure(exc)[1])
        ) from exc


def cancel_document_job(settings: Settings, job_id: uuid.UUID) -> None:
    """Request cancellation and release the lease immediately."""
    PipelineJobStore(settings).cancel(job_id)
