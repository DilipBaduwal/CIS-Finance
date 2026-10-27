"""Semantic theme discovery primitives for industry AI-readiness benchmarks.

The caller supplies the already-computed chunk embeddings. This keeps discovery
model-agnostic and lets us reuse the Qdrant/Ollama embedding representation.
"""

from dataclasses import dataclass
from math import sqrt
from typing import Dict, Iterable, List, Sequence


TOE_DIMENSIONS = ("technology", "organization", "environment")


@dataclass(frozen=True)
class BenchmarkChunk:
    chunk_id: str
    company_symbol: str
    page_start: int
    page_end: int
    text: str
    toe_dimension: str
    embedding: Sequence[float]


@dataclass(frozen=True)
class ThemeCluster:
    cluster_id: str
    toe_dimension: str
    chunk_ids: tuple
    company_symbols: tuple
    representative_chunk_id: str
    support_count: int
    question: str = ""


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def discover_theme_clusters(
    chunks: Iterable[BenchmarkChunk],
    similarity_threshold: float = 0.78,
    maximum_per_dimension: int = 10,
) -> List[ThemeCluster]:
    """Group similar chunks and keep the strongest recurring themes.

    A cluster is supported by the number of distinct companies, not raw chunk
    count, so one very long report cannot dominate the benchmark.
    """
    if not 0.0 < similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be between 0 and 1")
    if maximum_per_dimension < 1:
        raise ValueError("maximum_per_dimension must be positive")
    clusters: Dict[str, List[BenchmarkChunk]] = {}
    for chunk in chunks:
        if chunk.toe_dimension not in TOE_DIMENSIONS:
            continue
        matching = None
        best_similarity = 0.0
        for cluster_id, members in clusters.items():
            if members[0].toe_dimension != chunk.toe_dimension:
                continue
            similarity = cosine_similarity(chunk.embedding, members[0].embedding)
            if similarity >= similarity_threshold and similarity > best_similarity:
                matching = cluster_id
                best_similarity = similarity
        if matching is None:
            matching = "theme-{}".format(len(clusters) + 1)
            clusters[matching] = []
        clusters[matching].append(chunk)

    discovered = []
    for cluster_id, members in clusters.items():
        companies = tuple(sorted({member.company_symbol for member in members}))
        representative = max(
            members,
            key=lambda member: sum(cosine_similarity(member.embedding, other.embedding) for other in members),
        )
        discovered.append(
            ThemeCluster(
                cluster_id=cluster_id,
                toe_dimension=members[0].toe_dimension,
                chunk_ids=tuple(member.chunk_id for member in members),
                company_symbols=companies,
                representative_chunk_id=representative.chunk_id,
                support_count=len(companies),
            )
        )
    selected = []
    for dimension in TOE_DIMENSIONS:
        dimension_clusters = [cluster for cluster in discovered if cluster.toe_dimension == dimension]
        selected.extend(
            sorted(dimension_clusters, key=lambda cluster: (cluster.support_count, len(cluster.chunk_ids)), reverse=True)[
                :maximum_per_dimension
            ]
        )
    return selected
