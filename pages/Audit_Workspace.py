"""Human-vs-Ollama evidence audit workspace."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st

from cis_fin.audit_workspace import AuditWorkspaceError, audit_metrics, list_document_audit_reviews, save_evaluation
from cis_fin.company_queries import export_document_chunks, list_recent_documents
from cis_fin.qualitative_scoring import score_qualitative_evidence
from cis_fin.settings import Settings


@st.cache_data(ttl=30, max_entries=8, show_spinner=False)
def load_audit_metrics(settings):
    return audit_metrics(settings)


@st.cache_data(ttl=30, max_entries=8, show_spinner=False)
def load_recent_documents(settings):
    return list_recent_documents(settings, limit=100)


@st.cache_data(ttl=60, max_entries=20, show_spinner=False)
def load_document_chunks(settings, document_id):
    return export_document_chunks(settings, uuid.UUID(str(document_id)))


@st.cache_data(ttl=30, max_entries=8, show_spinner=False)
def load_document_audit_reviews(settings, document_id):
    return list_document_audit_reviews(settings, uuid.UUID(str(document_id)))

st.set_page_config(page_title="Evidence Audit Workspace", page_icon="🔎", layout="wide")
st.title("🔎 Evidence audit workspace")
st.caption("Compare Ollama scoring with an independent human evaluation. Every saved evaluation creates a database row and Markdown trace.")

if "authenticated_user" not in st.session_state:
    st.warning("Log in through the main researcher app before opening the audit workspace.")
    st.stop()
evaluator_identity = st.session_state["authenticated_user"]
st.info("Logged-in evaluator: {}".format(evaluator_identity))

try:
    settings = Settings.from_env()
    metrics = load_audit_metrics(settings)
except Exception as exc:
    metrics = {"total": 0, "exact": None, "within_one": None, "mae": None, "by_dimension": {}}
    st.warning("Audit metrics are unavailable until the database is configured: {}".format(exc))

cols = st.columns(4)
cols[0].metric("Evaluations", metrics["total"])
cols[1].metric("Exact agreement", "—" if metrics["exact"] is None else "{:.1%}".format(metrics["exact"]))
cols[2].metric("Agreement ±1", "—" if metrics["within_one"] is None else "{:.1%}".format(metrics["within_one"]))
cols[3].metric("Mean absolute error", "—" if metrics["mae"] is None else "{:.2f}".format(metrics["mae"]))

try:
    documents = load_recent_documents(settings)
except Exception as exc:
    documents = []
    st.error("Could not load completed documents: {}".format(exc))

completed = [row for row in documents if len(row) >= 5 and str(row[-1]).lower() == "completed"]
if not completed:
    st.info("No completed documents are available for audit.")
else:
    labels = ["{} · {} · {}".format(row[2], row[1] or "Untitled", row[-2] or "year unknown") for row in completed]
    selected_label = st.selectbox("Completed report", labels)
    selected_document = completed[labels.index(selected_label)]
    selected_document_id = str(selected_document[0])
    loaded_document_id = st.session_state.get("audit_loaded_document_id")
    if loaded_document_id != selected_document_id:
        records = []
        st.info("Choose a report, then load its evidence when you are ready to audit it.")
        if st.button("Load evidence", key="load_audit_evidence"):
            st.session_state["audit_loaded_document_id"] = selected_document_id
            st.rerun()
    else:
        try:
            records = load_document_chunks(settings, selected_document_id)
            saved_reviews = load_document_audit_reviews(settings, selected_document_id)
        except Exception as exc:
            records = []
            saved_reviews = {}
            st.error("Could not load evidence chunks: {}".format(exc))
    candidates = [(record, item) for record in records for item in record.get("question_candidates", [])]
    if loaded_document_id != selected_document_id:
        pass
    elif not candidates:
        st.info("This report has no stored question/evidence candidates yet.")
    else:
        def candidate_label(index):
            record, candidate = candidates[index]
            review = saved_reviews.get((str(candidate.get("question_id")), str(record.get("chunk_id"))))
            marker = "✅ REVIEWED · {} · ".format(review.get("review_status", "done")) if review else "⬜ NOT REVIEWED · "
            return "{}Pages {}–{} · {}".format(marker, record.get("page_start"), record.get("page_end"), candidate.get("question", "")[:100])

        selected_index = st.selectbox("Evidence evaluation", list(range(len(candidates))), format_func=candidate_label)
        record, candidate = candidates[selected_index]
        prior_review = saved_reviews.get((str(candidate.get("question_id")), str(record.get("chunk_id"))))
        if prior_review:
            st.success("Already reviewed: {}. Saving again will overwrite this review with the new decision.".format(prior_review.get("review_status", "done")))
        st.markdown("**Question:** {}".format(candidate.get("question", "")))
        st.markdown("**TOE dimension:** {} · **Pages:** {}–{} · **Chunk ID:** {}".format(candidate.get("dimension", "unknown").title(), record.get("page_start"), record.get("page_end"), record.get("chunk_id")))
        st.text_area("Source evidence", record.get("chunk_text", ""), height=260, disabled=True)
        score_key = "audit_score_{}".format(candidate.get("question_id"))
        if st.button("Score selected evidence with Ollama", key="score_" + str(selected_index)):
            try:
                result = score_qualitative_evidence(settings, candidate.get("question", ""), record.get("chunk_text", ""), record.get("page_start"), candidate.get("dimension", "unknown"))
                result["model"] = settings.ollama_model
                st.session_state[score_key] = result
            except Exception as exc:
                st.error("Ollama scoring failed: {}".format(exc))
        ollama = st.session_state.get(score_key)
        if ollama:
            st.info("Ollama score: {}/5 · confidence: {} · {}".format(ollama.get("score"), ollama.get("confidence"), ollama.get("rationale", "")))
            with st.form("human_audit_form_" + str(selected_index)):
                human_score = st.number_input("Human/evaluator score", min_value=0, max_value=5, value=int(prior_review.get("human_score", ollama.get("score", 0))) if prior_review else int(ollama.get("score", 0)), step=1)
                human_rationale = st.text_area("Human rationale", value=prior_review.get("human_rationale", "") if prior_review else "", placeholder="Explain why this score is correct based only on the source evidence.")
                statuses = ("accepted", "corrected", "rejected")
                status = st.selectbox("Review status", statuses, index=statuses.index(prior_review.get("review_status")) if prior_review and prior_review.get("review_status") in statuses else 0)
                save = st.form_submit_button("Overwrite review" if prior_review else "Save evaluation and audit trace")
            if save:
                if not human_rationale.strip():
                    st.error("Human rationale is required.")
                else:
                    try:
                        path = save_evaluation(settings, selected_document[0], selected_document[2], candidate["question_id"], candidate.get("question", ""), candidate.get("dimension", "unknown"), record, ollama, human_score, human_rationale, evaluator_identity, status)
                        load_document_audit_reviews.clear()
                        st.success("Saved evaluation and trace: {}".format(path))
                        st.rerun()
                    except AuditWorkspaceError as exc:
                        st.error(str(exc))

if metrics["by_dimension"]:
    st.subheader("Error by TOE dimension")
    st.dataframe([{"Dimension": key, **value} for key, value in metrics["by_dimension"].items()], hide_index=True, use_container_width=True)
