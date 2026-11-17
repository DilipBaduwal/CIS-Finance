"""Restore the original evidence-matched healthcare question wording."""
import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
BENCHMARK = "healthcare_ai_readiness"


def restore(text):
    text = str(text)
    prefix = "For AI-readiness assessment, does the annual report disclose "
    if text.startswith(prefix):
        return "Does the company disclose " + text[len(prefix):]
    if text.startswith("For AI-readiness assessment, what does the annual report disclose about the company's Board-meeting cadence and governance capacity?"):
        return "How many Board meetings were held during the financial year 2025-26?"
    if text.startswith("For AI-readiness assessment, does the annual report disclose whether the company completed its required regulatory filing"):
        return "Did the company submit its Integrated Filing with BSE Limited within the prescribed timeline for the quarter ended June 2025?"
    return text


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT theme_index, question_text FROM healthcare_benchmark_questions WHERE benchmark_source=%s ORDER BY theme_index", (BENCHMARK,))
            pairs = [(index, old, restore(old)) for index, old in cursor.fetchall()]
            for index, old, new in pairs:
                if old == new:
                    continue
                cursor.execute("UPDATE benchmark_chunk_scores SET question_text=%s WHERE source_benchmark=%s AND question_text=%s", (new, SOURCE, old))
                cursor.execute("UPDATE healthcare_benchmark_questions SET question_text=%s, updated_at=now() WHERE benchmark_source=%s AND theme_index=%s", (new, BENCHMARK, index))
    print("Restored original healthcare question wording; scores were not changed.")


if __name__ == "__main__":
    main()
