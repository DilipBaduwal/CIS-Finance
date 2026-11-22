"""Retrieve and strictly score the 46 Healthcare questions for one company."""
import argparse
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings
from cis_fin.vector_index import OllamaEmbedder, QdrantRestClient
from score_healthcare_custom_chunks import score_with_retry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    settings = Settings.from_env()
    embedder = OllamaEmbedder(settings.ollama_base_url, settings.ollama_embedding_model)
    qdrant = QdrantRestClient(settings.qdrant_url, settings.qdrant_api_key, timeout_seconds=60)
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT question_id, question_key, question_text FROM healthcare_custom_questions ORDER BY question_key")
            questions = cursor.fetchall()
            cursor.execute("SELECT chunk_id::text, page_start, page_end, chunk_text FROM chunks WHERE company_id=%s", (args.company_id,))
            chunks = {row[0]: row[1:] for row in cursor.fetchall()}
            if not chunks:
                raise RuntimeError("No chunks found for company {}".format(args.company_id))
            chunk_ids = set(chunks)
            for number, (question_id, question_key, question) in enumerate(questions, 1):
                vector = embedder.embed([question])[0]
                result = qdrant._request("POST", "/collections/{}/points/query".format(settings.qdrant_collection), {"query": vector, "limit": 100, "with_payload": True, "with_vector": False})
                candidates = []
                for point in result.get("result", {}).get("points", []):
                    chunk_id = str(point.get("payload", {}).get("chunk_id", ""))
                    if chunk_id in chunk_ids:
                        candidates.append((float(point.get("score", 0)), chunk_id))
                candidates = sorted(candidates, reverse=True)[:args.top_k]
                cursor.execute("DELETE FROM healthcare_custom_question_evidence WHERE question_id=%s AND chunk_id IN (SELECT chunk_id FROM chunks WHERE company_id=%s)", (question_id, args.company_id))
                for similarity, chunk_id in candidates:
                    cursor.execute("""INSERT INTO healthcare_custom_question_evidence
                        (question_id, chunk_id, match_count, ai_score, ai_confidence, ai_rationale, evidence_quote)
                        VALUES (%s,%s,%s,NULL,NULL,'','')""", (question_id, chunk_id, round(similarity * 1000)))
                connection.commit()
                print("Retrieved {}/{} {}: {} candidates".format(number, len(questions), question_key, len(candidates)), flush=True)

            cursor.execute("""SELECT e.question_id, e.chunk_id, q.question_text, c.chunk_text
                               FROM healthcare_custom_question_evidence e
                               JOIN healthcare_custom_questions q ON q.question_id=e.question_id
                               JOIN chunks c ON c.chunk_id=e.chunk_id
                              WHERE c.company_id=%s AND e.ai_score IS NULL
                              ORDER BY e.question_id, e.chunk_id""", (args.company_id,))
            rows = cursor.fetchall()
            for number, (question_id, chunk_id, question, text) in enumerate(rows, 1):
                result = score_with_retry(settings, question, text)
                relevant = bool(result.get("relevant", False))
                score = int(result.get("score", 0)) if relevant else 0
                quote = result.get("evidence_quote", "") if relevant else ""
                cursor.execute("""UPDATE healthcare_custom_question_evidence
                                  SET ai_score=%s, ai_confidence=%s, ai_rationale=%s, evidence_quote=%s
                                WHERE question_id=%s AND chunk_id=%s""", (score, float(result.get("confidence", 0)), result.get("rationale", ""), quote, question_id, chunk_id))
                connection.commit()
                print("Scored {}/{}".format(number, len(rows)), flush=True)
    print("Healthcare scoring complete for {}".format(args.company_id), flush=True)


if __name__ == "__main__":
    main()
