"""Semantic-match the 46 healthcare questions against indexed Qdrant chunks."""
import json
import argparse
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings
from cis_fin.vector_index import OllamaEmbedder, QdrantRestClient


BENCHMARK = "healthcare_46_question_results"


def main():
    parser = argparse.ArgumentParser(description="Semantic-match healthcare questions against Qdrant chunks")
    parser.add_argument("--top-k", type=int, default=5, help="Number of semantic candidates to keep per question")
    args = parser.parse_args()
    if args.top_k < 1:
        raise ValueError("--top-k must be positive")
    settings = Settings.from_env()
    embedder = OllamaEmbedder(settings.ollama_base_url, settings.ollama_embedding_model)
    qdrant = QdrantRestClient(settings.qdrant_url, settings.qdrant_api_key, timeout_seconds=60)
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT question_id, question_key, question_text FROM healthcare_custom_questions ORDER BY question_key""")
            questions = cursor.fetchall()
            cursor.execute("""SELECT c.chunk_id::text, co.symbol, lower(coalesce(co.industry,''))
                              FROM chunks c JOIN companies co ON co.company_id=c.company_id
                             WHERE lower(coalesce(co.industry,'')) LIKE '%health%'
                                OR lower(coalesce(co.industry,'')) LIKE '%pharma%'""")
            healthcare_ids = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT chunk_id::text, score, confidence, rationale, evidence_quote FROM chunk_ai_scores WHERE status='completed'")
            scores = {row[0]: row[1:] for row in cursor.fetchall()}
            cursor.execute("SELECT chunk_id::text, page_start, page_end, chunk_text FROM chunks WHERE chunk_id::text = ANY(%s)", (list(healthcare_ids),))
            chunk_text = {row[0]: row[1:] for row in cursor.fetchall()}
            for question_id, question_key, question_text in questions:
                vector = embedder.embed([question_text])[0]
                result = qdrant._request("POST", "/collections/{}/points/query".format(settings.qdrant_collection), {
                    "query": vector, "limit": args.top_k, "with_payload": True, "with_vector": False,
                })
                points = result.get("result", {}).get("points", [])
                matches = []
                for point in points:
                    payload = point.get("payload", {})
                    chunk_id = str(payload.get("chunk_id", ""))
                    if chunk_id not in healthcare_ids or chunk_id not in chunk_text:
                        continue
                    page_start, page_end, text = chunk_text[chunk_id]
                    # Scores must be generated for the question+chunk pair.
                    # Never copy a generic chunk score into a new question.
                    matches.append((float(point.get("score", 0)), chunk_id, page_start, page_end, text))
                matches.sort(reverse=True, key=lambda x: x[0])
                cursor.execute("DELETE FROM healthcare_custom_question_evidence WHERE question_id=%s", (question_id,))
                for similarity, chunk_id, page_start, page_end, text in matches[:args.top_k]:
                    cursor.execute("""INSERT INTO healthcare_custom_question_evidence
                        (question_id, chunk_id, match_count, ai_score, ai_confidence, ai_rationale, evidence_quote)
                        VALUES (%s,%s,%s,NULL,NULL,'','')""", (question_id, chunk_id, round(similarity * 1000)))
                print("{}/{} {}: semantic matches {} (top-k={})".format(questions.index((question_id, question_key, question_text)) + 1, len(questions), question_key, len(matches[:args.top_k]), args.top_k), flush=True)
        connection.commit()
    print("Semantic matching complete for {} questions.".format(len(questions)))


if __name__ == "__main__":
    main()
