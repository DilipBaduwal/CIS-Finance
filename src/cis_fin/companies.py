"""Explicit company-master registration without guessing index membership."""

import uuid
import json
from datetime import date

from cis_fin.ingestion import IngestionError
from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


def register_company(
    settings: Settings,
    symbol: str,
    name: str,
    sector: str,
    industry: str,
    list_version: str,
    as_of: date,
) -> tuple:
    if not settings.database_url:
        raise IngestionError("Set DATABASE_URL in the private .env file before registering companies.")
    fields = (symbol, name, sector, industry, list_version)
    if any(not value.strip() for value in fields):
        raise IngestionError("Symbol, company name, sector, industry, and constituent-list version are required.")
    try:
        import psycopg
    except ImportError as exc:
        raise IngestionError("PostgreSQL driver is missing; install requirements.txt.") from exc

    company_id = uuid.uuid4()
    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO companies (
                        company_id, symbol, company_name, sector, industry,
                        constituent_list_version, constituent_as_of
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO NOTHING
                    RETURNING company_id
                    """,
                    (company_id, symbol.strip().upper(), name.strip(), sector.strip(), industry.strip(), list_version.strip(), as_of),
                )
                created = cursor.fetchone()
                if created:
                    cursor.execute(
                        """
                        INSERT INTO audit_events (
                            audit_id, actor_id, action, entity_type, entity_id, details
                        )
                        VALUES (%s, NULL, 'company.registered', 'company', %s, %s::jsonb)
                        """,
                        (
                            uuid.uuid4(),
                            company_id,
                            json.dumps(
                                {
                                    "symbol": symbol.strip().upper(),
                                    "constituent_list_version": list_version.strip(),
                                    "constituent_as_of": as_of.isoformat(),
                                }
                            ),
                        ),
                    )
                    return company_id, True
                cursor.execute(
                    "SELECT company_id FROM companies WHERE symbol = %s",
                    (symbol.strip().upper(),),
                )
                existing = cursor.fetchone()
                if not existing:
                    raise IngestionError("Company symbol conflict occurred, but the existing row could not be read.")
                return existing[0], False
    except IngestionError:
        raise
    except psycopg.Error as exc:
        if getattr(exc, "sqlstate", None) == "23503":
            raise IngestionError("Company registration was rejected by a database constraint.") from exc
        raise IngestionError(
            "Company registration failed ({}). Verify migrations and database permissions.".format(type(exc).__name__)
        ) from exc


def register_detected_company(
    settings: Settings,
    symbol: str,
    name: str,
    sector: str,
    industry: str,
    profile_source: str,
) -> tuple:
    """Create a pending-review company profile from user-confirmed PDF suggestions."""
    if not settings.database_url:
        raise IngestionError("Set DATABASE_URL in the private .env file before registering companies.")
    fields = (symbol, name, sector, industry, profile_source)
    if any(not value.strip() for value in fields):
        raise IngestionError(
            "Confirm a ticker, company name, sector, and industry before processing the report."
        )
    try:
        import psycopg
    except ImportError as exc:
        raise IngestionError("PostgreSQL driver is missing; install requirements.txt.") from exc

    company_id = uuid.uuid4()
    normalized_symbol = symbol.strip().upper()
    try:
        with psycopg.connect(secure_postgres_dsn(settings.database_url)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO companies (
                        company_id, symbol, company_name, sector, industry,
                        constituent_list_version, constituent_as_of,
                        profile_status, profile_source
                    )
                    VALUES (%s, %s, %s, %s, %s, NULL, NULL, 'pending_review', %s)
                    ON CONFLICT (symbol) DO NOTHING
                    RETURNING company_id
                    """,
                    (
                        company_id,
                        normalized_symbol,
                        name.strip(),
                        sector.strip(),
                        industry.strip(),
                        profile_source.strip()[:500],
                    ),
                )
                inserted = cursor.fetchone()
                if inserted:
                    cursor.execute(
                        """
                        INSERT INTO audit_events (
                            audit_id, actor_id, action, entity_type, entity_id, details
                        )
                        VALUES (%s, NULL, 'company.auto_detected', 'company', %s, %s::jsonb)
                        """,
                        (
                            uuid.uuid4(),
                            company_id,
                            json.dumps(
                                {
                                    "symbol": normalized_symbol,
                                    "profile_status": "pending_review",
                                    "profile_source": profile_source.strip()[:500],
                                }
                            ),
                        ),
                    )
                    return company_id, True
                cursor.execute(
                    "SELECT company_id FROM companies WHERE symbol = %s",
                    (normalized_symbol,),
                )
                existing = cursor.fetchone()
                if not existing:
                    raise IngestionError(
                        "Company symbol conflict occurred, but the existing row could not be read."
                    )
                return existing[0], False
    except IngestionError:
        raise
    except psycopg.Error as exc:
        if getattr(exc, "sqlstate", None) == "23503":
            raise IngestionError("Company profile was rejected by a database constraint.") from exc
        raise IngestionError(
            "Could not save the auto-detected company profile ({}). Verify migrations and database permissions.".format(
                type(exc).__name__
            )
        ) from exc
