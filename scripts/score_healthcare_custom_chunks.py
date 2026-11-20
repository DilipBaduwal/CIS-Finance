"""Score persisted healthcare question/chunk matches using five disjoint workers."""
import argparse
import json
import time
from urllib.request import Request, urlopen

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 5},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["score", "confidence", "rationale", "evidence_quote"],
}


def score_one(settings, question, text):
    prompt = (
        "Score this annual-report evidence for the supplied healthcare AI-readiness question from 0 to 5. "
        "Use only the evidence; do not infer missing facts. 0=no evidence, 1=aspirational, 2=early or isolated, "
        "3=established, 4=strong/scaled, 5=integrated/leading with clear outcomes. "
        "Return JSON only with score, confidence, rationale, and evidence_quote.\n"
        "Question: {}\nEvidence: {}"
    ).format(question, text[:6000])
    body = {"model": settings.ollama_model, "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 256}, "messages": [{"role": "user", "content": prompt}]}
    request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=300) as response:
        payload = json.loads(response.read())
    content = payload["message"]["content"].strip().strip("`")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(content[start:end + 1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", type=int, required=True, help="Worker number, 1 through 5")
    parser.add_argument("--systems", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=5)
    args = parser.parse_args()
    if args.systems != 5 or not 1 <= args.system <= args.systems:
        raise ValueError("Use exactly five systems and --system from 1 to 5")
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT e.question_id, e.chunk_id, q.question_text, c.chunk_text
                               FROM healthcare_custom_question_evidence e
                               JOIN healthcare_custom_questions q ON q.question_id=e.question_id
                               JOIN chunks c ON c.chunk_id=e.chunk_id
                              WHERE e.ai_score IS NULL
                              ORDER BY e.question_id, e.chunk_id""")
            all_rows = cursor.fetchall()
            assignments = [row for index, row in enumerate(all_rows) if index % args.systems == args.system - 1]
            total = len(assignments)
            print("System {}/{} assigned {} unscored question-chunk pairs.".format(args.system, args.systems, total), flush=True)
            completed = 0
            for start in range(0, total, args.batch_size):
                batch = assignments[start:start + args.batch_size]
                for question_id, chunk_id, question, text in batch:
                    try:
                        result = score_one(settings, question, text)
                        score = int(result["score"]); confidence = float(result["confidence"])
                        if not 0 <= score <= 5 or not 0 <= confidence <= 1:
                            raise ValueError("score/confidence outside allowed range")
                        cursor.execute("""UPDATE healthcare_custom_question_evidence
                           SET ai_score=%s, ai_confidence=%s, ai_rationale=%s, evidence_quote=%s
                           WHERE question_id=%s AND chunk_id=%s""", (score, confidence, result.get("rationale", ""), result.get("evidence_quote", ""), question_id, chunk_id))
                        completed += 1
                    except Exception as exc:
                        print("System {} failed chunk {}: {}".format(args.system, chunk_id, type(exc).__name__), flush=True)
                connection.commit()
                print("System {}/{} progress: {}/{}".format(args.system, args.systems, min(start + len(batch), total), total), flush=True)
    print("System {} complete: {}/{} scored.".format(args.system, completed, total), flush=True)


if __name__ == "__main__":
    main()
