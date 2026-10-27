"""Deduplicate normalized benchmark questions without losing evidence support."""
import json
from pathlib import Path


def deduplicate_benchmark(source_path, output_path):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    merged = {}
    for item in data.get("themes", []):
        key = " ".join(item.get("question", "").casefold().split())
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(item)
            merged[key]["merged_cluster_ids"] = [item.get("cluster_id")]
            continue
        current = merged[key]
        current["merged_cluster_ids"].append(item.get("cluster_id"))
        current["supporting_companies"] = sorted(set(current.get("supporting_companies", [])) | set(item.get("supporting_companies", [])))
        current["support_count"] = len(current["supporting_companies"])
        current["chunk_ids"] = sorted(set(current.get("chunk_ids", [])) | set(item.get("chunk_ids", [])))
    result = dict(data)
    result["themes"] = list(merged.values())
    result["original_theme_count"] = len(data.get("themes", []))
    result["deduplicated_theme_count"] = len(result["themes"])
    result["deduplication_complete"] = True
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
