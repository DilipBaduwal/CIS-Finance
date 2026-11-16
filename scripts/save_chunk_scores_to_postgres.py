"""Persist checkpointed benchmark chunk scores in PostgreSQL."""
import json
from pathlib import Path

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


CHECKPOINT = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_chunk_scores.checkpoint.json")


def main():
    settings = Settings.from_env()
    state = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    scores = list(state.get("scores", {}).values())
    if not scores:
        print("No checkpointed scores found.")
        return
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_chunk_scores (
                    score_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    source_benchmark text NOT NULL,
                    chunk_id uuid NOT NULL REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
                    question_text text NOT NULL,
                    toe_dimension text NOT NULL,
                    score integer NOT NULL CHECK (score BETWEEN 0 AND 5),
                    confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                    rationale text NOT NULL DEFAULT '',
                    evidence_quote text NOT NULL DEFAULT '',
                    model text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (source_benchmark, chunk_id, question_text)
                )
                """
            )
            for item in scores:
                cursor.execute(
                    """
                    INSERT INTO benchmark_chunk_scores
                        (source_benchmark, chunk_id, question_text, toe_dimension, score,
                         confidence, rationale, evidence_quote, model)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_benchmark, chunk_id, question_text) DO UPDATE SET
                        toe_dimension=EXCLUDED.toe_dimension,
                        score=EXCLUDED.score,
                        confidence=EXCLUDED.confidence,
                        rationale=EXCLUDED.rationale,
                        evidence_quote=EXCLUDED.evidence_quote,
                        model=EXCLUDED.model,
                        created_at=now()
                    """,
                    (
                        "healthcare_ai_readiness_benchmark_2025-2026_all_chunks",
                        item["chunk_id"], item["question"], item["toe_dimension"],
                        item["score"], item["confidence"], item.get("rationale", ""),
                        item.get("evidence_quote", ""), "ollama",
                    ),
                )
    print("Saved {} checkpointed scores to benchmark_chunk_scores.".format(len(scores)))


if __name__ == "__main__":
    main()
