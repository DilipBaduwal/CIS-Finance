"""Run the first same-industry/year AI-readiness benchmark pilot."""

import json
from pathlib import Path
from urllib.request import Request, urlopen

from cis_fin.benchmark_discovery import BenchmarkChunk, discover_theme_clusters
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
                GROUP BY ch.chunk_id, c.symbol, d.document_id, ch.page_start,
                         ch.page_end, ch.chunk_text
                """ + symbol_filter,
                params,
            )
            rows = cursor.fetchall()

    point_map = {}
    scroll_url = settings.qdrant_url.rstrip("/") + "/collections/{}/points/scroll".format(settings.qdrant_collection)
    for document_id in sorted({row[2] for row in rows}):
        request = Request(
            scroll_url,
            data=json.dumps({
                "limit": 100,
                "with_payload": True,
                "with_vector": False,
                "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
            }).encode(),
            headers={"Content-Type": "application/json", "api-key": settings.qdrant_api_key or ""},
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            points = json.loads(response.read())["result"]["points"]
        for point in points:
            chunk_id = point.get("payload", {}).get("chunk_id")
            if chunk_id:
                point_map[chunk_id] = point["id"]

    vectors = load_vectors_by_point_ids(settings, list(point_map.values()), batch_size=16)
    chunks = []
    for chunk_id, symbol, _document_id, page_start, page_end, text, dimensions in rows:
        point_id = point_map.get(chunk_id)
        if point_id is None:
            continue
        dimension = next((item for item in dimensions if item in ("technology", "organization", "environment")), "technology")
        chunks.append(BenchmarkChunk(chunk_id, symbol, page_start, page_end, text, dimension, vectors[str(point_id)]))

    clusters = discover_theme_clusters(chunks, similarity_threshold=0.70, maximum_per_dimension=10)
    recurring = [cluster for cluster in clusters if cluster.support_count >= 2]
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
            }
            for cluster in recurring
        ],
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
