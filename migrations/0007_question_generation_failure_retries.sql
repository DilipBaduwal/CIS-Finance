ALTER TABLE question_generation_checkpoints
    DROP CONSTRAINT question_generation_checkpoints_status_check;

ALTER TABLE question_generation_checkpoints
    ADD CONSTRAINT question_generation_checkpoints_status_check
    CHECK (status IN ('generated', 'abstained', 'failed', 'skipped'));

ALTER TABLE question_generation_checkpoints
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ADD COLUMN last_error text;
