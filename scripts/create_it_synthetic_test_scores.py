"""Create clearly labelled synthetic IT scores for UI/testing only."""
import hashlib
import json
from pathlib import Path

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


def synthetic_score(company, index):
    digest = hashlib.sha256("{}:{}".format(company, index).encode()).digest()
    return digest[0] % 6


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT companies, themes
                FROM benchmark_versions
                WHERE industry='Information Technology' AND fiscal_year='2025-2026'
                ORDER BY version_number DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("No IT benchmark version found.")
            companies, themes = row
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS it_synthetic_test_scores (
                    score_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    company text NOT NULL,
                    fiscal_year text NOT NULL,
                    theme_index integer NOT NULL,
                    question_text text NOT NULL,
                    toe_dimension text NOT NULL,
                    score integer NOT NULL CHECK (score BETWEEN 0 AND 5),
                    score_type text NOT NULL DEFAULT 'synthetic_test',
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (company, fiscal_year, theme_index)
                )
                """
            )
            matrix_rows = []
            for company in companies:
                features = {}
                for index, theme in enumerate(themes):
                    score = synthetic_score(company, index)
                    features["theme_{}".format(index)] = float(score)
                    cursor.execute(
                        """
                        INSERT INTO it_synthetic_test_scores
                            (company, fiscal_year, theme_index, question_text, toe_dimension, score, score_type)
                        VALUES (%s,%s,%s,%s,%s,%s,'synthetic_test')
                        ON CONFLICT (company, fiscal_year, theme_index) DO UPDATE SET
                            question_text=EXCLUDED.question_text,
                            toe_dimension=EXCLUDED.toe_dimension,
                            score=EXCLUDED.score,
                            score_type='synthetic_test',
                            created_at=now()
                        """,
                        (company, "2025-2026", index, theme.get("question", ""), theme.get("toe_dimension", "unknown"), score),
                    )
                matrix_rows.append({"company": company, "features": features, "feature_vector": list(features.values()), "provenance": {key: {"status": "synthetic_test", "score": value} for key, value in features.items()}})
    matrix = {
        "industry": "Information Technology",
        "fiscal_year": "2025-2026",
        "feature_names": ["theme_{}".format(i) for i in range(len(themes))],
        "rows": matrix_rows,
        "excluded": [],
        "training_ready": False,
        "training_note": "Synthetic test scores only; not evidence-based and not for decisions.",
        "scoring_model": "synthetic_test",
    }
    Path("artifacts/it_ai_readiness_regression_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved synthetic test scores for {} companies and {} questions.".format(len(companies), len(themes)))


if __name__ == "__main__":
    main()
