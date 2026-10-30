"""Client-facing AI-readiness benchmark dashboard."""
import json
import uuid
import sys
import time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import streamlit as st

from cis_fin.cohort_update import update_cohort
from cis_fin.automatic_features import build_features_from_document, score_document_themes_with_llm
from cis_fin.settings import Settings
from cis_fin.company_queries import CompanyLookupError, export_document_chunks, list_recent_documents
from cis_fin.benchmark_update import generate_review_candidates, approve_candidates, required_company_support
from cis_fin.benchmark_reporting import cohort_summary, question_comparison, score_deltas
from cis_fin.benchmark_store import persist_benchmark_version, persist_benchmark_approval
from cis_fin.benchmark import normalize_industry
from cis_fin.pilot_regression import train_pilot

st.set_page_config(page_title="Benchmark Dashboard", page_icon="📈", layout="wide")
st.title("📈 AI-readiness benchmark")
st.caption("Information Technology · 2025–2026 cohort")

version_paths = []
for candidate in sorted(Path("artifacts").glob("benchmark_it_2025-2026_v*.json")):
    candidate_data = json.loads(candidate.read_text(encoding="utf-8"))
    # Ignore historical versions created before industry validation existed.
    if any(company in {"TATASTEEL", "TRENT"} for company in candidate_data.get("companies", [])):
        continue
    version_paths.append(candidate)
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


def auto_add_completed_companies():
    """Automatically score completed reports and add new cohort members."""
    if not version_path or not themes:
        return False, []
    try:
        documents = list_recent_documents(Settings.from_env(), limit=100)
    except CompanyLookupError:
        return False, ["Could not load completed documents from the database."]
    matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
    targets_path = Path("artifacts/it_ai_readiness_provisional_targets.json")
    score_path = Path("artifacts/it_ai_readiness_pilot_regression.json")
    if not (matrix_path.exists() and targets_path.exists() and score_path.exists()):
        return False, ["Required regression artifacts are missing."]
    current_version = version_data
    current_version_path = version_path
    changed = False
    failures = []
    for document in documents:
        # list_recent_documents returns: document_id, title, symbol, year, status
        if str(document[5] or "").lower() != "completed":
            continue
        symbol = str(document[2] or "").strip().upper()
        document_industry = str(document[3] or "").strip()
        year = document[4] or ""
        identity = symbol
        if not identity or identity in current_version.get("companies", []):
            continue
        if normalize_industry(document_industry) != normalize_industry(current_version.get("industry", "")):
            continue
        try:
            records = export_document_chunks(Settings.from_env(), uuid.UUID(str(document[0])))
            scored = score_document_themes_with_llm(Settings.from_env(), themes, records)
            values = {name: item.get("score") for name, item in scored.items() if item.get("score") is not None}
            if not values:
                continue
            next_number = int(current_version.get("version_number", 1)) + 1
            result = update_cohort(
                current_version_path, matrix_path, targets_path, score_path,
                identity, current_version.get("industry", "Information Technology"), year or current_version.get("fiscal_year", "2025-2026"), values,
                Path("artifacts/benchmark_it_2025-2026_v{}.json".format(next_number)),
                Path("artifacts/it_ai_readiness_regression_matrix_v{}.json".format(next_number)),
                Path("artifacts/it_ai_readiness_provisional_targets_v{}.json".format(next_number)),
                Path("artifacts/it_ai_readiness_pilot_regression_v{}.json".format(next_number)),
            )
            current_version = result["version"]
            current_version_path = Path("artifacts/benchmark_it_2025-2026_v{}.json".format(next_number))
            matrix_path = Path("artifacts/it_ai_readiness_regression_matrix_v{}.json".format(next_number))
            targets_path = Path("artifacts/it_ai_readiness_provisional_targets_v{}.json".format(next_number))
            score_path = Path("artifacts/it_ai_readiness_pilot_regression_v{}.json".format(next_number))
            changed = True
        except Exception as exc:
            failures.append("{}: {}".format(identity, exc))
            continue
    return changed, failures


if version_data:
    # Re-check on every dashboard rerun so a report that finishes processing
    # after the dashboard was opened is picked up without a manual action.
    training_started = time.perf_counter()
    training_started_at = datetime.now().astimezone()
    with st.status("Automatic benchmark training in progress…", expanded=True) as training_status:
        st.write("Started: {}".format(training_started_at.strftime("%Y-%m-%d %H:%M:%S %Z")))
        st.write("Completed companies are trained sequentially; the next company starts after the current run finishes.")
        automatic_changed, automatic_failures = auto_add_completed_companies()
        training_elapsed = time.perf_counter() - training_started
        training_finished_at = datetime.now().astimezone()
        st.write("Elapsed: {:.1f} seconds".format(training_elapsed))
        st.write("Finished: {}".format(training_finished_at.strftime("%Y-%m-%d %H:%M:%S %Z")))
        training_status.update(
            label="Automatic benchmark training completed in {:.1f} seconds".format(training_elapsed),
            state="complete",
            expanded=False,
        )
    st.session_state["last_training_elapsed"] = training_elapsed
    st.session_state["last_training_finished_at"] = training_finished_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    for failure in automatic_failures:
        st.error("Automatic benchmark enrollment failed — {}".format(failure))
    if automatic_changed:
        st.success("Completed company reports were automatically scored and added to the benchmark.")
        st.rerun()
score_path = Path("artifacts/it_ai_readiness_pilot_regression.json")
versioned_scores = []
for candidate in sorted(Path("artifacts").glob("it_ai_readiness_pilot_regression_v*.json")):
    try:
        version_number = int(candidate.stem.rsplit("_v", 1)[-1])
    except ValueError:
        continue
    if version_data and version_number <= int(version_data.get("version_number", 0)):
        versioned_scores.append(candidate)
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
    with st.container(border=True):
        st.subheader("Re-score the complete cohort with one consistent method")
        st.caption("This uses the same Ollama rubric for every company and does not use deterministic fallback scores.")
        active_matrix_path = Path("artifacts/it_ai_readiness_regression_matrix_v{}.json".format(version_data.get("version_number")))
        if not active_matrix_path.exists():
            active_matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
        active_matrix = json.loads(active_matrix_path.read_text(encoding="utf-8"))
        individual_company = st.selectbox(
            "Re-score one company only",
            [row.get("company") for row in active_matrix.get("rows", [])],
            key="individual_rescore_company",
        )
        if st.button("Re-score selected company", key="rescore_selected_company"):
            try:
                settings = Settings.from_env()
                documents = list_recent_documents(settings, limit=100)
                document = next((row for row in documents if str(row[2]).strip().upper() == str(individual_company).upper() and str(row[5]).lower() == "completed"), None)
                if not document:
                    raise RuntimeError("No completed report found for {}.".format(individual_company))
                matrix_path = active_matrix_path
                matrix = active_matrix
                target_row = next(row for row in matrix["rows"] if row.get("company") == individual_company)
                records = export_document_chunks(settings, uuid.UUID(str(document[0])))
                with st.spinner("Strictly re-scoring {}…".format(individual_company)):
                    scored = score_document_themes_with_llm(settings, themes, records, strict=True)
                for name in matrix["feature_names"]:
                    target_row.setdefault("features", {})[name] = float(scored[name]["score"])
                target_row["feature_vector"] = [target_row["features"].get(name) for name in matrix["feature_names"]]
                matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
                versioned_matrix_path = Path("artifacts/it_ai_readiness_regression_matrix_v{}.json".format(version_data.get("version_number")))
                if versioned_matrix_path.exists():
                    versioned_matrix = json.loads(versioned_matrix_path.read_text(encoding="utf-8"))
                    versioned_row = next((row for row in versioned_matrix.get("rows", []) if row.get("company") == individual_company), None)
                    if versioned_row is not None:
                        versioned_row["features"] = dict(target_row["features"])
                        versioned_row["feature_vector"] = list(target_row["feature_vector"])
                        versioned_matrix_path.write_text(json.dumps(versioned_matrix, ensure_ascii=False, indent=2), encoding="utf-8")
                targets_path = Path("artifacts/it_ai_readiness_provisional_targets.json")
                target_rows = []
                for row in matrix["rows"]:
                    values = [float(value) for value in row.get("feature_vector", []) if value is not None]
                    if not values:
                        raise RuntimeError("No scored factors are available for {}.".format(row.get("company")))
                    target_rows.append({"company": row["company"], "target_score": round(sum(values) / len(values) * 20, 1), "provisional": True})
                targets = {"targets": target_rows, "provisional": True}
                targets_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
                train_pilot(matrix_path, targets_path, Path("artifacts/it_ai_readiness_pilot_regression.json"))
                st.success("{} was re-scored successfully and the pilot model was retrained.".format(individual_company))
                st.rerun()
            except Exception as exc:
                st.error("Individual re-scoring failed: {}".format(exc))
        if st.button("Re-score all companies", key="rescore_entire_cohort"):
            try:
                settings = Settings.from_env()
                documents = list_recent_documents(settings, limit=100)
                matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
                matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
                by_company = {str(row[2]).strip().upper(): row for row in documents if len(row) > 5 and str(row[5]).lower() == "completed"}
                rescored = 0
                with st.status("Strict cohort re-scoring in progress…", expanded=True) as status:
                    for row in matrix.get("rows", []):
                        company = str(row.get("company", "")).upper()
                        document = by_company.get(company)
                        if not document:
                            raise RuntimeError("No completed report found for {}.".format(company))
                        records = export_document_chunks(settings, uuid.UUID(str(document[0])))
                        scored = score_document_themes_with_llm(settings, themes, records, strict=True)
                        for name in matrix.get("feature_names", []):
                            row.setdefault("features", {})[name] = float(scored[name]["score"])
                        row["feature_vector"] = [row["features"].get(name) for name in matrix.get("feature_names", [])]
                        rescored += 1
                        status.write("Re-scored {}".format(company))
                    matrix["training_ready"] = False
                    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
                    target_rows = []
                    for row in matrix["rows"]:
                        values = [float(value) for value in row.get("feature_vector", []) if value is not None]
                        if not values:
                            raise RuntimeError("No scored factors are available for {}.".format(row.get("company")))
                        target_rows.append({"company": row["company"], "target_score": round(sum(values) / len(values) * 20, 1), "provisional": True})
                    targets = {"targets": target_rows, "provisional": True}
                    targets_path = Path("artifacts/it_ai_readiness_provisional_targets.json")
                    targets_path.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
                    train_pilot(matrix_path, targets_path, Path("artifacts/it_ai_readiness_pilot_regression.json"))
                    status.update(label="Re-scored {} companies consistently".format(rescored), state="complete", expanded=False)
                st.success("All cohort scores were replaced using the same strict Ollama scoring path.")
                st.rerun()
            except Exception as exc:
                st.error("Cohort re-scoring stopped without partial completion: {}".format(exc))

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
                labels = ["{} · {} · {}".format(row[2], row[1] or "Untitled", row[4] or "year unknown") for row in saved_documents]
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
                            generate_review_candidates(Settings.from_env(), themes, saved_records, candidate_path, cohort_size=len(companies) + 1)
                            candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
                            candidates = candidate_data.get("candidates", [])
                            eligible = [index for index, candidate in enumerate(candidates) if candidate.get("auto_eligible") and candidate.get("confidence", 0) >= 0.70]
                            if eligible:
                                next_number = int(version_data.get("version_number", 1)) + 1
                                created = approve_candidates(
                                    version_path,
                                    candidate_path,
                                    eligible,
                                    Path("artifacts/benchmark_it_2025-2026_v{}.json".format(next_number)),
                                )
                                persist_benchmark_version(Settings.from_env(), created, st.session_state.get("authenticated_user"))
                                candidate_path.unlink(missing_ok=True)
                                st.success("Automatically approved {} strong recurring themes and created benchmark v{}.".format(len(eligible), next_number))
                                st.rerun()
                            else:
                                st.info("No candidate met the automatic majority-support and quality rules.")
                        except Exception as exc:
                            st.error("Automatic theme discovery failed: {}".format(exc))
                if candidate_path.exists():
                    candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
                    candidates = candidate_data.get("candidates", [])
                    if candidates:
                        st.markdown("#### Recurring-theme candidates")
                        st.caption("Strong candidates are approved automatically when they meet the majority-support and question-quality rules.")
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
