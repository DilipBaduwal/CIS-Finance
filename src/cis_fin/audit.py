"""Independent audit dataset creation and agreement metrics."""

from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Optional

@dataclass(frozen=True)
class AuditRow:
    text: str
    expected_score: int
    given_score: int
    company: str = ""
    question: str = ""
    dimension: str = ""
    source_document: str = ""
    page_number: Optional[int] = None
    chunk_id: str = ""
    auditor: str = ""
    comments: str = ""

def validate_score(value: int) -> int:
    value = int(value)
    if not 0 <= value <= 5:
        raise ValueError("scores must be integers between 0 and 5")
    return value

def audit_metrics(rows: Iterable[AuditRow]) -> dict:
    rows = list(rows)
    if not rows:
        return {"count": 0, "mae": None, "exact_agreement": 0.0, "within_one": 0.0}
    errors = [abs(validate_score(row.expected_score) - validate_score(row.given_score)) for row in rows]
    return {"count": len(rows), "mae": round(mean(errors), 4), "exact_agreement": round(sum(error == 0 for error in errors) / len(errors), 4), "within_one": round(sum(error <= 1 for error in errors) / len(errors), 4)}

