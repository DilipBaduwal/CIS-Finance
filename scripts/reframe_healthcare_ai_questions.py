"""Create natural TOE AI-readiness questions while preserving saved scores."""
import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
BENCHMARK = "healthcare_ai_readiness"

QUESTIONS = [
    "Does the company maintain effective leadership oversight for technology and AI adoption decisions?",
    "Does the company demonstrate reliable reporting and process discipline needed to operate AI-enabled systems?",
    "Does independent oversight review the controls, risks, and assurance processes supporting digital and AI operations?",
    "Does the company invest in assets and initiatives that build its digital and AI capability?",
    "Does the company allocate capital responsibly for long-term technology, data, and AI initiatives?",
    "Does the company maintain accurate, reliable financial data that can support analytics and AI decision-making?",
    "Does the company maintain audited records and information-governance practices that support trustworthy AI?",
    "Does the board oversee the quality, integrity, and governance of information used for technology and AI decisions?",
    "Does the company protect sensitive data and strengthen cybersecurity controls for AI-enabled healthcare operations?",
    "Does the company use systems and controls that ensure the accuracy and integrity of data used by digital tools and AI?",
    "Does the company maintain leadership continuity and oversight capacity for long-term technology and AI transformation?",
    "Does the company assign clear organizational responsibility for implementing and monitoring technology and AI initiatives?",
    "Does the company maintain remuneration and incentive practices that support digital skills, innovation, and responsible AI adoption?",
    "Does the company provide governance structures and terms of reference that support accountable technology and AI oversight?",
    "Does the company maintain internal controls that protect technology assets, data, and AI-related resources from misuse?",
    "Does the company have a process for bringing technology-enabled products and services to the right markets?",
    "Does the company demonstrate operational practices that turn technology and innovation into sustainable business value?",
    "Does the company monitor internal controls to protect digital assets and information from unauthorized use or disposal?",
    "Does the company provide employees with digital, AI, and human-rights training and safe channels for raising concerns?",
    "Does the company use appropriate digital processes to enable transparent and accessible stakeholder participation?",
    "Does the company provide sufficient notice and coordination for governance decisions involving technology and AI priorities?",
    "Does the company obtain independent assurance over governance and control practices relevant to technology and AI readiness?",
    "Does the company use secure digital processes to enable transparent stakeholder participation and decision-making?",
    "Does the company improve technology utilization, cost efficiency, and product mix after major investments?",
    "Does the company maintain an enterprise risk framework covering data, technology, sustainability, and cybersecurity risks?",
    "Does the company have a mechanism to identify and mitigate AI risks such as privacy loss, bias, and cybersecurity threats?",
    "Does the company monitor external policy, market, and regulatory changes that may affect its technology and AI investment priorities?",
    "Does the company use employee incentives to attract and retain people with technology, data, and AI capabilities?",
    "Does the company provide a confidential mechanism for reporting technology, data, AI, or compliance concerns?",
    "Does the company implement energy-efficient technology and infrastructure practices that support sustainable digital operations?",
]


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT theme_index, question_text FROM healthcare_benchmark_questions WHERE benchmark_source=%s ORDER BY theme_index",
                (BENCHMARK,),
            )
            rows = cursor.fetchall()
            if len(rows) != len(QUESTIONS):
                raise RuntimeError("Expected 30 healthcare questions, found {}".format(len(rows)))
            for index, old_question in rows:
                new_question = QUESTIONS[index]
                cursor.execute(
                    "UPDATE benchmark_chunk_scores SET question_text=%s WHERE source_benchmark=%s AND question_text=%s",
                    (new_question, SOURCE, old_question),
                )
                cursor.execute(
                    """UPDATE healthcare_benchmark_questions
                       SET question_text=%s, theme=%s, updated_at=now()
                       WHERE benchmark_source=%s AND theme_index=%s""",
                    (new_question, new_question.rstrip("?"), BENCHMARK, index),
                )
    print("Updated 30 healthcare TOE AI-readiness questions; reused all existing scores and evidence.")


if __name__ == "__main__":
    main()
