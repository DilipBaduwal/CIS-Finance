"""Previous work dashboard for the Streamlit multi-page app."""

import streamlit as st
import uuid
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cis_fin.auth import authenticate, configured_users
from cis_fin.company_queries import CompanyLookupError, list_recent_documents
from cis_fin.company_queries import export_document_chunks
from cis_fin.settings import Settings


st.set_page_config(page_title="Previous Work · CIS-FIN", page_icon="📂", layout="wide")
st.markdown(
    """
    <style>
    .block-container { max-width: 1600px; padding-top: 2.5rem; }
    .hero { padding: 1.6rem 2rem; border-radius: 18px; background: linear-gradient(135deg,#172554,#0f766e); color: white; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "authenticated_user" not in st.session_state:
    st.markdown('<div class="hero"><h1>📂 Previous work</h1><p>Sign in to view saved company research.</p></div>', unsafe_allow_html=True)
    with st.form("previous_work_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", use_container_width=True):
            if authenticate(username, password):
                st.session_state["authenticated_user"] = username.strip()
                st.rerun()
            st.error("Invalid username or password.")
    st.stop()

st.markdown('<div class="hero"><h1>📂 Previous work</h1><p>Browse completed reports and continue reviewing evidence.</p></div>', unsafe_allow_html=True)
settings = Settings.from_env()
try:
    documents = list_recent_documents(settings)
except CompanyLookupError as exc:
    st.error("Could not load previous work: {}".format(exc))
    documents = []

if not documents:
    st.info("No saved company work is available yet. Upload a report from the New Document page.")
else:
    selected_id = st.session_state.get("previous_work_document_id")
    if selected_id:
        selected = next((row for row in documents if str(row[0]) == str(selected_id)), None)
        if st.button("← Back to previous work", type="secondary"):
            st.session_state.pop("previous_work_document_id", None)
            st.rerun()
        if selected:
            document_id, company, title, year, status = selected
            st.title(title or "Untitled report")
            st.caption("{} · Fiscal year: {} · Status: {}".format(company or "Unknown company", year or "not set", status))
            try:
                records = export_document_chunks(settings, uuid.UUID(str(document_id)))
            except (CompanyLookupError, ValueError) as exc:
                st.error("Could not load document details: {}".format(exc))
                records = []
            if records:
                page_start = min(record["page_start"] for record in records)
                page_end = max(record["page_end"] for record in records)
                question_count = sum(len(record.get("question_candidates", [])) for record in records)
                metrics = st.columns(3)
                metrics[0].metric("Pages chunked", "{}–{}".format(page_start, page_end))
                metrics[1].metric("Stored chunks", len(records))
                metrics[2].metric("Questions generated", question_count)
                st.markdown("### Chunks and questions")
                for record in records:
                    questions = record.get("question_candidates", [])
                    label = "Chunk {} · pages {}–{} · {} question{}".format(
                        record["chunk_index"], record["page_start"], record["page_end"],
                        len(questions), "" if len(questions) == 1 else "s",
                    )
                    with st.expander(label, expanded=False):
                        st.caption("Source pages {}–{}".format(record["page_start"], record["page_end"]))
                        st.text_area(
                            "Chunk text",
                            value=record["chunk_text"],
                            height=180,
                            disabled=True,
                            key="previous_chunk_{}_{}".format(document_id, record["chunk_index"]),
                        )
                        if not questions:
                            st.info("No question was generated for this chunk.")
                        for number, question in enumerate(questions, 1):
                            framework = question.get("framework", "TOE")
                            dimension = question.get("dimension", "unknown").title()
                            st.markdown("**Question {} · {} / {}**".format(number, framework, dimension))
                            st.write(question["question"])
                            st.caption(
                                "Evidence: {} · Review: {}".format(
                                    question.get("validation_status", "unknown"),
                                    question.get("review_status", "pending"),
                                )
                            )
                            st.info(question.get("evidence_quote", "No evidence quotation saved."))
            else:
                st.info("No chunk data is available for this document yet.")
    else:
        st.write("{} saved document{} · Select one to inspect its chunks and questions.".format(len(documents), "" if len(documents) == 1 else "s"))
        for document_id, company, title, year, status in documents:
            with st.container(border=True):
                left, right = st.columns([4, 1])
                with left:
                    st.subheader(company or "Unknown company")
                    st.write(title or "Untitled report")
                    st.caption("Fiscal year: {} · Status: {} · ID: {}".format(year or "not set", status, str(document_id)[:8]))
                with right:
                    if st.button("Open report", key="open_previous_" + str(document_id), use_container_width=True):
                        st.session_state["previous_work_document_id"] = str(document_id)
                        st.rerun()
