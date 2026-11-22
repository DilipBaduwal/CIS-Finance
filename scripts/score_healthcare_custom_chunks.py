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
        "relevant": {"type": "boolean"},
        "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 5},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["relevant", "score", "confidence", "rationale", "evidence_quote"],
}


SCORING_RULES = (
    "Set relevant=true only when the passage directly answers the specific claim in the question. "
    "Generic digitalisation, business growth, policy, ESG, leadership, or automation language is not relevant unless it directly answers the question. "
    "If relevant=false, score must be null and evidence_quote must be empty. Never infer missing facts. "
    "If relevant=true, score from 0 to 5: 1=aspirational, 2=early or isolated, 3=established, "
    "4=strong/scaled, 5=integrated/leading with clear outcomes. "
    "Use a short verbatim quote and a one-sentence rationale for relevant evidence."
)


def score_one(settings, question, text):
    prompt = (
        f"Evaluate this annual-report passage for the healthcare AI-readiness question. {SCORING_RULES}\n"
        f"Question: {question}\nEvidence: {text[:6000]}"
    )
    return request_scores(settings, prompt, SCHEMA, 384)


def request_scores(settings, prompt, schema, max_output_tokens):
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_predict": max_output_tokens},
        "messages": [{"role": "user", "content": prompt}],
    }
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


def score_passages(settings, question, texts):
    passages = {str(number): text[:6000] for number, text in enumerate(texts, 1)}
    schema = {
        "type": "object",
        "properties": dict.fromkeys(passages, SCHEMA),
        "required": list(passages),
        "additionalProperties": False,
    }
    prompt = (
        "Evaluate each numbered annual-report passage independently for the same healthcare AI-readiness question. "
        "Use only the text of that passage for its verdict. Return one result per passage using its number as the JSON key. "
        f"{SCORING_RULES}\nQuestion: {question}\nPassages: {json.dumps(passages, ensure_ascii=False)}"
    )
    results = request_scores(settings, prompt, schema, 384 * len(passages))
    if not isinstance(results, dict) or results.keys() != passages.keys():
        raise ValueError("Ollama must return exactly one verdict per passage.")
    for result in results.values():
        if not isinstance(result, dict) or not all(key in result for key in SCHEMA["required"]):
            raise ValueError("Ollama returned an incomplete verdict.")
        if type(result["relevant"]) is not bool:
            raise ValueError("Ollama returned an invalid relevance verdict.")
        score = result["score"]
        if result["relevant"] and (type(score) is not int or not 0 <= score <= 5):
            raise ValueError("Ollama returned an invalid evidence score.")
        confidence = result["confidence"]
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            raise ValueError("Ollama returned an invalid confidence.")
        if not isinstance(result["rationale"], str) or not isinstance(result["evidence_quote"], str):
            raise ValueError("Ollama returned invalid evidence text.")
    return [results[number] for number in passages]


def score_passages_with_retry(settings, question, texts, attempts=3):
    return retry_scoring(lambda: score_passages(settings, question, texts), attempts)


def score_with_retry(settings, question, text, attempts=3):
    return retry_scoring(lambda: score_one(settings, question, text), attempts)


def retry_scoring(score, attempts):
    last_error = None
    for attempt in range(attempts):
        try:
            return score()
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
                              WHERE (e.ai_score IS NULL AND e.ai_confidence IS NULL) OR %s
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
                        score = int(result["score"]) if relevant else None
                        confidence = float(result["confidence"])
                        evidence_quote = result.get("evidence_quote", "") if relevant else ""
                        if (score is not None and not 0 <= score <= 5) or not 0 <= confidence <= 1:
                            raise ValueError("score/confidence outside allowed range")
                        cursor.execute("""UPDATE healthcare_custom_question_evidence
                           SET ai_score=%s, ai_confidence=%s, ai_rationale=%s, evidence_quote=%s
                           WHERE question_id=%s AND chunk_id=%s""", (score, confidence, result.get("rationale", ""), evidence_quote, question_id, chunk_id))
                        completed += 1
                    except Exception as exc:
                        print("System {} failed chunk {}: {} {}".format(args.system, chunk_id, type(exc).__name__, exc), flush=True)
                connection.commit()
                print("System {}/{} progress: {}/{}".format(args.system, args.systems, min(start + len(batch), total), total), flush=True)
    print("System {} complete: {}/{} reviewed.".format(args.system, completed, total), flush=True)


if __name__ == "__main__":
    main()
