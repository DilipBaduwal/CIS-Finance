CREATE TABLE IF NOT EXISTS chunk_ai_scores (
    chunk_id uuid PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','failed')),
    generated_question text,
    toe_dimension text,
    score integer CHECK (score IS NULL OR score BETWEEN 0 AND 5),
    confidence numeric CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    rationale text,
    evidence_quote text,
    model text,
    attempts integer NOT NULL DEFAULT 0,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunk_ai_scores_status_idx ON chunk_ai_scores(status, updated_at);
