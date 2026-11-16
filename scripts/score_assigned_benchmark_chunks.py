"""Score every chunk assigned to the regenerated benchmark questions."""
import json
import os
import time
from pathlib import Path

import psycopg

from cis_fin.benchmark_cohort_scoring import _score_batch
from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


SOURCE = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_all_chunks.json")
FULL_RUN = os.environ.get("FULL_CHUNK_SCORING", "0") == "1"
OUTPUT = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores.json") if FULL_RUN else Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores_sampled.json")
CHECKPOINT = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores.checkpoint.json") if FULL_RUN else Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores_sampled.checkpoint.json")
MAX_CHUNKS_PER_QUESTION = None if FULL_RUN else 20


def main():
    settings = Settings.from_env()
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    assignments = []
    for theme_index, theme in enumerate(data["themes"]):
        chunk_ids = theme.get("chunk_ids", []) if MAX_CHUNKS_PER_QUESTION is None else theme.get("chunk_ids", [])[:MAX_CHUNKS_PER_QUESTION]
        for chunk_id in chunk_ids:
            assignments.append(
                {
                    "assignment_id": len(assignments),
                    "theme_index": theme_index,
                    "question": theme["question"],
                    "toe_dimension": theme.get("toe_dimension", "unknown"),
                    "chunk_id": str(chunk_id),
                }
            )
    state = json.loads(CHECKPOINT.read_text(encoding="utf-8")) if CHECKPOINT.exists() else {"scores": {}}
    completed = state.setdefault("scores", {})
    ids = [item["chunk_id"] for item in assignments]
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT chunk_id::text, page_start, page_end, chunk_text FROM chunks WHERE chunk_id::text = ANY(%s)", (ids,))
            records = {row[0]: row[1:] for row in cursor.fetchall()}
    pending = [item for item in assignments if str(item["assignment_id"]) not in completed and item["chunk_id"] in records]
    missing = [item["chunk_id"] for item in assignments if item["chunk_id"] not in records]
    print("Assigned chunks: {}; pending scores: {}; missing from database: {}".format(len(assignments), len(pending), len(missing)), flush=True)
    started = time.monotonic()
    for start in range(0, len(pending), 10):
        batch = pending[start : start + 10]
        items = []
        for item in batch:
            page_start, page_end, text = records[item["chunk_id"]]
            items.append(
                {
                    "theme_index": item["assignment_id"],
                    "toe_dimension": item["toe_dimension"],
                    "question": item["question"],
                    "evidence": "Pages {}–{}: {}".format(page_start, page_end, text[:700]),
                }
            )
        try:
            scored = _score_batch(settings, items)
        except Exception as batch_error:
            print("Batch failed; retrying {} chunks individually: {}".format(len(batch), type(batch_error).__name__), flush=True)
            scored = {}
            for item in items:
                try:
                    scored.update(_score_batch(settings, [item]))
                except Exception as item_error:
                    completed[str(item["theme_index"])] = {
                        **next(x for x in batch if x["assignment_id"] == item["theme_index"]),
                        "status": "failed",
                        "error": type(item_error).__name__,
                    }
                    print("Skipped chunk {} after individual retry failed: {}".format(item["theme_index"], type(item_error).__name__), flush=True)
        for item in batch:
            if item["assignment_id"] not in scored:
                continue
            judge = scored[item["assignment_id"]]
            page_start, page_end, text = records[item["chunk_id"]]
            completed[str(item["assignment_id"])] = {
                **item,
                "page_start": page_start,
                "page_end": page_end,
                "score": judge["score"],
                "confidence": judge["confidence"],
                "rationale": judge["rationale"],
                "evidence_quote": judge["evidence_quote"],
            }
        CHECKPOINT.write_text(json.dumps({"scores": completed}, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed = time.monotonic() - started
        rate = (len(completed) - (len(assignments) - len(pending))) / elapsed if elapsed else 0
        remaining = (len(assignments) - len(completed)) / rate if rate else 0
        print(
            "Scored {}/{} chunks ({:.1f}%%); elapsed {:.1f} min; estimated remaining {:.1f} min".format(
                len(completed), len(assignments), 100.0 * len(completed) / len(assignments), elapsed / 60, remaining / 60
            ),
            flush=True,
        )
    result = {
        "source_benchmark": str(SOURCE),
        "industry": data.get("industry"),
        "fiscal_year": data.get("fiscal_year"),
        "question_count": len(data.get("themes", [])),
        "assigned_chunk_count": len(assignments),
        "scored_chunk_count": len(completed),
        "missing_chunk_count": len(missing),
        "scoring_model": settings.ollama_model,
        "scores": [completed[str(i)] for i in range(len(assignments)) if str(i) in completed],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
from cis_fin.benchmark_cohort_scoring import _score_batch
