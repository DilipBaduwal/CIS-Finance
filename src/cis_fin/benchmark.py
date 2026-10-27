"""Cohort rules for explainable AI-readiness benchmarking."""

import re
from dataclasses import dataclass
from typing import Iterable, List


def normalize_fiscal_year(value: str) -> str:
    """Normalize fiscal-year spellings such as 25-26 and 2025-2026."""
    text = re.sub(r"\s+", "", (value or "").strip())
    match = re.fullmatch(r"(\d{2}|\d{4})[-/]((?:\d{2})|(?:\d{4}))", text)
    if not match:
        return text
    first, second = match.groups()
    first_year = int(first) if len(first) == 4 else 2000 + int(first)
    second_year = int(second) if len(second) == 4 else 2000 + int(second)
    if second_year != first_year + 1:
        return text
    return "{}-{}".format(first_year, second_year)


def normalize_industry(value: str) -> str:
    """Map common naming variants to a stable benchmark industry."""
    text = re.sub(r"\s+", " ", (value or "").strip().casefold())
    if any(term in text for term in ("information technology", "it consulting", "software")):
        return "Information Technology"
    if "telecommunication" in text:
        return "Telecommunications"
    if "steel" in text:
        return "Steel"
    if "retail" in text or "consumer services" in text:
        return "Consumer Services"
    return text.title()


@dataclass(frozen=True)
class BenchmarkCohort:
    industry: str
    fiscal_year: str
    companies: tuple


def build_cohorts(rows: Iterable[tuple]) -> List[BenchmarkCohort]:
    """Build same-industry, same-year cohorts from document rows.

    Rows are expected to contain symbol, company_name, industry, and fiscal_year.
    """
    grouped = {}
    for symbol, company_name, industry, fiscal_year in rows:
        key = (normalize_industry(industry), normalize_fiscal_year(fiscal_year))
        grouped.setdefault(key, set()).add((symbol, company_name))
    return [
        BenchmarkCohort(industry=industry, fiscal_year=year, companies=tuple(sorted(companies)))
        for (industry, year), companies in sorted(grouped.items())
        if industry and year and len(companies) >= 2
    ]
