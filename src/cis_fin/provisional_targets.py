"""Create explicitly provisional pilot targets from reviewed feature values."""
import json
from pathlib import Path


def build_provisional_targets(matrix_path, output_path):
    matrix = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    targets = []
    for row in matrix.get("rows", []):
        values = [value for value in row.get("feature_vector", []) if isinstance(value, (int, float))]
        if not values:
            continue
        targets.append({
            "company": row["company"],
            "target_score": round(sum(values) / len(values) * 20, 1),
            "source": "provisional_feature_average",
            "feature_count": len(values),
        })
    result = {
        "industry": matrix.get("industry"),
        "fiscal_year": matrix.get("fiscal_year"),
        "targets": targets,
        "provisional": True,
        "warning": "Replace these targets with human-reviewed scores before production use.",
    }
    Path(output_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
