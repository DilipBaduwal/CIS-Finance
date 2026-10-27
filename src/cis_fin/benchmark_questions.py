"""Validation and normalization for comparable industry benchmark questions."""

import json
from urllib.request import Request, urlopen


def normalize_question(settings, question, evidence, dimension):
    prompt = (
        "Rewrite this question as a company-agnostic AI-readiness benchmark question. "
        "Keep the TOE dimension and meaning. Do not mention any company, ticker, brand, person, "
        "product, country, or company-specific figure. Use 'a company' or 'organizations'. "
        "Return JSON only with keys question and valid. valid must be true only if the result is "
        "answerable from annual-report evidence and comparable across companies. TOE dimension: {}. "
        "Question: {} Evidence: {}"
    ).format(dimension, question, evidence[:3000])
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "valid": {"type": "boolean"}},
            "required": ["question", "valid"],
        },
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        settings.ollama_base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        result = json.loads(response.read())
    parsed = json.loads(result["message"]["content"])
    normalized = " ".join(parsed["question"].split()).rstrip(".!?") + "?"
    return normalized, bool(parsed["valid"])
