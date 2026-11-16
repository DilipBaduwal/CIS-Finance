import streamlit as st
from cis_fin.client_benchmark import find_benchmark, find_matrix, cohort_rows, company_scores, evidence

st.set_page_config(page_title="Peer comparison · CIS-FIN", page_icon="◈", layout="wide")
st.markdown("<style>.stApp{background:#f5f7fb}.block-container{max-width:1380px;padding:2rem 2.5rem 5rem}.hero{background:linear-gradient(135deg,#14264d,#2457d6);padding:2.3rem;border-radius:22px;color:#fff}.hero h1{color:#fff;font-size:3rem;margin:.2rem 0}.hero p{color:#dce7ff}.card{background:#fff;border:1px solid #e1e7f1;border-radius:18px;padding:1.4rem;box-shadow:0 8px 24px #243b6b0d}.insight{background:#edf3ff;border-left:4px solid #2457d6;border-radius:12px;padding:1rem;color:#31518b}</style>", unsafe_allow_html=True)
industry = st.session_state.get("selected_industry", "Information Technology")
year = st.session_state.get("selected_year", "2025-2026")
benchmark = find_benchmark(industry, year)
if not benchmark:
    st.error("No benchmark is available for this industry and year.")
    st.stop()
matrix = find_matrix(benchmark)
rows = cohort_rows(matrix, benchmark)
ranked = company_scores(matrix, benchmark)
options = [x["company"] for x in ranked]
st.markdown('<div class="hero"><div>PEER COMPARISON</div><h1>Compare companies</h1><p>{} · {} · evidence-led peer analysis</p></div>'.format(industry, year), unsafe_allow_html=True)
if st.button("← Change comparison type"):
    st.switch_page("client_app.py")
if not options:
    st.warning("Company scores are not available yet.")
    st.stop()
a, b = st.columns(2)
with a:
    company_a = st.selectbox("Company A", options, key="peer_a")
with b:
    company_b = st.selectbox("Company B", options, index=min(1, len(options) - 1), key="peer_b")
def row_for(name):
    return next(row for row in rows if row.get("company") == name)
ra, rb = row_for(company_a), row_for(company_b)
score_a = next(x["score"] for x in ranked if x["company"] == company_a)
score_b = next(x["score"] for x in ranked if x["company"] == company_b)
higher = company_a if score_a >= score_b else company_b
st.markdown('<div class="insight"><b>{}</b> is higher by <b>{} points</b> overall ({}/100 vs {}/100).</div>'.format(higher, abs(score_a - score_b), company_a, score_a, company_b, score_b), unsafe_allow_html=True)
question_rows = []
for i, theme in enumerate(benchmark.get("themes", [])):
    va = ra.get("features", {}).get("theme_{}".format(i))
    vb = rb.get("features", {}).get("theme_{}".format(i))
    question_rows.append({"TOE": theme.get("toe_dimension", "").title(), "Question": theme.get("question", ""), company_a: round(va * 20, 1) if va is not None else None, company_b: round(vb * 20, 1) if vb is not None else None, "Difference": round((va - vb) * 20, 1) if va is not None and vb is not None else None})
st.markdown("### Side-by-side question comparison")
st.caption("Compare both selected companies question by question.")
for item in question_rows:
    delta = item.get("Difference")
    direction = "Higher" if isinstance(delta, (int, float)) and delta > 0 else "Lower" if isinstance(delta, (int, float)) and delta < 0 else "Equal"
    st.markdown('<div class="card"><small>{}</small><h3>{}</h3><div style="display:flex;gap:2rem"><div><b>{}</b><br><span style="font-size:1.8rem;color:#2457d6">{}</span> / 100</div><div><b>{}</b><br><span style="font-size:1.8rem;color:#2457d6">{}</span> / 100</div><div><b>{}</b><br>{} points</div></div></div>'.format(item["TOE"], item["Question"], company_a, item[company_a] if item[company_a] is not None else "—", company_b, item[company_b] if item[company_b] is not None else "—", direction, abs(delta) if isinstance(delta, (int, float)) else "—"), unsafe_allow_html=True)
selected = st.selectbox("Deep analysis", [x["Question"] for x in question_rows])
st.markdown('<div class="card"><h2>{}</h2><p>Compare the evidence behind this question for both companies.</p></div>'.format(selected), unsafe_allow_html=True)
for company in (company_a, company_b):
    with st.expander("{} evidence".format(company), expanded=True):
        records = evidence(company, selected)
        if records:
            for record in records:
                st.caption("Pages {}–{} · {}".format(record.get("page_start", "?"), record.get("page_end", "?"), record.get("chunk_id", "")))
                st.write(record.get("chunk_text", "")[:1000])
        else:
            st.info("Evidence details are not available for this company yet.")
