"""Separate client-facing CIS-FIN benchmark application.

Run with: ``streamlit run client_app.py``
"""
import json
import csv
import io
import sys
import tempfile
import re
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import streamlit as st

from cis_fin.automatic_features import score_document_themes_with_llm
from cis_fin.benchmark import normalize_industry, normalize_fiscal_year
from cis_fin.extraction import extract_pdf, iter_chunks
from cis_fin.settings import Settings
from cis_fin.company_queries import export_document_chunks, export_chunk_questions_scores, list_recent_documents, CompanyLookupError


st.set_page_config(page_title="CIS-FIN Client Benchmark", page_icon="📊", layout="wide")
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] { background: #f5f7fb; color: #172033; }
html, body, [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] * { font-family: "Times New Roman", Times, serif !important; }
[data-testid="stAppViewContainer"] { color: #000000 !important; }
[data-testid="stHeader"] { background: #f5f7fb; }
section[data-testid="stSidebar"] { display: none !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label, [data-testid="stMarkdownContainer"] p { color: #000000 !important; }
[data-testid="stTextInput"] input, [data-baseweb="select"] > div { background: #ffffff !important; color: #172033 !important; border: 1px solid #dce3ef !important; border-radius: 10px !important; }
[data-testid="stFileUploader"] section { background: #ffffff !important; border: 1px solid #c9c5ba !important; }
[data-testid="stRadio"] label { color: #000000 !important; }
[data-testid="stAlert"] p { color: #263746 !important; }
.stButton > button { background: #2457d6 !important; color: #ffffff !important; border-radius: 10px; border: 0; padding: .65rem 1.2rem; font-weight: 650; }
.stButton > button p, .stButton > button span, .stButton > button div { color: #ffffff !important; }
.stButton > button:hover { background: #173ea7 !important; color: #ffffff !important; }
.stButton > button[kind="secondary"] { background: #ffffff !important; color: #2457d6 !important; border: 1px solid #cdd8ee; }
.step-strip { display:flex; gap:.8rem; margin:1rem 0 1.25rem; }
.step { flex:1; background:#fff; border:1px solid #e1e7f1; border-radius:14px; padding:.8rem 1rem; color:#62708a; box-shadow: 0 5px 18px #243b6b0a; }
.step strong { display:block; color:#172033; margin-bottom:.2rem; }
.helper { background:#edf3ff; border-radius:14px; padding:1rem 1.2rem; color:#31518b; margin:1rem 0; }
.block-container { max-width: 1380px; padding: 2rem 2.5rem 5rem; }
h1, h2, h3 { font-family: "Times New Roman", Times, serif !important; color: #000000; letter-spacing: -.02em; }
.hero { background: linear-gradient(135deg,#14264d 0%,#2457d6 100%); padding: 2.2rem 2.3rem; border-radius: 22px; color:#fff; box-shadow: 0 16px 40px #1e3b7a25; }
.hero h1 { color:#fff; font-size: 3.15rem; margin: 0; letter-spacing: -2px; }
.hero p { color: #dce7ff !important; font-size: 1.08rem; margin: .45rem 0 0; }
.card { background: #fff; border: 1px solid #e1e7f1; border-radius: 18px; padding: 1.35rem 1.5rem; box-shadow: 0 8px 24px #243b6b0d; min-height: 150px; }
.snapshot { height: 190px; display:flex; flex-direction:column; justify-content:center; box-sizing:border-box; }
.eyebrow { color: #62708a; font-size: .8rem; text-transform: uppercase; letter-spacing: .09em; font-weight: 700; }
.big-score { font: 4.8rem Inter, sans-serif; font-weight: 750; color:#173ea7; line-height: 1; margin-top: .45rem; }
.muted { color: #000000; }
.section { background: #fff; border: 1px solid #e1e7f1; border-radius: 18px; padding: 1.35rem 1.5rem; margin-top: 1.25rem; box-shadow: 0 8px 24px #243b6b0d; }
.factor { display: grid; grid-template-columns: 27% 53% 20%; align-items: center; gap: 1rem; padding: .8rem 0; border-top: 1px solid #ebe8df; }
.bar { height: 11px; background: #e7edf7; border-radius: 99px; overflow: hidden; }
.fill { height: 100%; border-radius: 99px; background: #2457d6; }
.factor-value { font-size: 1.05rem; text-align: right; }
.subhead { font-weight: 700; margin: 1.2rem 0 .3rem; font-size: 1.1rem; }
.hero-kicker { text-transform: uppercase; letter-spacing: .12em; font-size: .75rem; color: #32648f; font-weight: 700; }
.insight { background: #eef4f7; border-left: 4px solid #32648f; border-radius: 12px; padding: .9rem 1.1rem; color: #3e596b; }
.score-label { color:#000000; font-size:.9rem; text-transform:uppercase; letter-spacing:.08em; }
@media (prefers-color-scheme: dark) {
  html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] { background:#0f172a !important; color:#e5e7eb !important; }
  [data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label, [data-testid="stMarkdownContainer"] p, .muted, .score-label { color:#b8c3d6 !important; }
  h1, h2, h3, [data-testid="stMarkdownContainer"] strong, [data-testid="stMarkdownContainer"] b { color:#f3f6fb !important; }
  .step, .card, .section { background:#172033 !important; border-color:#334155 !important; color:#e5e7eb !important; }
  .step strong, .card h2, .card h3, .card b { color:#f3f6fb !important; }
  [data-testid="stTextInput"] input, [data-baseweb="select"] > div { background:#172033 !important; color:#f3f6fb !important; border-color:#475569 !important; }
  [data-testid="stRadio"] label, [data-testid="stAlert"] p { color:#dbe4f2 !important; }
  .bar { background:#334155 !important; }
  .stButton > button, .stButton > button[kind="secondary"] { background:#2457d6 !important; color:#ffffff !important; border-color:#2457d6 !important; }
  .stButton > button p, .stButton > button span, .stButton > button div { color:#ffffff !important; }
}
[data-theme="dark"] [data-testid="stAppViewContainer"], [data-theme="dark"] [data-testid="stHeader"] { background:#0f172a !important; color:#e5e7eb !important; }
[data-theme="dark"] .step, [data-theme="dark"] .card, [data-theme="dark"] .section { background:#172033 !important; border-color:#334155 !important; color:#e5e7eb !important; }
[data-theme="dark"] h1, [data-theme="dark"] h2, [data-theme="dark"] h3, [data-theme="dark"] .muted { color:#f3f6fb !important; }
[data-theme="dark"] div[data-testid="stButton"] button, [data-theme="dark"] div[data-testid="stButton"] button p { background:#2457d6 !important; color:#ffffff !important; }
[data-theme="dark"] div[data-testid="stButton"] button:hover, [data-theme="dark"] div[data-testid="stButton"] button:hover p { background:#3264df !important; color:#ffffff !important; }
/* Keep the local dashboard consistent even when the browser/Streamlit theme is dark. */
html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] { background:#f5f7fb !important; color:#000000 !important; }
[data-testid="stAppViewContainer"] h1, [data-testid="stAppViewContainer"] h2, [data-testid="stAppViewContainer"] h3,
[data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] .muted, [data-testid="stAppViewContainer"] .score-label,
[data-testid="stAppViewContainer"] strong, [data-testid="stAppViewContainer"] b { color:#000000 !important; }
[data-testid="stTextInput"] input, [data-baseweb="select"] > div { background:#ffffff !important; color:#000000 !important; }
.step, .card, .section { background:#ffffff !important; color:#000000 !important; }
.hero h1, .hero p, .hero .hero-kicker, .stButton > button, .stButton > button p, .stButton > button span { color:#ffffff !important; }
.card b, .card strong, .card span, .card small, .section b, .section strong, .factor, .factor-value,
[data-testid="stMarkdownContainer"] .card b, [data-testid="stMarkdownContainer"] .card span { color:#000000 !important; }
.hero b, .hero strong, .hero span, .hero small { color:#ffffff !important; }
</style>
""", unsafe_allow_html=True)


def find_benchmark(industry, year):
    normalized_year = normalize_fiscal_year(year)
    for path in sorted(Path("artifacts").glob("benchmark_*_v*.json"), key=lambda item: int(item.stem.rsplit("_v", 1)[-1]), reverse=True):
        item = json.loads(path.read_text(encoding="utf-8"))
        if normalize_industry(item.get("industry", "")) == normalize_industry(industry) and normalize_fiscal_year(item.get("fiscal_year", "")) == normalized_year:
            # Do not serve known contaminated IT versions created before
            # document-industry validation was added. v3 is the last valid
            # cohort containing LTM.
            if normalize_industry(industry) == "Information Technology" and any(company in {"TATASTEEL", "TRENT"} for company in item.get("companies", [])):
                continue
            return item
    industry_key = normalize_industry(industry).lower().replace(" ", "_")
    generated = Path("artifacts/{}_ai_readiness_benchmark_{}.json".format(industry_key, normalized_year))
    if generated.exists():
        return json.loads(generated.read_text(encoding="utf-8"))
    if normalize_industry(industry) == "Information Technology":
        generated = Path("artifacts/it_ai_readiness_benchmark_{}.json".format(normalized_year))
        if generated.exists():
            return json.loads(generated.read_text(encoding="utf-8"))
    fallback = Path("artifacts/it_ai_readiness_benchmark_2025-2026.final.json")
    if fallback.exists() and normalize_industry(industry) == "Information Technology":
        return json.loads(fallback.read_text(encoding="utf-8"))
    return None


def find_regression_matrix(benchmark=None):
    """Load the matrix belonging to the selected benchmark version.

    Cohort updates write versioned matrices; using the old unversioned matrix
    here made newly trained companies disappear from the client view.
    """
    candidates = []
    if benchmark:
        industry_key = normalize_industry(benchmark.get("industry", "")).lower().replace(" ", "_")
        version_number = benchmark.get("version_number")
        if version_number:
            candidates.append(
                Path("artifacts/{}_ai_readiness_regression_matrix_v{}.json".format(industry_key, version_number))
            )
        candidates.append(Path("artifacts/{}_ai_readiness_regression_matrix.json".format(industry_key)))
    candidates.append(Path("artifacts/it_ai_readiness_regression_matrix.json"))
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {"rows": [], "feature_names": []}


def benchmark_matrix_rows(matrix, benchmark):
    """Return only rows belonging to the selected industry/year cohort."""
    allowed = {str(company).strip().upper() for company in (benchmark or {}).get("companies", [])}
    if not allowed:
        return []
    return [row for row in matrix.get("rows", []) if str(row.get("company", "")).strip().upper() in allowed]


def company_overall_scores(matrix, benchmark):
    result = []
    for row in benchmark_matrix_rows(matrix, benchmark):
        values = [value for value in row.get("feature_vector", []) if isinstance(value, (int, float))]
        if values:
            result.append({"company": row.get("company", ""), "score": round(sum(values) / len(values) * 20, 1)})
    return sorted(result, key=lambda item: item["score"], reverse=True)


def _question_terms(text):
    return {word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", (text or "").lower()) if word not in {"what", "does", "this", "that", "with", "from", "evidence", "company"}}


def company_evidence_from_database(company, question, question_id=None):
    """Load the selected company's own chunks and match them to the question."""
    try:
        # Healthcare evidence and AI rationales live in PostgreSQL. Query it
        # first; the JSON artifact is a legacy fallback and often has scores
        # without the supporting text or rationale.
        import psycopg
        from cis_fin.postgres import secure_postgres_dsn

        settings = Settings.from_env()
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT e.chunk_id::text, c.page_start, c.page_end, c.chunk_text,
                           e.ai_score, e.ai_confidence, e.ai_rationale, e.evidence_quote
                      FROM healthcare_custom_question_evidence e
                      JOIN healthcare_custom_questions q ON q.question_id=e.question_id
                      JOIN chunks c ON c.chunk_id=e.chunk_id
                      JOIN companies co ON co.company_id=c.company_id
                     WHERE upper(coalesce(co.symbol, '')) = upper(%s)
                       AND (q.question_key = %s OR q.question_text = %s)
                     ORDER BY e.ai_score DESC NULLS LAST, e.match_count DESC NULLS LAST
                     LIMIT 5
                """, (str(company), question_id or "", question))
                live_records = [
                    {"chunk_id": row[0], "page_start": row[1], "page_end": row[2], "chunk_text": row[3] or "",
                     "ai_score": row[4], "confidence": row[5], "rationale": row[6] or "", "evidence_quote": row[7] or ""}
                    for row in cursor.fetchall()
                ]
        if live_records:
            return live_records

        cached = Path("artifacts/healthcare_46_question_benchmark.json")
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
            for item in data.get("themes", []):
                if item.get("question") == question:
                    matches = [m for m in item.get("matches", []) if str(m.get("company", "")).upper() == str(company).upper()]
                    if matches:
                        return matches[:3]
            return []
        settings = Settings.from_env()
        documents = list_recent_documents(settings, limit=100)
        selected = next((row for row in documents if str(row[2]).upper() == str(company).upper()), None)
        if not selected:
            return None
        records = export_document_chunks(settings, uuid.UUID(str(selected[0])))
        terms = _question_terms(question)
        ranked = sorted(records, key=lambda record: len(terms & _question_terms(record.get("chunk_text", ""))), reverse=True)
        useful = [record for record in ranked if len(terms & _question_terms(record.get("chunk_text", ""))) >= 2][:3]
        if not useful:
            useful = ranked[:1]
        return useful
    except (CompanyLookupError, ValueError, RuntimeError):
        return None


def load_uploaded_question_set(uploaded_file):
    """Read question text and TOE construct from the supplied workbook."""
    from openpyxl import load_workbook

    workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
    sheet = workbook["Results"] if "Results" in workbook.sheetnames else workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(value or "").strip() for value in rows[0]]
    positions = {name: index for index, name in enumerate(headers)}
    if "Question" not in positions:
        raise ValueError("The workbook must contain a Results sheet with a Question column.")
    questions = []
    for row in rows[1:]:
        question = str(row[positions["Question"]] or "").strip()
        if not question:
            continue
        construct = str(row[positions.get("Construct", 0)] or "").strip()
        toe_dimension = (
            "technology" if construct in {"Data", "Technology", "Technical Skills"}
            else "organization" if construct in {"Business Skills", "Inter-Departmental Coordination", "Organizational Change Capacity", "Basic Resources"}
            else "environment"
        )
        questions.append({
            "question_id": str(row[positions.get("Question ID", 0)] or ""),
            "construct": construct,
            "toe_dimension": toe_dimension,
            "question": question,
            "source": str(row[positions.get("Source", 0)] or "").strip(),
        })
    return questions


def supplied_question_evidence(question, limit=5):
    """Retrieve existing chunks and saved PostgreSQL AI scores; do not score/generate."""
    import psycopg
    from cis_fin.postgres import secure_postgres_dsn

    settings = Settings.from_env()
    stop_words = {"what", "does", "have", "with", "from", "into", "that", "this", "they", "their", "for", "and"}
    terms = {word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", question.lower()) if word not in stop_words}
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT c.chunk_id::text, c.page_start, c.page_end, c.chunk_text,
                          s.score, s.confidence, s.rationale, s.evidence_quote,
                          coalesce(co.symbol, '')
                   FROM chunks c
                   LEFT JOIN chunk_ai_scores s ON s.chunk_id=c.chunk_id AND s.status='completed'
                   LEFT JOIN companies co ON co.company_id=c.company_id
                   WHERE lower(coalesce(co.industry, '')) LIKE '%health%'
                      OR lower(coalesce(co.industry, '')) LIKE '%pharma%'"""
            )
            candidates = []
            for chunk_id, page_start, page_end, text_value, score, confidence, rationale, quote, symbol in cursor.fetchall():
                chunk_terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", (text_value or "").lower()))
                overlap = len(terms & chunk_terms)
                if overlap:
                    candidates.append({
                        "chunk_id": chunk_id, "company": symbol, "page_start": page_start,
                        "page_end": page_end, "chunk_text": text_value, "ai_score": score,
                        "confidence": confidence, "rationale": rationale or "", "evidence_quote": quote or "",
                        "match_terms": overlap,
                    })
    candidates.sort(key=lambda item: (item["match_terms"], item["ai_score"] is not None, item["ai_score"] or -1), reverse=True)
    return candidates[:limit]


def healthcare_custom_questions_from_database():
    """Load the persisted 46-question healthcare set and mapped evidence."""
    import psycopg
    from cis_fin.postgres import secure_postgres_dsn

    try:
        settings = Settings.from_env()
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""SELECT q.question_key, q.construct, q.toe_dimension, q.question_text, q.source,
                                        e.chunk_id::text, c.page_start, c.page_end, c.chunk_text, e.match_count, co.symbol,
                                        e.ai_score, e.ai_confidence, e.ai_rationale, e.evidence_quote
                                   FROM healthcare_custom_questions q
                                   LEFT JOIN healthcare_custom_question_evidence e ON e.question_id=q.question_id
                                   LEFT JOIN chunks c ON c.chunk_id=e.chunk_id
                                   LEFT JOIN companies co ON co.company_id=c.company_id
                                  ORDER BY q.question_key, e.match_count DESC NULLS LAST""")
                grouped = {}
                for key, construct, dimension, question, source, chunk_id, page_start, page_end, text_value, match_count, symbol, score, confidence, rationale, quote in cursor.fetchall():
                    item = grouped.setdefault(key, {"question_id": key, "construct": construct, "toe_dimension": dimension, "question": question, "source": source, "matches": []})
                    if chunk_id:
                        item["matches"].append({"chunk_id": chunk_id, "company": symbol or "Healthcare", "page_start": page_start, "page_end": page_end, "chunk_text": text_value or "", "match_terms": match_count, "ai_score": score, "confidence": confidence, "rationale": rationale or "", "evidence_quote": quote or ""})
        return list(grouped.values())
    except Exception:
        cached = Path("artifacts/healthcare_46_question_benchmark.json")
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8")).get("themes", [])
        raise


def healthcare_custom_matrix(questions):
    """Build company/question features from saved scores; missing values stay None."""
    by_company = {}
    for index, question in enumerate(questions):
        for match in question.get("matches", []):
            score = match.get("ai_score")
            company = match.get("company")
            if score is None or not company or company == "Healthcare":
                continue
            by_company.setdefault(company, {}).setdefault(index, []).append(float(score))
    rows = []
    for company, features in sorted(by_company.items()):
        values = {"theme_{}".format(index): round(sum(scores) / len(scores), 2) for index, scores in features.items()}
        rows.append({"company": company, "features": values, "feature_vector": [values.get("theme_{}".format(i)) for i in range(len(questions))], "provenance": {}})
    return {"industry": "Healthcare", "fiscal_year": "2025-2026", "feature_names": ["theme_{}".format(i) for i in range(len(questions))], "rows": rows, "excluded": [], "training_ready": False, "training_note": "Uses saved PostgreSQL chunk scores. Missing scores remain unavailable and are excluded from averages."}


def healthcare_score_color(score):
    """Use one monotonic blue scale for the provisional Healthcare pilot."""
    if score is None:
        return "#62708a"
    if score < 40:
        return "#9bb5ef"
    if score < 70:
        return "#5f83df"
    return "#2457d6"


def load_master_healthcare_companies():
    path = Path("BSE NSE MasterEquity list.csv")
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    terms = {"healthcare", "pharmaceuticals", "drugs & pharmaceuticals", "healthcare service provider"}
    return [row for row in rows if str(row.get("Sector Name", "")).strip().lower() in terms or str(row.get("Industry Name", "")).strip().lower() in terms]


def uploaded_reports_snapshot():
    try:
        # The shared read-only query intentionally caps this at 100 rows.
        rows = list_recent_documents(Settings.from_env(), limit=100)
        return rows, None
    except CompanyLookupError as exc:
        return [], str(exc)


def industry_matches(selected: str, stored: str) -> bool:
    selected = normalize_industry(selected)
    stored = normalize_industry(stored)
    if selected in {"Healthcare", "Pharmaceuticals"}:
        return stored in {"Healthcare", "Pharmaceuticals"}
    return selected == stored


st.markdown('<div class="hero"><div class="hero-kicker" style="color:#bcd0ff">CIS-FIN BENCHMARK</div><h1>AI readiness by industry and companies</h1><p>TOE framework · evidence-grounded scores · same industry and fiscal year</p></div>', unsafe_allow_html=True)
st.markdown('<div class="step-strip"><div class="step"><strong>1 · Choose your industry</strong>Select the peer group and year.</div><div class="step"><strong>2 · Review the benchmark</strong>See the industry score and TOE factors.</div><div class="step"><strong>3 · Explore the evidence</strong>Open any question and inspect company evidence.</div></div>', unsafe_allow_html=True)
NSE_INDUSTRIES = [
    "Information Technology", "FMCG", "Banking", "Financial Services", "Insurance",
    "Pharmaceuticals", "Healthcare", "Automobiles", "Auto Components", "Chemicals",
    "Construction", "Cement", "Consumer Durables", "Metals and Mining", "Oil and Gas",
    "Power", "Telecommunications", "Retail", "Real Estate", "Textiles", "Other / custom",
]
industry_choice = st.selectbox("Industry", ["Select an industry"] + NSE_INDUSTRIES)
industry = st.text_input("Custom industry", placeholder="Type an industry name") if industry_choice == "Other / custom" else ("" if industry_choice == "Select an industry" else industry_choice)
year = st.text_input("Fiscal year", value="2025-2026")
selection_key = "{}|{}".format(normalize_industry(industry), normalize_fiscal_year(year))
if st.session_state.get("comparison_selection_key") != selection_key:
    st.session_state.pop("comparison_mode", None)
    st.session_state.pop("comparison_selection_key", None)

if not industry:
    st.markdown('<div class="helper"><b>Start here</b><br>Choose an industry above to see how companies in that sector compare on AI readiness.</div>', unsafe_allow_html=True)
else:
    if st.session_state.get("comparison_mode") is None:
        st.markdown('<div class="section"><div class="hero-kicker">Choose your view</div><h2>How would you like to explore this benchmark?</h2><p class="muted">Select a comparison type before any scores or company information are displayed.</p>', unsafe_allow_html=True)
        selected_comparison = st.radio(
            "Comparison type",
            ["Industry comparison", "Peer comparison"],
            format_func=lambda value: "Industry comparison — understand the sector TOE score" if value == "Industry comparison" else "Peer comparison — compare companies side by side",
            key="comparison_choice",
        )
        if st.button("Continue to {} →".format(selected_comparison.lower()), type="primary", key="confirm_comparison_mode"):
            st.session_state["selected_industry"] = industry
            st.session_state["selected_year"] = year
            st.session_state["comparison_selection_key"] = selection_key
            st.session_state["comparison_mode"] = selected_comparison
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()
    comparison_mode = st.session_state.get("comparison_mode")
    if st.button("Prepare chunk + question + score CSV", key="prepare_chunk_export"):
        try:
            export_rows = export_chunk_questions_scores(Settings.from_env(), industry=industry, fiscal_year=year)
            output = io.StringIO()
            columns = ["symbol", "company_name", "industry", "fiscal_year", "document_title", "processing_status", "chunk_id", "chunk_index", "page_start", "page_end", "chunk_text", "question_id", "question_text", "dimension", "validation_status", "review_status", "evidence_quote", "semantic_support_status", "ai_score", "latest_reviewed_score"]
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            writer.writeheader(); writer.writerows(export_rows)
            st.download_button("Download CSV", output.getvalue(), file_name="cis_fin_{}_{}_chunks_questions_scores.csv".format(industry.lower().replace(" ", "_"), year.replace("/", "-")), mime="text/csv", key="download_chunk_export")
            st.caption("{} exported rows. AI and human-reviewed scores are separate; blank values mean that score has not been recorded.".format(len(export_rows)))
        except CompanyLookupError as exc:
            st.error(str(exc))
    benchmark = find_benchmark(industry, year)
    if benchmark and normalize_industry(industry) == "Healthcare":
        try:
            custom_questions = healthcare_custom_questions_from_database()
            if custom_questions:
                benchmark["themes"] = [
                    {
                        "question": item["question"],
                        "theme": item["construct"],
                        "toe_dimension": item["toe_dimension"],
                        "question_id": item["question_id"],
                        "matches": item["matches"],
                    }
                    for item in custom_questions
                ]
                benchmark["companies"] = sorted({match["company"] for item in custom_questions for match in item["matches"] if match.get("company") and match["company"] != "Healthcare"})
                # Build from the live rows above so completed scoring immediately
                # appears in the client app; the artifact is only a fallback for
                # database-unavailable mode.
                benchmark["_custom_matrix"] = healthcare_custom_matrix(custom_questions)
                benchmark["question_count"] = len(custom_questions)
        except Exception:
            pass
    if not benchmark:
        st.warning("No same-industry/year benchmark is available yet.")
        uploaded_rows, upload_error = uploaded_reports_snapshot()
        if upload_error:
            st.error("The app could not read uploaded reports from PostgreSQL: {}".format(upload_error))
            st.info("This is a database connectivity problem, not a missing Healthcare company list. Verify DATABASE_URL/network access, then refresh the app.")
        else:
            matching = [
                row for row in uploaded_rows
                if industry_matches(industry, row[3])
                and (not year or normalize_fiscal_year(row[4]) == normalize_fiscal_year(year))
            ]
            completed = [row for row in matching if str(row[5]).lower() == "completed"]
            st.subheader("Uploaded Healthcare reports")
            st.caption("{} completed report(s) found for {} / {}. Benchmark scoring is not required to view uploads.".format(len(completed), normalize_industry(industry), normalize_fiscal_year(year)))
            if completed:
                st.dataframe([{"Company": row[2], "Industry": row[3], "Fiscal year": row[4], "Status": row[5]} for row in completed], use_container_width=True, hide_index=True)
            elif matching:
                st.dataframe([{"Company": row[2], "Industry": row[3], "Fiscal year": row[4], "Status": row[5]} for row in matching], use_container_width=True, hide_index=True)
            else:
                st.info("No completed Healthcare reports matched this fiscal year. Check that ingestion finished with status 'completed' and that the fiscal year is entered as YYYY-YYYY.")
    elif comparison_mode in ("Industry comparison", "Peer comparison"):
        themes = benchmark.get("themes", [])
        # Version metadata intentionally stores the immutable question identity;
        # enrich the client view with evidence fields from the benchmark artifact.
        industry_key = normalize_industry(benchmark.get("industry", industry)).lower().replace(" ", "_")
        evidence_artifact = Path(
            "artifacts/{}_ai_readiness_benchmark_{}{}.json".format(
                industry_key,
                normalize_fiscal_year(year),
                ".final" if industry_key == "information_technology" else "",
            )
        )
        if not evidence_artifact.exists() and industry_key == "information_technology":
            evidence_artifact = Path("artifacts/it_ai_readiness_benchmark_2025-2026.final.json")
        if themes and not themes[0].get("chunk_ids") and evidence_artifact.exists():
            enriched = json.loads(evidence_artifact.read_text(encoding="utf-8")).get("themes", [])
            if len(enriched) >= len(themes):
                themes = enriched[:len(themes)]
        matrix = benchmark.get("_custom_matrix") if normalize_industry(industry) == "Healthcare" else find_regression_matrix(benchmark)
        if st.session_state.get("client_view") == "question_explorer":
            if st.button("← Back to industry overview", key="back_from_explorer"):
                st.session_state.pop("client_view", None)
                st.rerun()
            st.markdown('<div class="hero"><div class="hero-kicker">Benchmark explorer</div><h1>Questions and company comparison</h1><p>{} · {}</p></div>'.format(normalize_industry(industry), normalize_fiscal_year(year)), unsafe_allow_html=True)
            cohort = company_overall_scores(matrix, benchmark)
            if cohort:
                options = [item["company"] for item in cohort]
                left, right = st.columns(2)
                with left:
                    company_a = st.selectbox("Compare company A", options, index=0, key="compare_company_a")
                with right:
                    company_b = st.selectbox("Compare company B", options, index=min(1, len(options)-1), key="compare_company_b")
                row_a = next(row for row in benchmark_matrix_rows(matrix, benchmark) if row.get("company") == company_a)
                row_b = next(row for row in benchmark_matrix_rows(matrix, benchmark) if row.get("company") == company_b)
                st.markdown('<div class="insight"><b>{}</b> scores {} / 100 versus <b>{}</b> at {} / 100. {} is higher by {} points.</div>'.format(company_a, next(x["score"] for x in cohort if x["company"] == company_a), company_b, next(x["score"] for x in cohort if x["company"] == company_b), company_a if next(x["score"] for x in cohort if x["company"] == company_a) >= next(x["score"] for x in cohort if x["company"] == company_b) else company_b, abs(next(x["score"] for x in cohort if x["company"] == company_a) - next(x["score"] for x in cohort if x["company"] == company_b))), unsafe_allow_html=True)
                question_rows = []
                for index, theme in enumerate(themes):
                    a = row_a.get("features", {}).get("theme_{}".format(index))
                    b = row_b.get("features", {}).get("theme_{}".format(index))
                    values = [row.get("features", {}).get("theme_{}".format(index)) for row in benchmark_matrix_rows(matrix, benchmark)]
                    values = [value for value in values if isinstance(value, (int, float))]
                    question_rows.append({"#": index + 1, "TOE": theme.get("toe_dimension", "").title(), "Question": theme.get("question", ""), "Industry avg /100": round(sum(values) / len(values) * 20, 1) if values else None, company_a + " /100": round(a * 20, 1) if a is not None else None, company_b + " /100": round(b * 20, 1) if b is not None else None, "Difference": round((a - b) * 20, 1) if a is not None and b is not None else None})
                st.dataframe(question_rows, use_container_width=True, hide_index=True, height=520)
                selected_question = st.selectbox("Deep analysis question", [item["Question"] for item in question_rows], key="deep_analysis_question")
                selected_index = next(index for index, item in enumerate(question_rows) if item["Question"] == selected_question)
                selected_theme = themes[selected_index]
                st.markdown('<div class="section"><div class="hero-kicker">Deep analysis</div><h2>{}</h2><p class="muted">Industry average: {} / 100 · {}: {} / 100 · {}: {} / 100</p></div>'.format(selected_question, question_rows[selected_index]["Industry avg /100"], company_a, question_rows[selected_index][company_a + " /100"], company_b, question_rows[selected_index][company_b + " /100"]), unsafe_allow_html=True)
                for company_name in (company_a, company_b):
                    records = company_evidence_from_database(company_name, selected_question)
                    with st.expander("{} evidence".format(company_name), expanded=True):
                        st.write("Score: {} / 100".format(question_rows[selected_index][company_name + " /100"]))
                        if records:
                            for record in records[:2]:
                                st.caption("Pages {}–{} · {}".format(record.get("page_start", "?"), record.get("page_end", "?"), record.get("chunk_id", "")))
                                st.write(record.get("chunk_text", "")[:1000])
                        else:
                            st.info("Evidence details are not available for this company yet.")
            else:
                st.info("Company scores are not available for this benchmark yet.")
            st.stop()
        if st.session_state.get("client_view") == "company_detail":
            selected_company = st.session_state.get("client_selected_company")
            company_row = next((row for row in benchmark_matrix_rows(matrix, benchmark) if str(row.get("company")) == str(selected_company)), None)
            if not company_row:
                st.error("Company scores are not available for this benchmark yet.")
                st.stop()
            if st.button("← Back to industry overview", key="back_from_company_detail"):
                st.session_state.pop("client_view", None)
                st.session_state.pop("client_selected_company", None)
                st.rerun()
            values = company_row.get("feature_vector", [])
            available = [value for value in values if isinstance(value, (int, float))]
            overall = round(sum(available) / len(available) * 20, 1) if available else None
            st.markdown('<div class="hero"><div class="hero-kicker" style="color:#bcd0ff">COMPANY DETAIL</div><h1>{}</h1><p>{} · {} · question-level AI-readiness evidence</p></div>'.format(selected_company, normalize_industry(industry), normalize_fiscal_year(year)), unsafe_allow_html=True)
            summary = st.columns(3)
            with summary[0]:
                st.markdown('<div class="card snapshot"><small>COMPANY TOE SCORE</small><div class="big-score">{}</div><span>/ 100</span></div>'.format(overall if overall is not None else "—"), unsafe_allow_html=True)
            with summary[1]:
                st.markdown('<div class="card snapshot"><small>QUESTIONS WITH SCORES</small><div class="big-score">{}</div><span>/ {}</span></div>'.format(len(available), len(themes)), unsafe_allow_html=True)
            with summary[2]:
                st.markdown('<div class="card snapshot"><small>INDUSTRY</small><h2>{}</h2><span>{}</span></div>'.format(normalize_industry(industry), normalize_fiscal_year(year)), unsafe_allow_html=True)
            st.markdown("### TOE question scores")
            st.caption("All 46 Healthcare TOE questions are shown below. Expand any question to review its AI rationale, AI evidence quote, and source chunks.")
            question_items = []
            for index, theme in enumerate(themes):
                value = values[index] if index < len(values) and isinstance(values[index], (int, float)) else None
                question_items.append((index, theme, value))
            question_items.sort(key=lambda item: (item[2] is not None, item[2] if item[2] is not None else -1), reverse=True)
            for index, theme, value in question_items:
                score = round(value * 20, 1) if value is not None else None
                st.markdown('<div class="card" style="min-height:0"><small>{} · Question {}</small><h3>{}</h3><strong>{}</strong> / 100 <span class="muted"> · AI score: {} / 5</span></div>'.format(theme.get("toe_dimension", "TOE").title(), index + 1, theme.get("question", ""), score if score is not None else "Not available", value if value is not None else "—"), unsafe_allow_html=True)
                if value is not None:
                    with st.expander("View AI evidence and source chunks"):
                        records = company_evidence_from_database(selected_company, theme.get("question", ""), theme.get("question_id"))
                        rationale = next((record.get("rationale") for record in records or [] if record.get("rationale")), "No AI rationale was saved for this score.")
                        quotes = [record.get("evidence_quote") for record in records or [] if record.get("evidence_quote")]
                        st.markdown("**AI scoring rationale**")
                        st.write(rationale)
                        st.markdown("**AI evidence quote**")
                        st.write("\n\n".join(quotes) if quotes else "No AI evidence quote was saved for this score.")
                        st.markdown("**Source chunks**")
                        if records:
                            for record in records:
                                st.caption("Pages {}–{} · {}".format(record.get("page_start", "?"), record.get("page_end", "?"), record.get("chunk_id", "")))
                                st.write(record.get("chunk_text", "")[:2000])
                        else:
                            st.info("No supporting chunk was found for this company and question.")
            st.stop()
        if st.session_state.get("client_view") == "factor_detail":
            if st.button("← Back to industry overview", key="client_back_overview"):
                st.session_state.pop("client_view", None)
                st.session_state.pop("client_selected_factor", None)
                st.rerun()
            selected_index = int(st.session_state.get("client_selected_factor", 0))
            selected_index = max(0, min(selected_index, len(themes) - 1))
            selected_theme = themes[selected_index]
            selected_question = selected_theme.get("question", "Selected TOE factor")
            st.markdown('<div class="hero"><h1>Question detail</h1><p>{} · {} · {}</p></div>'.format(normalize_industry(industry), normalize_fiscal_year(year), selected_theme.get("toe_dimension", "TOE").title()), unsafe_allow_html=True)
            st.markdown('<div class="section"><h2>{}</h2><p class="muted">Scores for this question across every company in the same industry and fiscal year.</p></div>'.format(selected_question), unsafe_allow_html=True)
            feature_name = "theme_{}".format(selected_index)
            company_scores = []
            for company_row in benchmark_matrix_rows(matrix, benchmark):
                raw = company_row.get("features", {}).get(feature_name)
                company_scores.append({"Company": company_row.get("company"), "Score (0–5)": raw, "Score (0–100)": round(float(raw) * 20, 1) if raw is not None else None})
            st.dataframe(sorted(company_scores, key=lambda item: item["Score (0–5)"] if item["Score (0–5)"] is not None else -1, reverse=True), use_container_width=True, hide_index=True)
            company_options = [row["Company"] for row in company_scores]
            if company_options:
                selected_company = st.selectbox("Select a company to inspect evidence", company_options, key="client_detail_company")
                company_score = next(row for row in company_scores if row["Company"] == selected_company)
                evidence_text = selected_theme.get("representative_evidence_preview", "Evidence preview is not available in the current artifact.")
                chunk_ids = selected_theme.get("chunk_ids", [])
                rationale = "The company-level explanation is not available in this artifact."
                feature_path = Path("artifacts/it_ai_readiness_regression_features.json")
                if feature_path.exists():
                    feature_data = json.loads(feature_path.read_text(encoding="utf-8"))
                    feature = feature_data.get("regression_features", {}).get("{}:{}".format(selected_index, selected_company), {})
                    evidence_text = feature.get("evidence_quote", evidence_text)
                    rationale = feature.get("rationale", rationale)
                    chunk_ids = feature.get("supporting_chunk_ids", chunk_ids)
                database_records = company_evidence_from_database(selected_company, selected_question, selected_theme.get("question_id"))
                if database_records:
                    chunk_ids = [record.get("chunk_id") for record in database_records]
                    evidence_text = "\n\n".join("Pages {}–{}:\n{}".format(record.get("page_start", "?"), record.get("page_end", "?"), record.get("chunk_text", "")[:1200]) for record in database_records)
                    rationale = next((record.get("rationale") for record in database_records if record.get("rationale")), rationale)
                ai_evidence = "\n\n".join(record.get("evidence_quote", "") for record in (database_records or []) if record.get("evidence_quote"))
                st.markdown('<div class="section"><div class="eyebrow">{} · {}/100</div><h2>Why this company received this score</h2><p class="muted">This evidence is loaded for the selected company and selected question.</p></div>'.format(selected_company, company_score.get("Score (0–100)", "—")), unsafe_allow_html=True)
                st.markdown("### AI scoring rationale")
                st.write(rationale or "No AI rationale was saved for this score.")
                st.markdown("### AI evidence quote")
                st.write(ai_evidence or "No AI evidence quote was saved for this score.")
                st.markdown("### Supporting chunks")
                if database_records:
                    for record in database_records:
                        with st.expander("Pages {}–{} · {}".format(record.get("page_start", "?"), record.get("page_end", "?"), record.get("chunk_id", "")), expanded=True):
                            st.write(record.get("chunk_text", "")[:2000])
                else:
                    st.info("No supporting chunk was found for this company and question.")
            st.stop()
        st.markdown('<div class="hero"><div class="hero-kicker" style="color:#bcd0ff">INDUSTRY COMPARISON</div><h1>{}</h1><p>TOE framework · {} · {} peer companies</p></div>'.format(normalize_industry(industry), normalize_fiscal_year(year), len(benchmark.get("companies", []))), unsafe_allow_html=True)
        if normalize_industry(industry) == "Healthcare":
            st.warning("Healthcare scores are provisional. A review found some evidence/question mismatches and the cohort is being re-scored with stricter relevance checks.")
        rows = []
        if matrix.get("rows"):
            cohort_rows = benchmark_matrix_rows(matrix, benchmark)
            for name in matrix.get("feature_names", []):
                values = [row.get("features", {}).get(name) for row in cohort_rows if row.get("features", {}).get(name) is not None]
                if values:
                    rows.append({"TOE question": name, "Industry average (0–5)": round(sum(values) / len(values), 2)})
        overall = round(sum(row["Industry average (0–5)"] for row in rows) / len(rows) * 20, 1) if rows else None
        dimensions = {dimension: [] for dimension in ("technology", "organization", "environment")}
        for index, row in enumerate(rows):
            dimension = themes[index].get("toe_dimension", "technology") if index < len(themes) else "technology"
            dimensions.setdefault(dimension, []).append(row["Industry average (0–5)"] * 20)
        st.markdown("### Industry snapshot")
        snapshot = st.columns(3)
        company_rankings = company_overall_scores(matrix, benchmark)
        with snapshot[0]:
            st.markdown('<div class="card snapshot"><small>INDUSTRY TOE SCORE</small><div class="big-score">{}</div><span>/ 100</span></div>'.format(int(overall) if overall is not None else "—"), unsafe_allow_html=True)
        with snapshot[1]:
            best = company_rankings[0] if company_rankings else None
            st.markdown('<div class="card snapshot"><small>TOP COMPANY</small><h2>{}</h2><div class="big-score">{}</div><span>/ 100</span></div>'.format(best["company"] if best else "—", int(best["score"]) if best else "—"), unsafe_allow_html=True)
        with snapshot[2]:
            st.markdown('<div class="card snapshot"><small>BENCHMARK QUESTIONS</small><div class="big-score">{}</div><span>TOE factors</span></div>'.format(len(themes)), unsafe_allow_html=True)

        st.markdown("### TOE performance")
        colors = {"technology": "#2457d6", "organization": "#32648f", "environment": "#173ea7"}
        for dimension in ("technology", "organization", "environment"):
            values = dimensions.get(dimension, [])
            value = round(sum(values) / len(values)) if values else 0
            st.markdown('<div class="card" style="margin:.6rem 0;min-height:0;padding:1rem 1.2rem"><b>{}</b><div class="bar"><div class="fill" style="width:{}%;background:{}"></div></div><strong>{} / 100</strong></div>'.format(dimension.title(), value, colors[dimension], value), unsafe_allow_html=True)

        st.markdown("### Company ranking")
        st.caption("Open a company to review every available TOE question score.")
        for rank, company_row in enumerate(company_rankings, 1):
            company_name = company_row["company"]
            col_name, col_score, col_open, col_compare = st.columns([5, 2, 1, 1])
            with col_name:
                st.markdown("**{} · {}**".format(rank, company_name))
            with col_score:
                st.markdown('<b style="color:{}">{} / 100</b>'.format(healthcare_score_color(company_row["score"]) if normalize_industry(industry) == "Healthcare" else "#172033", company_row["score"]), unsafe_allow_html=True)
            with col_open:
                if st.button("Open", key="healthcare_company_open_{}".format(company_name)):
                    st.session_state["client_view"] = "company_detail"
                    st.session_state["client_detail_company"] = company_name
                    st.session_state["client_selected_company"] = company_name
                    st.rerun()
            with col_compare:
                if st.button("Compare", key="healthcare_company_compare_{}".format(company_name)):
                    st.session_state["client_view"] = "question_explorer"
                    st.session_state["compare_company_a"] = company_name
                    st.rerun()
        if st.button("Open question explorer", type="primary", key="open_question_explorer"):
            st.session_state["client_view"] = "question_explorer"
            st.rerun()
        st.markdown('<div class="section"><h3>TOE questions</h3><p class="muted">Industry-average score for every benchmark question. Scores are shown on a 0–100 scale.</p>', unsafe_allow_html=True)
        for index, row in enumerate(rows):
            dimension = themes[index].get("toe_dimension", "technology") if index < len(themes) else "technology"
            value = round(row["Industry average (0–5)"] * 20)
            question_text = themes[index].get("question", row["TOE question"]) if index < len(themes) else row["TOE question"]
            score_color = healthcare_score_color(value) if normalize_industry(industry) == "Healthcare" else "#2457d6"
            st.markdown('<div class="card" style="min-height:0;padding:1rem 1.2rem;margin:.6rem 0"><small>{} · Question {}</small><br><b>{}</b><br><span>Industry average: <strong style="color:{}">{} / 100</strong></span></div>'.format(dimension.title(), index + 1, question_text, score_color, value), unsafe_allow_html=True)
            if st.button("Open", key="open_factor_{}".format(index), type="secondary"):
                st.session_state["client_selected_factor"] = index
                st.session_state["client_view"] = "factor_detail"
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        if rows and st.session_state.get("client_view") == "legacy_detail_disabled":
            st.markdown('<div class="section"><h3>Factor detail</h3><p class="muted">Select a question to inspect every company and the evidence behind its score.</p>', unsafe_allow_html=True)
            question_options = [themes[index].get("question", row["TOE question"]) if index < len(themes) else row["TOE question"] for index, row in enumerate(rows)]
            selected_question = st.selectbox("Benchmark question", question_options, key="client_factor_question")
            selected_index = question_options.index(selected_question)
            selected_name = rows[selected_index]["TOE question"]
            matrix = find_regression_matrix(benchmark)
            company_scores = []
            for company_row in benchmark_matrix_rows(matrix, benchmark):
                raw = company_row.get("features", {}).get(selected_name)
                company_scores.append({"Company": company_row.get("company"), "Score (0–5)": raw, "Score (0–100)": round(float(raw) * 20, 1) if raw is not None else None})
            st.dataframe(sorted(company_scores, key=lambda item: item["Score (0–5)"] if item["Score (0–5)"] is not None else -1, reverse=True), use_container_width=True, hide_index=True)
            company_options = [row["Company"] for row in company_scores]
            if company_options:
                selected_company = st.selectbox("Company evidence", company_options, key="client_factor_company")
                selected_company_row = next(row for row in company_scores if row["Company"] == selected_company)
                evidence = themes[selected_index] if selected_index < len(themes) else {}
                st.markdown('<div class="card"><div class="eyebrow">{} · score {}/100</div><h3>Why this score?</h3><p class="muted">The score is based on evidence mapped to this benchmark question. The current pilot uses the company feature score and retains the source references below.</p><p><b>Supporting chunk IDs</b></p><p>{}</p><p><b>Representative evidence</b></p><p>{}</p></div>'.format(selected_company, selected_company_row.get("Score (0–100)", "—"), ", ".join(evidence.get("chunk_ids", [])) or "Evidence references are available after report processing.", evidence.get("representative_evidence_preview", "Evidence preview is not available in this benchmark artifact.")), unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
    else:
        company = st.text_input("Company name or ticker", placeholder="Your company")
        uploaded = st.file_uploader("Upload annual report PDF", type=["pdf"])
        if uploaded and st.button("Assess report", type="primary"):
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(uploaded.getvalue())
                pdf_path = Path(handle.name)
            try:
                extracted = extract_pdf(pdf_path)
                chunks = list(iter_chunks(extracted.pages, extracted.checksum))
                records = [{"chunk_id": c.chunk_id, "chunk_text": c.text, "page_start": c.page_start, "page_end": c.page_end, "symbol": company or "CLIENT"} for c in chunks]
                scored = score_document_themes_with_llm(Settings.from_env(), benchmark.get("themes", []), records)
                result_rows = []
                for key, value in scored.items():
                    index = int(key.split("_")[1])
                    result_rows.append({"TOE question": benchmark["themes"][index].get("question", key), "Score (0–5)": value.get("score"), "Status": value.get("status"), "Confidence": value.get("confidence", "—")})
                numeric = [row["Score (0–5)"] for row in result_rows if isinstance(row["Score (0–5)"], (int, float))]
                company_score = round(sum(numeric) / len(numeric) * 20, 1) if numeric else None
                industry_score = None
                if Path("artifacts/it_ai_readiness_regression_matrix.json").exists():
                    matrix = json.loads(Path("artifacts/it_ai_readiness_regression_matrix.json").read_text(encoding="utf-8"))
                    all_values = [v for row in matrix.get("rows", []) for v in row.get("feature_vector", []) if v is not None]
                    industry_score = round(sum(all_values) / len(all_values) * 20, 1) if all_values else None
                gap = round(company_score - industry_score, 1) if company_score is not None and industry_score is not None else None
                st.markdown('<div class="card"><div class="eyebrow">{} vs {}</div><div class="big-score">{}</div><div class="muted">{} the industry average of {} · / 100</div></div>'.format(company or "Your company", normalize_industry(industry), int(company_score) if company_score is not None else "—", ("+{} above".format(int(gap)) if gap is not None and gap >= 0 else "{} below".format(int(abs(gap))) if gap is not None else "Compared with"), int(industry_score) if industry_score is not None else "—"), unsafe_allow_html=True)
                st.markdown('<div class="section"><h3>Company against industry</h3><p class="muted">Dark bar: company · light comparison: industry</p>', unsafe_allow_html=True)
                for row in result_rows:
                    value = float(row["Score (0–5)"] or 0) * 20
                    st.markdown('<div class="factor"><span>{}</span><div><div class="bar"><div class="fill" style="width:{}%"></div></div><div class="bar" style="margin-top:5px"><div class="fill" style="width:{}%;background:#aaa69b"></div></div></div><span class="factor-value">{}</span></div>'.format(row["TOE question"], value, industry_score or 0, int(value)), unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
                st.caption("Scores use only evidence from the uploaded report and remain subject to review.")
            except Exception as exc:
                st.error("Could not assess this report: {}".format(exc))
