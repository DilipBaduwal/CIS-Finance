"""Comparable, confidence-aware financial commitment calculations."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import mean
from typing import Iterable

@dataclass(frozen=True)
class FinancialRecord:
    company: str
    fiscal_year: str
    revenue: Decimal
    ai_spend: Decimal
    currency: str = "INR"
    spend_type: str = "ai_direct"
    source_type: str = "management_disclosure"
    confidence: str = "medium"

    @property
    def ratio(self) -> Decimal:
        if self.revenue <= 0:
            raise ValueError("revenue must be positive")
        return (self.ai_spend / self.revenue * Decimal("100")).quantize(Decimal("0.01"))

def normalize_number(value) -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid financial number") from exc
    if number < 0:
        raise ValueError("financial values cannot be negative")
    return number

def company_summary(records: Iterable[FinancialRecord]) -> dict:
    grouped = {}
    for record in records:
        grouped.setdefault(record.company, []).append(record)
    result = {}
    for company, items in grouped.items():
        ratios = [item.ratio for item in items]
        ordered = sorted(items, key=lambda item: item.fiscal_year)
        result[company] = {"years": len(items), "annual_ratios": {item.fiscal_year: str(item.ratio) for item in ordered}, "five_year_average": str(Decimal(str(mean(ratios))).quantize(Decimal("0.01"))), "trend": str((ordered[-1].ratio - ordered[0].ratio).quantize(Decimal("0.01"))) if len(ordered) > 1 else "0.00", "confidence": min((item.confidence for item in items), key={"high": 2, "medium": 1, "low": 0}.get)}
    return result

