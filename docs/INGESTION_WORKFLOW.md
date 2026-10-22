# Ingestion workflow

## Persistent PDF upload workflow

In Streamlit, select a PDF, review the locally suggested company/report details, select an inclusive page range, and submit. New company profiles are marked `pending_review`; the upload does not claim that an inferred ticker or sector is verified. Only the selected pages are extracted and chunked, while citations retain original PDF page numbers.

After extraction, question generation and vector indexing run automatically. Questions and exact quotations are saved in PostgreSQL as candidates requiring human review. Chunk embeddings are generated locally with Ollama and stored in Qdrant under stable IDs. No separate question-generation button is required.

The document's progress panel refreshes every 10 seconds and reports question checkpoints, candidate counts, invalid quotation counts, and Qdrant indexing progress. If the browser was already open before a code update, use Streamlit's **Rerun** control once to load the updated page.

The uploader needs PostgreSQL, object storage, current migrations, local Ollama with both configured models, and Qdrant credentials. Before any write, the app verifies migration history and stops if it is inconsistent or pending. It does not modify migration history to work around a checksum mismatch.

1. Validate PDF type, nonempty file, page range, and checksum.
2. In a PostgreSQL transaction, register the document using `(company_id, checksum)` uniqueness and create a processing job. Report duplicates instead of creating another document.
3. Store the original at `source-documents/{company_id}/{sha256}.pdf`. On retry, verify length and SHA-256 metadata before skipping upload; mismatches stop for investigation.
4. Stream the selected pages through the configured extractor, retaining engine/version. Empty/scanned extraction fails rather than succeeding.
5. Insert deterministic page-referenced chunks in bounded PostgreSQL batches. Earlier committed batches remain safe on retry.
6. Generate questions in batches of at most 10 chunks, sequentially, with local Ollama. Validate the schema, normalize terminal punctuation, verify quotations against source text, and keep candidates pending review. Mismatched quotations are flagged invalid.
7. Generate local embeddings in batches of at most 16. Record pending embedding-registry rows in PostgreSQL, upsert deterministic UUIDs to Qdrant, then mark those rows indexed.
8. Mark the processing job and document complete only after questions, vectors, and status writes all succeed.

Question checkpoints are keyed by chunk, model, and prompt version. Embedding registry records are keyed by chunk, collection, model, and version. A retry therefore skips completed question work and repeats any uncertain Qdrant operation using the same point IDs.

## Consistency and recovery boundary

PostgreSQL transactions cannot include S3-compatible object storage or Qdrant. No atomic commit spans these systems. The pipeline uses deterministic object keys, checksums, stable IDs, idempotent upserts, stage checkpoints, and explicit job states. If Qdrant accepts an upsert before the PostgreSQL status update fails, the retry writes the same point ID; it does not create a duplicate.

Failed downstream work is recorded with a sanitized error and capped exponential backoff (maximum five attempts). The Streamlit upload runs stages inline. After the retry time, re-uploading the same PDF resumes the saved downstream job without re-extraction, or run:

```sh
PYTHONPATH=src python -m cis_fin.cli process-job --job-id JOB_UUID
```

This project does not currently include a background scheduler for unattended retry jobs, full company-wide reprocessing, or full object-storage/PostgreSQL reconciliation. `reconcile-vectors --document-id ...` provides a read-only per-document PostgreSQL/Qdrant comparison. Do not call a `retry_wait` job complete.

## Staged corpus operation

The Streamlit uploader accepts one PDF at a time, capped at 100 MB because Streamlit buffers the uploaded file. Page extraction and database chunk writes are streamed/batched; the whole corpus is not loaded. For larger individual PDFs, a direct-to-object-store multipart upload is still needed. Benchmark throughput, peak memory, recovery, question quality, and vector storage on a representative subset before processing all companies.

## Extraction choices

PyMuPDF is the tested default. Docling is optional and unverified in this environment. MinerU is a selectable placeholder, not an integrated engine, and must not be treated as producing extracted text. Use an extractor that preserves exact page provenance for every evidence quote.
