"""Score persisted healthcare question/chunk matches using five disjoint workers."""
import argparse
import json
import time
import time
from urllib.request import Request, urlopen

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "score": {"type": "integer", "minimum": 0, "maximum": 5},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["relevant", "score", "confidence", "rationale", "evidence_quote"],
}


def score_one(settings, question, text):
    prompt = (
        "Evaluate whether this annual-report evidence directly answers the supplied healthcare AI-readiness question. "
        "Set relevant=true only when it addresses the specific subject and claim in the question. "
        "Generic digitalisation, business growth, policy, ESG, leadership, or automation language is not relevant unless it directly answers the question. "
        "If relevant=false, score must be 0 and evidence_quote must be empty. Never infer missing facts. "
        "If relevant=true, score from 0 to 5: 1=aspirational, 2=early or isolated, 3=established, "
        "4=strong/scaled, 5=integrated/leading with clear outcomes. "
        "Return JSON only with relevant, score, confidence, rationale, and evidence_quote.\n"
        "Question: {}\nEvidence: {}"
    ).format(question, text[:6000])
    body = {"model": settings.ollama_model, "stream": False, "think": False, "format": SCHEMA, "options": {"temperature": 0, "num_predict": 384}, "messages": [{"role": "user", "content": prompt}]}
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


def score_with_retry(settings, question, text, attempts=3):
    last_error = None
    for attempt in range(attempts):
        try:
            return score_one(settings, question, text)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(attempt + 1)
    raise last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", type=int, required=True, help="Worker number, 1 through 5")
    parser.add_argument("--systems", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--rescore", action="store_true")
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
                              WHERE e.ai_score IS NULL OR %s
                              ORDER BY e.question_id, e.chunk_id""", (args.rescore,))
            all_rows = cursor.fetchall()
            assignments = [row for index, row in enumerate(all_rows) if index % args.systems == args.system - 1]
            total = len(assignments)
            print("System {}/{} assigned {} unscored question-chunk pairs.".format(args.system, args.systems, total), flush=True)
            completed = 0
            for start in range(0, total, args.batch_size):
                batch = assignments[start:start + args.batch_size]
                for question_id, chunk_id, question, text in batch:
                    try:
                        result = score_with_retry(settings, question, text)
                        relevant = bool(result.get("relevant", False))
                        score = int(result["score"]); confidence = float(result["confidence"])
                        if not relevant:
                            score = 0
                            evidence_quote = ""
                        else:
                            evidence_quote = result.get("evidence_quote", "")
                        if not 0 <= score <= 5 or not 0 <= confidence <= 1:
                            raise ValueError("score/confidence outside allowed range")
                        cursor.execute("""UPDATE healthcare_custom_question_evidence
                           SET ai_score=%s, ai_confidence=%s, ai_rationale=%s, evidence_quote=%s
                           WHERE question_id=%s AND chunk_id=%s""", (score, confidence, result.get("rationale", ""), evidence_quote, question_id, chunk_id))
                        completed += 1
                    except Exception as exc:
                        print("System {} failed chunk {}: {} {}".format(args.system, chunk_id, type(exc).__name__, exc), flush=True)
                connection.commit()
                print("System {}/{} progress: {}/{}".format(args.system, args.systems, min(start + len(batch), total), total), flush=True)
    print("System {} complete: {}/{} scored.".format(args.system, completed, total), flush=True)


if __name__ == "__main__":
    main()
