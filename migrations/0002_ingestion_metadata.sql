ALTER TABLE documents
    ADD COLUMN extraction_engine text,
    ADD COLUMN extraction_engine_version text;

CREATE UNIQUE INDEX processing_jobs_one_active_per_document
    ON processing_jobs (document_id)
    WHERE status IN ('queued', 'running', 'retry_wait');
