"""Separate client-facing CIS-FIN benchmark application.

Run with: ``streamlit run client_app.py``
"""
import json
import sys
import tempfile
import re
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import streamlit as st

from cis_fin.automatic_features import score_document_themes_with_llm
from cis_fin.benchmark import normalize_industry, normalize_fiscal_year
from cis_fin.extraction import extract_pdf, iter_chunks
from cis_fin.settings import Settings
from cis_fin.company_queries import export_document_chunks, list_recent_documents, CompanyLookupError


st.set_page_config(page_title="CIS-FIN Client Benchmark", page_icon="📊", layout="wide")
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] { background: #f7f6f1; color: #20242a; }
[data-testid="stHeader"] { background: #1d1f21; }
section[data-testid="stSidebar"] { display: none !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label, [data-testid="stMarkdownContainer"] p { color: #4e555d !important; }
[data-testid="stTextInput"] input { background: #ffffff !important; color: #20242a !important; border: 1px solid #c9c5ba !important; }
[data-testid="stFileUploader"] section { background: #ffffff !important; border: 1px solid #c9c5ba !important; }
[data-testid="stRadio"] label { color: #3e454d !important; }
[data-testid="stAlert"] p { color: #263746 !important; }
.stButton > button { background: #20242a; color: #ffffff; border-radius: 10px; border: 0; padding: .65rem 1.2rem; }
.stButton > button:hover { background: #32648f; color: #ffffff; }
.stButton > button[kind="secondary"] { background: #ffffff; color: #20242a; border: 1px solid #c9c5ba; }
.step-strip { display:flex; gap:.8rem; margin:1.2rem 0 1.5rem; }
.step { flex:1; background:#fff; border:1px solid #ddd9ce; border-radius:12px; padding:.8rem 1rem; color:#626a72; }
.step strong { display:block; color:#20242a; margin-bottom:.2rem; }
.helper { background:#eef4f7; border-radius:12px; padding:1rem 1.2rem; color:#3e596b; margin:1rem 0; }
.block-container { max-width: 1450px; padding: 2.8rem 2.5rem 5rem; }
h1, h2, h3 { font-family: Georgia, serif; color: #20242a; }
.hero { background: #f7f6f1; padding: 1rem 0 1.8rem; border-bottom: 1px solid #d8d5ca; }
.hero h1 { font-size: 3.5rem; margin: 0; letter-spacing: -2px; }
.hero p { color: #626a72; font-size: 1.15rem; margin: .3rem 0 0; }
.card { background: #fff; border: 1px solid #ddd9ce; border-radius: 18px; padding: 1.6rem 1.8rem; box-shadow: 0 2px 8px #262b3210; min-height: 150px; }
.eyebrow { color: #626a72; font-size: 1rem; }
.big-score { font: 5.7rem Georgia, serif; line-height: 1; margin-top: .4rem; }
.muted { color: #626a72; }
.section { background: #fff; border: 1px solid #ddd9ce; border-radius: 18px; padding: 1.5rem 1.8rem; margin-top: 1.4rem; }
.factor { display: grid; grid-template-columns: 27% 53% 20%; align-items: center; gap: 1rem; padding: .8rem 0; border-top: 1px solid #ebe8df; }
.bar { height: 13px; background: #e7e4db; border-radius: 99px; overflow: hidden; }
.fill { height: 100%; border-radius: 99px; background: #32648f; }
.factor-value { font-size: 1.05rem; text-align: right; }
.subhead { font-weight: 700; margin: 1.2rem 0 .3rem; font-size: 1.1rem; }
</style>
""", unsafe_allow_html=True)


def find_benchmark(industry, year):
    normalized_year = normalize_fiscal_year(year)
    for path in sorted(Path("artifacts").glob("benchmark_*_v*.json"), key=lambda item: int(item.stem.rsplit("_v", 1)[-1]), reverse=True):
        item = json.loads(path.read_text(encoding="utf-8"))
        if normalize_industry(item.get("industry", "")) == normalize_industry(industry) and normalize_fiscal_year(item.get("fiscal_year", "")) == normalized_year:
            return item
    fallback = Path("artifacts/it_ai_readiness_benchmark_2025-2026.final.json")
    if fallback.exists() and normalize_industry(industry) == "Information Technology":
        return json.loads(fallback.read_text(encoding="utf-8"))
    return None


def _question_terms(text):
    return {word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", (text or "").lower()) if word not in {"what", "does", "this", "that", "with", "from", "evidence", "company"}}


def company_evidence_from_database(company, question):
    """Load the selected company's own chunks and match them to the question."""
    try:
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


st.markdown('<div class="hero"><h1>AI readiness by industry</h1><p>TOE framework · evidence-grounded scores · same industry and fiscal year</p></div>', unsafe_allow_html=True)
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
mode = st.radio("Choose an assessment", ["Industry benchmark", "Assess annual report"], horizontal=True)

if not industry:
    st.markdown('<div class="helper"><b>Start here</b><br>Choose an industry above to see how companies in that sector compare on AI readiness.</div>', unsafe_allow_html=True)
else:
    benchmark = find_benchmark(industry, year)
    if not benchmark:
        st.warning("No same-industry/year benchmark is available yet.")
    elif mode == "Industry benchmark":
        themes = benchmark.get("themes", [])
        # Version metadata intentionally stores the immutable question identity;
        # enrich the client view with evidence fields from the benchmark artifact.
        evidence_artifact = Path("artifacts/it_ai_readiness_benchmark_2025-2026.final.json")
        if themes and not themes[0].get("chunk_ids") and evidence_artifact.exists():
            enriched = json.loads(evidence_artifact.read_text(encoding="utf-8")).get("themes", [])
            if len(enriched) >= len(themes):
                themes = enriched[:len(themes)]
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
            matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
            matrix = json.loads(matrix_path.read_text(encoding="utf-8")) if matrix_path.exists() else {"rows": []}
            feature_name = "theme_{}".format(selected_index)
            company_scores = []
            for company_row in matrix.get("rows", []):
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
                database_records = company_evidence_from_database(selected_company, selected_question)
                if database_records:
                    chunk_ids = [record.get("chunk_id") for record in database_records]
                    evidence_text = "\n\n".join("Pages {}–{}:\n{}".format(record.get("page_start", "?"), record.get("page_end", "?"), record.get("chunk_text", "")[:1200]) for record in database_records)
                st.markdown('<div class="card"><div class="eyebrow">{} · {}/100</div><h2>Why this company received this score</h2><p class="muted">This evidence is loaded for the selected company and selected question. Changing the company above changes this section.</p><p><b>Scoring rationale</b></p><p>{}</p><p><b>Supporting chunk IDs</b></p><p>{}</p><p><b>Source evidence</b></p><p>{}</p></div>'.format(selected_company, company_score.get("Score (0–100)", "—"), rationale, ", ".join(chunk_ids) or "Not stored in this artifact", evidence_text), unsafe_allow_html=True)
            st.stop()
        st.markdown('<div class="muted">{} · {} · {} peer companies · {} benchmark factors</div>'.format(normalize_industry(industry), normalize_fiscal_year(year), len(benchmark.get("companies", [])), len(themes)), unsafe_allow_html=True)
        matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
        rows = []
        if matrix_path.exists():
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
            for name in matrix.get("feature_names", []):
                values = [row.get("features", {}).get(name) for row in matrix.get("rows", []) if row.get("features", {}).get(name) is not None]
                if values:
                    rows.append({"TOE question": name, "Industry average (0–5)": round(sum(values) / len(values), 2)})
        overall = round(sum(row["Industry average (0–5)"] for row in rows) / len(rows) * 20, 1) if rows else None
        dimensions = {dimension: [] for dimension in ("technology", "organization", "environment")}
        for index, row in enumerate(rows):
            dimension = themes[index].get("toe_dimension", "technology") if index < len(themes) else "technology"
            dimensions.setdefault(dimension, []).append(row["Industry average (0–5)"] * 20)
        cols = st.columns(2)
        with cols[0]:
            st.markdown('<div class="card"><div class="eyebrow">{} cumulative score</div><div class="big-score">{}</div><div class="muted">/ 100 · peer companies weighted by factor</div></div>'.format(normalize_industry(industry), int(overall) if overall is not None else "—"), unsafe_allow_html=True)
        with cols[1]:
            html = '<div class="card"><h3>By TOE dimension</h3>'
            colors = {"technology": "#32648f", "organization": "#a06b0b", "environment": "#347751"}
            for dimension, values in dimensions.items():
                value = round(sum(values) / len(values)) if values else 0
                html += '<div class="factor"><span>{}</span><div class="bar"><div class="fill" style="width:{}%;background:{}"></div></div><b>{}</b></div>'.format(dimension.title(), value, colors[dimension], value)
            st.markdown(html + '</div>', unsafe_allow_html=True)
        st.markdown('<div class="section"><h3>Factors and weights</h3><p class="muted">Scores are calculated from comparable annual-report evidence for this industry and year.</p>', unsafe_allow_html=True)
        for index, row in enumerate(rows):
            dimension = themes[index].get("toe_dimension", "technology") if index < len(themes) else "technology"
            value = round(row["Industry average (0–5)"] * 20)
            question_text = themes[index].get("question", row["TOE question"]) if index < len(themes) else row["TOE question"]
            st.markdown('<div class="factor"><span>{}</span><div class="bar"><div class="fill" style="width:{}%;background:{}"></div></div><span class="factor-value">{} / 100 · {}<br><small>select below for detail</small></span></div>'.format(question_text, value, colors.get(dimension, "#32648f"), value, dimension.title()), unsafe_allow_html=True)
            if st.button("View question evidence →", key="open_factor_{}".format(index), type="secondary"):
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
            matrix = json.loads(matrix_path.read_text(encoding="utf-8")) if matrix_path.exists() else {"rows": []}
            company_scores = []
            for company_row in matrix.get("rows", []):
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
