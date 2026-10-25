# Operations

## Commands

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m cis_fin.cli check-db
PYTHONPATH=src python -m cis_fin.cli check-qdrant
PYTHONPATH=src python -m cis_fin.cli check-storage
PYTHONPATH=src python -m cis_fin.cli extract-pdf input.pdf --output artifacts/input.chunks.jsonl
PYTHONPATH=src python -m cis_fin.cli ingest-pdf input.pdf --company-id COMPANY_UUID --title "Annual report"
PYTHONPATH=src python -m cis_fin.cli process-job --job-id JOB_UUID
PYTHONPATH=src python -m cis_fin.cli reindex-document --document-id DOCUMENT_UUID --dry-run
PYTHONPATH=src python -m cis_fin.cli reconcile-vectors --document-id DOCUMENT_UUID
```

Connectivity tests are strictly read-only. The Streamlit upload becomes a write operation only after explicit form submission; it stores the PDF, writes chunks/questions/embedding registry rows, and upserts Qdrant vectors. The local UI has no authentication and must not be exposed to the network.

`reconcile-vectors` is a read-only, per-document PostgreSQL/Qdrant comparison. `reindex-document --dry-run` counts missing current-model vectors; omitting `--dry-run` writes vectors and records an audit event. Reindexing does not delete old collections or points.

## Preflight before full-corpus processing

- [ ] Owner provisioned central PostgreSQL, Qdrant, object storage, TLS, and least-privilege credentials.
- [ ] Migration history reviewed and current; managed researcher authentication/authorization and reviewer enforcement implemented.
- [ ] Official dated company list verified; inferred company profiles remain pending review.
- [ ] Secrets are private and absent from chat, source, screenshots, logs, and Git history.
- [ ] Representative PDFs include text, scanned pages, tables, and unusually large files.
- [ ] Extraction engines benchmarked for accuracy, page citations, failures, runtime, and memory.
- [ ] Chunk size/overlap checked against financial tables and numerical context.
- [ ] Retry, partial failure, stable-ID upsert, and PostgreSQL/Qdrant disagreement paths tested.
- [ ] Backups and restore drill completed; cross-service reconciliation plan reviewed.
- [ ] Question/evidence quality, abstention, prompt-injection handling, and reviewer workflow evaluated.
- [ ] Representative benchmark covers all requested throughput, storage, and time metrics before scaling.

## Benchmark metrics

Report sample size, machine/runtime/model versions, and:

- number of chunks and average chunk size;
- embedding throughput and estimated vector storage;
- question-generation throughput and invalid-quote/abstention/failure rates;
- database insertion throughput;
- end-to-end processing time, retry count, and failure rate.

These metrics have not yet been measured for a representative sample. Do not imply full-corpus readiness without those results.

## Queue and concurrency

The app processes submitted documents inline. Chunk, question, and embedding batches are bounded; model calls are sequential. Set `CIS_FIN_MAX_CONCURRENT_INGESTIONS=5` for the initial five-person deployment (the value accepts 1–16), enforced through PostgreSQL job claims. Five people may be signed in and submit work, but local Ollama, PostgreSQL, Qdrant, CPU, GPU, and memory remain shared bottlenecks.

Five local-development accounts are configured with `CIS_FIN_USER_1_USERNAME` / `CIS_FIN_USER_1_PASSWORD` through `CIS_FIN_USER_5_USERNAME` / `CIS_FIN_USER_5_PASSWORD`. Keep these values only in the private `.env` file. This is a lightweight app login, not a replacement for production identity management.

The **Stop this job** action marks the job `cancelled` and clears its lease immediately. The active worker notices the request between question/indexing batches and stops without changing completed checkpoints. Uploading the identical PDF with the identical selected page range resumes from those checkpoints. A stop during one individual model or extraction call is cooperative, so that current call finishes first.

The 30-minute lease is a crash-recovery safety bound, not a required wait time. It prevents a dead process from blocking a job forever. Normal stop and failure paths now clear the lease immediately; the 30-minute expiry is only the fallback when the process disappears without updating PostgreSQL. There is still no background scheduler for `retry_wait` jobs; after backoff, use the same upload or `process-job --job-id`.

## Recovery boundary

PostgreSQL transactions do not include object storage or Qdrant. Stable object keys and vector IDs make retries idempotent, but cross-service atomicity is not provided. If Qdrant upsert succeeds and the registry update fails, retry overwrites the same point ID. A document is complete only after question and indexing stages and their database status updates complete.
