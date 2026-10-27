"""Reliable batched retrieval of benchmark vectors from Qdrant."""

import json
import socket
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class BenchmarkVectorError(Exception):
    """Benchmark vectors could not be loaded safely."""


def load_vectors_by_point_ids(
    settings,
    point_ids,
    batch_size=16,
    retries=3,
    timeout_seconds=30,
    progress_callback=None,
):
    """Retrieve vectors in small retryable batches and return point-id mapping."""
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise BenchmarkVectorError("Qdrant URL and API key are required for benchmark vectors.")
    if not 1 <= batch_size <= 64:
        raise BenchmarkVectorError("Benchmark vector batch size must be between 1 and 64.")
    if not 1 <= retries <= 5:
        raise BenchmarkVectorError("Benchmark vector retries must be between 1 and 5.")
    unique_ids = list(dict.fromkeys(point_ids))
    endpoint = settings.qdrant_url.rstrip("/") + "/collections/{}/points".format(settings.qdrant_collection)
    vectors = {}
    total_batches = (len(unique_ids) + batch_size - 1) // batch_size
    for batch_number, start in enumerate(range(0, len(unique_ids), batch_size), 1):
        ids = unique_ids[start : start + batch_size]
        last_error = None
        for attempt in range(1, retries + 1):
            request = Request(
                endpoint,
                data=json.dumps({"ids": ids, "with_vector": True, "with_payload": False}).encode(),
                headers={"Content-Type": "application/json", "api-key": settings.qdrant_api_key},
                method="POST",
            )
            try:
                with urlopen(request, timeout=timeout_seconds) as response:
                    payload = json.loads(response.read())
                points = payload.get("result", [])
                for point in points:
                    if point.get("id") is not None and point.get("vector") is not None:
                        vectors[str(point["id"])] = point["vector"]
                last_error = None
                break
            except (HTTPError, URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
                last_error = exc
                if attempt < retries:
                    sleep(min(2 ** (attempt - 1), 4))
        if last_error is not None:
            raise BenchmarkVectorError(
                "Qdrant benchmark batch {}/{} failed after {} attempts ({}).".format(
                    batch_number, total_batches, retries, type(last_error).__name__
                )
            ) from last_error
        if progress_callback:
            progress_callback(batch_number, total_batches, len(vectors))
    missing = [point_id for point_id in unique_ids if str(point_id) not in vectors]
    if missing:
        raise BenchmarkVectorError("Qdrant returned no vectors for {} requested points.".format(len(missing)))
    return vectors
