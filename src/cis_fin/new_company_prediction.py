"""Predict a new same-cohort company using an existing regression artifact."""
import json
from pathlib import Path


def predict_new_company(model_path, company, features):
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    names = model["feature_names"]
    vector = [features.get(name, 0.0) for name in names]
    score = model["intercept"] + sum(coefficient * value for coefficient, value in zip(model["coefficients"], vector))
    return {
        "company": company,
        "industry": model["industry"],
        "fiscal_year": model["fiscal_year"],
        "predicted_score": round(max(0.0, min(100.0, score)), 1),
        "feature_vector": dict(zip(names, vector)),
        "provisional": model.get("provisional", True),
    }
