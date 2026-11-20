ALTER TABLE healthcare_benchmark_questions
    ADD COLUMN IF NOT EXISTS ai_theme_label text NOT NULL DEFAULT 'not_ai_theme'
        CHECK (ai_theme_label IN ('ai_theme', 'ai_enabling', 'not_ai_theme')),
    ADD COLUMN IF NOT EXISTS ai_theme_reason text NOT NULL DEFAULT '';

COMMENT ON COLUMN healthcare_benchmark_questions.ai_theme_label IS
    'Semantic relevance of the question to AI readiness: direct AI theme, AI-enabling capability, or unrelated/general.';
