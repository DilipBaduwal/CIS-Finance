"""Build a transparent feature matrix for the AI-readiness regression model."""
import json
from pathlib import Path


def build_regression_matrix(source_path, output_path):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    rows = {}
    excluded = []
    for key, feature in data.get("regression_features", {}).items():
        if not isinstance(feature, dict) or "score" not in feature:
            excluded.append({"key": key, "reason": "missing_valid_feature"})
            continue
        company = feature.get("company")
        row = rows.setdefault(company, {"company": company, "features": {}, "provenance": {}})
        name = "theme_{}".format(feature.get("theme_index"))
        row["features"][name] = feature["score"]
        row["provenance"][name] = feature.get("status", "valid")
    feature_names = sorted({name for row in rows.values() for name in row["features"]})
    for row in rows.values():
        row["feature_vector"] = [row["features"].get(name) for name in feature_names]
    result = {
        "industry": data.get("industry", "Information Technology"),
        "fiscal_year": data.get("fiscal_year", "2025-2026"),
        "feature_names": feature_names,
        "rows": list(rows.values()),
        "excluded": excluded,
        "training_ready": False,
        "training_note": "Reviewed target scores are required before regression training.",
    }
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
