"""Score, ranking, average, and question-level reporting helpers."""
import json
from pathlib import Path


def cohort_summary(model):
    rows = model.get("predictions", [])
    values = [float(row["predicted_score"]) for row in rows]
    return {"average": round(sum(values) / len(values), 1) if values else None, "highest": max(values) if values else None, "lowest": min(values) if values else None, "ranked": sorted(rows, key=lambda row: row.get("predicted_score", 0), reverse=True)}


def question_comparison(matrix_path):
    matrix = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    rows = matrix.get("rows", [])
    result = []
    for index, name in enumerate(matrix.get("feature_names", [])):
        values = [{"company": row.get("company"), "score": row.get("features", {}).get(name)} for row in rows]
        numeric = [item["score"] for item in values if item["score"] is not None]
        result.append({"question": name, "industry_average": round(sum(numeric) / len(numeric), 2) if numeric else None, "companies": sorted(values, key=lambda item: item["score"] if item["score"] is not None else -1, reverse=True)})
    return result


def score_deltas(previous_path, current_path):
    previous = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    current = json.loads(Path(current_path).read_text(encoding="utf-8"))
    before = {row["company"]: row.get("predicted_score") for row in previous.get("predictions", [])}
    return [{"company": row["company"], "previous_score": before.get(row["company"]), "current_score": row.get("predicted_score"), "change": None if before.get(row["company"]) is None else round(row.get("predicted_score", 0) - before[row["company"]], 1)} for row in current.get("predictions", [])]
