# Grounded question generation

## Local setup and automatic processing

Question generation and embeddings use local Ollama; the implementation rejects remote Ollama endpoints so report text is not sent to an external model provider.

```sh
ollama pull qwen2.5:3b
ollama pull nomic-embed-text:latest
```

Set in the private `.env`:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text:latest
QDRANT_COLLECTION=cis_fin_chunks_nomic_v1
QDRANT_EMBEDDING_VERSION=nomic-embed-text-v1
```

When a researcher submits a PDF in Streamlit, the app automatically processes all selected-page chunks in sequential batches of at most 10 for questions and at most 16 for embeddings. It writes questions/evidence to PostgreSQL and vector points to Qdrant. There is no manual question-generation step in the upload workflow. A downstream failure preserves completed work and can be retried with:

```sh
PYTHONPATH=src python -m cis_fin.cli process-job --job-id JOB_UUID
```

The job honors the recorded retry backoff and five-attempt cap. If Qdrant accepted points but PostgreSQL status writes failed, retries use the same deterministic point IDs. A Qdrant collection is created only if it does not exist; the app checks its vector dimension and will not delete or recreate a conflicting collection.

## Validation and approval boundary

- PDF text is untrusted evidence, not instructions; the prompt explicitly directs the model to ignore embedded requests or role changes.
- The model is asked for one or two useful questions when evidence supports them and may abstain when no useful evidence exists.
- Output must match the expected JSON shape. Missing terminal question punctuation is normalized, rather than incorrectly rejecting an otherwise valid candidate.
- The evidence quotation is checked against whitespace-normalized source text and stored using the exact source span if it matches. An unmatched quote is stored with `validation_status=invalid` and `semantic_support_status=unsupported`; it is not silently treated as valid.
- A matching quotation is only a lexical check, not proof of semantic support. It remains `needs_review`, and candidates are never auto-approved.
- Candidate rows retain model and prompt version, source chunk linkage, and exact quote. Database constraints block approval unless validation is `valid`.

Human review is still required to reject unsupported claims, inferential leaps, irrelevant questions, and incorrect AI-specific labels. Authentication, reviewer role enforcement, and the question approval UI are not implemented in this local development app.

## Embedding and Qdrant consistency

The default `nomic-embed-text:latest` model returns 768-dimensional vectors. The client checks that output dimension before indexing. Qdrant receives chunk/document/company IDs and original page-range metadata; PostgreSQL remains the source of truth and the embedding registry links each point back to a chunk. The registry is staged before Qdrant upsert and marked indexed after the upsert succeeds. A keyword payload index on `document_id` supports per-document vector reconciliation.

Collection, model, and embedding-version settings define the registry scope. For a changed embedding model or vector dimension, select a new `QDRANT_COLLECTION` and `QDRANT_EMBEDDING_VERSION`; never delete or recreate an existing collection as part of an ordinary connectivity check or retry. Reindexing already-completed documents is an operator workflow and is not yet exposed in the UI.

## Confidentiality and limitations

Local Ollama prevents this code from sending source text to a remote model server; it does not secure the researcher’s machine or configured cloud services. Do not upload confidential notes unless the owner has approved local processing, PostgreSQL, object storage, and Qdrant data handling. Use least-privilege service credentials and do not include secrets in logs.

This is a candidate generator, not a validated financial reasoning model. Benchmark faithfulness, relevance, unsupported AI claims, abstention, quote matching, model latency, embedding throughput, and memory on a representative subset before processing the full corpus.
