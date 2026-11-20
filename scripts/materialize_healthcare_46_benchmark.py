"""Materialize the imported 46-question healthcare benchmark for fast client reads."""
import json
from pathlib import Path

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


OUT = Path("artifacts/healthcare_46_question_benchmark.json")


def main():
    with psycopg.connect(secure_postgres_dsn(Settings.from_env().database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT q.question_key, q.construct, q.toe_dimension, q.question_text, q.source,
                                    e.chunk_id::text, c.page_start, c.page_end, c.chunk_text, e.match_count,
                                    co.symbol, e.ai_score, e.ai_confidence, e.ai_rationale, e.evidence_quote
                               FROM healthcare_custom_questions q
                               LEFT JOIN healthcare_custom_question_evidence e ON e.question_id=q.question_id
                               LEFT JOIN chunks c ON c.chunk_id=e.chunk_id
                               LEFT JOIN companies co ON co.company_id=c.company_id
                              ORDER BY q.question_key, e.match_count DESC NULLS LAST""")
            questions = {}
            for key, construct, dimension, text, source, chunk_id, start, end, chunk_text, match_count, company, score, confidence, rationale, quote in cursor.fetchall():
                item = questions.setdefault(key, {"question_id": key, "construct": construct, "toe_dimension": dimension, "question": text, "source": source, "matches": []})
                if chunk_id:
                    item["matches"].append({"chunk_id": chunk_id, "company": company or "Healthcare", "page_start": start, "page_end": end, "chunk_text": chunk_text or "", "match_terms": match_count, "ai_score": score, "confidence": confidence, "rationale": rationale or "", "evidence_quote": quote or ""})
    items = list(questions.values())
    companies = sorted({m["company"] for q in items for m in q["matches"] if m["company"] != "Healthcare"})
    rows = []
    for company in companies:
        features = {}
        for index, question in enumerate(items):
            scores = [m["ai_score"] for m in question["matches"] if m["company"] == company and m["ai_score"] is not None]
            if scores:
                features["theme_{}".format(index)] = round(sum(scores) / len(scores), 2)
        rows.append({"company": company, "features": features, "feature_vector": [features.get("theme_{}".format(i)) for i in range(len(items))], "provenance": {}})
    result = {"industry": "Healthcare", "fiscal_year": "2025-2026", "question_count": len(items), "companies": companies, "themes": items, "matrix": {"feature_names": ["theme_{}".format(i) for i in range(len(items))], "rows": rows, "training_ready": False, "training_note": "Saved PostgreSQL chunk scores; missing scores remain unavailable."}}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("Materialized {} questions, {} evidence mappings, {} companies into {}".format(len(items), sum(len(q["matches"]) for q in items), len(companies), OUT))


if __name__ == "__main__":
    main()
