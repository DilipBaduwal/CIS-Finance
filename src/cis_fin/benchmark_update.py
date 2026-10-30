"""Reviewable benchmark-update orchestration for the pilot workflow."""
import json
import re
from pathlib import Path

from cis_fin.benchmark_versions import create_next_benchmark_version
from cis_fin.benchmark_regeneration import generate_distinct_question
from cis_fin.benchmark_discovery import BenchmarkChunk, discover_theme_clusters
from cis_fin.benchmark_questions import normalize_question


def _terms(text):
    return {x for x in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", (text or '').lower()) if x not in {"this", "that", "with", "from", "company", "evidence"}}


def required_company_support(cohort_size):
    """Require majority evidence, with at least two companies."""
    if cohort_size < 1:
        raise ValueError("cohort_size must be positive")
    return max(2, (cohort_size + 1) // 2)


def discover_recurring_candidates(themes, records, minimum_company_support=None, cohort_size=None):
    """Find possible new themes from chunks; candidates require review."""
    if minimum_company_support is None:
        minimum_company_support = required_company_support(cohort_size or len({record.get("symbol", record.get("company_symbol")) for record in records}))
    embedded = [record for record in records if record.get("embedding") and record.get("toe_dimension")]
    if len(embedded) >= 2:
        clusters = discover_theme_clusters([
            BenchmarkChunk(str(record.get("chunk_id")), record.get("symbol", record.get("company_symbol", "unknown")), int(record.get("page_start", 0) or 0), int(record.get("page_end", 0) or 0), record.get("chunk_text", ""), record["toe_dimension"], record["embedding"])
            for record in embedded
        ])
        return [
            {"theme": cluster.cluster_id, "toe_dimension": cluster.toe_dimension, "support_count": cluster.support_count, "supporting_companies": list(cluster.company_symbols), "chunk_ids": list(cluster.chunk_ids), "question": cluster.question, "question_status": "needs_review"}
            for cluster in clusters if cluster.support_count >= minimum_company_support
        ]
    existing = [_terms("{} {}".format(item.get("theme"), item.get("question"))) for item in themes]
    by_topic = {}
    for record in records:
        words = _terms(record.get("chunk_text", ""))
        for item in themes:
            overlap = len(words & _terms("{} {}".format(item.get("theme"), item.get("question"))))
            if overlap >= 2:
                continue
        # Use the strongest repeated noun phrases as a transparent candidate signal.
        key = " ".join(sorted(words)[:5])
        if key:
            entry = by_topic.setdefault(key, {"supporting_companies": set(), "chunk_ids": []})
            entry["supporting_companies"].add(record.get("symbol", record.get("company_symbol", "unknown")))
            entry["chunk_ids"].append(record.get("chunk_id"))
    return [
        {"theme": key, "support_count": len(value["supporting_companies"]), "supporting_companies": sorted(value["supporting_companies"]), "chunk_ids": value["chunk_ids"], "question": "", "question_status": "needs_review"}
        for key, value in by_topic.items() if len(value["supporting_companies"]) >= minimum_company_support
    ]


def generate_review_candidates(settings, themes, records, output_path, cohort_size=None):
    candidates = discover_recurring_candidates(themes, records, cohort_size=cohort_size)
    existing_questions = [item.get("question", "") for item in themes]
    for candidate in candidates:
        evidence = "\n".join(record.get("chunk_text", "")[:1200] for record in records if record.get("chunk_id") in candidate["chunk_ids"][:3])
        try:
            candidate["question"] = generate_distinct_question(settings, "organization", evidence, existing_questions)
            candidate["question_status"] = "needs_review"
            normalized, valid = normalize_question(settings, candidate["question"], evidence, candidate.get("toe_dimension", "organization"))
            candidate["question"] = normalized
            candidate["confidence"] = 0.85 if valid else 0.55
            candidate["auto_eligible"] = candidate["confidence"] > 0.70 and valid
        except Exception as exc:
            candidate["generation_error"] = str(exc)
            candidate["confidence"] = 0.0
            candidate["auto_eligible"] = False
        if candidate.get("question"):
            existing_questions.append(candidate["question"])
    # Keep the benchmark compact: maximum ten candidate questions per TOE dimension.
    limited = []
    counts = {}
    for candidate in sorted(candidates, key=lambda item: (item.get("auto_eligible", False), item.get("support_count", 0), item.get("confidence", 0)), reverse=True):
        dimension = candidate.get("toe_dimension", "organization")
        if counts.get(dimension, 0) >= 10:
            continue
        counts[dimension] = counts.get(dimension, 0) + 1
        limited.append(candidate)
    result = {"status": "review_required", "candidates": limited, "auto_eligible_count": sum(1 for item in limited if item.get("auto_eligible")), "maximum_per_toe_dimension": 10, "confidence_threshold": 0.70}
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def score_deltas(previous_path, current_path):
    previous = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    current = json.loads(Path(current_path).read_text(encoding="utf-8"))
    before = {row["company"]: row.get("predicted_score") for row in previous.get("predictions", [])}
    return [{"company": row["company"], "previous_score": before.get(row["company"]), "current_score": row.get("predicted_score"), "change": None if before.get(row["company"]) is None else round(row.get("predicted_score", 0) - before[row["company"]], 1)} for row in current.get("predictions", [])]


def approve_candidates(version_path, candidates_path, approved_indexes, output_path):
    version = json.loads(Path(version_path).read_text(encoding="utf-8"))
    candidates = json.loads(Path(candidates_path).read_text(encoding="utf-8"))["candidates"]
    approved = [candidates[index] for index in approved_indexes]
    themes = list(version.get("themes", [])) + approved
    return create_next_benchmark_version(version_path, version.get("companies", []), output_path, themes=themes)
