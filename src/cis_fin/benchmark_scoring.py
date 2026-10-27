"""Explainable AI-readiness metric extraction and benchmark scoring."""

import json
from dataclasses import dataclass
from statistics import median
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ExtractedMetric:
    value: float
    unit: str
    direction: str
    evidence_quote: str
    page: int
    confidence: float


def extract_metric(settings, question, evidence, page, model="qwen2.5:3b"):
    prompt = (
        "Extract only a directly disclosed, comparable metric from this annual-report evidence for the question. "
        "If no numeric or clearly gradable evidence exists, return found=false. Do not infer or invent values. "
        "Return JSON with found, value, unit, direction (higher_is_better or lower_is_better), evidence_quote, confidence. "
        "Question: {} Evidence page {}: {}"
    ).format(question, page, evidence[:6000])
    schema = {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "value": {"type": "number"},
            "unit": {"type": "string"},
            "direction": {"type": "string", "enum": ["higher_is_better", "lower_is_better"]},
            "evidence_quote": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["found", "value", "unit", "direction", "evidence_quote", "confidence"],
    }
    body = {"model": model, "stream": False, "format": schema, "options": {"temperature": 0, "num_predict": 128}, "messages": [{"role": "user", "content": prompt}]}
    request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=300) as response:
        result = json.loads(response.read())
    item = json.loads(result["message"]["content"])
    if not item["found"]:
        return None
    if not 0 <= item["confidence"] <= 1:
        raise ValueError("Metric confidence must be between 0 and 1")
    return ExtractedMetric(float(item["value"]), item["unit"], item["direction"], item["evidence_quote"], page, float(item["confidence"]))


def benchmark_score(value, peer_values, direction="higher_is_better"):
    """Return a transparent 0–100 percentile score against peer values."""
    if value is None or not peer_values:
        return None
    peers = sorted(peer_values)
    if direction == "lower_is_better":
        rank = sum(peer >= value for peer in peers)
    else:
        rank = sum(peer <= value for peer in peers)
    return round(100 * rank / len(peers), 1)


def benchmark_summary(values):
    """Return median and average for a comparable metric cohort."""
    if not values:
        return {"median": None, "average": None}
    return {"median": median(values), "average": sum(values) / len(values)}
