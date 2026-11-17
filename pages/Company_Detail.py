import streamlit as st

from cis_fin.client_benchmark import find_benchmark, find_matrix, cohort_rows, company_scores

st.set_page_config(page_title="Company detail · CIS-FIN", page_icon="◈", layout="wide")
st.markdown("""<style>
.stApp{background:#f5f7fb}.block-container{max-width:1380px;padding:2rem 2.5rem 5rem}
.hero{background:linear-gradient(135deg,#14264d,#2457d6);padding:2.3rem;border-radius:22px;color:#fff}
.hero h1{color:#fff;font-size:2.7rem;margin:.3rem 0}.hero p{color:#dce7ff}
.card{background:#fff;border:1px solid #e1e7f1;border-radius:18px;padding:1.25rem 1.4rem;box-shadow:0 8px 24px #243b6b0d;margin:.7rem 0}
.score{font-size:3.5rem;font-weight:750;color:#173ea7;line-height:1}.muted{color:#62708a}
.stButton>button{background:#2457d6!important;color:#fff!important;border:0!important;border-radius:10px!important;font-weight:700!important}
.stButton>button p,.stButton>button span{color:#fff!important}
</style>""", unsafe_allow_html=True)

industry = st.session_state.get("selected_industry", "Information Technology")
year = st.session_state.get("selected_year", "2025-2026")
company = st.session_state.get("selected_company")
benchmark = find_benchmark(industry, year)
if not benchmark or not company:
    st.error("Select a company from the industry ranking first.")
    st.stop()
matrix = find_matrix(benchmark)
rows = cohort_rows(matrix, benchmark)
row = next((item for item in rows if str(item.get("company")) == str(company)), None)
if not row:
    st.error("No score record is available for {}.".format(company))
    st.stop()

values = row.get("feature_vector", [])
available = [value for value in values if isinstance(value, (int, float))]
ranked = company_scores(matrix, benchmark)
company_summary = next((item for item in ranked if item.get("company") == company), None)
overall = company_summary.get("score") if company_summary else None
st.markdown('<div class="hero"><div>COMPANY DETAIL</div><h1>{}</h1><p>{} · {} · question-level AI-readiness evidence</p></div>'.format(company, industry, year), unsafe_allow_html=True)
back, compare = st.columns([1, 1])
with back:
    if st.button("← Back to industry comparison"):
        st.switch_page("pages/Industry_Comparison.py")
with compare:
    if st.button("Compare {} with another company →".format(company)):
        st.session_state["peer_prefill_company"] = company
        st.session_state.pop("peer_a", None)
        st.switch_page("pages/Peer_Comparison.py")

a, b, c = st.columns(3)
with a:
    st.markdown('<div class="card"><small>COMPANY TOE SCORE</small><div class="score">{}</div><span>/ 100</span></div>'.format(int(overall) if overall is not None else "—"), unsafe_allow_html=True)
with b:
    st.markdown('<div class="card"><small>QUESTIONS WITH SCORES</small><div class="score">{}</div><span>/ {}</span></div>'.format(len(available), len(benchmark.get("themes", []))), unsafe_allow_html=True)
with c:
    st.markdown('<div class="card"><small>INDUSTRY</small><h2>{}</h2><span>{}</span></div>'.format(industry, year), unsafe_allow_html=True)

st.markdown("### TOE question scores")
st.caption("Questions are ordered by AI score from highest to lowest. Unavailable questions are shown last.")
question_items = []
for index, theme in enumerate(benchmark.get("themes", [])):
    value = values[index] if index < len(values) and isinstance(values[index], (int, float)) else None
    question_items.append((index, theme, value))
question_items.sort(key=lambda item: (item[2] is not None, item[2] if item[2] is not None else -1), reverse=True)
for index, theme, value in question_items:
    score = round(value * 20, 1) if value is not None else None
    st.markdown('<div class="card"><small>{} · Question {}</small><h3>{}</h3><strong>{}</strong> / 100 <span class="muted"> · AI score: {} / 5</span></div>'.format(theme.get("toe_dimension", "TOE").title(), index + 1, theme.get("question", ""), score if score is not None else "Not available", value if value is not None else "—"), unsafe_allow_html=True)
