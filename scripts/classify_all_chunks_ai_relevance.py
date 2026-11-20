"""Classify every PostgreSQL chunk by semantic AI-readiness relevance.

Labels:
  ai_theme     Direct AI/ML/GenAI use, governance, risk, or outcomes.
  ai_enabling  Data, infrastructure, cybersecurity, skills, or processes that
               enable AI even when the chunk does not say "AI".
  not_ai_theme No meaningful direct or enabling AI-readiness connection.

The run is resumable: completed chunk classifications are skipped.
"""
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
        "ai_relevance_label": {"type": "string", "enum": ["ai_theme", "ai_enabling", "not_ai_theme"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "required": ["ai_relevance_label", "confidence", "rationale"],
}


def classify(settings, text):
    prompt = (
        "Classify this annual-report chunk for healthcare AI-readiness using semantic meaning, not keyword matching. "
        "Use ai_theme for direct AI, machine learning, generative AI, AI governance, AI risk, AI deployment, or AI outcomes. "
        "Use ai_enabling for capabilities that materially enable AI even if AI is not explicitly mentioned: trustworthy data, "
        "interoperability, digital infrastructure, cloud, cybersecurity, technology governance, analytics, technical skills, "
        "or digital transformation. Use not_ai_theme for generic financial, legal, HR, environmental, or governance text with no "
        "clear AI or AI-enabling connection. Do not infer AI merely because the text discusses ordinary technology. "
        "Return JSON only, with exactly these keys: ai_relevance_label, confidence, rationale. "
        "Do not wrap the JSON in markdown fences. Chunk:\n{}"
    ).format(text[:6000])
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 160},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        settings.ollama_base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=300) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        if hasattr(exc, "read"):
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError("Ollama request failed: {} {}".format(getattr(exc, "code", ""), detail)) from exc
        raise
    content = payload["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`").replace("json\n", "", 1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Some local models add a short explanation before/after valid JSON.
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(content[start : end + 1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
    settings = Settings.from_env()
    dsn = secure_postgres_dsn(settings.database_url)
    processed = 0
    with psycopg.connect(dsn, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS chunk_ai_relevance (
                chunk_id uuid PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                ai_relevance_label text NOT NULL CHECK (ai_relevance_label IN ('ai_theme','ai_enabling','not_ai_theme')),
                confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                rationale text NOT NULL DEFAULT '', model text NOT NULL,
                attempts integer NOT NULL DEFAULT 1,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )""")
        connection.commit()
    while args.limit is None or processed < args.limit:
        take = min(args.batch_size, args.limit - processed) if args.limit is not None else args.batch_size
        with psycopg.connect(dsn, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                status_filter = "" if args.retry_failed else "AND r.chunk_id IS NULL"
                cursor.execute(
                    """SELECT c.chunk_id, c.chunk_text
                       FROM chunks c
                       LEFT JOIN chunk_ai_relevance r ON r.chunk_id=c.chunk_id
                       WHERE (r.chunk_id IS NULL OR (r.confidence < 0.5 AND %s))
                       ORDER BY c.created_at, c.chunk_index
                       LIMIT %s""",
                    (args.retry_failed, take),
                )
                rows = cursor.fetchall()
                if not rows:
                    print("No unclassified chunks remain.")
                    return
                for chunk_id, text in rows:
                    try:
                        result = classify(settings, text)
                        label = result["ai_relevance_label"]
                        confidence = float(result["confidence"])
                        if label not in {"ai_theme", "ai_enabling", "not_ai_theme"} or not 0 <= confidence <= 1:
                            raise ValueError("Invalid classifier output")
                        cursor.execute(
                            """INSERT INTO chunk_ai_relevance
                               (chunk_id, ai_relevance_label, confidence, rationale, model, attempts)
                               VALUES (%s,%s,%s,%s,%s,1)
                               ON CONFLICT (chunk_id) DO UPDATE SET
                                 ai_relevance_label=EXCLUDED.ai_relevance_label,
                                 confidence=EXCLUDED.confidence,
                                 rationale=EXCLUDED.rationale,
                                 model=EXCLUDED.model,
                                 attempts=chunk_ai_relevance.attempts+1,
                                 updated_at=now()""",
                            (chunk_id, label, confidence, result.get("rationale", ""), settings.ollama_model),
                        )
                        print(f"Classified {chunk_id}: {label}", flush=True)
                    except Exception as exc:
                        print(f"Failed {chunk_id}: {type(exc).__name__}", flush=True)
                connection.commit()
        processed += len(rows)
        print(f"Processed {processed} chunks in this run", flush=True)


if __name__ == "__main__":
    main()
