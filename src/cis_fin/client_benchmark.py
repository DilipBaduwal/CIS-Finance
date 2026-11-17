"""Data helpers shared by the client-facing comparison pages."""
import json
import re
import uuid
from pathlib import Path

import streamlit as st

from cis_fin.benchmark import normalize_industry, normalize_fiscal_year
from cis_fin.company_queries import CompanyLookupError, export_document_chunks, list_recent_documents
from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


@st.cache_data(ttl=300, show_spinner=False)
def find_benchmark(industry, year):
    normalized_year = normalize_fiscal_year(year)
    candidates = sorted(Path("artifacts").glob("benchmark_*_v*.json"), key=lambda item: int(item.stem.rsplit("_v", 1)[-1]), reverse=True)
    for path in candidates:
        item = json.loads(path.read_text(encoding="utf-8"))
        if normalize_industry(item.get("industry", "")) == normalize_industry(industry) and normalize_fiscal_year(item.get("fiscal_year", "")) == normalized_year:
            return item
    key = normalize_industry(industry).lower().replace(" ", "_")
    for path in (Path("artifacts/{}_ai_readiness_benchmark_{}.json".format(key, normalized_year)), Path("artifacts/it_ai_readiness_benchmark_{}.json".format(normalized_year))):
        if path.exists() and (normalize_industry(industry) == "Information Technology" or key in path.name):
            item = json.loads(path.read_text(encoding="utf-8"))
            if normalize_industry(industry) in {"Healthcare", "Pharmaceuticals"}:
                item = _healthcare_database_questions(item)
            return item
    return None


def _healthcare_database_questions(benchmark):
    """Use the persisted healthcare question set that produced the scores."""
    try:
        import psycopg

        settings = Settings.from_env()
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT theme_index, question_text, toe_dimension, chunk_ids
                       FROM healthcare_benchmark_questions
                       WHERE question_status IN ('active', 'generated')
                       ORDER BY theme_index"""
                )
                rows = cursor.fetchall()
        if rows:
            benchmark["themes"] = [
                {"question": question, "toe_dimension": dimension, "chunk_ids": chunk_ids}
                for _, question, dimension, chunk_ids in rows
            ]
            benchmark["question_count"] = len(rows)
    except Exception:
        pass
    return benchmark


@st.cache_data(ttl=300, show_spinner=False)
def find_matrix(benchmark):
    key = normalize_industry(benchmark.get("industry", "")).lower().replace(" ", "_")
    paths = [Path("artifacts/{}_ai_readiness_regression_matrix.json".format(key))]
    if normalize_industry(benchmark.get("industry", "")) == "Information Technology":
        paths.append(Path("artifacts/it_ai_readiness_regression_matrix.json"))
    for path in paths:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    # Healthcare scores are stored per question/chunk in PostgreSQL rather
    # than in the IT regression-matrix artifact. Build the same matrix shape
    # from those saved scores so all comparison pages use one interface.
    if normalize_industry(benchmark.get("industry", "")) in {"Healthcare", "Pharmaceuticals"}:
        try:
            import psycopg

            settings = Settings.from_env()
            source = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
            question_indexes = {
                theme.get("question", ""): index
                for index, theme in enumerate(benchmark.get("themes", []))
            }
            rows_by_company = {}
            with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT c.symbol, b.question_text, b.score
                        FROM benchmark_chunk_scores b
                        JOIN chunks ch ON ch.chunk_id = b.chunk_id
                        JOIN companies c ON c.company_id = ch.company_id
                        JOIN documents d ON d.document_id = ch.document_id
                        WHERE b.source_benchmark = %s
                          AND lower(c.industry) IN ('healthcare', 'pharmaceuticals')
                        """,
                        (source,),
                    )
                    for company, question, score in cursor.fetchall():
                        index = question_indexes.get(question)
                        if index is None or score is None:
                            continue
                        company = str(company).upper()
                        rows_by_company.setdefault(company, {}).setdefault(index, []).append(float(score))
            rows = []
            for company, scores in rows_by_company.items():
                feature_vector = []
                features = {}
                for index in range(len(benchmark.get("themes", []))):
                    values = scores.get(index, [])
                    value = round(sum(values) / len(values), 3) if values else None
                    feature_vector.append(value)
                    if value is not None:
                        features["theme_{}".format(index)] = value
                if any(value is not None for value in feature_vector):
                    rows.append({"company": company, "feature_vector": feature_vector, "features": features})
            return {"rows": rows, "feature_names": [theme.get("question", "") for theme in benchmark.get("themes", [])]}
        except (Exception,):
            return {"rows": [], "feature_names": []}
    return {"rows": [], "feature_names": []}


def cohort_rows(matrix, benchmark):
    allowed = {str(x).upper() for x in benchmark.get("companies", [])}
    return [row for row in matrix.get("rows", []) if str(row.get("company", "")).upper() in allowed]


def company_scores(matrix, benchmark):
    result = []
    for row in cohort_rows(matrix, benchmark):
        values = [x for x in row.get("feature_vector", []) if isinstance(x, (int, float))]
        if values:
            result.append({"company": row.get("company"), "score": round(sum(values) / len(values) * 20, 1)})
    return sorted(result, key=lambda x: x["score"], reverse=True)


@st.cache_data(ttl=300, show_spinner=False)
def evidence(company, question):
    try:
        documents = list_recent_documents(Settings.from_env(), limit=100)
        selected = next((row for row in documents if str(row[2]).upper() == str(company).upper()), None)
        if not selected:
            return []
        records = export_document_chunks(Settings.from_env(), uuid.UUID(str(selected[0])))
        terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", question.lower()))
        return sorted(records, key=lambda row: len(terms & set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", row.get("chunk_text", "").lower()))), reverse=True)[:2]
    except (CompanyLookupError, ValueError, RuntimeError):
        return []
