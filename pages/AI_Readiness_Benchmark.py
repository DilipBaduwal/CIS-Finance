"""Client-facing pilot benchmark view."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import streamlit as st

st.set_page_config(page_title="AI Readiness Benchmark", page_icon="📊", layout="wide")
st.title("📊 AI Readiness Benchmark")
st.caption("Information Technology · fiscal year 2025–2026 · TCS, Infosys, and Wipro")
path = Path("artifacts/it_ai_readiness_benchmark_2025-2026.cleaned.json")
if not path.exists():
    path = Path("artifacts/it_ai_readiness_benchmark_2025-2026.json")
if not path.exists():
    st.info("The benchmark artifact is not ready yet.")
    st.stop()
data = json.loads(path.read_text(encoding="utf-8"))
themes = data.get("themes", [])
st.write("{} benchmark themes discovered; maximum 10 per TOE dimension.".format(len(themes)))
for dimension in ("technology", "organization", "environment"):
    items = [item for item in themes if item.get("toe_dimension") == dimension]
    with st.expander("{} · {} themes".format(dimension.title(), len(items)), expanded=True):
        for item in items:
            st.markdown("**{}** · {}".format(item.get("theme", "Unnamed theme"), item.get("question_status", "pending")))
            st.write(item.get("question", "Question pending cleanup."))
            st.caption("Supported by: {}".format(", ".join(item.get("supporting_companies", []))))
