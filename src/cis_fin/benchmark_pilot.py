"""Run the first same-industry/year AI-readiness benchmark pilot."""

import json
from pathlib import Path
from urllib.request import Request, urlopen

from cis_fin.benchmark_discovery import BenchmarkChunk, discover_theme_clusters
from cis_fin.benchmark_regeneration import generate_distinct_question
from cis_fin.benchmark_vectors import load_vectors_by_point_ids
from cis_fin.postgres import secure_postgres_dsn


def run_pilot(settings, output_path="artifacts/it_ai_readiness_benchmark_2025-2026.json", industry="Information Technology", fiscal_year="2025-2026", symbols=None):
    import psycopg

    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            symbol_filter = ""
            params = []
            if symbols:
                symbol_filter = " AND c.symbol = ANY(%s)"
                params.append(list(symbols))
            cursor.execute(
                """
                SELECT ch.chunk_id::text, c.symbol, d.document_id::text, ch.page_start,
                       ch.page_end, ch.chunk_text,
                       COALESCE(array_agg(DISTINCT lower(q.dimension))
                       FILTER (WHERE q.dimension IS NOT NULL), ARRAY[]::text[])
                FROM chunks ch
                JOIN documents d ON d.document_id = ch.document_id
                JOIN companies c ON c.company_id = ch.company_id
                LEFT JOIN chunk_question_evidence ev ON ev.chunk_id = ch.chunk_id
                LEFT JOIN questions q ON q.question_id = ev.question_id
                WHERE d.processing_status = 'completed'
                  AND regexp_replace(COALESCE(d.fiscal_year, ''), '[^0-9]', '', 'g')
                      IN ('2526', '202526', '20252026')
                """ + symbol_filter + """
                GROUP BY ch.chunk_id, c.symbol, d.document_id, ch.page_start,
                         ch.page_end, ch.chunk_text
                """,
                params,
            )
            rows = cursor.fetchall()

    point_map = {}
    scroll_url = settings.qdrant_url.rstrip("/") + "/collections/{}/points/scroll".format(settings.qdrant_collection)
    for document_id in sorted({row[2] for row in rows}):
        offset = None
        while True:
            body = {
                "limit": 64,
                "with_payload": ["chunk_id"],
                "with_vector": False,
                "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
            }
            if offset is not None:
                body["offset"] = offset
            last_error = None
            for attempt in range(3):
                try:
                    request = Request(scroll_url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "api-key": settings.qdrant_api_key or ""}, method="POST")
                    with urlopen(request, timeout=120) as response:
                        result = json.loads(response.read())["result"]
                    last_error = None
                    break
                except (TimeoutError, OSError, ValueError) as exc:
                    last_error = exc
                    import time
                    time.sleep(2 ** attempt)
            if last_error is not None:
                raise RuntimeError("Qdrant scroll failed for document {} after 3 attempts".format(document_id)) from last_error
            points = result.get("points", [])
            for point in points:
                chunk_id = point.get("payload", {}).get("chunk_id")
                if chunk_id:
                    point_map[chunk_id] = point["id"]
            offset = result.get("next_page_offset")
            if not offset or not points:
                break

    vectors = load_vectors_by_point_ids(
        settings,
        list(point_map.values()),
        batch_size=32,
        retries=4,
        timeout_seconds=120,
        progress_callback=lambda batch, total, loaded: print(
            "Qdrant vectors: batch {}/{}; loaded {}".format(batch, total, loaded), flush=True
        ),
    )
    chunks = []
    for chunk_id, symbol, _document_id, page_start, page_end, text, dimensions in rows:
        point_id = point_map.get(chunk_id)
        if point_id is None:
            continue
        dimension = next((item for item in dimensions if item in ("technology", "organization", "environment")), "technology")
        chunks.append(BenchmarkChunk(chunk_id, symbol, page_start, page_end, text, dimension, vectors[str(point_id)]))

    clusters = discover_theme_clusters(chunks, similarity_threshold=0.70, maximum_per_dimension=10)
    recurring = [cluster for cluster in clusters if cluster.support_count >= 2]
    evidence_by_id = {chunk.chunk_id: chunk.text for chunk in chunks}
    existing_questions = []
    generated_questions = {}
    question_errors = {}
    for cluster in recurring:
        evidence = evidence_by_id.get(cluster.representative_chunk_id, "")
        try:
            question = generate_distinct_question(
                settings,
                cluster.toe_dimension,
                evidence,
                existing_questions,
            )
        except Exception as exc:
            question = ""
            cluster_error = type(exc).__name__
        else:
            cluster_error = None
        if question:
            existing_questions.append(question)
        generated_questions[cluster.cluster_id] = question
        question_errors[cluster.cluster_id] = cluster_error
    payload = {
        "industry": industry,
        "fiscal_year": fiscal_year,
        "companies": sorted({row[1] for row in rows}),
        "source_chunk_count": len(chunks),
        "cluster_count": len(clusters),
        "recurring_theme_count": len(recurring),
        "themes": [
            {
                "cluster_id": cluster.cluster_id,
                "toe_dimension": cluster.toe_dimension,
                "support_count": cluster.support_count,
                "supporting_companies": list(cluster.company_symbols),
                "chunk_ids": list(cluster.chunk_ids),
                "representative_chunk_id": cluster.representative_chunk_id,
                "question": generated_questions.get(cluster.cluster_id, ""),
                "question_error": question_errors.get(cluster.cluster_id),
            }
            for cluster in recurring
        ],
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
