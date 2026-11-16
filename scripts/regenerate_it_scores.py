"""Rebuild the IT benchmark source artifact and company score matrix from PostgreSQL."""
import json
from pathlib import Path

import psycopg

from cis_fin.benchmark_cohort_scoring import score_healthcare_cohort
from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


def main():
    settings = Settings.from_env()
    source = Path("artifacts/it_ai_readiness_benchmark_2025-2026.json")
    matrix = Path("artifacts/it_ai_readiness_regression_matrix.json")
    checkpoint = Path("artifacts/it_ai_readiness_score_checkpoint.json")
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT industry, fiscal_year, version_number, companies, themes
                FROM benchmark_versions
                WHERE industry = 'Information Technology' AND fiscal_year = '2025-2026'
                ORDER BY version_number DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
    if not row:
        raise RuntimeError("No IT benchmark version found in PostgreSQL.")
    industry, fiscal_year, version_number, companies, themes = row
    data = {
        "industry": industry,
        "fiscal_year": fiscal_year,
        "version_number": version_number,
        "companies": companies,
        "themes": themes,
    }
    source.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = score_healthcare_cohort(settings, source, matrix, checkpoint, batch_size=5)
    print("IT scoring complete: {} companies, {} rows.".format(len(result.get("rows", [])), len(result.get("rows", []))))


if __name__ == "__main__":
    main()
