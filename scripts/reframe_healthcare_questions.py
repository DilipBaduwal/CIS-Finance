"""Reframe healthcare questions for AI-readiness without changing their evidence."""
import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
BENCHMARK = "healthcare_ai_readiness"


def reframe(question):
    text = " ".join(str(question).split()).rstrip("?.")
    if text.lower().startswith("how many board meetings"):
        return "For AI-readiness assessment, what does the annual report disclose about the company's Board-meeting cadence and governance capacity?"
    if text.lower().startswith("did the company submit"):
        return "For AI-readiness assessment, does the annual report disclose whether the company completed its required regulatory filing within the prescribed timeline?"
    if text.lower().startswith("does the company disclose"):
        remainder = text[len("Does the company disclose") :].strip()
        return "For AI-readiness assessment, does the annual report disclose " + remainder + "?"
    return "For AI-readiness assessment, what evidence does the annual report disclose about " + text.rstrip("?") + "?"


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT theme_index, question_text
                   FROM healthcare_benchmark_questions
                   WHERE benchmark_source=%s ORDER BY theme_index""",
                (BENCHMARK,),
            )
            pairs = [(index, old, reframe(old)) for index, old in cursor.fetchall()]
            if len(pairs) != 30:
                raise RuntimeError("Expected 30 healthcare questions, found {}".format(len(pairs)))
            for index, old, new in pairs:
                cursor.execute(
                    """UPDATE benchmark_chunk_scores
                       SET question_text=%s
                       WHERE source_benchmark=%s AND question_text=%s""",
                    (new, SOURCE, old),
                )
                cursor.execute(
                    """UPDATE healthcare_benchmark_questions
                       SET question_text=%s, updated_at=now()
                       WHERE benchmark_source=%s AND theme_index=%s""",
                    (new, BENCHMARK, index),
                )
    print("Reframed 30 healthcare questions; existing scores and chunk assignments were preserved.")


if __name__ == "__main__":
    main()
