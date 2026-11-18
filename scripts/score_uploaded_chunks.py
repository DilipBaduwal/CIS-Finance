"""Form and score questions independently for newly ingested chunks."""
import argparse
import json
import time
from urllib.request import Request, urlopen

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


def score(settings, text):
    prompt = (
        "Read this annual-report evidence and create one natural question that the evidence can answer. "
        "Choose the closest TOE dimension (technology, organization, or environment). Then score how strongly "
        "the evidence supports the question from 0 to 5. Use only the evidence. Return JSON with question, "
        "toe_dimension, score, confidence, rationale, and evidence_quote. Evidence: {}"
    ).format(text[:5000])
    schema = {"type":"object","properties":{"question":{"type":"string"},"toe_dimension":{"type":"string"},"score":{"type":"integer","minimum":0,"maximum":5},"confidence":{"type":"number","minimum":0,"maximum":1},"rationale":{"type":"string"},"evidence_quote":{"type":"string"}},"required":["question","toe_dimension","score","confidence","rationale","evidence_quote"]}
    body = {"model":settings.ollama_model,"stream":False,"think":False,"format":schema,"options":{"temperature":0.1,"num_predict":384},"messages":[{"role":"user","content":prompt}]}
    req = Request(settings.ollama_base_url.rstrip('/')+'/api/chat', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'}, method='POST')
    with urlopen(req, timeout=300) as response:
        return json.loads(json.loads(response.read()).get('message', {}).get('content', '{}'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--watch', action='store_true', help='Keep polling for newly uploaded chunks')
    parser.add_argument('--poll-seconds', type=int, default=10)
    args = parser.parse_args()
    settings = Settings.from_env()
    while True:
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT s.chunk_id,ch.chunk_text FROM chunk_ai_scores s JOIN chunks ch ON ch.chunk_id=s.chunk_id WHERE s.status='pending' ORDER BY s.created_at LIMIT %s FOR UPDATE SKIP LOCKED""", (args.limit,))
                rows = cur.fetchall()
                for chunk_id, text in rows:
                    cur.execute("UPDATE chunk_ai_scores SET status='processing', attempts=attempts+1, updated_at=now() WHERE chunk_id=%s", (chunk_id,))
                    try:
                        result = score(settings, text)
                        cur.execute("""UPDATE chunk_ai_scores SET status='completed',generated_question=%s,toe_dimension=%s,score=%s,confidence=%s,rationale=%s,evidence_quote=%s,model=%s,updated_at=now() WHERE chunk_id=%s""", (result['question'],result['toe_dimension'],int(result['score']),float(result['confidence']),result['rationale'],result['evidence_quote'],settings.ollama_model,chunk_id))
                        print('Scored', chunk_id, flush=True)
                    except Exception as exc:
                        cur.execute("UPDATE chunk_ai_scores SET status='failed',error_message=%s,updated_at=now() WHERE chunk_id=%s", (type(exc).__name__,chunk_id))
                        print('Failed', chunk_id, type(exc).__name__, flush=True)
        print('Processed', len(rows), 'chunks', flush=True)
        if not args.watch:
            break
        if not rows:
            time.sleep(max(1, args.poll_seconds))


if __name__ == '__main__':
    main()
