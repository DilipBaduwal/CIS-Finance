"""Data helpers shared by the client-facing comparison pages."""
import json
import re
import uuid
from pathlib import Path

from cis_fin.benchmark import normalize_industry, normalize_fiscal_year
from cis_fin.company_queries import CompanyLookupError, export_document_chunks, list_recent_documents
from cis_fin.settings import Settings


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
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def find_matrix(benchmark):
    key = normalize_industry(benchmark.get("industry", "")).lower().replace(" ", "_")
    for path in (Path("artifacts/{}_ai_readiness_regression_matrix.json".format(key)), Path("artifacts/it_ai_readiness_regression_matrix.json")):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
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
