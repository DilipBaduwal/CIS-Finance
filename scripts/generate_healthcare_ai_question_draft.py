"""Generate a new healthcare TOE question draft from saved evidence.

This script does not modify the question registry or scores. It creates a
reviewable draft artifact first.
"""
import json
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
OUTPUT = Path("artifacts/healthcare_ai_readiness_questions_draft.json")


def generate(settings, dimension, evidence, existing):
    prompt = (
        "Create one natural, customer-facing annual-report question for a healthcare AI-readiness benchmark. "
        "It must assess a real capability under the TOE dimension '{}'. Do not begin with 'For AI-readiness'. "
        "Do not force the word AI into the question. Avoid generic compliance, tax, filing, or board-meeting questions "
        "unless the evidence clearly connects them to technology capability, organizational readiness, or the operating environment. "
        "Use only the evidence supplied. Return JSON with one field named question. "
        "Avoid duplicates with these existing draft questions: {}\nEvidence:\n{}"
    ).format(dimension, json.dumps(existing[-10:]), "\n---\n".join(evidence[:5]))
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "format": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
        "options": {"temperature": 0.4, "num_predict": 120},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=180) as response:
        result = json.loads(response.read())
    question = json.loads(result["message"]["content"])["question"]
    return " ".join(str(question).split()).rstrip(".?!") + "?"


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT theme_index, toe_dimension, question_text
                   FROM healthcare_benchmark_questions
                   WHERE benchmark_source='healthcare_ai_readiness'
                   ORDER BY theme_index"""
            )
            themes = cursor.fetchall()
            draft = []
            for number, (theme_index, dimension, old_question) in enumerate(themes, 1):
                cursor.execute(
                    """SELECT evidence_quote FROM benchmark_chunk_scores
                       WHERE source_benchmark=%s AND question_text=%s
                         AND evidence_quote IS NOT NULL AND evidence_quote <> ''
                       ORDER BY score DESC, confidence DESC NULLS LAST LIMIT 5""",
                    (SOURCE, old_question),
                )
                evidence = [row[0] for row in cursor.fetchall()]
                print("Generating draft question {}/{}...".format(number, len(themes)), flush=True)
                try:
                    question = generate(settings, dimension, evidence, [item["question"] for item in draft])
                    status = "generated"
                except Exception as exc:
                    question = ""
                    status = "failed:{}".format(type(exc).__name__)
                draft.append({"theme_index": theme_index, "toe_dimension": dimension, "question": question, "source_question": old_question, "evidence_quotes": evidence, "status": status})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"industry": "Healthcare", "fiscal_year": "2025-2026", "source": SOURCE, "question_count": len(draft), "themes": draft}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote draft to {}. No scores or database questions were changed.".format(OUTPUT))


if __name__ == "__main__":
    main()
