"""Streamlit landing page for configuration and read-only service checks."""

import hashlib
import tempfile
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from cis_fin.company_queries import (
    CompanyLookupError,
    export_document_chunks,
    list_recent_documents,
)
from cis_fin.companies import register_detected_company
from cis_fin.connectivity import check_database, check_qdrant, check_storage
from cis_fin.extraction import ExtractionError, available_extractors, pdf_page_count
from cis_fin.ingestion import (
    DocumentRequest,
    IngestionError,
    PostgresIngestionStore,
    ingest_pdf,
    resume_extraction_job,
)
from cis_fin.migrations import MigrationError, run_migrations
from cis_fin.pipeline import (
    PipelineProcessingError,
    get_document_progress,
    process_document_pipeline,
)
from cis_fin.question_review import QuestionReviewError, edit_question_candidate
from cis_fin.report_metadata import ReportMetadataError, suggest_report_metadata
from cis_fin.nifty_lookup import NiftyLookupError, lookup_nifty50
from cis_fin.settings import Settings
from cis_fin.storage import ObjectStoreError, S3ObjectStore


st.set_page_config(page_title="CIS-FIN Research Pipeline", page_icon="📚", layout="wide")


@st.fragment(run_every="10s")
def render_live_progress(current_settings, document_id):
    try:
        progress = get_document_progress(current_settings, document_id)
    except PipelineProcessingError as exc:
        st.error("Could not refresh processing progress: {}".format(exc))
        return
    st.write(
        "Document **{}** · stage **{}** · database status **{}** · attempt {}/{}".format(
            str(document_id)[:8],
            progress.stage,
            progress.job_status,
            progress.attempt_count,
            progress.max_attempts,
        )
    )
    if progress.lease_expires_at:
        st.caption("Worker lease expires at {} UTC.".format(progress.lease_expires_at.isoformat()))
        if progress.job_status == "running" and progress.lease_expires_at <= datetime.now(timezone.utc):
            st.warning(
                "The worker lease expired. If no other worker is running, resume the saved job here."
            )
            if st.button("Resume saved job in Streamlit", key="resume_job_" + str(progress.job_id)):
                try:
                    with st.spinner("Resuming saved question generation and indexing…"):
                        result = process_document_pipeline(
                            current_settings,
                            progress.job_id,
                            progress_callback=lambda message: st.write(message),
                        )
                    st.success(
                        "Resumed job: processed {} question chunks and indexed {} chunks.".format(
                            result.chunks_processed_for_questions,
                            result.newly_indexed_chunks,
                        )
                    )
                    st.rerun()
                except PipelineProcessingError as exc:
                    st.error("Could not resume saved job: {}".format(exc))
    if progress.chunk_count:
        st.progress(
            min(progress.question_checkpoints / progress.chunk_count, 1.0),
            text="Question generation: {} / {} chunks checkpointed".format(
                min(progress.question_checkpoints, progress.chunk_count),
                progress.chunk_count,
            ),
        )
        st.progress(
            min(progress.indexed_chunk_count / progress.chunk_count, 1.0),
            text="Qdrant indexing: {} / {} chunks indexed".format(
                progress.indexed_chunk_count,
                progress.chunk_count,
            ),
        )
    st.caption(
        "{} question candidates saved ({} flagged for unmatched quotations).".format(
            progress.candidate_count,
            progress.invalid_candidate_count,
        )
    )
    if progress.job_status == "completed":
        st.success("All configured processing stages completed.")
    elif progress.job_status == "retry_wait":
        st.warning("A stage failed and is waiting for its configured retry backoff.")
    elif progress.job_status == "failed":
        st.error("The job reached its retry limit and needs owner review.")
    elif progress.job_status == "running" and (
        not progress.lease_expires_at or progress.lease_expires_at > datetime.now(timezone.utc)
    ):
        st.info("Processing is active. This panel refreshes automatically every 10 seconds.")


st.title("CIS-FIN Research Pipeline")
st.caption("Research pipeline foundation · shared evidence setup")
st.warning(
    "This workspace does not create cloud accounts or issue database credentials. "
    "The project owner must provision managed services and share credentials securely."
)

settings = Settings.from_env()
st.subheader("Configuration status")
configuration = (
    ("Managed PostgreSQL", settings.database_url is not None),
    (
        "Qdrant",
        settings.qdrant_url is not None
        and (
            "localhost" in settings.qdrant_url
            or "127.0.0.1" in settings.qdrant_url
            or settings.qdrant_api_key is not None
        ),
    ),
    ("S3-compatible storage", settings.s3_bucket is not None),
)
for label, configured in configuration:
    st.write("{}: {}".format("✅" if configured else "⚪", label + (" configured" if configured else " not configured")))

st.subheader("Read-only connectivity tests")
st.caption("Each test performs only a read operation. No upload, collection creation, ingestion, or deletion is performed.")
checks = (
    ("Test PostgreSQL", check_database),
    ("Test Qdrant", check_qdrant),
    ("Test object storage", check_storage),
)
columns = st.columns(len(checks))
for column, (label, check) in zip(columns, checks):
    with column:
        if st.button(label, use_container_width=True):
            result = check(settings)
            (st.success if result.ok else st.error)(result.message)

st.info(
    "This Streamlit setup page is not authenticated and is intended for local development only. "
    "Do not expose it to a network or use it as a shared researcher portal."
)

st.subheader("Upload and ingest a PDF")
st.caption(
    "Choose a PDF from your browser. It is sent to this local Streamlit server, then stored in the configured "
    "object-storage bucket and its metadata/chunks are written to PostgreSQL only after you submit."
)
engine = st.selectbox(
    "Extraction engine",
    options=available_extractors(),
    index=available_extractors().index(settings.extractor)
    if settings.extractor in available_extractors()
    else 0,
)
if engine == "mineru":
    st.warning("MinerU is not integrated in this release. Select PyMuPDF; Docling remains optional and unverified.")
if engine == "docling":
    st.warning("Docling is an optional adapter, not installed or verified in the base environment.")

uploaded_pdf = st.file_uploader(
    "Choose your annual report or other source PDF",
    type=["pdf"],
    accept_multiple_files=False,
    help="Maximum upload size is 100 MB per PDF.",
)
inspected_page_count = None
if uploaded_pdf and uploaded_pdf.size <= 100 * 1024 * 1024:
    uploaded_bytes = uploaded_pdf.getvalue()
    upload_checksum = hashlib.sha256(uploaded_bytes).hexdigest()
    try:
        with tempfile.TemporaryDirectory(prefix="cis-fin-inspect-") as temp_dir:
            inspect_path = Path(temp_dir) / "selected.pdf"
            with inspect_path.open("wb") as target:
                target.write(uploaded_bytes)
            inspected_page_count = pdf_page_count(inspect_path)
        st.caption("PDF contains {} pages.".format(inspected_page_count))
        if st.session_state.get("metadata_analysis_checksum") != upload_checksum:
            st.session_state["metadata_analysis_checksum"] = upload_checksum
            st.session_state.pop("nifty_lookup_source", None)
            st.session_state.pop("nse_classification_source", None)
            st.session_state.pop("nifty_lookup_match", None)
            st.session_state.pop("nifty_lookup_error", None)
            st.session_state["metadata_analysis_error"] = None
            try:
                with tempfile.TemporaryDirectory(prefix="cis-fin-metadata-") as temp_dir:
                    analysis_path = Path(temp_dir) / "report.pdf"
                    analysis_path.write_bytes(uploaded_bytes)
                    with st.spinner("Reading report details locally with {}…".format(settings.ollama_model)):
                        metadata = suggest_report_metadata(
                            analysis_path,
                            settings.ollama_base_url,
                            settings.ollama_model,
                        )
                detected = {
                    "company_name_input": metadata.company_name,
                    "ticker_symbol_input": metadata.ticker_symbol,
                    "sector_input": metadata.sector,
                    "industry_input": metadata.industry,
                    "document_title_input": metadata.document_title or uploaded_pdf.name.rsplit(".", 1)[0],
                    "document_type_input": (
                        metadata.document_type
                        if metadata.document_type in (
                            "annual_report",
                            "integrated_report",
                            "sustainability_report",
                            "investor_presentation",
                            "earnings_call_transcript",
                            "official_disclosure",
                            "other",
                        )
                        else "annual_report"
                    ),
                    "fiscal_year_input": metadata.fiscal_year,
                }
                # Verify/fill company fields from official exchange sources. This runs
                # even when the model supplied values, since its classifications may be wrong.
                if metadata.company_name:
                    try:
                        company = lookup_nifty50(metadata.company_name, metadata.ticker_symbol)
                        detected["ticker_symbol_input"] = company.symbol
                        # Do not retain an LLM guess when official classification is absent.
                        detected["sector_input"] = company.sector
                        detected["industry_input"] = company.industry
                        st.session_state["nifty_lookup_source"] = company.source_url
                        st.session_state["nse_classification_source"] = company.classification_source_url
                        st.session_state["nifty_lookup_match"] = company.company_name
                    except NiftyLookupError as exc:
                        st.session_state["nifty_lookup_error"] = str(exc)
                for key, value in detected.items():
                    st.session_state[key] = value
                st.session_state["metadata_analysis_complete"] = True
            except ReportMetadataError as exc:
                st.session_state["metadata_analysis_error"] = str(exc)
                st.session_state["metadata_analysis_complete"] = False
    except (ExtractionError, OSError) as exc:
        st.error(str(exc))
elif uploaded_pdf:
    st.error("This PDF exceeds the 100 MB per-file limit.")

if uploaded_pdf and st.session_state.get("metadata_analysis_error"):
    st.warning(
        "Automatic metadata detection did not finish: {} You can enter the report details below; "
        "this does not prevent local PDF extraction.".format(st.session_state["metadata_analysis_error"])
    )
elif uploaded_pdf and st.session_state.get("metadata_analysis_complete"):
    st.success(
        "Company and report details were suggested from the first four pages using local Ollama. "
        "Please review and correct them before submission. Sector and industry are suggestions, not verified classifications."
    )
if uploaded_pdf and st.session_state.get("nifty_lookup_match"):
    st.info(
        "Verified {} against Nifty 50, then searched market-information pages for sector and industry. "
        "Sources: Nifty constituents — {}; web classifications — {}. "
        "Review the values before submitting.".format(
            st.session_state["nifty_lookup_match"],
            st.session_state["nifty_lookup_source"],
            st.session_state.get("nse_classification_source") or "no Tavily result; add TAVILY_API_KEY to .env",
        )
    )
elif uploaded_pdf and st.session_state.get("nifty_lookup_error"):
    st.caption("Nifty lookup note: {}".format(st.session_state["nifty_lookup_error"]))

if uploaded_pdf and inspected_page_count:
    page_widget_key = "{}:{}".format(uploaded_pdf.name, uploaded_pdf.size)
    lower_column, upper_column = st.columns(2)
    with lower_column:
        selected_page_start = st.number_input(
            "First page to process",
            min_value=1,
            max_value=inspected_page_count,
            value=1,
            step=1,
            key="page_start_" + page_widget_key,
        )
    with upper_column:
        selected_page_end = st.number_input(
            "Last page to process",
            min_value=1,
            max_value=inspected_page_count,
            value=inspected_page_count,
            step=1,
            key="page_end_" + page_widget_key,
        )
    st.caption(
        "Only pages {}–{} will be extracted and chunked. Citations retain their original PDF page numbers.".format(
            selected_page_start, selected_page_end
        )
    )
else:
    selected_page_start = None
    selected_page_end = None

with st.form("pdf_ingestion_form", clear_on_submit=False):
    st.markdown("**Review the automatically detected details**")
    company_name = st.text_input("Company name", key="company_name_input")
    ticker_symbol = st.text_input("Ticker symbol", key="ticker_symbol_input")
    sector = st.text_input("Sector (web search)", key="sector_input")
    industry = st.text_input("Industry (web search)", key="industry_input")
    document_title = st.text_input("Document title", key="document_title_input")
    document_type = st.selectbox(
        "Document type",
        options=(
            "annual_report",
            "integrated_report",
            "sustainability_report",
            "investor_presentation",
            "earnings_call_transcript",
            "official_disclosure",
            "other",
        ),
        key="document_type_input",
    )
    fiscal_year = st.text_input("Fiscal year", key="fiscal_year_input")
    source_url = st.text_input("Official source URL (optional)")
    source_classification = st.selectbox(
        "Source classification",
        options=("public_source", "confidential_research"),
        help="Only upload confidential notes if authorized and the storage provider is approved for them.",
    )
    submitted = st.form_submit_button("Upload and process selected pages")

if submitted:
    if not uploaded_pdf:
        st.error("Choose a PDF before submitting.")
    elif uploaded_pdf.size <= 0:
        st.error("The uploaded PDF is empty.")
    elif not all(value.strip() for value in (company_name, ticker_symbol, document_title)):
        st.error("Confirm the company name, ticker symbol, and document title. Use the Nifty lookup if the ticker is missing.")
    elif not inspected_page_count:
        st.error("Upload a valid PDF so the page range can be checked.")
    elif selected_page_end < selected_page_start:
        st.error("The last page must be greater than or equal to the first page.")
    elif engine == "mineru":
        st.error("MinerU is not integrated. Select PyMuPDF or an installed Docling adapter.")
    else:
        progress = st.status("Uploading original and extracting selected pages…", expanded=True)
        try:
            pending_migrations = run_migrations(
                settings,
                Path(__file__).resolve().parent / "migrations",
                apply=False,
            )
            if pending_migrations:
                raise MigrationError(
                    "Database schema is not current. Review `python -m cis_fin.cli migrate --dry-run`, "
                    "then apply migrations before uploading."
                )
            lookup_suffix = (
                "; official Nifty lookup: {}; web classification sources: {}".format(
                    st.session_state.get("nifty_lookup_source"),
                    st.session_state.get("nse_classification_source") or "not available",
                )
                if st.session_state.get("nifty_lookup_match")
                else ""
            )
            company_id, company_created = register_detected_company(
                settings,
                ticker_symbol,
                company_name,
                sector.strip() or "Unclassified (requires review)",
                industry.strip() or "Unclassified (requires review)",
                "Auto-detected from PDF {} (SHA-256 {})".format(
                    uploaded_pdf.name,
                    upload_checksum,
                ) + lookup_suffix,
                official_exchange_lookup=bool(st.session_state.get("nifty_lookup_match")),
            )
            with tempfile.TemporaryDirectory(prefix="cis-fin-upload-") as temp_dir:
                pdf_path = Path(temp_dir) / "upload.pdf"
                with pdf_path.open("wb") as target:
                    target.write(uploaded_bytes)
                request = DocumentRequest(
                    company_id=company_id,
                    title=document_title.strip(),
                    document_type=document_type,
                    path=pdf_path,
                    engine=engine,
                    fiscal_year=fiscal_year.strip() or None,
                    source_url=source_url.strip() or None,
                    source_classification=source_classification,
                    page_start=int(selected_page_start),
                    page_end=int(selected_page_end),
                )
                postgres_store = PostgresIngestionStore(settings)
                object_store = S3ObjectStore(settings)
                result = ingest_pdf(
                    request,
                    postgres_store,
                    object_store,
                    batch_size=100,
                )
                st.session_state["last_ingested_document_id"] = str(result.document_id)
                pipeline_result = None
                if (
                    result.duplicate
                    and result.existing_status in ("failed", "retry_wait")
                    and result.existing_stage in ("registered", "stored", "extracted")
                ):
                    result = resume_extraction_job(
                        result.job_id,
                        request,
                        postgres_store,
                        object_store,
                        batch_size=100,
                    )
                if not result.duplicate or (
                    result.existing_stage in ("chunked", "questions", "embedded", "indexed")
                    and result.existing_status in ("queued", "retry_wait", "running")
                ):
                    pipeline_result = process_document_pipeline(
                        settings,
                        result.job_id,
                        progress_callback=lambda message: progress.update(label=message, state="running"),
                    )
            if result.duplicate:
                progress.update(label="Existing document processing checked", state="complete")
                if pipeline_result is None:
                    st.warning(
                        "This company already has this document checksum. No duplicate was uploaded or inserted. "
                        "Existing job: {} (status {}, stage {}).".format(
                            result.job_id,
                            result.existing_status or "unknown",
                            result.existing_stage or "unknown",
                        )
                    )
                else:
                    st.info("Duplicate source detected; resumed the saved downstream job without re-extracting the PDF.")
            else:
                progress.update(label="Questions and vector indexing complete", state="complete")
                if company_created:
                    st.info(
                        "Created a company profile from the PDF suggestions. It is marked pending review, "
                        "not as verified constituent data."
                    )
                st.success(
                    "Pages {}–{} of {} were processed. {} question candidates were saved for review; "
                    "{} chunks were indexed in Qdrant. Job {} is complete. Questions are not auto-approved.".format(
                        selected_page_start,
                        selected_page_end,
                        inspected_page_count,
                        pipeline_result.question_candidates_created if pipeline_result else 0,
                        pipeline_result.newly_indexed_chunks if pipeline_result else 0,
                        result.job_id,
                    )
                )
            st.session_state["last_ingested_document_id"] = str(result.document_id)
        except (
            IngestionError,
            MigrationError,
            ObjectStoreError,
            PipelineProcessingError,
            OSError,
            ValueError,
        ) as exc:
            progress.update(label="Ingestion did not complete", state="error")
            st.error(str(exc))

last_document_id = st.session_state.get("last_ingested_document_id")
try:
    recent_documents = list_recent_documents(settings)
except CompanyLookupError as exc:
    recent_documents = []
    st.error("Could not load saved documents for question review: {}".format(exc))

if recent_documents:
    recent_document_ids = [row[0] for row in recent_documents]
    selected_index = (
        recent_document_ids.index(last_document_id)
        if last_document_id in recent_document_ids
        else 0
    )
    selected_recent_document_id = st.selectbox(
        "Choose a saved document to view or edit questions",
        options=recent_document_ids,
        index=selected_index,
        format_func=lambda document_id: next(
            "{} · {} · {} · {}".format(
                row[2],
                row[1],
                row[3] or "year not set",
                row[4],
            )
            for row in recent_documents
            if row[0] == document_id
        ),
        key="saved_document_question_review",
    )
    last_document_id = selected_recent_document_id
    st.session_state["last_ingested_document_id"] = last_document_id

if last_document_id:
    st.subheader("Live processing progress")
    render_live_progress(settings, uuid.UUID(last_document_id))
    st.subheader("Processed evidence")
    st.caption(
        "Grounded question candidates are generated automatically with local Ollama ({}), then chunks are embedded "
        "and indexed in Qdrant. Source text is not sent to a cloud model. Candidates require researcher review and are never auto-approved.".format(
            settings.ollama_model
        )
    )
    try:
        records = export_document_chunks(settings, uuid.UUID(last_document_id))
        if records:
            st.download_button(
                "Download evidence and question-candidates JSON",
                data=json.dumps(records, ensure_ascii=False, indent=2),
                file_name="document-{}-evidence.json".format(last_document_id[:8]),
                mime="application/json",
                key="download_question_evidence",
            )
            st.info(
                "JSON includes company, sector, industry, source/page citations, chunks and any generated candidates. "
                "Every candidate remains pending review; non-matching quotations are flagged invalid."
            )
            candidates = [
                (record, candidate)
                for record in records
                for candidate in record["question_candidates"]
            ]
            st.subheader("Review and edit generated questions")
            st.caption(
                "Edit the question or its quotation, then save. The app checks the quote against the original chunk. "
                "Edits remain pending review and are never auto-approved."
            )
            edit_notice = st.session_state.pop("question_edit_notice", None)
            if edit_notice:
                level, message = edit_notice
                getattr(st, level)(message)
            review_filter = st.selectbox(
                "Show candidates",
                options=("All candidates", "Invalid quotations", "Quotes matching source", "Pending review"),
                key="question_review_filter_" + last_document_id,
            )
            if review_filter == "Invalid quotations":
                candidates = [
                    item for item in candidates if item[1]["validation_status"] == "invalid"
                ]
            elif review_filter == "Quotes matching source":
                candidates = [
                    item for item in candidates if item[1]["validation_status"] != "invalid"
                ]
            elif review_filter == "Pending review":
                candidates = [
                    item for item in candidates if item[1]["review_status"] == "pending"
                ]

            if not candidates:
                st.info("No question candidates match this filter yet.")
            else:
                candidate_ids = [candidate["question_id"] for _, candidate in candidates]
                candidate_lookup = {
                    candidate["question_id"]: (record, candidate)
                    for record, candidate in candidates
                }
                selected_candidate_id = st.selectbox(
                    "Question candidate",
                    options=candidate_ids,
                    format_func=lambda candidate_id: "{} · pages {}–{} · {}".format(
                        "INVALID QUOTE"
                        if candidate_lookup[candidate_id][1]["validation_status"] == "invalid"
                        else "QUOTE MATCH",
                        candidate_lookup[candidate_id][0]["page_start"],
                        candidate_lookup[candidate_id][0]["page_end"],
                        candidate_lookup[candidate_id][1]["question"][:100],
                    ),
                    key="question_review_candidate_" + last_document_id,
                )
                record, candidate = candidate_lookup[selected_candidate_id]
                if candidate["validation_status"] == "invalid":
                    st.warning(
                        "The saved quotation did not match the source text. Edit the quotation below to an exact "
                        "quote from this chunk; it will remain invalid until it matches."
                    )
                else:
                    st.success("The quotation matches source text. The candidate still needs human review.")
                st.markdown(
                    "**Source pages:** {}–{} · **Chunk:** {}".format(
                        record["page_start"],
                        record["page_end"],
                        record["chunk_index"],
                    )
                )
                with st.expander("View original source chunk", expanded=True):
                    st.text(record["chunk_text"])
                if candidate["review_status"] != "pending":
                    st.info(
                        "This candidate is {} and is read-only here. Editing is limited to pending candidates "
                        "so prior review decisions are preserved.".format(candidate["review_status"])
                    )
                else:
                    with st.form("edit_question_" + selected_candidate_id):
                        edited_question = st.text_area(
                            "Question",
                            value=candidate["question"],
                            height=100,
                            key="question_text_" + selected_candidate_id,
                        )
                        edited_quote = st.text_area(
                            "Evidence quotation",
                            value=candidate["evidence_quote"],
                            height=100,
                            key="evidence_quote_" + selected_candidate_id,
                        )
                        save_edit = st.form_submit_button("Save question and quotation")
                    if save_edit:
                        try:
                            edit_result = edit_question_candidate(
                                settings,
                                uuid.UUID(selected_candidate_id),
                                edited_question,
                                edited_quote,
                            )
                            for key in (
                                "question_text_" + selected_candidate_id,
                                "evidence_quote_" + selected_candidate_id,
                            ):
                                st.session_state.pop(key, None)
                            if edit_result.quote_matches_source:
                                st.session_state["question_edit_notice"] = (
                                    "success",
                                    "Edit saved. The quotation matches the source; the question remains pending human review.",
                                )
                            else:
                                st.session_state["question_edit_notice"] = (
                                    "warning",
                                    "Edit saved, but the quotation still does not match the source. It remains flagged invalid.",
                                )
                            st.rerun()
                        except QuestionReviewError as exc:
                            st.error(str(exc))
    except (CompanyLookupError, ValueError) as exc:
        st.error("Could not prepare the evidence export: {}".format(exc))
