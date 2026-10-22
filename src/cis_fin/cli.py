"""CLI entry point for safe, isolated connectivity checks."""

import argparse
import sys
from pathlib import Path
import uuid
from datetime import date
from typing import Sequence

from cis_fin.companies import register_company
from cis_fin.connectivity import check_database, check_qdrant, check_storage
from cis_fin.extraction import ExtractionError, extract_pdf, iter_chunks, write_jsonl
from cis_fin.ingestion import (
    DocumentRequest,
    IngestionError,
    PostgresIngestionStore,
    ingest_pdf,
    resume_extraction_job,
)
from cis_fin.migrations import MigrationError, run_migrations
from cis_fin.pipeline import PipelineProcessingError, process_document_pipeline
from cis_fin.questions import QuestionGenerationError, generate_document_questions
from cis_fin.settings import Settings
from cis_fin.storage import ObjectStoreError, S3ObjectStore
from cis_fin.vector_index import (
    PostgresVectorStore,
    VectorIndexError,
    index_document_chunks,
    reconcile_document_vectors,
)


CHECKS = {
    "check-db": check_database,
    "check-qdrant": check_qdrant,
    "check-storage": check_storage,
}


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cis-fin",
        description="Research pipeline commands. Connectivity checks are read-only.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in CHECKS:
        subparsers.add_parser(name, help="Run a read-only connectivity check")
    migration_parser = subparsers.add_parser("migrate", help="Inspect or explicitly apply PostgreSQL schema migrations")
    migration_mode = migration_parser.add_mutually_exclusive_group(required=True)
    migration_mode.add_argument("--dry-run", action="store_true", help="List pending migrations without changing the database")
    migration_mode.add_argument("--apply", action="store_true", help="Apply pending migrations transactionally")
    extraction_parser = subparsers.add_parser(
        "extract-pdf",
        help="Extract one local PDF and write page-cited chunks to a new JSONL file",
    )
    extraction_parser.add_argument("pdf", type=Path)
    extraction_parser.add_argument("--output", type=Path, required=True)
    extraction_parser.add_argument(
        "--engine",
        choices=("pymupdf", "docling", "mineru"),
        default=Settings.from_env().extractor,
        help="PDF extraction engine (default: CIS_FIN_EXTRACTOR or pymupdf)",
    )
    extraction_parser.add_argument("--chunk-tokens", type=int, default=500)
    extraction_parser.add_argument("--overlap-tokens", type=int, default=50)
    extraction_parser.add_argument("--page-start", type=int, help="First one-based PDF page to extract")
    extraction_parser.add_argument("--page-end", type=int, help="Last one-based PDF page to extract")
    ingestion_parser = subparsers.add_parser(
        "ingest-pdf",
        help="Register a PDF, store its original, and persist page-cited chunks",
    )
    ingestion_parser.add_argument("pdf", type=Path)
    ingestion_parser.add_argument("--company-id", type=uuid.UUID, required=True)
    ingestion_parser.add_argument("--title", required=True)
    ingestion_parser.add_argument("--document-type", default="annual_report")
    ingestion_parser.add_argument("--fiscal-year")
    ingestion_parser.add_argument("--source-url")
    ingestion_parser.add_argument(
        "--source-classification",
        choices=("public_source", "confidential_research"),
        default="public_source",
        help="Distinguish public source material from confidential research notes",
    )
    ingestion_parser.add_argument("--researcher-id", type=uuid.UUID)
    ingestion_parser.add_argument(
        "--engine",
        choices=("pymupdf", "docling", "mineru"),
        default=Settings.from_env().extractor,
    )
    ingestion_parser.add_argument("--batch-size", type=int, default=100)
    ingestion_parser.add_argument("--page-start", type=int, help="First one-based PDF page to ingest")
    ingestion_parser.add_argument("--page-end", type=int, help="Last one-based PDF page to ingest")
    resume_parser = subparsers.add_parser(
        "resume-job",
        help="Resume a failed extraction job using its original PDF and recorded engine",
    )
    resume_parser.add_argument("job_id", type=uuid.UUID)
    resume_parser.add_argument("--pdf", type=Path, required=True)
    resume_parser.add_argument("--batch-size", type=int, default=100)
    process_parser = subparsers.add_parser(
        "process-job",
        help="Resume saved question-generation and Qdrant indexing checkpoints",
    )
    process_parser.add_argument("--job-id", type=uuid.UUID, required=True)
    reindex_parser = subparsers.add_parser(
        "reindex-document",
        help="Embed and index one document with the currently configured model and collection",
    )
    reindex_parser.add_argument("--document-id", type=uuid.UUID, required=True)
    reindex_parser.add_argument("--dry-run", action="store_true", help="Count chunks requiring indexing without writes")
    reconcile_parser = subparsers.add_parser(
        "reconcile-vectors",
        help="Read-only comparison of PostgreSQL vector registry and Qdrant points for one document",
    )
    reconcile_parser.add_argument("--document-id", type=uuid.UUID, required=True)
    question_parser = subparsers.add_parser(
        "generate-questions",
        help="Generate local, evidence-quoted question candidates for pending chunks",
    )
    question_parser.add_argument("--document-id", type=uuid.UUID, required=True)
    question_parser.add_argument("--limit", type=int, default=10, help="Maximum chunks to process (1-100)")
    company_parser = subparsers.add_parser(
        "add-company",
        help="Add one company using owner-verified constituent and classification data",
    )
    company_parser.add_argument("--symbol", required=True)
    company_parser.add_argument("--name", required=True)
    company_parser.add_argument("--sector", required=True)
    company_parser.add_argument("--industry", required=True)
    company_parser.add_argument("--list-version", required=True)
    company_parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    args = parser.parse_args(argv)

    if args.command in CHECKS:
        result = CHECKS[args.command](Settings.from_env())
        print("{}: {}".format("OK" if result.ok else "ERROR", result.message))
        return 0 if result.ok else 1

    if args.command == "extract-pdf":
        try:
            document = extract_pdf(
                args.pdf,
                engine=args.engine,
                page_start=args.page_start,
                page_end=args.page_end,
            )
            count = write_jsonl(
                iter_chunks(
                    document.pages,
                    document.checksum,
                    target_tokens=args.chunk_tokens,
                    overlap_tokens=args.overlap_tokens,
                ),
                args.output,
            )
            if count == 0:
                args.output.unlink(missing_ok=True)
                raise ExtractionError("PDF produced no non-empty chunks and was not considered successfully extracted.")
        except (ExtractionError, ValueError) as exc:
            print("ERROR: {}".format(exc))
            return 1
        print(
            "Extracted {} pages into {} chunks using {} {} (SHA-256 {}).".format(
                len(document.pages),
                count,
                document.engine,
                document.engine_version,
                document.checksum,
            )
        )
        for warning in document.warnings:
            print("WARNING: {}".format(warning))
        return 0

    if args.command == "generate-questions":
        try:
            settings = Settings.from_env()
            migration_dir = Path(__file__).resolve().parents[2] / "migrations"
            pending = run_migrations(settings, migration_dir, apply=False)
            if pending:
                raise MigrationError(
                    "Database schema is not current. Review and apply migrations before question generation."
                )
            result = generate_document_questions(settings, args.document_id, limit=args.limit)
        except (MigrationError, QuestionGenerationError, ValueError) as exc:
            print("ERROR: {}".format(exc))
            return 1
        print(
            "Processed {} chunks; saved {} candidates needing review; {} had invalid quotations.".format(
                result.chunks_processed,
                result.candidates_needing_review,
                result.invalid_candidates,
            )
        )
        return 0

    if args.command == "process-job":
        try:
            settings = Settings.from_env()
            migration_dir = Path(__file__).resolve().parents[2] / "migrations"
            if run_migrations(settings, migration_dir, apply=False):
                raise MigrationError("Database schema is not current; review and apply pending migrations first.")
            result = process_document_pipeline(
                settings,
                args.job_id,
                progress_callback=lambda message: print(message, flush=True),
            )
        except (PipelineProcessingError, MigrationError, ValueError) as exc:
            print("ERROR: {}".format(exc))
            return 1
        print(
            "COMPLETE: processed {} chunks for questions, saved {} candidates, and indexed {} chunks.".format(
                result.chunks_processed_for_questions,
                result.question_candidates_created,
                result.newly_indexed_chunks,
            )
        )
        return 0

    if args.command in ("reindex-document", "reconcile-vectors"):
        try:
            settings = Settings.from_env()
            migration_dir = Path(__file__).resolve().parents[2] / "migrations"
            if run_migrations(settings, migration_dir, apply=False):
                raise MigrationError("Database schema is not current; review and apply pending migrations first.")
            if args.command == "reconcile-vectors":
                report = reconcile_document_vectors(settings, args.document_id)
                print(
                    "Read-only reconciliation: {} chunks lack an indexed registry row; "
                    "{} indexed rows are missing in Qdrant; {} Qdrant points are untracked; "
                    "{} older model/collection registry rows exist.".format(
                        report.chunks_without_indexed_records,
                        report.indexed_records_missing_from_qdrant,
                        report.untracked_qdrant_points,
                        report.older_index_rows,
                    )
                )
                return 0
            if args.dry_run:
                store = PostgresVectorStore(settings)
                count = store.count_pending(
                    args.document_id,
                    settings.qdrant_collection,
                    settings.ollama_embedding_model,
                    settings.qdrant_embedding_version,
                )
                print(
                    "Dry run: {} chunks need indexing into collection '{}' with model '{}'. No writes were made.".format(
                        count, settings.qdrant_collection, settings.ollama_embedding_model
                    )
                )
                return 0
            count = index_document_chunks(
                settings,
                args.document_id,
                progress_callback=lambda total: print("Indexed {} additional chunks…".format(total), flush=True),
            )
            PostgresVectorStore(settings).record_reindex(
                args.document_id,
                settings.qdrant_collection,
                settings.ollama_embedding_model,
                settings.qdrant_embedding_version,
                count,
            )
            print("Indexed {} additional chunks.".format(count))
            return 0
        except (MigrationError, VectorIndexError, ValueError) as exc:
            print("ERROR: {}".format(exc))
            return 1

    if args.command in ("ingest-pdf", "resume-job"):
        try:
            settings = Settings.from_env()
            migration_dir = Path(__file__).resolve().parents[2] / "migrations"
            pending = run_migrations(settings, migration_dir, apply=False)
            if pending:
                raise MigrationError(
                    "Database schema is not current. Review and apply migrations with "
                    "`python -m cis_fin.cli migrate --dry-run` and then `--apply` before ingestion."
                )
            store = PostgresIngestionStore(settings)
            object_store = S3ObjectStore(settings)
            if args.command == "ingest-pdf":
                request = DocumentRequest(
                    company_id=args.company_id,
                    title=args.title,
                    document_type=args.document_type,
                    path=args.pdf,
                    engine=args.engine,
                    page_start=args.page_start,
                    page_end=args.page_end,
                    researcher_id=args.researcher_id,
                    fiscal_year=args.fiscal_year,
                    source_url=args.source_url,
                    source_classification=args.source_classification,
                )
                registration = ingest_pdf(request, store, object_store, args.batch_size)
                if registration.duplicate:
                    print(
                        "DUPLICATE: document {} already exists (job {}, status {}, stage {}). "
                        "No file or database record was duplicated.".format(
                            registration.document_id,
                            registration.job_id,
                            registration.existing_status or "no job",
                            registration.existing_stage or "unknown",
                        )
                    )
                    if (
                        registration.existing_stage in ("chunked", "questions", "embedded", "indexed")
                        and registration.existing_status in ("queued", "retry_wait")
                    ):
                        result = process_document_pipeline(
                            settings,
                            registration.job_id,
                            progress_callback=lambda message: print(message, flush=True),
                        )
                        print("COMPLETE: saved {} candidates and indexed {} chunks.".format(
                            result.question_candidates_created, result.newly_indexed_chunks
                        ))
                    return 0
            else:
                request, _registered_document_id = store.load_request(args.job_id, args.pdf)
                registration = resume_extraction_job(
                    args.job_id,
                    request,
                    store,
                    object_store,
                    args.batch_size,
                )
            result = process_document_pipeline(
                settings,
                registration.job_id,
                progress_callback=lambda message: print(message, flush=True),
            )
            print("COMPLETE: document {}; job {}; processed {} chunks for questions, saved {} candidates, indexed {} chunks.".format(
                registration.document_id,
                registration.job_id,
                result.chunks_processed_for_questions,
                result.question_candidates_created,
                result.newly_indexed_chunks,
            ))
            return 0
        except (IngestionError, MigrationError, ObjectStoreError, PipelineProcessingError, OSError, ValueError) as exc:
            print("ERROR: {}".format(exc))
            return 1

    if args.command == "add-company":
        try:
            pending = run_migrations(
                Settings.from_env(),
                Path(__file__).resolve().parents[2] / "migrations",
                apply=False,
            )
            if pending:
                raise MigrationError(
                    "Database schema history must be reconciled before registering company data."
                )
            company_id, created = register_company(
                Settings.from_env(),
                args.symbol,
                args.name,
                args.sector,
                args.industry,
                args.list_version,
                args.as_of,
            )
        except (IngestionError, MigrationError, ValueError) as exc:
            print("ERROR: {}".format(exc))
            return 1
        if created:
            print("Added verified company {} with ID {}.".format(args.symbol.upper(), company_id))
        else:
            print("Company {} already exists with ID {}; no changes were made.".format(args.symbol.upper(), company_id))
        return 0

    migration_dir = Path(__file__).resolve().parents[2] / "migrations"
    try:
        pending = run_migrations(Settings.from_env(), migration_dir, apply=args.apply)
    except MigrationError as exc:
        print("ERROR: {}".format(exc))
        return 1
    if not pending:
        print("No pending migrations.")
    elif args.dry_run:
        print("Pending migrations (no database changes):")
        for migration in pending:
            print("  {}".format(migration))
    else:
        print("Applied migrations:")
        for migration in pending:
            print("  {}".format(migration))
    return 0


if __name__ == "__main__":
    sys.exit(main())
