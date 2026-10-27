"""Build explainable benchmark results from extracted metric evidence."""
import json
from pathlib import Path
from cis_fin.benchmark_scoring import benchmark_score, benchmark_summary


def build_benchmark_results(source_path, output_path):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    results = []
    for item in data.get("themes", []):
        metric = item.get("extracted_metric")
        result = {
            "theme": item.get("theme", "AI-readiness theme"),
            "question": item.get("question", ""),
            "toe_dimension": item.get("toe_dimension", ""),
            "supporting_companies": item.get("supporting_companies", []),
            "metric": metric,
        }
        if not metric or "value" not in metric:
            result["status"] = "needs_review" if metric else "no_comparable_metric"
            result["score"] = None
        else:
            result["status"] = "insufficient_peer_values"
            result["score"] = None
            result["benchmark"] = benchmark_summary([metric["value"]])
        results.append(result)
    output = dict(data)
    output["results"] = results
    output["scoreable_result_count"] = sum(result["status"] == "scoreable" for result in results)
    output["scoring_complete"] = True
    Path(output_path).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
