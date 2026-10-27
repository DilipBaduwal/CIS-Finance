"""Extract benchmark metrics separately for every company in every theme."""
import json
from pathlib import Path
from cis_fin.benchmark_scoring import extract_metric


def extract_company_metrics(settings, source_path, output_path, checkpoint_path, evidence_by_chunk, batch_size=1):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    checkpoint = Path(checkpoint_path)
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"metrics": {}}
    jobs = []
    for theme_index, theme in enumerate(data.get("themes", [])):
        for company in theme.get("supporting_companies", []):
            key = "{}:{}".format(theme_index, company)
            if key not in state["metrics"]:
                jobs.append((key, theme_index, theme, company))
    for key, theme_index, theme, company in jobs[:batch_size]:
        chunks = [evidence_by_chunk.get(chunk_id) for chunk_id in theme.get("chunk_ids", [])]
        evidence = next((text for text in chunks if text and company.casefold() in text.casefold()), "")
        if not evidence:
            evidence = next((text for text in chunks if text), "")
        try:
            metric = extract_metric(settings, theme.get("question", ""), evidence, 0)
            state["metrics"][key] = None if metric is None else {
                "company": company,
                "value": metric.value,
                "unit": metric.unit,
                "direction": metric.direction,
                "evidence_quote": metric.evidence_quote,
                "page": metric.page,
                "confidence": metric.confidence,
            }
        except Exception as exc:
            state["metrics"][key] = {"company": company, "status": "needs_review", "error": type(exc).__name__}
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps(state, indent=2), encoding="utf-8")
    result = dict(data)
    result["company_metrics"] = state["metrics"]
    result["company_metric_extraction_complete"] = len(state["metrics"]) == sum(len(t.get("supporting_companies", [])) for t in data.get("themes", []))
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"processed": len(state["metrics"]), "total": sum(len(t.get("supporting_companies", [])) for t in data.get("themes", [])), "complete": result["company_metric_extraction_complete"]}
