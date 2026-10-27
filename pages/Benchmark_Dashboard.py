"""Client-facing AI-readiness benchmark dashboard."""
import json
import uuid
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import streamlit as st

from cis_fin.cohort_update import update_cohort
from cis_fin.automatic_features import build_features_from_document, score_document_themes_with_llm
from cis_fin.settings import Settings
from cis_fin.company_queries import CompanyLookupError, export_document_chunks, list_recent_documents
from cis_fin.benchmark_update import generate_review_candidates, approve_candidates
from cis_fin.benchmark_reporting import cohort_summary, question_comparison, score_deltas
from cis_fin.benchmark_store import persist_benchmark_version, persist_benchmark_approval

st.set_page_config(page_title="Benchmark Dashboard", page_icon="📈", layout="wide")
st.title("📈 AI-readiness benchmark")
st.caption("Information Technology · 2025–2026 cohort")

version_paths = sorted(Path("artifacts").glob("benchmark_it_2025-2026_v*.json"))
version_path = version_paths[-1] if version_paths else None
path = Path("artifacts/it_ai_readiness_benchmark_2025-2026.final.json")
if not path.exists():
    path = Path("artifacts/it_ai_readiness_benchmark_2025-2026.cleaned.json")
if not path.exists():
    st.info("The benchmark artifact is not available yet.")
    st.stop()

data = json.loads(path.read_text(encoding="utf-8"))
version_data = json.loads(version_path.read_text(encoding="utf-8")) if version_path else {}
companies = version_data.get("companies") or data.get("companies", ["TCS", "INFY", "WIPRO"])
themes = data.get("themes", [])
score_path = Path("artifacts/it_ai_readiness_pilot_regression.json")
versioned_scores = sorted(Path("artifacts").glob("it_ai_readiness_pilot_regression_v*.json"))
if versioned_scores:
    score_path = versioned_scores[-1]
score_data = json.loads(score_path.read_text(encoding="utf-8")) if score_path.exists() else {}
if version_data:
    st.success("Benchmark {} loaded · cohort: {}".format(version_data["version"], " · ".join(companies)))
    st.caption("Benchmark ID: {} · Previous versions remain immutable.".format(version_data["benchmark_id"]))
else:
    st.success("Cohort loaded: {}".format(" · ".join(companies)))

metrics = st.columns(4)
metrics[0].metric("Companies", len(companies))
metrics[1].metric("Source chunks", data.get("source_chunk_count", "—"))
metrics[2].metric("TOE themes", len(themes))
metrics[3].metric("Regression", "Pilot active" if score_data else "Ready after features")

if version_data:
    with st.expander("Benchmark version history and update rules"):
        st.write("Current version: **{}** (v{}).".format(version_data["version"], version_data["version_number"]))
        st.write("Adding a same-industry, same-year company creates the next version and recalculates cohort scores. Existing questions are carried forward unless a new recurring theme is reviewed and approved.")
        history = []
        for candidate in version_paths:
            item = json.loads(candidate.read_text(encoding="utf-8"))
            history.append({"Version": item.get("version"), "Companies": len(item.get("companies", [])), "Themes": len(item.get("themes", [])), "Benchmark ID": item.get("benchmark_id")})
        st.dataframe(history, use_container_width=True, hide_index=True)

    matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
    targets_path = Path("artifacts/it_ai_readiness_provisional_targets.json")
    if matrix_path.exists() and targets_path.exists():
        with st.expander("Add a reviewed company to this cohort"):
            st.caption("Use this control after the new report has been chunked and its TOE questions have been reviewed. Values are 0–5 per theme; this creates the next immutable benchmark version.")
            matrix_data = json.loads(matrix_path.read_text(encoding="utf-8"))
            feature_names = matrix_data.get("feature_names", [])
            saved_records = st.session_state.get("benchmark_saved_records", [])
            try:
                saved_documents = list_recent_documents(Settings.from_env(), limit=100)
            except CompanyLookupError:
                saved_documents = []
            if saved_documents:
                labels = ["{} · {} · {}".format(row[2], row[1] or "Untitled", row[3] or "year unknown") for row in saved_documents]
                selected_label = st.selectbox("Use a processed report already in the database", ["Choose a report"] + labels)
                if selected_label != "Choose a report" and st.button("Load saved chunks", key="load_saved_benchmark_report"):
                    selected_row = saved_documents[labels.index(selected_label)]
                    try:
                        saved_records = export_document_chunks(Settings.from_env(), uuid.UUID(str(selected_row[0])))
                        st.session_state["benchmark_saved_records"] = saved_records
                        st.session_state["benchmark_new_company"] = selected_row[2]
                        st.success("Loaded {} saved chunks from {}.".format(len(saved_records), selected_row[2]))
                        st.rerun()
                    except (CompanyLookupError, ValueError) as exc:
                        st.error("Could not load saved report: {}".format(exc))
            if saved_records:
                st.info("Using {} chunks loaded directly from the database; no JSON re-upload is required.".format(len(saved_records)))
                if st.button("Score saved report with local Ollama", key="score_saved_benchmark_report"):
                    with st.spinner("Ollama is scoring saved evidence…"):
                        try:
                            st.session_state["benchmark_automatic_values"] = score_document_themes_with_llm(Settings.from_env(), themes, saved_records)
                            st.success("Saved report scored. Review the values below.")
                        except Exception as exc:
                            st.error("Ollama scoring failed: {}".format(exc))
                candidate_path = Path("artifacts/benchmark_theme_candidates.json")
                if st.button("Find recurring themes for review", key="discover_benchmark_themes"):
                    with st.spinner("Comparing the new report with existing benchmark themes…"):
                        try:
                            generate_review_candidates(Settings.from_env(), themes, saved_records, candidate_path)
                            st.success("Theme candidates generated. Review them below before approving.")
                        except Exception as exc:
                            st.error("Theme discovery failed: {}".format(exc))
                if candidate_path.exists():
                    candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
                    candidates = candidate_data.get("candidates", [])
                    if candidates:
                        st.markdown("#### Review recurring-theme candidates")
                        st.caption("Candidates never change the benchmark automatically. Select only questions that are genuinely recurring and comparable across companies.")
                        approved_indexes = []
                        for index, candidate in enumerate(candidates):
                            label = "{} · confidence {}% · {}".format(candidate.get("question") or candidate.get("theme", "Unnamed theme"), int(float(candidate.get("confidence", 0)) * 100), "auto-eligible" if candidate.get("auto_eligible") else "manual review")
                            if st.checkbox(label, key="approve_theme_{}".format(index)):
                                approved_indexes.append(index)
                            st.caption("Status: {} · Supporting chunks: {}".format(candidate.get("question_status", "needs_review"), len(candidate.get("chunk_ids", []))))
                        if approved_indexes and st.button("Approve selected themes and create next version", key="approve_benchmark_themes"):
                            next_number = int(version_data.get("version_number", 1)) + 1
                            try:
                                created = approve_candidates(
                                    version_path,
                                    candidate_path,
                                    approved_indexes,
                                    Path("artifacts/benchmark_it_2025-2026_v{}.json".format(next_number)),
                                )
                                try:
                                    persist_benchmark_version(Settings.from_env(), created, st.session_state.get("authenticated_user"))
                                    for approved_index in approved_indexes:
                                        persist_benchmark_approval(Settings.from_env(), created["benchmark_id"], approved_index, candidates[approved_index], "approved", st.session_state.get("authenticated_user"))
                                except Exception as persistence_error:
                                    st.warning("Version file created, but PostgreSQL persistence needs attention: {}".format(persistence_error))
                                st.success("Created benchmark v{} with {} approved new themes.".format(next_number, len(approved_indexes)))
                                st.rerun()
                            except (ValueError, KeyError, OSError) as exc:
                                st.error("Could not create benchmark version: {}".format(exc))
                    else:
                        st.info("No recurring-theme candidates were found in the loaded report.")
            evidence_upload = st.file_uploader("Optional: upload the processed evidence JSON", type=["json"], key="benchmark_evidence_json")
            automatic_values = {}
            if evidence_upload is not None:
                try:
                    evidence_payload = json.loads(evidence_upload.getvalue().decode("utf-8"))
                    evidence_records = evidence_payload if isinstance(evidence_payload, list) else evidence_payload.get("records", evidence_payload.get("chunks", []))
                    automatic_values = build_features_from_document(themes, evidence_records)
                    st.info("Automatically matched {} benchmark themes from {} saved chunks. Review the values before creating the next version.".format(len(automatic_values), len(evidence_records)))
                    if st.button("Score matched evidence with local Ollama", key="score_benchmark_evidence"):
                        with st.spinner("Ollama is scoring matched evidence…"):
                            try:
                                automatic_values = score_document_themes_with_llm(Settings.from_env(), themes, evidence_records)
                                st.session_state["benchmark_automatic_values"] = automatic_values
                                st.success("Local Ollama scoring completed. Review the scores below.")
                            except Exception as exc:
                                st.error("Ollama scoring failed; deterministic suggestions remain available. {}".format(exc))
                    automatic_values = st.session_state.get("benchmark_automatic_values", automatic_values)
                except (ValueError, UnicodeDecodeError, AttributeError) as exc:
                    st.error("Could not read evidence JSON: {}".format(exc))
            with st.form("add_cohort_company"):
                new_company = st.text_input("Company symbol", value=st.session_state.get("benchmark_new_company", ""), placeholder="Example: HCLTECH").strip().upper()
                new_industry = st.text_input("Industry", value=version_data.get("industry", "Information Technology"))
                new_year = st.text_input("Fiscal year", value=version_data.get("fiscal_year", "2025-2026"))
                st.write("Reviewed theme scores")
                feature_values = {}
                columns = st.columns(3)
                for index, feature_name in enumerate(feature_names):
                    with columns[index % 3]:
                        suggested = float(automatic_values.get(feature_name, {}).get("score", 0.0))
                        feature_values[feature_name] = st.number_input(feature_name, min_value=0.0, max_value=5.0, value=suggested, step=0.5, key="new_" + feature_name)
                submitted = st.form_submit_button("Create next benchmark version")
            if submitted:
                if not new_company:
                    st.error("Enter a company symbol first.")
                elif not feature_names:
                    st.error("No reviewed benchmark features are available yet.")
                else:
                    next_number = int(version_data.get("version_number", 1)) + 1
                    stem = "artifacts/it_ai_readiness_pilot_regression_v{}".format(next_number)
                    try:
                        result = update_cohort(
                            version_path, matrix_path, targets_path, score_path,
                            new_company, new_industry, new_year, feature_values,
                            Path("artifacts/benchmark_it_2025-2026_v{}.json".format(next_number)),
                            Path("artifacts/it_ai_readiness_regression_matrix_v{}.json".format(next_number)),
                            Path("artifacts/it_ai_readiness_provisional_targets_v{}.json".format(next_number)),
                            Path(stem + ".json"),
                        )
                        try:
                            persist_benchmark_version(Settings.from_env(), result["version"], st.session_state.get("authenticated_user"))
                        except Exception as persistence_error:
                            st.warning("Version file created, but PostgreSQL persistence needs attention: {}".format(persistence_error))
                        st.success("Created benchmark v{} with {} companies and recalculated provisional scores.".format(next_number, len(result["version"]["companies"])))
                        st.rerun()
                    except (ValueError, KeyError, OSError) as exc:
                        st.error(str(exc))

if score_data:
    st.warning("These are provisional pilot regression scores and must be replaced with reviewed targets before production use.")
    st.subheader("Pilot company scores")
    st.dataframe(score_data.get("predictions", []), use_container_width=True, hide_index=True)
    summary = cohort_summary(score_data)
    average = summary.get("average")
    if average is not None:
        st.metric("Industry average score", average)
    st.subheader("Company ranking")
    st.dataframe(summary["ranked"], use_container_width=True, hide_index=True)

    if len(versioned_scores) >= 2:
        st.subheader("Score changes from previous benchmark version")
        st.dataframe(score_deltas(versioned_scores[-2], versioned_scores[-1]), use_container_width=True, hide_index=True)

matrix_report_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
if matrix_report_path.exists():
    comparisons = question_comparison(matrix_report_path)
    st.subheader("TOE question-level comparison")
    question_labels = [item["question"] for item in comparisons]
    if question_labels:
        selected_question = st.selectbox("Select a benchmark question/theme", question_labels)
        selected_comparison = comparisons[question_labels.index(selected_question)]
        st.metric("Industry average for selected theme", selected_comparison["industry_average"] if selected_comparison["industry_average"] is not None else "—")
        st.dataframe(selected_comparison["companies"], use_container_width=True, hide_index=True)

if not data.get("scores"):
    st.info(
        "Numeric scores are not shown yet because comparable metric extraction has not been completed. "
        "The dashboard will calculate them after the LLM extracts reviewed values from the evidence."
    )

for dimension in ("technology", "organization", "environment"):
    items = [item for item in themes if item.get("toe_dimension") == dimension]
    with st.expander("{} · {} themes".format(dimension.title(), len(items)), expanded=True):
        for item in items:
            st.markdown("### {}".format(item.get("theme", "AI-readiness theme")))
            st.write(item.get("question", "Question pending review."))
            st.caption(
                "Supporting companies: {} · Support count: {} · Status: {}".format(
                    ", ".join(item.get("supporting_companies", [])),
                    item.get("support_count", "—"),
                    item.get("question_status", "needs review"),
                )
            )
