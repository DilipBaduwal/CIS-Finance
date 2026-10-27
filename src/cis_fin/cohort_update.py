"""Safe pilot update flow for adding a company to an existing benchmark cohort."""
import json
from pathlib import Path

from cis_fin.benchmark import normalize_fiscal_year, normalize_industry
from cis_fin.benchmark_versions import create_next_benchmark_version
from cis_fin.pilot_regression import train_pilot


def update_cohort(
    version_path,
    matrix_path,
    targets_path,
    model_path,
    company,
    industry,
    fiscal_year,
    feature_values,
    output_version_path,
    output_matrix_path,
    output_targets_path,
    output_model_path,
):
    """Add one already-processed company and produce a new pilot version.

    This function expects ``feature_values`` to come from the reviewed chunk /
    question scoring step. It deliberately rejects mismatched cohorts and does
    not modify any input artifact.
    """
    version = json.loads(Path(version_path).read_text(encoding="utf-8"))
    expected_industry = normalize_industry(version["industry"])
    expected_year = normalize_fiscal_year(version["fiscal_year"])
    if normalize_industry(industry) != expected_industry:
        raise ValueError("Company industry does not match the benchmark cohort.")
    if normalize_fiscal_year(fiscal_year) != expected_year:
        raise ValueError("Company fiscal year does not match the benchmark cohort.")
    if company in version.get("companies", []):
        raise ValueError("Company is already present in this benchmark version.")

    matrix = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    targets = json.loads(Path(targets_path).read_text(encoding="utf-8"))
    names = list(matrix["feature_names"])
    row = {"company": company, "features": {}, "provenance": {}}
    for name in names:
        value = feature_values.get(name)
        if value is not None:
            value = float(value)
        row["features"][name] = value
        row["provenance"][name] = "new_company_input"
    row["feature_vector"] = [row["features"].get(name) for name in names]
    rows = [existing for existing in matrix.get("rows", []) if existing.get("company") != company]
    rows.append(row)
    matrix_out = dict(matrix, rows=rows, training_ready=False)
    Path(output_matrix_path).write_text(json.dumps(matrix_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Pilot targets are provisional feature-derived targets until human labels exist.
    target_rows = [item for item in targets.get("targets", []) if item.get("company") != company]
    observed = [float(v) for v in feature_values.values() if v is not None]
    if not observed:
        raise ValueError("At least one scored feature is required for the new company.")
    target_rows.append({"company": company, "target_score": round(sum(observed) / len(observed) * 20.0, 1), "provisional": True})
    targets_out = dict(targets, targets=target_rows, provisional=True)
    Path(output_targets_path).write_text(json.dumps(targets_out, ensure_ascii=False, indent=2), encoding="utf-8")

    model = train_pilot(output_matrix_path, output_targets_path, output_model_path)
    new_version = create_next_benchmark_version(
        version_path,
        list(version.get("companies", [])) + [company],
        output_version_path,
    )
    return {"version": new_version, "model": model, "matrix": matrix_out}
