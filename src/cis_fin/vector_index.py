"""Bounded local embeddings and idempotent Qdrant vector indexing."""

import json
import math
import socket
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cis_fin.postgres import secure_postgres_dsn
from cis_fin.settings import Settings


VECTOR_NAMESPACE = uuid.UUID("0e628e84-1378-4fee-85a0-c0aca9c271ef")
MAX_VECTOR_BATCH = 32
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
EXPECTED_EMBEDDING_DIMENSIONS = 768


class VectorIndexError(Exception):
    """Embedding or Qdrant indexing failed; safe to retry using stable point IDs."""


@dataclass(frozen=True)
class VectorChunk:
    chunk_id: uuid.UUID
    vector_id: uuid.UUID
    text: str
    document_id: uuid.UUID
    company_id: uuid.UUID
    page_start: int
    page_end: int


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, timeout_seconds: int = 180):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise VectorIndexError(
                "Ollama embeddings must use a local HTTP endpoint; source text will not be sent to a remote model."
            )
        if not model.strip() or not 1 <= timeout_seconds <= 600:
            raise VectorIndexError("Set a local OLLAMA_EMBEDDING_MODEL and a timeout between 1 and 600 seconds.")
        self.endpoint = base_url.rstrip("/") + "/api/embed"
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts or len(texts) > MAX_VECTOR_BATCH:
            raise VectorIndexError("Embedding batch must contain between 1 and {} chunks.".format(MAX_VECTOR_BATCH))
        body = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        request = Request(self.endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise VectorIndexError("Ollama returned an oversized embedding response.")
            payload = json.loads(response_body.decode("utf-8"))
            vectors = payload["embeddings"]
            if (
                not isinstance(vectors, list)
                or len(vectors) != len(texts)
                or any(
                    not isinstance(vector, list)
                    or len(vector) != EXPECTED_EMBEDDING_DIMENSIONS
                    or any(
                        not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in vector
                    )
                    for vector in vectors
                )
            ):
                raise VectorIndexError(
                    "Ollama embedding dimensions did not match the expected {}. Check the configured embedding model."
                    .format(EXPECTED_EMBEDDING_DIMENSIONS)
                )
            return vectors
        except HTTPError as exc:
            raise VectorIndexError(
                "Local Ollama returned HTTP {} during embedding. Check that the embedding model is installed.".format(
                    exc.code
                )
            ) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise VectorIndexError("Could not reach local Ollama for embeddings; start Ollama and retry.") from exc
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VectorIndexError("Ollama returned an invalid embedding response.") from exc


class QdrantRestClient:
    def __init__(self, base_url: str, api_key: Optional[str], timeout_seconds: int = 30):
        parsed = urlsplit(base_url)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            parsed.scheme not in (("http", "https") if local else ("https",))
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise VectorIndexError(
                "QDRANT_URL must be a valid HTTPS cloud endpoint (HTTP is allowed only for localhost)."
            )
        if not local and not api_key:
            raise VectorIndexError("Set QDRANT_API_KEY before writing to remote Qdrant.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, body: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise VectorIndexError("Qdrant returned an oversized response.")
            return json.loads(response_body.decode("utf-8")) if response_body else {}
        except HTTPError as exc:
            detail = exc.read(512).decode("utf-8", errors="replace")
            if exc.code == 404:
                raise
            if exc.code in (401, 403):
                raise VectorIndexError("Qdrant rejected the API key or write permission (HTTP {}).".format(exc.code)) from exc
            if exc.code == 409:
                raise
            raise VectorIndexError("Qdrant returned HTTP {}: {}.".format(exc.code, detail[:180])) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise VectorIndexError(
                "Could not reach Qdrant. Check QDRANT_URL, TLS, firewall, and cluster availability."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VectorIndexError("Qdrant returned an invalid JSON response.") from exc

    def ensure_collection(self, collection: str, dimensions: int) -> None:
        path = "/collections/" + collection
        try:
            result = self._request("GET", path)
        except HTTPError as exc:
            if exc.code != 404:
                raise VectorIndexError("Could not inspect Qdrant collection (HTTP {}).".format(exc.code)) from exc
            try:
                self._request(
                    "PUT",
                    path,
                    {"vectors": {"size": dimensions, "distance": "Cosine"}},
                )
            except HTTPError as create_error:
                if create_error.code != 409:
                    raise VectorIndexError(
                        "Could not create Qdrant collection (HTTP {}).".format(create_error.code)
                    ) from create_error
            result = self._request("GET", path)
        try:
            actual = result["result"]["config"]["params"]["vectors"]["size"]
        except (KeyError, TypeError):
            raise VectorIndexError("Qdrant collection has an unsupported vector configuration.")
        if actual != dimensions:
            raise VectorIndexError(
                "Qdrant collection '{}' uses {} dimensions, but the embedding model returns {}. "
                "Use a new collection name; the existing collection was not modified.".format(
                    collection, actual, dimensions
                )
            )
        self.ensure_document_id_index(collection)

    def ensure_document_id_index(self, collection: str) -> None:
        try:
            result = self._request(
                "PUT",
                "/collections/{}/index?wait=true".format(collection),
                {"field_name": "document_id", "field_schema": "keyword"},
            )
        except HTTPError as exc:
            raise VectorIndexError(
                "Qdrant could not create the document_id payload index (HTTP {}). "
                "Check that the cluster supports payload indexes and the key has collection-write permission.".format(
                    exc.code
                )
            ) from exc
        if result.get("status") != "ok" or "result" not in result:
            raise VectorIndexError(
                "Qdrant did not confirm the document_id payload index required for safe reconciliation."
            )

    def upsert(self, collection: str, chunks: Sequence[VectorChunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors) or not chunks:
            raise VectorIndexError("Qdrant point and vector batch sizes do not match.")
        points = []
        for chunk, vector in zip(chunks, vectors):
            points.append(
                {
                    "id": str(chunk.vector_id),
                    "vector": vector,
                    "payload": {
                        "chunk_id": str(chunk.chunk_id),
                        "document_id": str(chunk.document_id),
                        "company_id": str(chunk.company_id),
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                    },
                }
            )
        result = self._request(
            "PUT",
            "/collections/{}/points?wait=true".format(collection),
            {"points": points},
        )
        if result.get("status") != "ok" or "result" not in result:
            raise VectorIndexError("Qdrant did not confirm the vector upsert; PostgreSQL records remain pending.")

    def document_point_ids(self, collection: str, document_id: uuid.UUID) -> set:
        point_ids = set()
        offset = None
        while True:
            body = {
                "filter": {
                    "must": [{"key": "document_id", "match": {"value": str(document_id)}}]
                },
                "limit": 256,
                "with_payload": False,
                "with_vectors": False,
            }
            if offset is not None:
                body["offset"] = offset
            result = self._request(
                "POST",
                "/collections/{}/points/scroll".format(collection),
                body,
            )
            try:
                page = result["result"]
                points = page["points"]
                point_ids.update(str(point["id"]) for point in points)
                offset = page.get("next_page_offset")
            except (KeyError, TypeError) as exc:
                raise VectorIndexError("Qdrant returned an invalid scroll response during reconciliation.") from exc
            if offset is None or not points:
                return point_ids


class PostgresVectorStore:
    def __init__(self, settings: Settings):
        if not settings.database_url:
            raise VectorIndexError("Set DATABASE_URL before embedding chunks.")
        try:
            import psycopg
        except ImportError as exc:
            raise VectorIndexError("PostgreSQL driver is missing; install requirements.txt.") from exc
        self.psycopg = psycopg
        self.dsn = secure_postgres_dsn(settings.database_url)

    def pending_chunks(
        self,
        document_id: uuid.UUID,
        collection: str,
        model: str,
        version: str,
        limit: int,
    ) -> Sequence[VectorChunk]:
        try:
            with self.psycopg.connect(self.dsn, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT ch.chunk_id, ch.chunk_text, ch.document_id, ch.company_id,
                               ch.page_start, ch.page_end
                        FROM chunks AS ch
                        WHERE ch.document_id = %s
                          AND NOT EXISTS (
                              SELECT 1 FROM embedding_registry AS er
                              WHERE er.chunk_id = ch.chunk_id
                                AND er.collection_name = %s
                                AND er.embedding_model = %s
                                AND er.embedding_version = %s
                                AND er.indexing_status = 'indexed'
                          )
                        ORDER BY ch.chunk_index
                        LIMIT %s
                        """,
                        (document_id, collection, model, version, limit),
                    )
                    rows = cursor.fetchall()
                    chunks = []
                    for chunk_id, text, doc_id, company_id, page_start, page_end in rows:
                        vector_id = uuid.uuid5(
                            VECTOR_NAMESPACE,
                            "{}:{}:{}:{}".format(collection, model, version, chunk_id),
                        )
                        chunks.append(
                            VectorChunk(
                                chunk_id=chunk_id,
                                vector_id=vector_id,
                                text=text,
                                document_id=doc_id,
                                company_id=company_id,
                                page_start=page_start,
                                page_end=page_end,
                            )
                        )
                    return chunks
        except self.psycopg.Error as exc:
            raise VectorIndexError(
                "Could not load chunks for embedding ({}). Verify database access and migrations.".format(type(exc).__name__)
            ) from exc

    def count_pending(self, document_id: uuid.UUID, collection: str, model: str, version: str) -> int:
        try:
            with self.psycopg.connect(self.dsn, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT count(*) FROM chunks AS ch
                        WHERE ch.document_id = %s AND NOT EXISTS (
                            SELECT 1 FROM embedding_registry AS er
                            WHERE er.chunk_id = ch.chunk_id AND er.collection_name = %s
                              AND er.embedding_model = %s AND er.embedding_version = %s
                              AND er.indexing_status = 'indexed'
                        )
                        """,
                        (document_id, collection, model, version),
                    )
                    return cursor.fetchone()[0]
        except self.psycopg.Error as exc:
            raise VectorIndexError(
                "Could not count chunks awaiting embedding ({}).".format(type(exc).__name__)
            ) from exc

    def stage(self, chunks: Sequence[VectorChunk], collection: str, model: str, version: str, dimensions: int) -> None:
        try:
            with self.psycopg.connect(self.dsn, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    for chunk in chunks:
                        embedding_id = uuid.uuid5(VECTOR_NAMESPACE, "registry:" + str(chunk.vector_id))
                        cursor.execute(
                            """
                            INSERT INTO embedding_registry (
                                embedding_id, chunk_id, vector_id, collection_name, embedding_model,
                                embedding_version, vector_dimensions, indexing_status
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                            ON CONFLICT (chunk_id, collection_name, embedding_model, embedding_version)
                            DO UPDATE SET vector_id = EXCLUDED.vector_id,
                                          vector_dimensions = EXCLUDED.vector_dimensions,
                                          indexing_status = CASE
                                              WHEN embedding_registry.indexing_status = 'indexed' THEN 'indexed'
                                              ELSE 'pending'
                                          END,
                                          updated_at = now()
                            """,
                            (
                                embedding_id,
                                chunk.chunk_id,
                                chunk.vector_id,
                                collection,
                                model,
                                version,
                                dimensions,
                            ),
                        )
        except self.psycopg.Error as exc:
            raise VectorIndexError("Could not stage embedding records ({}).".format(type(exc).__name__)) from exc

    def mark_indexed(self, chunks: Sequence[VectorChunk], collection: str, model: str, version: str) -> None:
        try:
            with self.psycopg.connect(self.dsn, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    for chunk in chunks:
                        cursor.execute(
                            """
                        UPDATE embedding_registry
                        SET indexing_status = 'indexed', updated_at = now()
                        WHERE chunk_id = %s AND collection_name = %s
                          AND embedding_model = %s AND embedding_version = %s
                        """,
                            (chunk.chunk_id, collection, model, version),
                        )
                        if cursor.rowcount != 1:
                            raise VectorIndexError(
                                "Some indexed vectors could not be linked back to their PostgreSQL chunks."
                            )
        except VectorIndexError:
            raise
        except self.psycopg.Error as exc:
            raise VectorIndexError(
                "Qdrant upsert succeeded, but PostgreSQL index status did not save ({}). "
                "Retrying is safe because point IDs are stable.".format(type(exc).__name__)
            ) from exc

    def reconciliation_snapshot(
        self,
        document_id: uuid.UUID,
        collection: str,
        model: str,
        version: str,
    ) -> Tuple[set, int, int]:
        try:
            with self.psycopg.connect(self.dsn, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT vector_id FROM embedding_registry
                        WHERE collection_name = %s AND embedding_model = %s
                          AND embedding_version = %s AND indexing_status = 'indexed'
                          AND chunk_id IN (SELECT chunk_id FROM chunks WHERE document_id = %s)
                        """,
                        (collection, model, version, document_id),
                    )
                    expected_ids = {str(row[0]) for row in cursor.fetchall()}
                    cursor.execute(
                        """
                        SELECT count(*) FROM chunks AS ch
                        WHERE ch.document_id = %s AND NOT EXISTS (
                            SELECT 1 FROM embedding_registry AS er
                            WHERE er.chunk_id = ch.chunk_id AND er.collection_name = %s
                              AND er.embedding_model = %s AND er.embedding_version = %s
                              AND er.indexing_status = 'indexed'
                        )
                        """,
                        (document_id, collection, model, version),
                    )
                    chunks_without_indexed_records = cursor.fetchone()[0]
                    cursor.execute(
                        """
                        SELECT count(*) FROM embedding_registry AS er
                        JOIN chunks AS ch ON ch.chunk_id = er.chunk_id
                        WHERE ch.document_id = %s AND er.indexing_status = 'indexed'
                          AND (er.collection_name <> %s OR er.embedding_model <> %s
                               OR er.embedding_version <> %s)
                        """,
                        (document_id, collection, model, version),
                    )
                    older_index_rows = cursor.fetchone()[0]
                    return expected_ids, chunks_without_indexed_records, older_index_rows
        except self.psycopg.Error as exc:
            raise VectorIndexError(
                "Could not read PostgreSQL vector records for reconciliation ({}).".format(type(exc).__name__)
            ) from exc

    def record_reindex(
        self,
        document_id: uuid.UUID,
        collection: str,
        model: str,
        version: str,
        indexed_count: int,
    ) -> None:
        try:
            with self.psycopg.connect(self.dsn, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO audit_events (audit_id, action, entity_type, entity_id, details)
                        VALUES (%s, 'document.vectors_reindexed', 'document', %s, %s::jsonb)
                        """,
                        (
                            uuid.uuid4(),
                            document_id,
                            json.dumps(
                                {
                                    "collection": collection,
                                    "model": model,
                                    "embedding_version": version,
                                    "indexed_chunks": indexed_count,
                                }
                            ),
                        ),
                    )
        except self.psycopg.Error as exc:
            raise VectorIndexError(
                "Vectors were indexed, but the reprocessing audit record did not save ({}). "
                "The upserts are idempotent; retry after checking PostgreSQL permissions.".format(type(exc).__name__)
            ) from exc


def index_document_chunks(
    settings: Settings,
    document_id: uuid.UUID,
    store: Optional[PostgresVectorStore] = None,
    embedder: Optional[OllamaEmbedder] = None,
    qdrant: Optional[QdrantRestClient] = None,
    batch_size: int = 16,
    progress_callback=None,
) -> int:
    if not 1 <= batch_size <= MAX_VECTOR_BATCH:
        raise VectorIndexError("Embedding batch size must be between 1 and {}.".format(MAX_VECTOR_BATCH))
    if not settings.qdrant_url:
        raise VectorIndexError("Set QDRANT_URL before indexing vectors.")
    store = store or PostgresVectorStore(settings)
    embedder = embedder or OllamaEmbedder(settings.ollama_base_url, settings.ollama_embedding_model)
    qdrant = qdrant or QdrantRestClient(settings.qdrant_url, settings.qdrant_api_key)
    qdrant.ensure_collection(settings.qdrant_collection, EXPECTED_EMBEDDING_DIMENSIONS)
    total = 0
    while True:
        chunks = store.pending_chunks(
            document_id,
            settings.qdrant_collection,
            embedder.model,
            settings.qdrant_embedding_version,
            batch_size,
        )
        if not chunks:
            return total
        store.stage(
            chunks,
            settings.qdrant_collection,
            embedder.model,
            settings.qdrant_embedding_version,
            EXPECTED_EMBEDDING_DIMENSIONS,
        )
        vectors = embedder.embed([chunk.text for chunk in chunks])
        qdrant.upsert(settings.qdrant_collection, chunks, vectors)
        store.mark_indexed(chunks, settings.qdrant_collection, embedder.model, settings.qdrant_embedding_version)
        total += len(chunks)
        if progress_callback:
            progress_callback(total)


@dataclass(frozen=True)
class ReconciliationResult:
    chunks_without_indexed_records: int
    indexed_records_missing_from_qdrant: int
    untracked_qdrant_points: int
    older_index_rows: int


def reconcile_document_vectors(settings: Settings, document_id: uuid.UUID) -> ReconciliationResult:
    """Compare read-only PostgreSQL registry state with Qdrant point IDs for one document."""
    if not settings.qdrant_url:
        raise VectorIndexError("Set QDRANT_URL before reconciling vector records.")
    store = PostgresVectorStore(settings)
    qdrant = QdrantRestClient(settings.qdrant_url, settings.qdrant_api_key)
    indexed_ids, unindexed_count, older_count = store.reconciliation_snapshot(
        document_id,
        settings.qdrant_collection,
        settings.ollama_embedding_model,
        settings.qdrant_embedding_version,
    )
    try:
        actual_ids = qdrant.document_point_ids(settings.qdrant_collection, document_id)
    except HTTPError as exc:
        if exc.code == 404:
            actual_ids = set()
        else:
            raise VectorIndexError("Qdrant reconciliation read failed (HTTP {}).".format(exc.code)) from exc
    return ReconciliationResult(
        chunks_without_indexed_records=unindexed_count,
        indexed_records_missing_from_qdrant=len(indexed_ids - actual_ids),
        untracked_qdrant_points=len(actual_ids - indexed_ids),
        older_index_rows=older_count,
    )
