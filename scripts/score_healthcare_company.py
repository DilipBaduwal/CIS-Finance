"""Retrieve and strictly score the 46 Healthcare questions for one company."""
import argparse
import sys
from itertools import groupby
from pathlib import Path
from uuid import UUID

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings
from cis_fin.vector_index import OllamaEmbedder, QdrantRestClient
from score_healthcare_custom_chunks import score_passages_with_retry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-id", required=True, type=UUID)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="Reuse retrieved candidates and recheck legacy zero scores without evidence.")
    args = parser.parse_args()
    settings = Settings.from_env()
    embedder = OllamaEmbedder(settings.ollama_base_url, settings.ollama_embedding_model)
    qdrant = QdrantRestClient(settings.qdrant_url, settings.qdrant_api_key, timeout_seconds=60)
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT question_id, question_key, question_text FROM healthcare_custom_questions ORDER BY question_key")
            questions = cursor.fetchall()
            cursor.execute(
                "SELECT chunk_id::text, document_id::text FROM chunks WHERE company_id=%s",
                (args.company_id,),
            )
            chunks = cursor.fetchall()
            if not chunks:
                raise RuntimeError("No chunks found for company {}".format(args.company_id))
            chunk_ids = {chunk_id for chunk_id, _ in chunks}
            document_ids = sorted({document_id for _, document_id in chunks})
            print("Searching {} company chunks across {} documents.".format(
                len(chunk_ids), len(document_ids)
            ), flush=True)
            if not args.resume:
                for number, (question_id, question_key, question) in enumerate(questions, 1):
                    vector = embedder.embed([question])[0]
                    result = qdrant._request(
                        "POST",
                        "/collections/{}/points/query".format(settings.qdrant_collection),
                        {
                            "query": vector,
                            "filter": {
                                "must": [
                                    {"key": "document_id", "match": {"any": document_ids}}
                                ]
                            },
                            "params": {"exact": True},
                            "limit": args.top_k,
                            "with_payload": ["chunk_id"],
                            "with_vector": False,
                        },
                    )
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

            cursor.execute("""SELECT e.question_id, e.chunk_id, q.question_key, q.question_text, c.chunk_text
                               FROM healthcare_custom_question_evidence e
                               JOIN healthcare_custom_questions q ON q.question_id=e.question_id
                               JOIN chunks c ON c.chunk_id=e.chunk_id
                              WHERE c.company_id=%s
                                 AND ((e.ai_score IS NULL AND e.ai_confidence IS NULL)
                                      OR (%s AND e.ai_score=0 AND e.evidence_quote=''))
                              ORDER BY e.question_id, e.chunk_id""", (args.company_id, args.resume))
            rows = cursor.fetchall()
            reviewed = 0
            for (question_id, question_key, question), matches in groupby(
                rows, key=lambda row: (row[0], row[2], row[3])
            ):
                passages = [(row[1], row[4]) for row in matches]
                verdicts = score_passages_with_retry(
                    settings, question, [text for _, text in passages]
                )
                outcomes = []
                for (chunk_id, _), verdict in zip(passages, verdicts, strict=True):
                    relevant = verdict["relevant"]
                    score = verdict["score"] if relevant else None
                    quote = verdict["evidence_quote"] if relevant else ""
                    cursor.execute(
                        """UPDATE healthcare_custom_question_evidence
                           SET ai_score=%s, ai_confidence=%s, ai_rationale=%s, evidence_quote=%s
                           WHERE question_id=%s AND chunk_id=%s""",
                        (score, verdict["confidence"], verdict["rationale"], quote, question_id, chunk_id),
                    )
                    outcomes.append("irrelevant" if score is None else "{}/5".format(score))
                connection.commit()
                for outcome in outcomes:
                    reviewed += 1
                    print("Reviewed {}/{} {}: {}".format(reviewed, len(rows), question_key, outcome), flush=True)
    print("Healthcare scoring complete for {}".format(args.company_id), flush=True)


if __name__ == "__main__":
    main()
