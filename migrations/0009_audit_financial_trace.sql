CREATE TABLE audit_rows (
    audit_row_id uuid PRIMARY KEY, text_evidence text NOT NULL,
    expected_score integer NOT NULL CHECK (expected_score BETWEEN 0 AND 5),
    given_score integer NOT NULL CHECK (given_score BETWEEN 0 AND 5),
    company text, question text, dimension text, source_document text,
    page_number integer CHECK (page_number IS NULL OR page_number > 0), chunk_id uuid,
    auditor text, comments text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_rows_company_idx ON audit_rows (company, dimension);
CREATE TABLE financial_metrics (
    metric_id uuid PRIMARY KEY, company_id uuid NOT NULL REFERENCES companies(company_id) ON DELETE RESTRICT,
    fiscal_year text NOT NULL, revenue numeric NOT NULL CHECK (revenue >= 0), ai_spend numeric NOT NULL CHECK (ai_spend >= 0),
    currency text NOT NULL, spend_type text NOT NULL, source_type text NOT NULL,
    confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')), source_document text, source_page integer,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (company_id, fiscal_year, spend_type, source_document, source_page)
);
CREATE TABLE score_traces (
    trace_id uuid PRIMARY KEY, company_id uuid REFERENCES companies(company_id) ON DELETE RESTRICT,
    document_id uuid REFERENCES documents(document_id) ON DELETE RESTRICT, question_id uuid REFERENCES questions(question_id) ON DELETE RESTRICT,
    trace_path text NOT NULL UNIQUE, source_checksum char(64), model text, prompt_version text,
    score integer CHECK (score IS NULL OR score BETWEEN 0 AND 5), trace_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE document_index_entries (
    entry_id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(document_id) ON DELETE RESTRICT,
    title text NOT NULL, index_page integer, start_page integer NOT NULL, end_page integer NOT NULL,
    selected boolean NOT NULL DEFAULT false, CHECK (start_page > 0 AND end_page >= start_page)
);
CREATE INDEX document_index_entries_document_idx ON document_index_entries (document_id, selected);
