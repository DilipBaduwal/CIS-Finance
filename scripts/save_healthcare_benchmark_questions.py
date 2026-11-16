"""Create and persist the current healthcare benchmark question registry."""
import json
from pathlib import Path

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


SOURCE = Path("artifacts/healthcare_ai_readiness_benchmark_2025-2026_all_chunks.json")


def main():
    settings = Settings.from_env()
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    themes = data.get("themes", [])
    if len(themes) != 30:
        raise ValueError("Expected exactly 30 healthcare benchmark questions.")
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS healthcare_benchmark_questions (
                    question_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    benchmark_source text NOT NULL,
                    fiscal_year text NOT NULL,
                    theme_index integer NOT NULL CHECK (theme_index BETWEEN 0 AND 29),
                    toe_dimension text NOT NULL CHECK (toe_dimension IN ('technology','organization','environment')),
                    question_text text NOT NULL,
                    theme text NOT NULL,
                    support_count integer NOT NULL DEFAULT 0,
                    supporting_companies jsonb NOT NULL DEFAULT '[]'::jsonb,
                    chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
                    representative_chunk_id uuid REFERENCES chunks(chunk_id) ON DELETE RESTRICT,
                    representative_pages jsonb NOT NULL DEFAULT '[]'::jsonb,
                    question_status text NOT NULL DEFAULT 'active',
                    strength_score numeric NOT NULL DEFAULT 0,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (benchmark_source, theme_index),
                    UNIQUE (benchmark_source, question_text)
                )
                """
            )
            for index, theme in enumerate(themes):
                cursor.execute(
                    """
                    INSERT INTO healthcare_benchmark_questions
                        (benchmark_source, fiscal_year, theme_index, toe_dimension, question_text,
                         theme, support_count, supporting_companies, chunk_ids,
                         representative_chunk_id, representative_pages, question_status, strength_score)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s)
                    ON CONFLICT (benchmark_source, theme_index) DO UPDATE SET
                        fiscal_year=EXCLUDED.fiscal_year,
                        toe_dimension=EXCLUDED.toe_dimension,
                        question_text=EXCLUDED.question_text,
                        theme=EXCLUDED.theme,
                        support_count=EXCLUDED.support_count,
                        supporting_companies=EXCLUDED.supporting_companies,
                        chunk_ids=EXCLUDED.chunk_ids,
                        representative_chunk_id=EXCLUDED.representative_chunk_id,
                        representative_pages=EXCLUDED.representative_pages,
                        question_status=EXCLUDED.question_status,
                        strength_score=EXCLUDED.strength_score,
                        updated_at=now()
                    """,
                    (
                        "healthcare_ai_readiness",
                        data.get("fiscal_year", "2025-2026"),
                        index,
                        theme.get("toe_dimension", "unknown"),
                        theme["question"],
                        theme.get("theme", theme["question"]),
                        int(theme.get("support_count", 0)),
                        json.dumps(theme.get("supporting_companies", [])),
                        json.dumps(theme.get("chunk_ids", [])),
                        theme.get("representative_chunk_id"),
                        json.dumps(theme.get("representative_pages", [])),
                        theme.get("question_status", "generated"),
                        float(theme.get("support_count", 0)),
                    ),
                )
    print("Saved {} healthcare benchmark questions.".format(len(themes)))


if __name__ == "__main__":
    main()
