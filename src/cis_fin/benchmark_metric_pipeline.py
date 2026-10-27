"""Checkpointed metric extraction for benchmark themes."""
import json
from pathlib import Path
from cis_fin.benchmark_scoring import extract_metric


def extract_benchmark_metrics(settings, source_path, output_path, checkpoint_path, batch_size=1):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    checkpoint = Path(checkpoint_path)
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"metrics": {}}
    themes = data.get("themes", [])
    pending = [i for i in range(len(themes)) if str(i) not in state["metrics"]]
    for index in pending[:batch_size]:
        theme = themes[index]
        evidence = theme.get("representative_evidence_preview", "")
        try:
            metric = extract_metric(
                settings,
                theme.get("question", ""),
                evidence,
                theme.get("representative_pages", [0])[0] if theme.get("representative_pages") else 0,
            )
            state["metrics"][str(index)] = None if metric is None else {
                "value": metric.value,
                "unit": metric.unit,
                "direction": metric.direction,
                "evidence_quote": metric.evidence_quote,
                "page": metric.page,
                "confidence": metric.confidence,
            }
        except Exception as exc:
            state["metrics"][str(index)] = {"status": "needs_review", "error": type(exc).__name__}
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps(state, indent=2), encoding="utf-8")
    for index, metric in state["metrics"].items():
        themes[int(index)]["extracted_metric"] = metric
    result = dict(data)
    result["themes"] = themes
    result["metric_extraction_complete"] = len(state["metrics"]) == len(themes)
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"processed": len(state["metrics"]), "total": len(themes), "complete": result["metric_extraction_complete"]}
