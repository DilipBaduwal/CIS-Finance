"""Post-index benchmark refresh for completed same-cohort reports."""
import os
from pathlib import Path

from cis_fin.benchmark_pilot import run_pilot
from cis_fin.benchmark_versions import create_benchmark_version
from cis_fin.benchmark_store import persist_benchmark_version
import json
import uuid
from cis_fin.company_queries import list_recent_documents, export_document_chunks, list_active_companies
from cis_fin.automatic_features import build_features_from_document
from cis_fin.pilot_regression import train_pilot


def _refresh_feature_model(settings, themes):
    """Rebuild the cohort feature matrix from every completed matching report."""
    documents = list_recent_documents(settings, limit=500)
    company_industries = {row[1]: (row[4] or "") for row in list_active_companies(settings)}
    cohort = [row for row in documents if row[3].replace('-', '').replace('/', '') in ('2526', '202526', '20252026') and any(token in company_industries.get(row[2], '').lower() for token in ('information technology', 'software', 'it services'))]
    rows = []
    names = ["theme_{}".format(index) for index in range(len(themes))]
    targets = []
    for document in cohort:
        records = export_document_chunks(settings, uuid.UUID(str(document[0])))
        features = build_features_from_document(themes, records)
        feature_values = {name: features.get(name, {}).get("score") for name in names}
        row = {"company": document[2], "features": feature_values, "provenance": {name: features.get(name, {}).get("status", "automatic_pilot") for name in names}}
        row["feature_vector"] = [feature_values.get(name) for name in names]
        rows.append(row)
        values = [value for value in row["feature_vector"] if value is not None]
        if values:
            targets.append({"company": document[2], "target_score": round(sum(values) / len(values) * 20.0, 1), "provisional": True})
    if len(rows) < 3:
        return {"status": "deferred", "reason": "fewer_than_three_companies"}
    matrix = {"industry": "Information Technology", "fiscal_year": "2025-2026", "feature_names": names, "rows": rows, "excluded": [], "training_ready": False, "training_note": "Provisional evidence-derived targets."}
    matrix_path = Path("artifacts/it_ai_readiness_regression_matrix.json")
    target_path = Path("artifacts/it_ai_readiness_provisional_targets.json")
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    target_path.write_text(json.dumps({"industry": matrix["industry"], "fiscal_year": matrix["fiscal_year"], "targets": targets, "provisional": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    model = train_pilot(matrix_path, target_path, "artifacts/it_ai_readiness_pilot_regression.json")
    return {"status": "completed", "companies": len(rows), "predictions": len(model.get("predictions", []))}


def auto_update_after_index(settings, progress_callback=None):
    """Refresh the semantic benchmark after indexing, without failing ingestion.

    The refresh is deliberately isolated from the document job: source chunks
    and vectors remain completed if Ollama/Qdrant is temporarily unavailable.
    """
    enabled = os.environ.get("CIS_FIN_AUTO_BENCHMARK_UPDATE", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"status": "disabled"}
    if progress_callback:
        progress_callback("Refreshing the same-industry TOE benchmark…")
    output = Path("artifacts/it_ai_readiness_benchmark_2025-2026.json")
    try:
        result = run_pilot(settings, output)
        feature_refresh = _refresh_feature_model(settings, result.get("themes", []))
        version_paths = sorted(Path("artifacts").glob("benchmark_it_2025-2026_v*.json"))
        current = json.loads(version_paths[-1].read_text(encoding="utf-8")) if version_paths else None
        companies = sorted(result.get("companies", []))
        if current and companies != sorted(current.get("companies", [])):
            next_number = int(current.get("version_number", 1)) + 1
            version = create_benchmark_version(
                current["industry"], current["fiscal_year"], companies,
                current.get("themes", []), next_number,
                Path("artifacts/benchmark_it_2025-2026_v{}.json".format(next_number)),
            )
            try:
                persist_benchmark_version(settings, version, "automatic-post-index-refresh")
            except Exception:
                # The JSON version remains available for recovery if DB persistence
                # is temporarily unavailable; the document job is not failed.
                pass
        else:
            version = current
        if progress_callback:
            progress_callback("Benchmark refreshed: {} recurring TOE themes found.".format(result.get("recurring_theme_count", 0)))
        return {"status": "completed", "version": version.get("version") if version else None, "feature_refresh": feature_refresh, "recurring_theme_count": result.get("recurring_theme_count", 0), "source_chunk_count": result.get("source_chunk_count", 0)}
    except Exception as exc:
        if progress_callback:
            progress_callback("Benchmark refresh deferred; document processing remains complete.")
        return {"status": "deferred", "error": type(exc).__name__}
