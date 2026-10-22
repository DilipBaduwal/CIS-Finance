# Data dictionary

The column definitions and enforced constraints live in [the initial migration](../migrations/0001_core_schema.sql). UUIDs are application-generated. UTC-aware `timestamptz` columns are used for event times. This dictionary describes the intended relational records; several are not populated until later phases.

| Table | Key fields | Meaning |
|---|---|---|
| `companies` | `company_id`, `symbol`, `sector`, `industry`, `constituent_list_version`, `constituent_as_of`, `profile_status`, `profile_source` | Company profile. Official constituent fields are nullable for PDF-detected profiles; these are stored as `pending_review`, not falsely marked as verified. |
| `researchers` | `researcher_id`, `user_identifier`, `role`, `active_status` | Identifiable team member record. `role` labels are not a substitute for authenticated authorization. |
| `company_assignments` | `assignment_id`, `researcher_id`, `company_id`, `assignment_status` | Research ownership history and progress status. One active assignment owner per company is enforced. |
| `documents` | `document_id`, `company_id`, `checksum`, `object_storage_key`, `source_classification`, `page_count`, `selected_page_start`, `selected_page_end`, `extracted_page_count`, `extraction_status`, `processing_status` | Source metadata and object pointer; checksum is SHA-256 and unique per company. Selected bounds are one-based and inclusive. `public_source` and `confidential_research` are separate classifications. |
| `chunks` | `chunk_id`, `document_id`, `page_start`, `page_end`, `chunk_index`, `chunk_hash`, `extraction_version` | Page-cited extracted text. Chunk index is unique per document; stable UUID includes document scope, source checksum, index, and text hash. Repeated identical passages on different pages are valid separate occurrences. |
| `questions` | `question_id`, `normalized_question_hash`, `framework`, `dimension`, model/prompt/taxonomy versions, `validation_status`, `review_status` | Generated evidence-derived candidate. Local candidates with a matching quotation remain `needs_review` and `pending`; unmatched quotations are `invalid`. |
| `chunk_question_evidence` | `evidence_id`, `chunk_id`, `question_id`, `evidence_quote`, `ai_specific_evidence`, `semantic_support_status` | Many-to-many supporting quote and evidence review state. Stored quotation is the exact extracted source span; a quote match alone is not proof of semantic support. |
| `question_generation_checkpoints` | `chunk_id`, `generation_model`, `prompt_version`, `status`, `attempted_at` | Prevents repeated work for generated or abstained chunks in a bounded model batch. Failed calls are not checkpointed and can be retried. |
| `embedding_registry` | `embedding_id`, `chunk_id`, `vector_id`, `collection_name`, `embedding_model`, `embedding_version`, `vector_dimensions`, `indexing_status` | Link from relational chunk to versioned Qdrant vector. A changed embedding model uses a different version/collection. |
| `processing_jobs` | `job_id`, `document_id`, `stage`, `status`, `attempt_count`, `max_attempts`, `checkpoint`, `lease_*`, `error_*` | Recoverable document work state. Error fields must be sanitized; never store credentials or full sensitive source excerpts in logs. |
| `audit_events` | `audit_id`, `actor_id`, `action`, `entity_type`, `entity_id`, `occurred_at`, `details` | Audit trail for reviewer actions and reprocessing. Restrict access and avoid sensitive details. |

Document UUIDs, chunk UUIDs, and vector UUIDs are different identifiers and are linked explicitly. The Qdrant index is rebuildable; PostgreSQL and object storage retain the primary research record and source.
