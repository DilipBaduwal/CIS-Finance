"""Score one non-overlapping partition of healthcare benchmark chunks."""
import argparse
import json
import time
import uuid
from pathlib import Path

import psycopg

from cis_fin.benchmark_cohort_scoring import _score_batch
from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", type=int, choices=range(1, 6), required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT theme_index, question_text, toe_dimension, chunk_ids FROM healthcare_benchmark_questions WHERE question_status IN ('active','generated') ORDER BY theme_index")
            themes = cursor.fetchall()
            assignments = []
            for theme_index, question, dimension, chunk_ids in themes:
                for chunk_id in (chunk_ids if isinstance(chunk_ids, list) else json.loads(chunk_ids)):
                    assignments.append({"assignment_id": len(assignments), "theme_index": theme_index, "question": question, "toe_dimension": dimension, "chunk_id": str(chunk_id)})
            cursor.execute("SELECT chunk_id::text, question_text FROM benchmark_chunk_scores WHERE source_benchmark=%s", (SOURCE,))
            completed = {(str(chunk_id), question) for chunk_id, question in cursor.fetchall()}
            pending = [item for item in assignments if (item["chunk_id"], item["question"]) not in completed and item["assignment_id"] % 5 == args.system - 1]
            ids = [item["chunk_id"] for item in pending]
            cursor.execute("SELECT chunk_id::text, page_start, page_end, chunk_text FROM chunks WHERE chunk_id::text = ANY(%s)", (ids,))
            records = {row[0]: row[1:] for row in cursor.fetchall()}
    print("System {}: {} chunks assigned, {} pending.".format(args.system, sum(1 for x in assignments if x["assignment_id"] % 5 == args.system - 1), len(pending)), flush=True)
    done = 0
    failed = 0
    for item in pending:
        page_start, page_end, text = records.get(item["chunk_id"], (None, None, ""))
        if not text:
            failed += 1
            print("Skipped missing chunk {}".format(item["chunk_id"]), flush=True)
            continue
        request_item = {
            "theme_index": item["assignment_id"],
            "toe_dimension": item["toe_dimension"],
            "question": item["question"],
            "evidence": "Pages {}–{}: {}".format(page_start, page_end, text[:700]),
        }
        judge = None
        for attempt in range(3):
            try:
                judge = _score_batch(settings, [request_item])[item["assignment_id"]]
                break
            except Exception as exc:
                if attempt == 2:
                    print("Skipped {} after 3 retries ({})".format(item["chunk_id"], type(exc).__name__), flush=True)
                else:
                    time.sleep(attempt + 1)
        if judge is None:
            failed += 1
            continue
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO benchmark_chunk_scores
                    (source_benchmark,chunk_id,question_text,toe_dimension,score,confidence,rationale,evidence_quote,model)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_benchmark,chunk_id,question_text) DO UPDATE SET score=EXCLUDED.score,confidence=EXCLUDED.confidence,rationale=EXCLUDED.rationale,evidence_quote=EXCLUDED.evidence_quote,model=EXCLUDED.model,created_at=now()""",
                    (SOURCE, item["chunk_id"], item["question"], item["toe_dimension"], int(judge["score"]), float(judge["confidence"]), judge.get("rationale", ""), judge.get("evidence_quote", ""), settings.ollama_model),
                )
        done += 1
        print("System {}: {}/{} completed; {} failed.".format(args.system, done, len(pending), failed), flush=True)
    print("System {} finished: {} scored, {} failed.".format(args.system, done, failed), flush=True)


if __name__ == "__main__":
    main()
