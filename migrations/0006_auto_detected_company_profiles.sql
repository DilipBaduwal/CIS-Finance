ALTER TABLE companies
    ALTER COLUMN constituent_list_version DROP NOT NULL,
    ALTER COLUMN constituent_as_of DROP NOT NULL,
    ADD COLUMN profile_status text NOT NULL DEFAULT 'verified'
        CHECK (profile_status IN ('pending_review', 'verified')),
    ADD COLUMN profile_source text;

CREATE INDEX companies_profile_status_idx
    ON companies (profile_status, active_status);
