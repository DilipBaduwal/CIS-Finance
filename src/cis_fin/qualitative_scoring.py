"""Explainable 0–5 qualitative AI-readiness scoring."""
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


RUBRIC = {
    0: "No evidence or explicit absence of capability.",
    1: "Very limited or aspirational mention without implementation evidence.",
    2: "Early activity or isolated initiative with limited detail.",
    3: "Established capability or repeated activity with some evidence.",
    4: "Strong, scaled capability with outcomes, governance, or measurable progress.",
    5: "Leading, integrated capability with clear outcomes, scale, and continuous improvement.",
}


def score_qualitative_evidence(settings, question, evidence, page, dimension, model=None):
    model = model or settings.ollama_model
    prompt = (
        "Score this annual-report evidence for AI readiness on a 0–5 scale. "
        "Use only the rubric and evidence; do not infer missing facts. Return JSON only with score, "
        "rationale, evidence_quote, confidence. TOE dimension: {}. Question: {}. Page: {}. "
        "Rubric: {} Evidence: {}"
    ).format(dimension, question[:400], page, json.dumps(RUBRIC), evidence[:1400])
    schema = {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 5}, "rationale": {"type": "string"}, "evidence_quote": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["score", "rationale", "evidence_quote", "confidence"]}
    body = {"model": model, "stream": False, "format": schema, "options": {"temperature": 0, "num_predict": 256, "num_ctx": 2048}, "messages": [{"role": "user", "content": prompt}]}
    request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response:
                result = json.loads(response.read())
            break
        except (TimeoutError, URLError) as exc:
            last_error = exc
            if attempt == 2:
                raise TimeoutError("Ollama did not finish after 3 attempts") from exc
            time.sleep(1)
    content = result["message"]["content"].strip()
    if "```" in content:
        content = content.replace("```json", "").replace("```", "").strip()
    try:
        scored = json.loads(content)
    except json.JSONDecodeError as parse_error:
        # Retry malformed/truncated local-model output with a compact response.
        retry_body = dict(body)
        retry_body["options"] = {"temperature": 0, "num_predict": 512, "num_ctx": 2048}
        retry_body["messages"] = [{"role": "user", "content": prompt + " Return compact JSON with short strings only."}]
        for _ in range(2):
            retry_request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(retry_body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(retry_request, timeout=180) as response:
                retry_result = json.loads(response.read())
            retry_content = retry_result["message"]["content"].strip().replace("```json", "").replace("```", "").strip()
            try:
                scored = json.loads(retry_content)
                break
            except json.JSONDecodeError:
                continue
        else:
            raise parse_error
    scored["score"] = int(float(scored["score"]))
    scored["confidence"] = max(0.0, min(1.0, float(scored["confidence"])))
    return scored
