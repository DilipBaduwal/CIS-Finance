"""Remove repetitive AI wording from healthcare question labels without rescoring."""
import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
BENCHMARK = "healthcare_ai_readiness"

REPLACEMENTS = (
    ("technology and AI adoption decisions", "technology adoption decisions"),
    ("needed to operate AI-enabled systems", "needed to operate digital systems"),
    ("supporting digital and AI operations", "supporting digital operations"),
    ("digital and AI capability", "digital capability"),
    ("technology, data, and AI initiatives", "technology and data initiatives"),
    ("analytics and AI decision-making", "analytics and decision-making"),
    ("technology and AI decisions", "technology decisions"),
    ("technology and AI transformation", "technology transformation"),
    ("technology and AI oversight", "technology oversight"),
    ("technology and AI priorities", "technology priorities"),
    ("technology, data, AI, or compliance concerns", "technology, data, or compliance concerns"),
    ("digital, AI, and human-rights training", "digital and human-rights training"),
)


def naturalize(text):
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    return text


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT theme_index, question_text FROM healthcare_benchmark_questions WHERE benchmark_source=%s ORDER BY theme_index", (BENCHMARK,))
            for index, old in cursor.fetchall():
                new = naturalize(old)
                if new == old:
                    continue
                cursor.execute("UPDATE benchmark_chunk_scores SET question_text=%s WHERE source_benchmark=%s AND question_text=%s", (new, SOURCE, old))
                cursor.execute("UPDATE healthcare_benchmark_questions SET question_text=%s, theme=%s, updated_at=now() WHERE benchmark_source=%s AND theme_index=%s", (new, new.rstrip("?"), BENCHMARK, index))
    print("Naturalized healthcare question wording; scores and evidence were preserved.")


if __name__ == "__main__":
    main()
