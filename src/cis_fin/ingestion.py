"""Idempotent PostgreSQL registration and recoverable PDF extraction jobs."""

import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from cis_fin.extraction import (
    ExtractionError,
    Chunk,
    PageText,
    extractor_version,
    file_sha256,
    iter_chunks,
    pdf_page_count,
    stream_pdf_pages,
)
from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings
from cis_fin.storage import ObjectStore, ObjectStoreError


DOCUMENT_NAMESPACE = uuid.UUID("e14ae7af-c319-4279-8daf-a51dd9202d41")
JOB_NAMESPACE = uuid.UUID("dfeee659-42d1-46ef-8f50-6b7ac608a124")
MAX_BATCH_CHUNKS = 1000
RETRY_BASE_SECONDS = 15


class IngestionError(Exception):
    """An ingestion operation could not safely proceed."""


@dataclass(frozen=True)
class DocumentRequest:
    company_id: uuid.UUID
    title: str
    document_type: str
    path: Path
    engine: str = "pymupdf"
    researcher_id: Optional[uuid.UUID] = None
    fiscal_year: Optional[str] = None
    source_url: Optional[str] = None
    source_classification: str = "public_source"
    page_start: Optional[int] = None
    page_end: Optional[int] = None


@dataclass(frozen=True)
class Registration:
    document_id: uuid.UUID
    job_id: uuid.UUID
    object_key: str
    checksum: str
    duplicate: bool
    existing_status: Optional[str] = None
    existing_stage: Optional[str] = None


class IngestionStore(Protocol):
    def register(self, request: DocumentRequest, checksum: str, file_size: int) -> Registration:
        ...

    def claim(self, job_id: uuid.UUID, owner: str) -> Dict[str, object]:
        ...

    def mark_stored(self, job_id: uuid.UUID, object_key: str) -> None:
        ...

    def store_chunks(self, job_id: uuid.UUID, chunks: Sequence[Chunk]) -> None:
        ...

    def finish_extraction(
        self,
        job_id: uuid.UUID,
        page_count: int,
        extracted_page_count: int,
        page_start: int,
        page_end: int,
        engine: str,
        version: str,
    ) -> None:
        ...

    def fail(self, job_id: uuid.UUID, error_code: str, error_message: str) -> str:
        ...

    def load_request(self, job_id: uuid.UUID, path: Path) -> Tuple[DocumentRequest, uuid.UUID]:
        ...


class PostgresIngestionStore:
    def __init__(self, settings: Settings):
        if not settings.database_url:
            raise IngestionError("Set DATABASE_URL in the private .env file before ingestion.")
        try:
            import psycopg
        except ImportError as exc:
            raise IngestionError("PostgreSQL driver is missing; install requirements.txt.") from exc
        self.psycopg = psycopg
        self.max_concurrent_ingestions = settings.max_concurrent_ingestions
        try:
            self.database_url = secure_postgres_dsn(settings.database_url)
        except ValueError as exc:
            raise IngestionError(str(exc)) from exc

    def _connect(self):
        try:
            return self.psycopg.connect(self.database_url, connect_timeout=10)
        except self.psycopg.Error as exc:
            raise IngestionError(
                "Could not connect to PostgreSQL ({}). Check DATABASE_URL, TLS, firewall, and provider access.".format(
                    type(exc).__name__
                )
            ) from exc

    def register(self, request: DocumentRequest, checksum: str, file_size: int) -> Registration:
        document_id = uuid.uuid5(DOCUMENT_NAMESPACE, "{}:{}".format(request.company_id, checksum))
        object_key = "source-documents/{}/{}.pdf".format(request.company_id, checksum)
        job_id = uuid.uuid5(JOB_NAMESPACE, str(document_id))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO documents (
                            document_id, company_id, document_title, document_type, fiscal_year,
                            source_url, source_classification, object_storage_key, checksum, file_size_bytes, mime_type,
                            created_by, processing_status, extraction_status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'application/pdf', %s, 'queued', 'pending')
                        ON CONFLICT (company_id, checksum) DO NOTHING
                        RETURNING document_id
                        """,
                        (
                            document_id,
                            request.company_id,
                            request.title,
                            request.document_type,
                            request.fiscal_year,
                            request.source_url,
                            request.source_classification,
                            object_key,
                            checksum,
                            file_size,
                            request.researcher_id,
                        ),
                    )
                    inserted = cursor.fetchone()
                    if not inserted:
                        cursor.execute(
                            """
                            SELECT document_id, object_storage_key, processing_status
                            FROM documents
                            WHERE company_id = %s AND checksum = %s
                            """,
                            (request.company_id, checksum),
                        )
                        existing = cursor.fetchone()
                        if not existing:
                            raise IngestionError("Document conflict occurred, but the existing record could not be read.")
                        cursor.execute(
                            """
                            SELECT job_id, stage, status
                            FROM processing_jobs
                            WHERE document_id = %s
                            ORDER BY updated_at DESC
                            LIMIT 1
                            """,
                            (existing[0],),
                        )
                        job = cursor.fetchone()
                        return Registration(
                            document_id=existing[0],
                            job_id=job[0] if job else job_id,
                            object_key=existing[1],
                            checksum=checksum,
                            duplicate=True,
                            existing_status=job[2] if job else existing[2],
                            existing_stage=job[1] if job else None,
                        )
                    cursor.execute(
                        """
                        INSERT INTO processing_jobs (
                            job_id, document_id, researcher_id, stage, status, max_attempts, checkpoint
                        )
                        VALUES (%s, %s, %s, 'registered', 'queued', 5, %s)
                        """,
                        (
                            job_id,
                            document_id,
                            request.researcher_id,
                            json.dumps(
                                {
                                    "engine": request.engine,
                                    "object_key": object_key,
                                    "selected_page_start": request.page_start or 1,
                                    "selected_page_end": request.page_end,
                                }
                            ),
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO audit_events (
                            audit_id, actor_id, action, entity_type, entity_id, details
                        )
                        VALUES (%s, %s, 'document.ingest_registered', 'document', %s, %s::jsonb)
                        """,
                        (
                            uuid.uuid4(),
                            request.researcher_id,
                            document_id,
                            json.dumps(
                                {
                                    "job_id": str(job_id),
                                    "checksum": checksum,
                                    "source_classification": request.source_classification,
                                }
                            ),
                        ),
                    )
            return Registration(document_id, job_id, object_key, checksum, duplicate=False)
        except IngestionError:
            raise
        except self.psycopg.Error as exc:
            sqlstate = getattr(exc, "sqlstate", None)
            if sqlstate == "23503":
                raise IngestionError("Company or researcher ID was not found. Check the assigned UUID with the owner.") from exc
            raise IngestionError(
                "PostgreSQL rejected document registration ({}). Verify migrations and write permissions.".format(
                    type(exc).__name__
                )
            ) from exc

    def claim(self, job_id: uuid.UUID, owner: str) -> Dict[str, object]:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(198734, 2)")
                    cursor.execute(
                        """
                        SELECT document_id, status, stage, attempt_count, max_attempts,
                               next_attempt_at, lease_owner, lease_expires_at, checkpoint, researcher_id
                        FROM processing_jobs
                        WHERE job_id = %s
                        FOR UPDATE
                        """,
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise IngestionError("Processing job {} does not exist.".format(job_id))
                    (
                        document_id,
                        status,
                        stage,
                        attempts,
                        maximum,
                        next_at,
                        lease_owner,
                        lease_until,
                        checkpoint,
                        researcher_id,
                    ) = row
                    now = datetime.now(timezone.utc)
                    if status == "completed":
                        raise IngestionError("Job is already complete; no work was repeated.")
                    if status == "running" and lease_until and lease_until > now:
                        raise IngestionError("Job is already leased by another worker; no work was started.")
                    if status == "queued" and stage in ("chunked", "questions", "embedded", "indexed"):
                        raise IngestionError(
                            "Extraction checkpoint is {}. Downstream processing is not implemented yet; job remains queued.".format(stage)
                        )
                    if status == "retry_wait" and next_at and next_at > now:
                        raise IngestionError(
                            "Job is waiting for its retry backoff until {}; run resume-job after that time.".format(
                                next_at.isoformat()
                            )
                        )
                    cursor.execute(
                        """
                        SELECT count(*)
                        FROM processing_jobs
                        WHERE status = 'running'
                          AND lease_expires_at > now()
                          AND job_id <> %s
                        """,
                        (job_id,),
                    )
                    active_jobs = cursor.fetchone()[0]
                    if active_jobs >= self.max_concurrent_ingestions:
                        raise IngestionError(
                            "Ingestion concurrency limit ({}) is reached; no job was started. Retry later.".format(
                                self.max_concurrent_ingestions
                            )
                        )
                    if attempts >= maximum:
                        cursor.execute(
                            """
                            UPDATE processing_jobs
                            SET status = 'failed', error_code = 'retry_limit',
                                error_message = 'Maximum retry count reached.',
                                lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                            WHERE job_id = %s
                            """,
                            (job_id,),
                        )
                        raise IngestionError("Job reached its maximum of {} attempts and cannot be retried.".format(maximum))
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET status = 'running', attempt_count = attempt_count + 1,
                            started_at = COALESCE(started_at, now()),
                            next_attempt_at = NULL, lease_owner = %s,
                            lease_expires_at = now() + interval '30 minutes',
                            error_code = NULL, error_message = NULL, updated_at = now()
                        WHERE job_id = %s
                        """,
                        (owner, job_id),
                    )
                    cursor.execute(
                        "UPDATE documents SET processing_status = 'processing', extraction_status = 'processing', updated_at = now() WHERE document_id = %s",
                        (document_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO audit_events (
                            audit_id, actor_id, action, entity_type, entity_id, details
                        )
                        VALUES (%s, %s, 'processing_job.attempt_started', 'processing_job', %s, %s::jsonb)
                        """,
                        (
                            uuid.uuid4(),
                            researcher_id,
                            job_id,
                            json.dumps({"attempt": attempts + 1, "stage": stage}),
                        ),
                    )
                    return {
                        "document_id": document_id,
                        "stage": stage,
                        "checkpoint": checkpoint or {},
                        "attempt": attempts + 1,
                        "max_attempts": maximum,
                    }
        except IngestionError:
            raise
        except self.psycopg.Error as exc:
            raise IngestionError("Could not claim processing job ({}).".format(type(exc).__name__)) from exc

    def load_request(self, job_id: uuid.UUID, path: Path) -> Tuple[DocumentRequest, uuid.UUID]:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT d.company_id, d.document_title, d.document_type, d.fiscal_year,
                               d.source_url, d.created_by, d.source_classification, j.document_id, j.checkpoint
                        FROM processing_jobs AS j
                        JOIN documents AS d ON d.document_id = j.document_id
                        WHERE j.job_id = %s
                        """,
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise IngestionError("Processing job {} does not exist.".format(job_id))
                    (
                        company_id,
                        title,
                        document_type,
                        fiscal_year,
                        source_url,
                        researcher_id,
                        source_classification,
                        document_id,
                        checkpoint,
                    ) = row
                    checkpoint = checkpoint or {}
                    engine = checkpoint.get("engine", "pymupdf")
                    return (
                        DocumentRequest(
                            company_id=company_id,
                            title=title,
                            document_type=document_type,
                            path=path,
                            engine=engine,
                            researcher_id=researcher_id,
                            fiscal_year=fiscal_year,
                            source_url=source_url,
                            source_classification=source_classification,
                            page_start=checkpoint.get("selected_page_start"),
                            page_end=checkpoint.get("selected_page_end"),
                        ),
                        document_id,
                    )
        except IngestionError:
            raise
        except self.psycopg.Error as exc:
            raise IngestionError("Could not load job metadata ({}).".format(type(exc).__name__)) from exc

    def mark_stored(self, job_id: uuid.UUID, object_key: str) -> None:
        self._checkpoint(job_id, "stored", {"object_key": object_key, "object_verified": True})

    def store_chunks(self, job_id: uuid.UUID, chunks: Sequence[object]) -> None:
        if len(chunks) > MAX_BATCH_CHUNKS:
            raise IngestionError("Chunk batch exceeds the bounded maximum of {}.".format(MAX_BATCH_CHUNKS))
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT document_id, researcher_id FROM processing_jobs WHERE job_id = %s AND status = 'running' FOR UPDATE",
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise IngestionError("Job lease was lost before chunk persistence.")
                    document_id, researcher_id = row
                    cursor.execute(
                        "SELECT company_id FROM documents WHERE document_id = %s",
                        (document_id,),
                    )
                    company_row = cursor.fetchone()
                    if not company_row:
                        raise IngestionError("Document disappeared before chunk persistence.")
                    company_id = company_row[0]
                    for chunk in chunks:
                        cursor.execute(
                            """
                            INSERT INTO chunks (
                                chunk_id, document_id, company_id, page_start, page_end,
                                chunk_index, chunk_text, chunk_hash, extraction_version, researcher_id
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (document_id, chunk_index) DO NOTHING
                            RETURNING chunk_id, chunk_hash, page_start, page_end
                            """,
                            (
                                uuid.UUID(chunk.chunk_id),
                                document_id,
                                company_id,
                                chunk.page_start,
                                chunk.page_end,
                                chunk.chunk_index,
                                chunk.text,
                                chunk.chunk_hash,
                                "local-preview-v1",
                                researcher_id,
                            ),
                        )
                        existing = cursor.fetchone()
                        if not existing:
                            cursor.execute(
                                """
                                SELECT chunk_id, chunk_hash, page_start, page_end
                                FROM chunks WHERE chunk_id = %s AND document_id = %s
                                """,
                                (uuid.UUID(chunk.chunk_id), document_id),
                            )
                            existing = cursor.fetchone()
                        if not existing or existing != (
                            uuid.UUID(chunk.chunk_id),
                            chunk.chunk_hash,
                            chunk.page_start,
                            chunk.page_end,
                        ):
                            raise IngestionError(
                                "A saved chunk differs from the deterministic retry result at index {}; "
                                "preserve existing research and investigate parser/version drift.".format(chunk.chunk_index)
                            )
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET stage = 'extracted',
                            checkpoint = checkpoint || %s::jsonb,
                            lease_expires_at = now() + interval '30 minutes',
                            updated_at = now()
                        WHERE job_id = %s AND status = 'running'
                        """,
                        (
                            json.dumps({"last_chunk_index": chunks[-1].chunk_index, "chunks_persisted_through": chunks[-1].chunk_index}),
                            job_id,
                        ),
                    )
        except IngestionError:
            raise
        except self.psycopg.Error as exc:
            raise IngestionError(
                "PostgreSQL chunk batch failed ({}); prior committed batches are retained for idempotent retry.".format(
                    type(exc).__name__
                )
            ) from exc

    def finish_extraction(
        self,
        job_id: uuid.UUID,
        page_count: int,
        extracted_page_count: int,
        page_start: int,
        page_end: int,
        engine: str,
        version: str,
    ) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT document_id FROM processing_jobs
                        WHERE job_id = %s AND status = 'running'
                        FOR UPDATE
                        """,
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise IngestionError("Job lease was lost before extraction checkpoint.")
                    document_id = row[0]
                    cursor.execute(
                        """
                        UPDATE documents
                        SET page_count = %s, extraction_status = 'succeeded',
                            extraction_engine = %s, extraction_engine_version = %s,
                            extracted_page_count = %s, selected_page_start = %s,
                            selected_page_end = %s,
                            processing_status = 'queued', updated_at = now()
                        WHERE document_id = %s
                        """,
                        (
                            page_count,
                            engine,
                            version,
                            extracted_page_count,
                            page_start,
                            page_end,
                            document_id,
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET stage = 'chunked', status = 'queued', lease_owner = NULL,
                            lease_expires_at = NULL, next_attempt_at = NULL,
                            checkpoint = checkpoint || %s::jsonb, updated_at = now()
                        WHERE job_id = %s
                        """,
                        (
                            json.dumps(
                                {
                                    "page_count": page_count,
                                    "extracted_page_count": extracted_page_count,
                                    "selected_page_start": page_start,
                                    "selected_page_end": page_end,
                                    "extraction_engine": engine,
                                    "extraction_engine_version": version,
                                    "awaiting_stages": ["questions", "embedded", "indexed"],
                                }
                            ),
                            job_id,
                        ),
                    )
        except IngestionError:
            raise
        except self.psycopg.Error as exc:
            raise IngestionError(
                "Could not persist the extraction checkpoint ({}); durable chunks are retained for retry.".format(
                    type(exc).__name__
                )
            ) from exc

    def fail(self, job_id: uuid.UUID, error_code: str, error_message: str) -> str:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT document_id, attempt_count, max_attempts
                        FROM processing_jobs WHERE job_id = %s FOR UPDATE
                        """,
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        return "unknown"
                    document_id, attempts, maximum = row
                    status = "retry_wait" if attempts < maximum else "failed"
                    delay_seconds = min(RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)), 3600)
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET status = %s, error_code = %s, error_message = %s,
                            next_attempt_at = CASE WHEN %s = 'retry_wait'
                                THEN now() + (%s * interval '1 second') ELSE NULL END,
                            lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                        WHERE job_id = %s
                        """,
                        (status, error_code, error_message[:500], status, delay_seconds, job_id),
                    )
                    cursor.execute(
                        "UPDATE documents SET processing_status = 'failed', extraction_status = CASE WHEN extraction_status = 'succeeded' THEN extraction_status ELSE 'failed' END, updated_at = now() WHERE document_id = %s",
                        (document_id,),
                    )
                    return status
        except self.psycopg.Error:
            return "status_update_failed"

    def _checkpoint(self, job_id: uuid.UUID, stage: str, values: Dict[str, object]) -> None:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE processing_jobs
                        SET stage = %s, checkpoint = checkpoint || %s::jsonb,
                            lease_expires_at = now() + interval '30 minutes', updated_at = now()
                        WHERE job_id = %s AND status = 'running'
                        """,
                        (stage, json.dumps(values), job_id),
                    )
                    if cursor.rowcount != 1:
                        raise IngestionError("Job lease was lost while saving checkpoint.")
        except IngestionError:
            raise
        except self.psycopg.Error as exc:
            raise IngestionError("Could not persist job checkpoint ({}).".format(type(exc).__name__)) from exc


def _safe_failure_message(exc: Exception) -> Tuple[str, str]:
    if isinstance(exc, ObjectStoreError):
        return "object_storage_error", "Object storage stage failed; check endpoint, bucket, and service-account permissions."
    if isinstance(exc, ExtractionError):
        return "extraction_error", "PDF extraction failed; inspect file validity, encryption, selectable text, and parser configuration."
    if isinstance(exc, IngestionError):
        return "ingestion_error", "Ingestion stage failed; inspect sanitized CLI output and service connectivity."
    return "unexpected_{}".format(type(exc).__name__.lower()), (
        "Unexpected processing failure ({}). Sensitive exception details were not recorded.".format(type(exc).__name__)
    )


def run_extraction_job(
    request: DocumentRequest,
    registration: Registration,
    store: IngestionStore,
    object_store: ObjectStore,
    batch_size: int = 100,
) -> Registration:
    if not 1 <= batch_size <= MAX_BATCH_CHUNKS:
        raise IngestionError("batch_size must be between 1 and {}.".format(MAX_BATCH_CHUNKS))
    if registration.duplicate:
        return registration

    owner = "{}:{}".format(socket.gethostname(), os.getpid())
    job_id = registration.job_id
    job = store.claim(job_id, owner)
    try:
        if job["document_id"] != registration.document_id:
            raise IngestionError("Job ID and input PDF do not refer to the same registered document.")
        checkpoint = job.get("checkpoint") or {}
        if checkpoint.get("engine", request.engine) != request.engine:
            raise IngestionError(
                "Extraction engine differs from the registered job. Resume with the original engine; "
                "engine-change reprocessing requires a separate reviewed operation."
            )
        object_store.ensure_pdf(request.path, registration.object_key, registration.checksum)
        store.mark_stored(job_id, registration.object_key)

        total_page_count = pdf_page_count(request.path)
        page_start = request.page_start or 1
        page_end = request.page_end or total_page_count
        if page_start < 1 or page_end < page_start or page_end > total_page_count:
            raise ExtractionError(
                "Selected page range must be between 1 and {}.".format(total_page_count)
            )
        pages = stream_pdf_pages(
            request.path,
            request.engine,
            page_start=page_start,
            page_end=page_end,
        )
        nonempty_chars = 0
        extracted_pages = 0

        def tracked_pages() -> Iterable[PageText]:
            nonlocal nonempty_chars, extracted_pages
            for page in pages:
                nonempty_chars += len(page.text.strip())
                extracted_pages += 1
                yield page

        chunks = iter_chunks(
            tracked_pages(),
            registration.checksum,
            target_tokens=500,
            overlap_tokens=50,
            document_scope=str(registration.document_id),
        )
        batch = []
        total_chunks = 0
        for chunk in chunks:
            batch.append(chunk)
            if len(batch) >= batch_size:
                store.store_chunks(job_id, batch)
                total_chunks += len(batch)
                batch = []
        if batch:
            store.store_chunks(job_id, batch)
            total_chunks += len(batch)
        if nonempty_chars == 0 or total_chunks == 0:
            raise ExtractionError(
                "No selectable PDF text was extracted; scanned PDFs need an OCR-capable engine."
            )
        # The current built-in parser exposes page numbers. Avoid retaining document pages in memory.
        store.finish_extraction(
            job_id,
            total_page_count,
            extracted_pages,
            page_start,
            page_end,
            request.engine,
            extractor_version(request.engine),
        )
        return registration
    except Exception as exc:
        code, message = _safe_failure_message(exc)
        state = store.fail(job_id, code, message)
        if state == "status_update_failed":
            raise IngestionError(
                "Processing failed and the PostgreSQL status update also failed. Retry with the same document/job; "
                "stable IDs and upserts prevent duplicate chunks."
            ) from exc
        if state == "retry_wait":
            raise IngestionError(
                "{} Job {} is retryable after exponential backoff; committed chunks and stored objects are retained.".format(
                    message, job_id
                )
            ) from exc
        raise IngestionError("{} Job {} reached a terminal failure or could not be updated.".format(message, job_id)) from exc


def ingest_pdf(
    request: DocumentRequest,
    store: IngestionStore,
    object_store: ObjectStore,
    batch_size: int = 100,
) -> Registration:
    if not request.title.strip() or not request.document_type.strip():
        raise IngestionError("Document title and document type are required.")
    if request.source_classification not in ("public_source", "confidential_research"):
        raise IngestionError("source_classification must be public_source or confidential_research.")
    if request.path.suffix.lower() != ".pdf":
        raise IngestionError("Input must be a PDF file.")
    page_count = pdf_page_count(request.path)
    page_start = request.page_start or 1
    page_end = request.page_end or page_count
    if page_start < 1 or page_end < page_start or page_end > page_count:
        raise IngestionError("Selected page range must be between 1 and {}.".format(page_count))
    checksum = file_sha256(request.path)
    file_size = request.path.stat().st_size
    if file_size == 0:
        raise IngestionError("Input PDF is empty; no document record was created.")
    registration = store.register(request, checksum, file_size)
    if registration.duplicate:
        return registration
    return run_extraction_job(request, registration, store, object_store, batch_size)


def resume_extraction_job(
    job_id: uuid.UUID,
    request: DocumentRequest,
    store: IngestionStore,
    object_store: ObjectStore,
    batch_size: int = 100,
) -> Registration:
    checksum = file_sha256(request.path)
    expected_document_id = uuid.uuid5(
        DOCUMENT_NAMESPACE, "{}:{}".format(request.company_id, checksum)
    )
    saved_request, registered_document_id = store.load_request(job_id, request.path)
    if registered_document_id != expected_document_id:
        raise IngestionError(
            "The supplied PDF checksum/company does not match the document registered to this job; no work was started."
        )
    if saved_request.engine != request.engine:
        raise IngestionError("Resume with the extraction engine recorded in the job checkpoint.")
    if (saved_request.page_start, saved_request.page_end) != (request.page_start, request.page_end):
        raise IngestionError("Resume using the same page range recorded for this job.")
    registration = Registration(
        document_id=registered_document_id,
        job_id=job_id,
        object_key="source-documents/{}/{}.pdf".format(saved_request.company_id, checksum),
        checksum=checksum,
        duplicate=False,
    )
    return run_extraction_job(request, registration, store, object_store, batch_size)
