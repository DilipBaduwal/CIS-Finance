"""Apply a cleaned, natural TOE question set while preserving scores."""
import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings

SOURCE = "healthcare_ai_readiness_benchmark_2025-2026_all_chunks"
BENCHMARK = "healthcare_ai_readiness"

QUESTIONS = [
    "Does the company maintain leadership oversight for major technology decisions?",
    "Does the company maintain reliable information and reporting processes for operational decisions?",
    "Does independent oversight review the effectiveness of controls and risk-management processes?",
    "Does the company invest in infrastructure and systems that improve operational capability?",
    "Does the company allocate capital responsibly between immediate operations and long-term capability building?",
    "Does the company maintain accurate financial and operational data for management decision-making?",
    "Does the company maintain reliable records and documentation for important business processes?",
    "Does the board receive sufficient information to oversee business performance and reporting quality?",
    "Does the company protect sensitive patient, customer, and business information from cyber threats?",
    "Does the company use systems and controls that preserve the accuracy and integrity of its data?",
    "Does the company maintain leadership continuity for long-term transformation and innovation?",
    "Does the company assign clear responsibility for implementing and monitoring strategic initiatives?",
    "Do leadership appointment and incentive practices support the skills needed for innovation and change?",
    "Does the company maintain clear governance responsibilities for strategic and technology-related decisions?",
    "Does the company monitor internal controls to protect physical and digital assets from misuse?",
    "Does the company have a repeatable process for developing and bringing new products or services to market?",
    "Does the company convert innovation and operational improvements into measurable business value?",
    "Does the company periodically test whether its internal controls remain effective as operations change?",
    "Can employees and stakeholders report concerns safely through accessible grievance channels?",
    "Does the company use transparent digital processes to support stakeholder participation?",
    "Does the company coordinate governance decisions with timely information and clear accountability?",
    "Does the company obtain independent assurance over important governance and compliance processes?",
    "Does the company maintain transparent and secure processes for stakeholder decisions and approvals?",
    "Does the company improve resource utilization and cost efficiency after major investments?",
    "Does the company maintain an enterprise risk framework covering operational, information, and cyber risks?",
    "Does the company have controls for identifying and managing risks from emerging technologies?",
    "Does the company monitor regulatory and market changes that could affect its strategic priorities?",
    "Does the company use fair incentives to attract and retain employees with critical skills?",
    "Does the company protect people who report misconduct, control weaknesses, or data concerns?",
    "Does the company manage energy use and environmental impacts as its operations and infrastructure grow?",
]


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT theme_index, question_text FROM healthcare_benchmark_questions WHERE benchmark_source=%s ORDER BY theme_index", (BENCHMARK,))
            rows = cursor.fetchall()
            if len(rows) != 30:
                raise RuntimeError("Expected 30 questions, found {}".format(len(rows)))
            for index, old_question in rows:
                new_question = QUESTIONS[index]
                cursor.execute("UPDATE benchmark_chunk_scores SET question_text=%s WHERE source_benchmark=%s AND question_text=%s", (new_question, SOURCE, old_question))
                cursor.execute("UPDATE healthcare_benchmark_questions SET question_text=%s, theme=%s, updated_at=now() WHERE benchmark_source=%s AND theme_index=%s", (new_question, new_question.rstrip("?"), BENCHMARK, index))
    print("Applied 30 cleaned TOE questions; preserved all existing scores and evidence mappings.")


if __name__ == "__main__":
    main()
