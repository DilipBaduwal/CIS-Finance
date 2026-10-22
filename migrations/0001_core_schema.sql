CREATE TABLE companies (
    company_id uuid PRIMARY KEY,
    symbol text NOT NULL UNIQUE,
    company_name text NOT NULL,
    sector text NOT NULL,
    industry text NOT NULL,
    constituent_list_version text NOT NULL,
    constituent_as_of date NOT NULL,
    active_status boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(trim(symbol)) > 0),
    CHECK (length(trim(company_name)) > 0)
);

CREATE TABLE researchers (
    researcher_id uuid PRIMARY KEY,
    user_identifier text NOT NULL UNIQUE,
    display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('owner', 'researcher', 'reviewer')),
    active_status boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(trim(user_identifier)) > 0),
    CHECK (length(trim(display_name)) > 0)
);

CREATE TABLE company_assignments (
    assignment_id uuid PRIMARY KEY,
    researcher_id uuid NOT NULL REFERENCES researchers(researcher_id) ON DELETE RESTRICT,
    company_id uuid NOT NULL REFERENCES companies(company_id) ON DELETE RESTRICT,
    assignment_status text NOT NULL DEFAULT 'assigned'
        CHECK (assignment_status IN ('unassigned', 'assigned', 'in_progress', 'submitted_for_review', 'completed')),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    notes text,
    CHECK (completed_at IS NULL OR assignment_status = 'completed')
);

CREATE UNIQUE INDEX company_assignments_one_active_owner
    ON company_assignments (company_id)
    WHERE assignment_status IN ('assigned', 'in_progress', 'submitted_for_review');
CREATE INDEX company_assignments_researcher_idx ON company_assignments (researcher_id, assignment_status);

CREATE TABLE documents (
    document_id uuid PRIMARY KEY,
    company_id uuid NOT NULL REFERENCES companies(company_id) ON DELETE RESTRICT,
    document_title text NOT NULL,
    document_type text NOT NULL,
    fiscal_year text,
    publication_date date,
    source_url text,
    source_classification text NOT NULL DEFAULT 'public_source'
        CHECK (source_classification IN ('public_source', 'confidential_research')),
    object_storage_key text NOT NULL UNIQUE,
    checksum char(64) NOT NULL CHECK (checksum ~ '^[a-f0-9]{64}$'),
    file_size_bytes bigint NOT NULL CHECK (file_size_bytes > 0),
    mime_type text NOT NULL,
    page_count integer CHECK (page_count IS NULL OR page_count > 0),
    extraction_status text NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending', 'processing', 'succeeded', 'failed', 'needs_review')),
    processing_status text NOT NULL DEFAULT 'queued'
        CHECK (processing_status IN ('queued', 'processing', 'failed', 'completed', 'needs_review')),
    created_by uuid REFERENCES researchers(researcher_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, checksum),
    UNIQUE (document_id, company_id)
);
CREATE INDEX documents_company_status_idx ON documents (company_id, processing_status);
CREATE INDEX documents_checksum_idx ON documents (checksum);

CREATE TABLE chunks (
    chunk_id uuid PRIMARY KEY,
    document_id uuid NOT NULL,
    company_id uuid NOT NULL,
    page_start integer NOT NULL CHECK (page_start > 0),
    page_end integer NOT NULL CHECK (page_end >= page_start),
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    chunk_text text NOT NULL CHECK (length(trim(chunk_text)) > 0),
    chunk_hash char(64) NOT NULL CHECK (chunk_hash ~ '^[a-f0-9]{64}$'),
    extraction_version text NOT NULL,
    researcher_id uuid REFERENCES researchers(researcher_id) ON DELETE RESTRICT,
    review_status text NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, company_id) REFERENCES documents(document_id, company_id) ON DELETE RESTRICT,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX chunks_company_document_idx ON chunks (company_id, document_id);

CREATE TABLE questions (
    question_id uuid PRIMARY KEY,
    question_text text NOT NULL CHECK (length(trim(question_text)) > 0),
    normalized_question_hash char(64) NOT NULL CHECK (normalized_question_hash ~ '^[a-f0-9]{64}$'),
    framework text NOT NULL,
    dimension text NOT NULL,
    taxonomy_version text NOT NULL,
    generation_model text NOT NULL,
    prompt_version text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    validation_status text NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN ('pending', 'valid', 'invalid', 'needs_review')),
    review_status text NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected')),
    reviewer_id uuid REFERENCES researchers(researcher_id) ON DELETE RESTRICT,
    reviewed_at timestamptz,
    reviewer_notes text,
    CHECK ((reviewer_id IS NULL) = (reviewed_at IS NULL)),
    CHECK (review_status <> 'approved' OR validation_status = 'valid')
);
CREATE INDEX questions_review_status_idx ON questions (review_status, validation_status);
CREATE INDEX questions_normalized_hash_idx ON questions (normalized_question_hash);

CREATE TABLE chunk_question_evidence (
    evidence_id uuid PRIMARY KEY,
    chunk_id uuid NOT NULL REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
    question_id uuid NOT NULL REFERENCES questions(question_id) ON DELETE RESTRICT,
    evidence_quote text NOT NULL CHECK (length(trim(evidence_quote)) > 0),
    evidence_type text NOT NULL,
    ai_specific_evidence boolean NOT NULL DEFAULT false,
    semantic_support_status text NOT NULL DEFAULT 'pending'
        CHECK (semantic_support_status IN ('pending', 'supported', 'unsupported', 'needs_review')),
    reviewer_notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (chunk_id, question_id, evidence_quote)
);
CREATE INDEX evidence_question_idx ON chunk_question_evidence (question_id);

CREATE TABLE embedding_registry (
    embedding_id uuid PRIMARY KEY,
    chunk_id uuid NOT NULL REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
    vector_id uuid NOT NULL UNIQUE,
    collection_name text NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    vector_dimensions integer NOT NULL CHECK (vector_dimensions > 0),
    indexing_status text NOT NULL DEFAULT 'pending'
        CHECK (indexing_status IN ('pending', 'indexed', 'stale', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (chunk_id, collection_name, embedding_model, embedding_version)
);
CREATE INDEX embedding_registry_status_idx ON embedding_registry (indexing_status, collection_name);

CREATE TABLE processing_jobs (
    job_id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(document_id) ON DELETE RESTRICT,
    researcher_id uuid REFERENCES researchers(researcher_id) ON DELETE RESTRICT,
    stage text NOT NULL
        CHECK (stage IN ('registered', 'stored', 'extracted', 'chunked', 'questions', 'embedded', 'indexed', 'complete')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'retry_wait', 'failed', 'completed', 'cancelled')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    next_attempt_at timestamptz,
    lease_owner text,
    lease_expires_at timestamptz,
    checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    error_message text,
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (attempt_count <= max_attempts),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (status <> 'running' OR lease_expires_at IS NOT NULL)
);
CREATE INDEX processing_jobs_resume_idx ON processing_jobs (status, next_attempt_at, updated_at);
CREATE INDEX processing_jobs_document_idx ON processing_jobs (document_id, updated_at);

CREATE TABLE audit_events (
    audit_id uuid PRIMARY KEY,
    actor_id uuid REFERENCES researchers(researcher_id) ON DELETE RESTRICT,
    action text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_events_entity_idx ON audit_events (entity_type, entity_id, occurred_at);
CREATE INDEX audit_events_actor_idx ON audit_events (actor_id, occurred_at);
