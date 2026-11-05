"""Deterministic TOE relevance filtering for storage-efficient evidence extraction."""

import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, Sequence

TOE_TERMS = {
    "technology": ("artificial intelligence", "machine learning", "generative ai", "data platform", "cloud", "algorithm", "automation", "model"),
    "organization": ("training", "skill", "talent", "workforce", "leadership", "governance", "responsible ai", "research and development"),
    "environment": ("regulation", "regulatory", "compliance", "customer", "supplier", "partner", "ecosystem", "competition", "market"),
    "financial": ("revenue", "investment", "capital expenditure", "operating expense", "cloud spend", "infrastructure spend", "ai spend", "technology spend"),
}

@dataclass(frozen=True)
class RelevantEvidence:
    text: str
    page_start: int
    page_end: int
    dimensions: tuple
    relevance_score: float
    matched_terms: tuple

def classify_text(text: str, question: Optional[str] = None) -> tuple:
    haystack = (text + " " + (question or "")).lower()
    matches = {dimension: tuple(term for term in terms if term in haystack) for dimension, terms in TOE_TERMS.items()}
    matches = {key: value for key, value in matches.items() if value}
    return matches

def filter_evidence(records: Iterable, question: Optional[str] = None, threshold: float = 0.08) -> Iterator[RelevantEvidence]:
    """Keep only evidence with TOE/financial signals; preserve source page metadata."""
    for record in records:
        text = getattr(record, "text", None) or getattr(record, "chunk_text", None) or str(record)
        matches = classify_text(text, question)
        score = min(1.0, sum(len(items) for items in matches.values()) / 8.0)
        if matches and score >= threshold:
            yield RelevantEvidence(text, int(getattr(record, "page_start", 0)), int(getattr(record, "page_end", 0)), tuple(matches), score, tuple(sorted({item for values in matches.values() for item in values})))

