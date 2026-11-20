"""Import the supplied 46-question healthcare question set into PostgreSQL.

This does not generate questions or run AI scoring. It matches each supplied
question to existing healthcare chunks and carries over any saved chunk_ai_scores.
"""
import re
from pathlib import Path

import psycopg
from openpyxl import load_workbook

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


WORKBOOK = Path("/Users/dilipgreyhat/Downloads/46_Question_Results.xlsx")
SOURCE = "healthcare_46_question_results"


def terms(value):
    stop = {"what", "does", "have", "with", "from", "into", "that", "this", "they", "their", "for", "and", "the", "our"}
    return {x for x in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", (value or "").lower()) if x not in stop}


def toe(construct):
    if construct in {"Data", "Technology", "Technical Skills"}:
        return "technology"
    if construct in {"Business Skills", "Inter-Departmental Coordination", "Organizational Change Capacity", "Basic Resources"}:
        return "organization"
    return "environment"


def main():
    if not WORKBOOK.exists():
        raise FileNotFoundError(WORKBOOK)
    sheet = load_workbook(WORKBOOK, read_only=True, data_only=True)["Results"]
    rows = list(sheet.values)
    headers = [str(x or "").strip() for x in rows[0]]
    pos = {name: i for i, name in enumerate(headers)}
    questions = []
    for row in rows[1:]:
        question = str(row[pos["Question"]] or "").strip()
        if question:
            construct = str(row[pos["Construct"]] or "").strip()
            questions.append((str(row[pos["Question ID"]] or ""), construct, question, str(row[pos["Source"]] or "")))
    if len(questions) != 46:
        raise RuntimeError("Expected 46 questions, found {}".format(len(questions)))

    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=15) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS healthcare_custom_questions (
                question_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                question_key text NOT NULL UNIQUE, construct text NOT NULL,
                toe_dimension text NOT NULL CHECK (toe_dimension IN ('technology','organization','environment')),
                question_text text NOT NULL, source text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS healthcare_custom_question_evidence (
                question_id uuid NOT NULL REFERENCES healthcare_custom_questions(question_id) ON DELETE CASCADE,
                chunk_id uuid NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                match_count integer NOT NULL,
                ai_score integer,
                ai_confidence numeric,
                ai_rationale text NOT NULL DEFAULT '',
                evidence_quote text NOT NULL DEFAULT '',
                PRIMARY KEY (question_id, chunk_id)
            )""")
            cursor.execute("""SELECT c.chunk_id, c.page_start, c.page_end, c.chunk_text,
                              s.score, s.confidence, s.rationale, s.evidence_quote
                       FROM chunks c JOIN companies co ON co.company_id=c.company_id
                       LEFT JOIN chunk_ai_scores s ON s.chunk_id=c.chunk_id AND s.status='completed'
                       WHERE lower(coalesce(co.industry,'')) LIKE '%health%'
                          OR lower(coalesce(co.industry,'')) LIKE '%pharma%'""")
            chunks = cursor.fetchall()
            for key, construct, question, source in questions:
                dimension = toe(construct)
                cursor.execute("""INSERT INTO healthcare_custom_questions
                    (question_key, construct, toe_dimension, question_text, source)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (question_key) DO UPDATE SET construct=EXCLUDED.construct,
                      toe_dimension=EXCLUDED.toe_dimension, question_text=EXCLUDED.question_text,
                      source=EXCLUDED.source, updated_at=now()
                    RETURNING question_id""", (key, construct, dimension, question, source))
                question_id = cursor.fetchone()[0]
                q_terms = terms(question)
                ranked = []
                for chunk_id, page_start, page_end, text, score, confidence, rationale, quote in chunks:
                    overlap = len(q_terms & terms(text))
                    if overlap:
                        ranked.append((overlap, chunk_id, score, confidence, rationale or "", quote or ""))
                ranked.sort(key=lambda x: (x[0], x[2] is not None, x[2] or -1), reverse=True)
                cursor.execute("DELETE FROM healthcare_custom_question_evidence WHERE question_id=%s", (question_id,))
                for overlap, chunk_id, score, confidence, rationale, quote in ranked[:5]:
                    cursor.execute("""INSERT INTO healthcare_custom_question_evidence
                        (question_id, chunk_id, match_count, ai_score, ai_confidence, ai_rationale, evidence_quote)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""", (question_id, chunk_id, overlap, score, confidence, rationale, quote))
                print("{}: saved {} evidence chunks".format(key, min(5, len(ranked))), flush=True)
    print("Imported 46 healthcare questions and persisted their existing evidence/AI scores.")


if __name__ == "__main__":
    main()
