CREATE TABLE IF NOT EXISTS chunk_ai_relevance (
    chunk_id uuid PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    ai_relevance_label text NOT NULL CHECK (ai_relevance_label IN ('ai_theme', 'ai_enabling', 'not_ai_theme')),
    confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rationale text NOT NULL DEFAULT '',
    model text NOT NULL,
    attempts integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunk_ai_relevance_label_idx ON chunk_ai_relevance(ai_relevance_label);
