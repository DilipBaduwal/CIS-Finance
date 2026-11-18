ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS chunk_number integer;

UPDATE chunks
SET chunk_number = chunk_index + 1
WHERE chunk_number IS NULL;

ALTER TABLE chunks
    ALTER COLUMN chunk_number SET NOT NULL,
    ADD CONSTRAINT chunks_chunk_number_positive CHECK (chunk_number > 0);

CREATE UNIQUE INDEX IF NOT EXISTS chunks_document_number_idx
    ON chunks (document_id, chunk_number);
