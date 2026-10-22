CREATE TABLE question_generation_checkpoints (
    chunk_id uuid NOT NULL REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
    generation_model text NOT NULL,
    prompt_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('generated', 'abstained')),
    attempted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, generation_model, prompt_version)
);
CREATE INDEX question_generation_checkpoints_status_idx
    ON question_generation_checkpoints (status, attempted_at);
