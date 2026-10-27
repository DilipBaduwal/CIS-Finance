"""PostgreSQL persistence for immutable benchmark versions and approvals."""
import json
import uuid

from cis_fin.postgres import secure_postgres_dsn


def persist_benchmark_version(settings, version, created_by=None):
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required to persist benchmark versions.")
    import psycopg
    with psycopg.connect(secure_postgres_dsn(settings.database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO benchmark_versions (benchmark_id, industry, fiscal_year, version_number, status, immutable, companies, themes, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                ON CONFLICT (benchmark_id) DO NOTHING
            """, (version["benchmark_id"], version["industry"], version["fiscal_year"], version["version_number"], version.get("status", "active"), version.get("immutable", True), json.dumps(version.get("companies", [])), json.dumps(version.get("themes", [])), created_by))
            cursor.execute("""INSERT INTO audit_events (audit_id, actor_id, action, entity_type, entity_id, details)
                VALUES (%s, NULL, 'benchmark.version_created', 'benchmark', %s, %s::jsonb)""", (uuid.uuid4(), uuid.uuid5(uuid.NAMESPACE_URL, version["benchmark_id"]), json.dumps({"benchmark_id": version["benchmark_id"], "version": version.get("version")})))


def persist_benchmark_approval(settings, benchmark_id, candidate_index, candidate, decision, reviewer=None):
    if not settings.database_url:
        raise ValueError("DATABASE_URL is required to persist approvals.")
    import psycopg
    with psycopg.connect(secure_postgres_dsn(settings.database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""INSERT INTO benchmark_approvals (approval_id, benchmark_id, candidate_index, question, confidence, decision, reviewer, reviewed_at, evidence_chunk_ids)
                VALUES (%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s IN ('approved','rejected') THEN now() ELSE NULL END,%s::jsonb)""", (uuid.uuid4(), benchmark_id, candidate_index, candidate.get("question", ""), float(candidate.get("confidence", 0)), decision, reviewer, decision, json.dumps(candidate.get("chunk_ids", []))))
