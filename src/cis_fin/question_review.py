"""Human edits for generated questions, with source quote validation and audit history."""

import json
import hashlib
import uuid
from dataclasses import dataclass

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.questions import _find_exact_source_quote, _normalize_whitespace
from cis_fin.settings import Settings


class QuestionReviewError(Exception):
    """A question candidate could not be safely updated."""


@dataclass(frozen=True)
class QuestionEditResult:
    validation_status: str
    evidence_quote: str
    quote_matches_source: bool


def edit_question_candidate(
    settings: Settings,
    question_id: uuid.UUID,
    question_text: str,
    evidence_quote: str,
) -> QuestionEditResult:
    question_text = " ".join(question_text.split())
    evidence_quote = evidence_quote.strip()
    if not settings.database_url:
        raise QuestionReviewError("Set DATABASE_URL before editing a question candidate.")
    if not question_text or len(question_text) > 2000:
        raise QuestionReviewError("Enter a question between 1 and 2,000 characters.")
    if not evidence_quote:
        raise QuestionReviewError("An evidence quotation is required; it cannot be blank.")
    try:
        import psycopg
    except ImportError as exc:
        raise QuestionReviewError("PostgreSQL driver is missing; install requirements.txt.") from exc

    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT q.question_text, q.review_status, ev.evidence_id, ev.evidence_quote,
                           ev.chunk_id, ch.chunk_text
                    FROM questions q
                    JOIN chunk_question_evidence ev ON ev.question_id = q.question_id
                    JOIN chunks ch ON ch.chunk_id = ev.chunk_id
                    WHERE q.question_id = %s
                    FOR UPDATE OF q, ev
                    """,
                    (question_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise QuestionReviewError("Question candidate was not found; refresh the page and try again.")
                old_text, review_status, evidence_id, old_quote, chunk_id, chunk_text = row
                if review_status != "pending":
                    raise QuestionReviewError(
                        "This candidate is already {} and cannot be edited. Create a new candidate or ask a reviewer.".format(
                            review_status
                        )
                    )

                exact_quote = _find_exact_source_quote(chunk_text, evidence_quote)
                if exact_quote is None:
                    saved_quote = evidence_quote
                    validation_status = "invalid"
                    semantic_support_status = "unsupported"
                else:
                    saved_quote = exact_quote
                    validation_status = "needs_review"
                    semantic_support_status = "needs_review"

                cursor.execute(
                    """
                    SELECT 1 FROM chunk_question_evidence
                    WHERE chunk_id = %s AND question_id = %s
                      AND evidence_quote = %s AND evidence_id <> %s
                    """,
                    (chunk_id, question_id, saved_quote, evidence_id),
                )
                if cursor.fetchone():
                    raise QuestionReviewError(
                        "This exact quotation is already attached to the candidate; leave it unchanged or choose another quotation."
                    )

                question_hash = hashlib.sha256(
                    _normalize_whitespace(question_text).casefold().encode("utf-8")
                ).hexdigest()
                cursor.execute(
                    """
                    UPDATE questions
                    SET question_text = %s, normalized_question_hash = %s,
                        validation_status = %s, review_status = 'pending',
                        reviewer_id = NULL, reviewed_at = NULL, reviewer_notes = NULL
                    WHERE question_id = %s AND review_status = 'pending'
                    """,
                    (question_text, question_hash, validation_status, question_id),
                )
                if cursor.rowcount != 1:
                    raise QuestionReviewError("Candidate status changed while editing; refresh and try again.")
                cursor.execute(
                    """
                    UPDATE chunk_question_evidence
                    SET evidence_quote = %s, semantic_support_status = %s
                    WHERE evidence_id = %s
                    """,
                    (saved_quote, semantic_support_status, evidence_id),
                )
                cursor.execute(
                    """
                    INSERT INTO audit_events (audit_id, action, entity_type, entity_id, details)
                    VALUES (%s, 'question.candidate_edited', 'question', %s, %s::jsonb)
                    """,
                    (
                        uuid.uuid4(),
                        question_id,
                        json.dumps(
                            {
                                "previous_question_sha256": hashlib.sha256(old_text.encode("utf-8")).hexdigest(),
                                "question_sha256": hashlib.sha256(question_text.encode("utf-8")).hexdigest(),
                                "quote_matches_source": exact_quote is not None,
                                "review_status": "pending",
                            }
                        ),
                    ),
                )
        return QuestionEditResult(
            validation_status=validation_status,
            evidence_quote=saved_quote,
            quote_matches_source=exact_quote is not None,
        )
    except QuestionReviewError:
        raise
    except psycopg.Error as exc:
        raise QuestionReviewError(
            "Could not save the candidate edit ({}). Check database permissions and retry; the transaction was rolled back.".format(
                type(exc).__name__
            )
        ) from exc
