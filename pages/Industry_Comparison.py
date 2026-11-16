import streamlit as st
from cis_fin.client_benchmark import find_benchmark, find_matrix, cohort_rows, company_scores

st.set_page_config(page_title="Industry comparison · CIS-FIN", page_icon="◈", layout="wide")
st.markdown("<style>.stApp{background:#f5f7fb}.block-container{max-width:1380px;padding:2rem 2.5rem 5rem}.hero{background:linear-gradient(135deg,#14264d,#2457d6);padding:2.3rem;border-radius:22px;color:#fff}.hero h1{color:#fff;font-size:3rem;margin:.2rem 0}.hero p{color:#dce7ff}.card{background:#fff;border:1px solid #e1e7f1;border-radius:18px;padding:1.4rem;box-shadow:0 8px 24px #243b6b0d;min-height:150px;box-sizing:border-box}.snapshot{height:190px;display:flex;flex-direction:column;justify-content:center}.score{font-size:4.5rem;font-weight:750;color:#173ea7;line-height:1}.bar{height:11px;background:#e7edf7;border-radius:99px;overflow:hidden;margin:1rem 0 .55rem}.fill{height:100%;background:#2457d6;border-radius:99px}.stButton>button{background:#2457d6!important;color:#fff!important;border:0!important;border-radius:10px!important;font-weight:700!important;padding:.7rem 1.2rem!important}.stButton>button p,.stButton>button span,.stButton>button div{color:#fff!important}.stButton>button:hover{background:#173ea7!important;color:#fff!important}</style>", unsafe_allow_html=True)
industry = st.session_state.get("selected_industry", "Information Technology")
year = st.session_state.get("selected_year", "2025-2026")
benchmark = find_benchmark(industry, year)
if not benchmark:
    st.error("No benchmark is available for this industry and year.")
    st.stop()
matrix = find_matrix(benchmark)
rows = cohort_rows(matrix, benchmark)
values = [v for row in rows for v in row.get("feature_vector", []) if isinstance(v, (int, float))]
overall = round(sum(values) / len(values) * 20, 1) if values else None
ranked = company_scores(matrix, benchmark)
st.markdown('<div class="hero"><div>INDUSTRY COMPARISON</div><h1>{}</h1><p>TOE framework · {} · {} peer companies</p></div>'.format(industry, year, len(ranked)), unsafe_allow_html=True)
if st.button("← Change comparison type"):
    st.switch_page("client_app.py")
st.markdown("### Industry snapshot")
a, b, c = st.columns(3)
with a:
    st.markdown('<div class="card snapshot"><small>INDUSTRY TOE SCORE</small><div class="score">{}</div><span>/ 100</span></div>'.format(int(overall) if overall is not None else "—"), unsafe_allow_html=True)
with b:
    st.markdown('<div class="card snapshot"><small>TOP COMPANY</small><h2>{}</h2><div class="score">{}</div><span>/ 100</span></div>'.format(ranked[0]["company"] if ranked else "—", int(ranked[0]["score"]) if ranked else "—"), unsafe_allow_html=True)
with c:
    st.markdown('<div class="card snapshot"><small>BENCHMARK QUESTIONS</small><div class="score">{}</div><span>TOE factors</span></div>'.format(len(benchmark.get("themes", []))), unsafe_allow_html=True)
st.markdown("### TOE performance")
for dimension in ("technology", "organization", "environment"):
    indexes = [i for i, t in enumerate(benchmark.get("themes", [])) if t.get("toe_dimension") == dimension]
    vals = [row.get("feature_vector", [])[i] for row in rows for i in indexes if i < len(row.get("feature_vector", [])) and isinstance(row.get("feature_vector", [])[i], (int, float))]
    score = round(sum(vals) / len(vals) * 20) if vals else 0
    st.markdown('<div class="card" style="margin:.6rem 0"><b>{}</b><div class="bar"><div class="fill" style="width:{}%"></div></div><strong>{} / 100</strong></div>'.format(dimension.title(), score, score), unsafe_allow_html=True)
st.markdown("### Company ranking")
st.dataframe(ranked, use_container_width=True, hide_index=True)
st.markdown("### TOE questions")
st.caption("Industry-average score for every benchmark question. Scores are shown on a 0–100 scale.")
question_rows = []
for index, theme in enumerate(benchmark.get("themes", [])):
    values = [row.get("feature_vector", [])[index] for row in rows if index < len(row.get("feature_vector", [])) and isinstance(row.get("feature_vector", [])[index], (int, float))]
    average = round(sum(values) / len(values) * 20, 1) if values else None
    question_rows.append({"#": index + 1, "TOE": theme.get("toe_dimension", "").title(), "Question": theme.get("question", ""), "Industry average": average})
st.markdown("### Open question evidence")
st.caption("Select a question to view its score and the evidence used by the AI.")
for index, item in enumerate(question_rows):
    left, right = st.columns([8, 1])
    with left:
        st.markdown('<div class="card" style="min-height:0;padding:1rem 1.2rem"><small>{} · Question {}</small><br><b>{}</b><br><span>Industry average: {} / 100</span></div>'.format(item["TOE"], item["#"], item["Question"], item["Industry average"] if item["Industry average"] is not None else "—"), unsafe_allow_html=True)
    with right:
        if st.button("Open", key="open_question_{}".format(index)):
            st.session_state["selected_question_index"] = index
            st.switch_page("pages/Question_Detail.py")
