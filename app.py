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
    cancel_document_job,
    get_document_progress,
    process_document_pipeline,
)
from cis_fin.auth import authenticate, configured_users
from cis_fin.question_review import QuestionReviewError, edit_question_candidate
from cis_fin.report_metadata import ReportMetadataError, suggest_report_metadata
from cis_fin.nifty_lookup import (
    NiftyLookupError,
    fallback_ticker,
    find_local_company_hint,
    lookup_local_nifty500,
    lookup_nifty50,
)
from cis_fin.settings import Settings
from cis_fin.storage import ObjectStoreError, S3ObjectStore


st.set_page_config(page_title="CIS-FIN Research Pipeline", page_icon="📚", layout="wide")

st.markdown(
    """
    <style>
    .block-container { max-width: 1850px; padding-top: 2rem; padding-bottom: 4rem; }
    [data-testid="stSidebar"] { border-right: 1px solid rgba(128,128,128,.18); }
    [data-testid="stMetric"] { background: rgba(127,127,127,.08); border: 1px solid rgba(128,128,128,.16); padding: 14px; border-radius: 12px; }
    div[data-testid="stStatusWidget"] { border-radius: 12px; }
    .hero { padding: 1.8rem 2rem; border-radius: 20px; background: linear-gradient(135deg,#172554 0%,#0f766e 100%); color: white; margin-bottom: 1.2rem; box-shadow: 0 12px 30px rgba(15,118,110,.18); }
    .hero h1 { margin: 0; font-size: 2.45rem; }
    .hero p { margin: .35rem 0 0; opacity: .86; }
    .section-label { color: #64748b; font-size: .75rem; text-transform: uppercase; letter-spacing: .14em; font-weight: 800; margin: 1.5rem 0 .45rem; }
    div[data-testid="stFileUploader"] { border: 2px dashed rgba(20,184,166,.55); border-radius: 16px; padding: .5rem; background: rgba(20,184,166,.04); }
    div[data-testid="stForm"] { border-radius: 16px; padding: 1.2rem; border: 1px solid rgba(128,128,128,.22); }
    .step-card { border-left: 4px solid #14b8a6; padding: .25rem 0 .25rem 1rem; margin: 1rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "authenticated_user" not in st.session_state:
    st.markdown(
        '<div class="hero"><h1>📚 CIS-FIN</h1><p>Evidence-grounded financial research, in one calm workspace.</p></div>',
        unsafe_allow_html=True,
    )
    _, login_column, _ = st.columns([1, 1.2, 1])
    with login_column:
        st.subheader("Welcome back")
        st.caption("Sign in to upload reports and continue your research work.")
        with st.form("login_form"):
            login_username = st.text_input("Username", placeholder="Enter your username")
            login_password = st.text_input("Password", type="password", placeholder="Enter your password")
            login_submitted = st.form_submit_button("Sign in", use_container_width=True)
    if login_submitted:
        if authenticate(login_username, login_password):
            st.session_state["authenticated_user"] = login_username.strip()
            st.rerun()
        st.error("Invalid username or password.")
    if not configured_users():
        st.warning("No users are configured. Set CIS_FIN_USER_1_USERNAME/PASSWORD through CIS_FIN_USER_5_USERNAME/PASSWORD.")
    st.stop()


def run_pipeline_with_ui_progress(current_settings, job_id, status_container):
    question_bar = st.progress(0, text="Question generation: waiting for the first chunk…")

    def report(message):
        if message.startswith("QUESTION_PROGRESS|"):
            _, completed_text, total_text = message.split("|", 2)
            completed, total = int(completed_text), int(total_text)
            if total:
                question_bar.progress(
                    min(completed / total, 1.0),
                    text="Question generation: {} / {} chunks checkpointed".format(completed, total),
                )
            status_container.update(label="Generating grounded questions…", state="running")
        else:
            status_container.update(label=message, state="running")

    return process_document_pipeline(current_settings, job_id, progress_callback=report)


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
                        resume_status = st.status("Resuming saved processing job…", expanded=True)
                        result = run_pipeline_with_ui_progress(
                            current_settings, progress.job_id, resume_status
                        )
                        resume_status.update(label="Saved processing job completed", state="complete")
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
    if progress.job_status in ("running", "queued", "retry_wait"):
        if st.button("Stop this job", key="stop_job_" + str(progress.job_id)):
            try:
                cancel_document_job(current_settings, progress.job_id)
                st.warning("Stop requested. The worker will release the lease at the next batch boundary.")
                st.rerun()
            except PipelineProcessingError as exc:
                st.error(str(exc))
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


with st.sidebar:
    st.markdown("### 📚 CIS-FIN")
    st.caption("Evidence-grounded research workspace")
    st.divider()
    st.write("**Signed in as**")
    st.info(st.session_state["authenticated_user"])
    if st.button("Sign out", use_container_width=True):
        st.session_state.pop("authenticated_user", None)
        st.rerun()
    st.divider()
    st.caption("Processing capacity")
    st.metric("Concurrent jobs", Settings.from_env().max_concurrent_ingestions)
    st.caption("Jobs are checkpointed and can be resumed safely.")

st.markdown(
    '<div class="hero"><h1>CIS-FIN Research Pipeline</h1>'
    '<p>Upload reports, extract evidence, generate grounded questions, and index research data.</p></div>',
    unsafe_allow_html=True,
)
st.warning(
    "This workspace does not create cloud accounts or issue database credentials. "
    "The project owner must provision managed services and share credentials securely."
)

settings = Settings.from_env()
st.markdown('<div class="section-label">System readiness</div>', unsafe_allow_html=True)
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
metrics = st.columns(len(configuration))
for metric, (label, configured) in zip(metrics, configuration):
    with metric:
        st.metric(label, "Ready" if configured else "Missing", delta=None)

with st.expander("Run connectivity checks", expanded=False):
    st.caption("These checks are read-only and do not upload, modify, or delete data.")
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
st.markdown('<div class="section-label">Step 1 · Source document</div>', unsafe_allow_html=True)
st.subheader("Start a new research document")
st.caption("Each test performs only a read operation. No upload, collection creation, ingestion, or deletion is performed.")
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
                filename_company = None
                try:
                    filename_company = find_local_company_hint(uploaded_pdf.name)
                except NiftyLookupError:
                    pass
                detected = {
                    "company_name_input": filename_company.company_name if filename_company else metadata.company_name,
                    "ticker_symbol_input": filename_company.symbol if filename_company else metadata.ticker_symbol,
                    "sector_input": metadata.sector,
                    "industry_input": filename_company.industry if filename_company else metadata.industry,
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
                if detected["company_name_input"]:
                    try:
                        company = lookup_local_nifty500(
                            detected["company_name_input"], detected["ticker_symbol_input"]
                        )
                        detected["ticker_symbol_input"] = company.symbol
                        if company.industry:
                            detected["industry_input"] = company.industry
                        st.session_state["nifty_lookup_source"] = "data/nifty500.csv"
                        st.session_state["nse_classification_source"] = "Nifty 500 local reference snapshot"
                        st.session_state["nifty_lookup_match"] = company.company_name
                    except NiftyLookupError as exc:
                        try:
                            company = lookup_nifty50(
                                detected["company_name_input"], detected["ticker_symbol_input"]
                            )
                            detected["ticker_symbol_input"] = company.symbol
                            if company.industry:
                                detected["industry_input"] = company.industry
                            st.session_state["nifty_lookup_source"] = company.source_url
                            st.session_state["nse_classification_source"] = company.classification_source_url
                            st.session_state["nifty_lookup_match"] = company.company_name
                        except NiftyLookupError as live_exc:
                            st.session_state["nifty_lookup_error"] = str(live_exc)
                            if not detected["ticker_symbol_input"]:
                                detected["ticker_symbol_input"] = fallback_ticker(metadata.company_name)
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
    if not st.session_state.get("ticker_symbol_input", "").strip():
        st.warning("The ticker was not detected automatically.")
        if st.button("Retry automatic metadata detection", key="retry_metadata_detection"):
            st.session_state.pop("metadata_analysis_checksum", None)
            st.session_state["metadata_analysis_complete"] = False
            st.rerun()
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
    st.markdown('<div class="section-label">Step 2 · Select pages</div>', unsafe_allow_html=True)
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
    st.markdown('<div class="step-card"><strong>Step 2 · Confirm report details</strong><br><small>These values are suggested from the first pages. Review them before processing.</small></div>', unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        company_name = st.text_input("Company name", key="company_name_input")
        sector = st.text_input("Sector", key="sector_input")
        document_title = st.text_input("Document title", key="document_title_input")
        fiscal_year = st.text_input("Fiscal year", key="fiscal_year_input")
    with right:
        ticker_symbol = st.text_input("Ticker symbol", key="ticker_symbol_input")
        industry = st.text_input("Industry", key="industry_input")
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
        source_url = st.text_input("Official source URL (optional)")
    source_classification = st.selectbox(
        "Source classification",
        options=("public_source", "confidential_research"),
        help="Only upload confidential notes if authorized and the storage provider is approved for them.",
    )
    submitted = st.form_submit_button("🚀 Upload and start processing", use_container_width=True)

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
                    and result.existing_status in ("failed", "retry_wait", "cancelled")
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
                    and result.existing_status in ("queued", "retry_wait", "cancelled")
                ):
                    pipeline_result = run_pipeline_with_ui_progress(settings, result.job_id, progress)
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

if last_document_id:
    st.markdown('<div class="section-label">Step 3 · Results and review</div>', unsafe_allow_html=True)
    st.subheader("Processing and evidence review")
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
                        "{} / {} · {}".format(
                            candidate_lookup[candidate_id][1].get("framework", "TOE"),
                            candidate_lookup[candidate_id][1].get("dimension", "unknown").title(),
                            candidate_lookup[candidate_id][1]["question"][:80],
                        ),
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
                    "**Framework:** {} · **Category:** {} · **Source pages:** {}–{} · **Chunk:** {}".format(
                        candidate.get("framework", "TOE"),
                        candidate.get("dimension", "unknown").title(),
                        record["page_start"],
                        record["page_end"],
                        record["chunk_index"],
                    )
                )
                with st.expander("View original source chunk", expanded=True):
                    st.text_area(
                        "Source text",
                        value=record["chunk_text"],
                        height=280,
                        disabled=True,
                        label_visibility="collapsed",
                        key="source_preview_" + selected_candidate_id,
                    )
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
