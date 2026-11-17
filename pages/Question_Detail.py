import streamlit as st
from cis_fin.client_benchmark import find_benchmark, find_matrix, cohort_rows, ai_justification

st.set_page_config(page_title="Question evidence · CIS-FIN", page_icon="◈", layout="wide")
st.markdown("""<style>
.stApp{background:#f5f7fb}.block-container{max-width:1380px;padding:2rem 2.5rem 5rem}.hero{background:linear-gradient(135deg,#14264d,#2457d6);padding:2.3rem;border-radius:22px;color:#fff}.hero h1{color:#fff;font-size:2.4rem;margin:.3rem 0}.hero p{color:#dce7ff}.card{background:#fff;border:1px solid #e1e7f1;border-radius:18px;padding:1.4rem;box-shadow:0 8px 24px #243b6b0d;margin:.8rem 0}.score{font-size:3.5rem;font-weight:750;color:#173ea7}.evidence{background:#f8faff;border-left:4px solid #2457d6;padding:1rem;border-radius:10px;color:#263754}[data-testid="stSlider"] [role="slider"]{background:#2457d6!important;border:3px solid #fff!important;box-shadow:0 2px 8px #173ea755!important;cursor:grab!important}[data-testid="stSlider"] [role="slider"]:active{cursor:grabbing!important}[data-testid="stTextInput"]{margin-left:0;max-width:360px}[data-testid="stTextInput"] input{border-radius:14px!important;border:0!important;background:#edf0f4!important;color:#263754!important;padding:.7rem 1rem!important;box-shadow:none!important}
</style>""", unsafe_allow_html=True)
industry = st.session_state.get("selected_industry", "Information Technology")
year = st.session_state.get("selected_year", "2025-2026")
benchmark = find_benchmark(industry, year)
if not benchmark:
    st.error("No benchmark is available for this industry and year.")
    st.stop()
matrix = find_matrix(benchmark)
themes = benchmark.get("themes", [])
index = int(st.session_state.get("selected_question_index", 0))
index = max(0, min(index, len(themes) - 1))
theme = themes[index]
rows = cohort_rows(matrix, benchmark)
values = [row.get("features", {}).get("theme_{}".format(index)) for row in rows]
values = [value for value in values if isinstance(value, (int, float))]
average = round(sum(values) / len(values) * 20, 1) if values else None
st.markdown('<div class="hero"><div>{} · QUESTION {}</div><h1>{}</h1><p>{} · {} · AI-readiness evidence review</p></div>'.format(theme.get("toe_dimension", "TOE").upper(), index + 1, theme.get("question", ""), industry, year), unsafe_allow_html=True)
if st.button("← Back to industry comparison"):
    st.switch_page("pages/Industry_Comparison.py")
left, right = st.columns([1, 2])
with left:
    st.markdown('<div class="card"><small>INDUSTRY AVERAGE</small><div class="score">{}</div><span>/ 100</span></div>'.format(int(average) if average is not None else "—"), unsafe_allow_html=True)
with right:
    st.markdown('<div class="card"><small>WHAT THIS SHOWS</small><h3>AI evidence used for this TOE question</h3><p class="muted">The evidence below comes from the chunks selected and scored by the AI for each company. It is not the full annual report.</p></div>', unsafe_allow_html=True)
st.markdown("### Company scores and AI evidence")
company_search = st.text_input("Search company", placeholder="Type a company name…", key="question_company_search")
filtered_rows = [row for row in rows if not company_search.strip() or company_search.strip().casefold() in str(row.get("company", "")).casefold()]
filtered_rows.sort(
    key=lambda row: (
        isinstance(row.get("feature_vector", [])[index] if index < len(row.get("feature_vector", [])) else None, (int, float)),
        row.get("feature_vector", [])[index] if index < len(row.get("feature_vector", [])) and isinstance(row.get("feature_vector", [])[index], (int, float)) else -1,
    ),
    reverse=True,
)
company_names = [str(row.get("company", "Unknown")) for row in filtered_rows]
if company_search.strip() and not company_names:
    st.info("No company matched '{}'. Try another company name.".format(company_search.strip()))
selector_a, selector_b = st.columns(2)
with selector_a:
    company_a = st.selectbox("Company A", company_names, key="question_detail_company_a_{}".format(index)) if company_names else None
with selector_b:
    b_key = "question_detail_company_b_{}".format(index)
    if company_a and st.session_state.get(b_key) == company_a:
        st.session_state.pop(b_key, None)
    company_b = st.selectbox("Company B", company_names, index=min(1, len(company_names) - 1), key=b_key) if company_names else None
selected_names = [name for name in (company_a, company_b) if name]
# Preserve the selector order. ``rows`` is database/ranking order, which
# previously made Company A and Company B cards appear swapped.
rows_by_company = {str(row.get("company")): row for row in rows}
visible_rows = [rows_by_company[name] for name in selected_names if name in rows_by_company]
company_columns = st.columns(2)
for position, row in enumerate(visible_rows):
    company = row.get("company", "Unknown")
    value = row.get("features", {}).get("theme_{}".format(index))
    score = round(value * 20, 1) if isinstance(value, (int, float)) else None
    with company_columns[position % len(company_columns)]:
        saved = ai_justification(company, selected_question) if value is not None else {"rationale": "No saved score or evidence for this question.", "confidence": None}
        st.markdown('<div class="card company-card"><small>{}</small><div class="score">{}</div><b>/ 100</b><p class="muted">AI score: {} / 5<br>Saved confidence: {}</p></div>'.format(company, score if score is not None else "—", value if value is not None else "—", saved.get("confidence") if saved.get("confidence") is not None else "—"), unsafe_allow_html=True)
        if value is not None:
            st.markdown('<div class="evidence"><b>Evidence supporting this score</b><br>{}</div>'.format(saved.get("rationale", "No saved evidence available.").replace("<", "&lt;").replace(">", "&gt;")), unsafe_allow_html=True)
        else:
            st.info("No saved score or supporting evidence for this question.")
