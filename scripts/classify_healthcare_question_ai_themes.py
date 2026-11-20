"""Classify the 30 healthcare benchmark questions by semantic AI relevance."""
import psycopg

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


BENCHMARK = "healthcare_ai_readiness"

# This is a question-level semantic label, not a keyword check.  ai_enabling
# covers capabilities such as trustworthy data, cybersecurity, skills and
# governance that enable AI even when the question does not say "AI".
CLASSIFICATIONS = {
    0: ("ai_enabling", "Technology oversight enables responsible digital and AI adoption."),
    1: ("ai_enabling", "Reliable reporting and process discipline support AI-enabled operations."),
    2: ("ai_enabling", "Independent controls and risk assurance support digital and AI operations."),
    3: ("ai_enabling", "Infrastructure and systems are foundational AI capabilities."),
    4: ("ai_enabling", "Capital allocation can support long-term technology and AI capability building."),
    5: ("ai_enabling", "Accurate data is an AI-enabling capability."),
    6: ("ai_enabling", "Information governance and trustworthy records enable responsible AI."),
    7: ("ai_enabling", "Information quality and governance support technology and AI decisions."),
    8: ("ai_theme", "Directly concerns cybersecurity and sensitive data in AI-enabled healthcare operations."),
    9: ("ai_enabling", "Data integrity is an AI-enabling capability for digital tools and AI."),
    10: ("ai_enabling", "Leadership capacity supports technology and AI transformation."),
    11: ("ai_theme", "Directly concerns organizational accountability for technology and AI initiatives."),
    12: ("ai_enabling", "Digital skills and responsible AI adoption are explicit readiness capabilities."),
    13: ("ai_theme", "Directly concerns accountable technology and AI oversight."),
    14: ("ai_enabling", "Protection of technology assets and data supports responsible AI."),
    15: ("ai_enabling", "Technology-enabled product development is an AI-adjacent capability."),
    16: ("ai_enabling", "Innovation-to-value capability supports adoption of AI and digital systems."),
    17: ("not_ai_theme", "Generic digital-asset control question without a clear AI-readiness connection."),
    18: ("ai_theme", "Directly mentions digital and AI skills, training and responsible adoption."),
    19: ("not_ai_theme", "Generic stakeholder participation question without a clear AI connection."),
    20: ("ai_enabling", "Governance coordination can support technology and AI priorities."),
    21: ("ai_enabling", "Independent assurance supports trustworthy technology and AI governance."),
    22: ("not_ai_theme", "Generic digital stakeholder-process question without a clear AI connection."),
    23: ("ai_enabling", "Technology utilization and investment efficiency support digital capability."),
    24: ("ai_enabling", "Data, technology and cybersecurity risk management enable responsible AI."),
    25: ("ai_theme", "Directly concerns AI risks including privacy, bias and cybersecurity."),
    26: ("ai_theme", "Directly concerns external policy and regulation affecting technology and AI."),
    27: ("ai_enabling", "Technology, data and AI skills are explicit workforce capabilities."),
    28: ("ai_theme", "Directly concerns reporting technology, data and AI concerns."),
    29: ("ai_enabling", "Energy-efficient digital infrastructure is an AI-enabling sustainability capability."),
}


def main():
    settings = Settings.from_env()
    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """ALTER TABLE healthcare_benchmark_questions
                   ADD COLUMN IF NOT EXISTS ai_theme_label text NOT NULL DEFAULT 'not_ai_theme',
                   ADD COLUMN IF NOT EXISTS ai_theme_reason text NOT NULL DEFAULT ''"""
            )
            cursor.execute(
                "SELECT theme_index FROM healthcare_benchmark_questions WHERE benchmark_source=%s ORDER BY theme_index",
                (BENCHMARK,),
            )
            indexes = [row[0] for row in cursor.fetchall()]
            if len(indexes) != 30 or set(indexes) != set(range(30)):
                raise RuntimeError("Expected theme indexes 0 through 29, found {}".format(indexes))
            for index, (label, reason) in CLASSIFICATIONS.items():
                cursor.execute(
                    """UPDATE healthcare_benchmark_questions
                       SET ai_theme_label=%s, ai_theme_reason=%s, updated_at=now()
                       WHERE benchmark_source=%s AND theme_index=%s""",
                    (label, reason, BENCHMARK, index),
                )
    print("Classified 30 healthcare benchmark questions by semantic AI relevance.")


if __name__ == "__main__":
    main()
