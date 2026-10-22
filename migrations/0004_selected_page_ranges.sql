ALTER TABLE documents
    ADD COLUMN selected_page_start integer,
    ADD COLUMN selected_page_end integer,
    ADD COLUMN extracted_page_count integer,
    ADD CONSTRAINT documents_selected_page_range_check
        CHECK (
            (selected_page_start IS NULL AND selected_page_end IS NULL)
            OR (
                selected_page_start > 0
                AND selected_page_end >= selected_page_start
                AND (page_count IS NULL OR selected_page_end <= page_count)
            )
        ),
    ADD CONSTRAINT documents_extracted_page_count_check
        CHECK (extracted_page_count IS NULL OR extracted_page_count > 0);
