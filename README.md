# CIS-FIN Research Pipeline

This project establishes a foundation for a shared, evidence-grounded research dataset. It requires owner-provisioned PostgreSQL, Qdrant, and object storage; this code does not create cloud accounts, resources, or credentials.

## Current phase

Implemented:

- Streamlit setup page with configuration status and manually triggered connectivity checks.
- Separate CLI commands for PostgreSQL, Qdrant, and S3-compatible storage.
- Read-only probes: `SELECT 1`, Qdrant `GET /collections`, and S3 `HeadBucket`.
- Initial PostgreSQL schema for companies, five-person research assignments, documents, chunks, grounded questions/evidence, embeddings, recoverable jobs, and audit events.
- Transactional numbered SQL migration runner with checksum protection and explicit `--dry-run` / `--apply`.
- Local environment configuration template and credential-safe Git ignore rules.
- Automated unit tests that run without cloud credentials or live services.

Implemented: idempotent PostgreSQL document registration, verified S3-compatible original storage, streamed page extraction with bounded PostgreSQL chunk batches, retryable jobs, duplicate detection, and selected-page extraction. After extraction, Streamlit automatically generates grounded question candidates in sequential batches using local Ollama, saves questions and exact source quotations in PostgreSQL, embeds chunks locally, and idempotently upserts them to Qdrant. A job is marked complete only after these stages and the corresponding PostgreSQL status writes succeed. Candidates remain unapproved for researcher review.

Still not implemented: production authentication/authorization and a question approval UI, scheduled retries, company-wide reprocessing, and full cross-service reconciliation. PyMuPDF ingestion is tested locally with fake repositories/storage; the live question/vector processing path must still be validated against the configured managed services. Docling remains optional/unverified and MinerU is not integrated.

## Requirements

- Python 3.9 or later (Python 3.11+ is recommended for deployment).
- The project owner provisions managed PostgreSQL, Qdrant, and S3-compatible storage and distributes least-privilege credentials securely.

## Local setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` using credentials supplied by the project owner. Never put secrets in source files, screenshots, tickets, or Git commits. `python-dotenv` loads `.env` for the application without overriding already-set shell environment variables.

Run credential-independent unit tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

Launch Streamlit locally (the page is unauthenticated; do not expose it to your LAN):

```sh
PYTHONPATH=src streamlit run app.py --server.address 127.0.0.1
```

Keep the development UI bound to loopback; it has no authentication and is not a shared researcher portal:

```sh
PYTHONPATH=src streamlit run app.py --server.address 127.0.0.1
```

## Read-only connectivity commands

Run one check at a time:

```sh
PYTHONPATH=src python -m cis_fin.cli check-db
PYTHONPATH=src python -m cis_fin.cli check-qdrant
PYTHONPATH=src python -m cis_fin.cli check-storage
```

The database test runs only `SELECT 1`; the Qdrant test only lists collection metadata; the storage test uses `HeadBucket`. None creates, uploads, updates, or deletes production data. Missing configuration reports the exact environment-variable names needed and exits nonzero.

Schema migration is a separate, state-changing operation. Inspect first, then apply intentionally:

```sh
PYTHONPATH=src python -m cis_fin.cli migrate --dry-run
PYTHONPATH=src python -m cis_fin.cli migrate --apply
```

The owner must supply a migration-capable database role. Do not use a connectivity command as a substitute for migration or vice versa.

For a fully verified company profile, the owner can still use the explicit command below with official constituent and classification data. The Streamlit upload flow does not require this first: it suggests metadata from the PDF and creates a new profile as `pending_review`. No Nifty 50 list is invented or seeded:

```sh
PYTHONPATH=src python -m cis_fin.cli add-company \
  --symbol EXAMPLE --name "Verified company name" \
  --sector "Approved sector" --industry "Approved industry" \
  --list-version "official-list-version" --as-of 2026-10-21
```

Replace all example values with owner-verified current constituent and classification data. The command refuses to overwrite an existing symbol.

## Local PDF extraction preview

Extract one PDF into a new JSONL output file without connecting to cloud services:

```sh
PYTHONPATH=src python -m cis_fin.cli extract-pdf ./annual-report.pdf \
  --output ./artifacts/annual-report.chunks.jsonl \
  --engine pymupdf
```

The Streamlit page accepts a PDF upload, then automatically asks the local Ollama model to suggest company name, ticker, report year/type, sector, and industry from the first four pages. Review and correct these fields before submission; sector and industry are suggestions, not verified classifications. A company record is created automatically with status `pending_review` if the ticker is new—there is no separate company-registration step. If the PDF does not clearly identify a ticker, enter it in the form. The page also lets you set inclusive first and last page numbers; only that range is extracted and original PDF page numbers are preserved in citations. The server-side upload limit is 100 MB per file; Streamlit holds one uploaded file in its upload buffer, then the pipeline streams page extraction and writes bounded chunk batches. After extraction, download the evidence JSON; it includes company, sector, industry, source metadata, page references, and chunk text.

Automatic metadata detection requires Ollama running locally with `OLLAMA_MODEL` installed. If that is unavailable or the report is scanned, the UI explains the issue and you can enter metadata manually. A new company is marked pending review rather than claimed as verified constituent data. Set `CIS_FIN_EXTRACTOR=docling` to select the optional adapter. Docling must be installed separately and requires a compatible Python runtime; it is not included in the base dependencies. MinerU is currently not integrated. Only PyMuPDF has been tested. The upload form is unauthenticated, local-development only, and must not be exposed to a network.

The local extraction preview and database-ingestion CLI also accept inclusive page bounds:

```sh
PYTHONPATH=src python -m cis_fin.cli extract-pdf ./annual-report.pdf \
  --output ./artifacts/annual-report.chunks.jsonl --page-start 20 --page-end 35
PYTHONPATH=src python -m cis_fin.cli ingest-pdf ./annual-report.pdf \
  --company-id COMPANY_UUID --title "Annual report" --document-type annual_report \
  --page-start 20 --page-end 35
```

Chunk size is approximate (four characters per token), defaulting to 500 tokens with 50 token overlap. Inspect page references and extraction quality before using any output for research.

## Persist one PDF (explicit write operation)

This command writes document metadata/chunks to PostgreSQL and the original PDF to object storage. It requires an existing company UUID; the Streamlit upload flow can create a pending-review company profile automatically after you review the extracted suggestions. Then run:

```sh
PYTHONPATH=src python -m cis_fin.cli ingest-pdf ./annual-report.pdf \
  --company-id COMPANY_UUID --title "Annual report" \
  --document-type annual_report --fiscal-year 2025 \
  --source-classification public_source --engine pymupdf \
  --page-start 1 --page-end 30
```

The page bounds are optional; omitting them processes the full PDF. The document checksum is unique per company. Existing duplicates are reported without rewriting their records. The original object uses a deterministic key and checksum metadata; a retry verifies that stored object rather than uploading a second copy. Chunk writes are bounded (default 100 per transaction) and stable IDs allow partial batches to be safely retried.

## Automatic question generation and Qdrant indexing

Install Ollama locally and pull both models:

```sh
ollama pull qwen2.5:3b
ollama pull nomic-embed-text:latest
```

Set `OLLAMA_BASE_URL=http://127.0.0.1:11434`, `OLLAMA_MODEL=qwen2.5:3b`, and `OLLAMA_EMBEDDING_MODEL=nomic-embed-text:latest` in `.env`. After you submit a PDF, the application automatically processes all selected-page chunks in bounded sequential batches, saves question/evidence candidates to PostgreSQL, creates the versioned Qdrant collection if needed, and indexes vectors. Source text stays local; remote Ollama URLs are rejected. Review candidates before approving them. See [docs/GROUNDED_QUESTION_GENERATION.md](docs/GROUNDED_QUESTION_GENERATION.md).

On failure, the job records a sanitized error and exponential retry time, capped at five attempts. Resume downstream question/vector processing after the reported backoff:

```sh
PYTHONPATH=src python -m cis_fin.cli process-job --job-id JOB_UUID
PYTHONPATH=src python -m cis_fin.cli reindex-document --document-id DOCUMENT_UUID --dry-run
PYTHONPATH=src python -m cis_fin.cli reconcile-vectors --document-id DOCUMENT_UUID
```

The command resumes from the saved `chunked` checkpoint; it does not re-extract the PDF. Embedding IDs are stable, so if Qdrant accepted an upsert but the PostgreSQL status update failed, retries overwrite the same points rather than creating duplicates. Simultaneous jobs are bounded by `CIS_FIN_MAX_CONCURRENT_INGESTIONS` (default `2`, range `1`–`16`).

PostgreSQL transactions do not include S3 or Qdrant. The pipeline uses checksums, deterministic IDs, stage checkpoints, and idempotent upserts; it does not claim a single transaction across services.

`reindex-document --dry-run` counts chunks that need vectors for the currently configured collection/model without writes. Omit `--dry-run` to index them. `reconcile-vectors` is read-only and compares PostgreSQL registry rows to Qdrant points for one document; use it to spot missing, untracked, or older-model records.

## Database credentials

There is no central account or password included in this project. The project owner must create the managed PostgreSQL project and database roles, then share credentials through an approved secrets manager. Do not commit credentials or grant researchers owner/admin privileges. See [docs/SETUP_OWNER.md](docs/SETUP_OWNER.md).

## Project documents

- [Architecture](docs/ARCHITECTURE.md)
- [Database schema](docs/DATABASE_SCHEMA.md)
- [Owner setup](docs/SETUP_OWNER.md)
- [Researcher setup](docs/SETUP_RESEARCHER.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Data dictionary](docs/DATA_DICTIONARY.md)
- [Ingestion workflow](docs/INGESTION_WORKFLOW.md)
- [Question generation](docs/GROUNDED_QUESTION_GENERATION.md)
- [Operations](docs/OPERATIONS.md)
- [Backup and recovery](docs/BACKUP_AND_RECOVERY.md)
