# Troubleshooting

## Configuration is missing

Set the reported variable in the private `.env` file or shell and restart the app. Connectivity probes do not attempt network access when required settings are absent.

## PostgreSQL cannot connect

- Verify `DATABASE_URL`, TLS, network/IP allowlists, database name, and role with the owner.
- `check-db` runs only `SELECT 1`; it does not validate migrations or write access.
- Migration mismatch is a hard stop. Do not edit the migration ledger or bypass its checksum check. Have the database owner restore the exact historical file or review a forward-only reconciliation migration.

## Qdrant denies access or times out

- Confirm `QDRANT_URL` is the cluster endpoint, not the dashboard URL; cloud endpoints require HTTPS.
- Confirm `QDRANT_API_KEY` has collection/vector write permission.
- `check-qdrant` only lists collection metadata and does not test write permissions.
- Ingestion creates the configured collection only if missing and requires 768 dimensions with the default embedding model. A mismatch is reported without deleting or recreating the existing collection; configure a new collection name after owner review.

## Object storage denies access

Verify bucket, endpoint, region, key pair, and bucket-scoped read/write permissions with the owner. `check-storage` uses `HeadBucket` only; it does not upload or delete a test object. Remote endpoints require HTTPS.

## Automatic question generation fails

- Confirm Ollama is running at `http://127.0.0.1:11434` and `OLLAMA_MODEL` is installed (`ollama pull qwen2.5:3b`).
- Question punctuation without a final `?` is normalized automatically. Empty questions or empty quotations are rejected; quotations not found in the source are stored as invalid and never approved.
- Model calls run locally and sequentially. Ensure enough memory and wait for a long report to complete.
- The job reports a sanitized stage failure and retry time. After backoff, re-upload the same PDF to resume, or run `PYTHONPATH=src python -m cis_fin.cli process-job --job-id JOB_UUID`.

## Embedding or Qdrant indexing fails

- Confirm `ollama pull nomic-embed-text:latest` completed and local Ollama is running. The configured model must return the expected 768 dimensions.
- Confirm Qdrant URL, API key, TLS, and write permissions. A first ingestion may create the configured collection; ordinary connectivity tests never do.
- If Qdrant accepted a vector but PostgreSQL status update failed, retry is safe: the same stable point IDs are upserted.
- After five failures, stop and ask the owner to inspect the job and service permissions. Do not edit job status manually to bypass the retry limit.

## Duplicate upload or extraction retry

The same company/checksum is already registered. No duplicate document is inserted. A queued/retryable downstream job at `chunked` resumes questions and embedding without extracting again. For incomplete extraction, use `resume-job JOB_UUID --pdf ORIGINAL.pdf` with the exact original source. Respect `next_attempt_at` backoff.

## PDF has no selectable text

Encrypted PDFs need an authorized decrypted copy. The verified PyMuPDF path does not provide OCR; scanned pages fail rather than being reported as successful. Docling is optional and unverified; MinerU is not integrated.

## Tests on an unconfigured machine

Unit tests use fakes and local-only configuration; they do not require cloud credentials or live services:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```
