"""Run a same-industry/year AI-readiness benchmark pilot."""

import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

from cis_fin.benchmark_discovery import BenchmarkChunk, discover_theme_clusters
from cis_fin.benchmark_regeneration import generate_distinct_question
from cis_fin.benchmark_vectors import load_vectors_by_point_ids
from cis_fin.postgres import secure_postgres_dsn


def run_pilot(
    settings,
    output_path="artifacts/it_ai_readiness_benchmark_2025-2026.json",
    industry="Information Technology",
    fiscal_year="2025-2026",
    symbols=None,
    max_chunks_per_company=None,
):
    """Discover recurring report themes, optionally sampling each company evenly.

    ``max_chunks_per_company`` bounds retrieval and clustering work while still
    including chunks spread across every matching company's report.
    """
    import psycopg

    with psycopg.connect(secure_postgres_dsn(settings.database_url), connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            symbol_filter = ""
            params = [max_chunks_per_company, max_chunks_per_company]
            if symbols:
                symbol_filter = " AND ch.symbol = ANY(%s)"
                params.append(list(symbols))
            cursor.execute(
                """
                WITH company_chunks AS (
                    SELECT ch.chunk_id::text AS chunk_id,
                           c.symbol,
                           d.document_id::text AS document_id,
                           ch.page_start,
                           ch.page_end,
                           ch.chunk_text,
                           COALESCE(
                               array_agg(DISTINCT lower(q.dimension))
                               FILTER (WHERE q.dimension IS NOT NULL),
                               ARRAY[]::text[]
                           ) AS dimensions,
                           row_number() OVER (
                               PARTITION BY c.symbol ORDER BY ch.chunk_index, ch.chunk_id
                           ) AS company_chunk_rank,
                           count(*) OVER (PARTITION BY c.symbol) AS company_chunk_total
                    FROM chunks ch
                    JOIN documents d ON d.document_id = ch.document_id
                    JOIN companies c ON c.company_id = ch.company_id
                    LEFT JOIN chunk_question_evidence ev ON ev.chunk_id = ch.chunk_id
                    LEFT JOIN questions q ON q.question_id = ev.question_id
                    WHERE d.processing_status = 'completed'
                      AND lower(c.industry) IN ('healthcare', 'pharmaceuticals')
                      AND regexp_replace(COALESCE(d.fiscal_year, ''), '[^0-9]', '', 'g')
                          IN ('2526', '202526', '20252026')
                    GROUP BY ch.chunk_id, c.symbol, d.document_id, ch.page_start,
                             ch.page_end, ch.chunk_text, ch.chunk_index
                )
                SELECT ch.chunk_id, ch.symbol, ch.document_id, ch.page_start,
                       ch.page_end, ch.chunk_text, ch.dimensions
                FROM company_chunks AS ch
                WHERE (%s::int IS NULL OR MOD(
                    ch.company_chunk_rank - 1,
                    GREATEST(1, CEIL(ch.company_chunk_total::numeric / %s)::int)
                ) = 0)
                """ + symbol_filter + """
                ORDER BY ch.symbol, ch.company_chunk_rank
                """,
                params,
            )
            rows = cursor.fetchall()

    companies = sorted({row[1] for row in rows})
    if max_chunks_per_company is not None:
        print(
            "Selected evenly spaced chunks from {} companies (target {} chunks/company; {} chunks selected).".format(
                len(companies), max_chunks_per_company, len(rows)
            ),
            flush=True,
        )
    if not rows:
        raise RuntimeError("No completed report chunks matched the requested cohort.")

    point_map = {}
    scroll_url = settings.qdrant_url.rstrip("/") + "/collections/{}/points/scroll".format(settings.qdrant_collection)
    for document_id in sorted({row[2] for row in rows}):
        offset = None
        while True:
            body = {
                "limit": 128,
                "with_payload": ["chunk_id"],
                "with_vector": False,
                "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]},
            }
            if offset is not None:
                body["offset"] = offset
            last_error = None
            for attempt in range(3):
                try:
                    request = Request(
                        scroll_url,
                        data=json.dumps(body).encode(),
                        headers={
                            "Content-Type": "application/json",
                            "api-key": settings.qdrant_api_key or "",
                        },
                        method="POST",
                    )
                    with urlopen(request, timeout=120) as response:
                        result = json.loads(response.read())["result"]
                    last_error = None
                    break
                except (TimeoutError, OSError, ValueError) as exc:
                    last_error = exc
                    time.sleep(2**attempt)
            if last_error is not None:
                raise RuntimeError(
                    "Qdrant scroll failed for document {} after 3 attempts".format(document_id)
                ) from last_error
            points = result.get("points", [])
            for point in points:
                chunk_id = point.get("payload", {}).get("chunk_id")
                if chunk_id:
                    point_map[chunk_id] = point["id"]
            offset = result.get("next_page_offset")
            if not offset or not points:
                break

    selected_point_ids = [point_map[row[0]] for row in rows if row[0] in point_map]
    vectors = load_vectors_by_point_ids(
        settings,
        selected_point_ids,
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
        dimension = next(
            (item for item in dimensions if item in ("technology", "organization", "environment")),
            "technology",
        )
        chunks.append(
            BenchmarkChunk(
                chunk_id, symbol, page_start, page_end, text, dimension, vectors[str(point_id)]
            )
        )

    print(
        "Clustering {} healthcare chunks from {} companies...".format(len(chunks), len(companies)),
        flush=True,
    )
    clusters = discover_theme_clusters(chunks, similarity_threshold=0.70, maximum_per_dimension=10)
    recurring = [cluster for cluster in clusters if cluster.support_count >= 2]
    print(
        "Found {} clusters; generating questions for {} recurring themes...".format(
            len(clusters), len(recurring)
        ),
        flush=True,
    )
    evidence_by_id = {chunk.chunk_id: chunk.text for chunk in chunks}
    page_by_id = {chunk.chunk_id: chunk.page_start for chunk in chunks}
    existing_questions = []
    generated_questions = {}
    question_errors = {}
    for number, cluster in enumerate(recurring, 1):
        evidence = evidence_by_id.get(cluster.representative_chunk_id, "")
        print(
            "Generating question {}/{} ({})...".format(number, len(recurring), cluster.toe_dimension),
            flush=True,
        )
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
            print("Question {}/{} generated.".format(number, len(recurring)), flush=True)
        else:
            print(
                "Question {}/{} generation failed ({}).".format(
                    number, len(recurring), cluster_error
                ),
                flush=True,
            )
        generated_questions[cluster.cluster_id] = question
        question_errors[cluster.cluster_id] = cluster_error

    themes = []
    for cluster in recurring:
        question = generated_questions.get(cluster.cluster_id, "")
        themes.append(
            {
                "cluster_id": cluster.cluster_id,
                "theme": question.rstrip("?") if question else cluster.cluster_id,
                "toe_dimension": cluster.toe_dimension,
                "support_count": cluster.support_count,
                "supporting_companies": list(cluster.company_symbols),
                "chunk_ids": list(cluster.chunk_ids),
                "representative_chunk_id": cluster.representative_chunk_id,
                "representative_evidence_preview": evidence_by_id.get(
                    cluster.representative_chunk_id, ""
                )[:900],
                "representative_pages": [page_by_id.get(cluster.representative_chunk_id, 0)],
                "question": question,
                "question_error": question_errors.get(cluster.cluster_id),
                "question_status": "generated" if question else "needs_review",
            }
        )
    payload = {
        "industry": industry,
        "fiscal_year": fiscal_year,
        "companies": companies,
        "cohort_company_count": len(companies),
        "source_chunk_count": len(chunks),
        "chunks_per_company_target": max_chunks_per_company,
        "source_chunk_sampling": (
            "evenly spaced within each completed company report"
            if max_chunks_per_company is not None
            else "all matching chunks"
        ),
        "cluster_count": len(clusters),
        "recurring_theme_count": len(recurring),
        "question_count": sum(bool(item["question"]) for item in themes),
        "question_generation_model": settings.ollama_model,
        "themes": themes,
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
