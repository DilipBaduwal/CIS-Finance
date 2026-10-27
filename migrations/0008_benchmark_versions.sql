CREATE TABLE benchmark_versions (
    benchmark_id text PRIMARY KEY,
    industry text NOT NULL,
    fiscal_year text NOT NULL,
    version_number integer NOT NULL CHECK (version_number > 0),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'superseded')),
    immutable boolean NOT NULL DEFAULT true,
    companies jsonb NOT NULL DEFAULT '[]'::jsonb,
    themes jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (industry, fiscal_year, version_number)
);

CREATE TABLE benchmark_approvals (
    approval_id uuid PRIMARY KEY,
    benchmark_id text NOT NULL REFERENCES benchmark_versions(benchmark_id) ON DELETE RESTRICT,
    candidate_index integer NOT NULL CHECK (candidate_index >= 0),
    question text NOT NULL,
    confidence numeric(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'pending')),
    reviewer text,
    reviewed_at timestamptz,
    evidence_chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX benchmark_versions_cohort_idx ON benchmark_versions (industry, fiscal_year, version_number DESC);
CREATE INDEX benchmark_approvals_benchmark_idx ON benchmark_approvals (benchmark_id, decision);
