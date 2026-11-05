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


def _parse_ollama_score(response_body):
    result = json.loads(response_body) if isinstance(response_body, (bytes, bytearray, str)) else response_body
    message = result.get("message") if isinstance(result, dict) else None
    content = message.get("content", "").strip() if isinstance(message, dict) else ""
    if not content:
        if result.get("done_reason") == "length":
            raise ValueError("Ollama reached its output token limit before returning a score. Disable thinking or increase num_predict.")
        raise ValueError("Ollama returned an empty scoring response; check that the configured model supports structured JSON output.")
    content = content.replace("```json", "").replace("```", "").strip()
    scored = json.loads(content)
    if not isinstance(scored, dict):
        raise ValueError("Ollama scoring response must be a JSON object.")
    return scored, result


def score_qualitative_evidence(settings, question, evidence, page, dimension, model=None):
    model = model or settings.ollama_model
    prompt = (
        "Score this annual-report evidence for AI readiness on a 0–5 scale. "
        "Use only the rubric and evidence; do not infer missing facts. Return JSON only with score, "
        "rationale, evidence_quote, confidence. TOE dimension: {}. Question: {}. Page: {}. "
        "Rubric: {} Evidence: {}"
    ).format(dimension, question[:400], page, json.dumps(RUBRIC), evidence[:1400])
    schema = {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 5}, "rationale": {"type": "string"}, "evidence_quote": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["score", "rationale", "evidence_quote", "confidence"]}
    body = {"model": model, "stream": False, "think": False, "format": schema, "options": {"temperature": 0, "num_predict": 512, "num_ctx": 2048}, "messages": [{"role": "user", "content": prompt}]}
    request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response:
                raw_response = response.read().decode("utf-8", errors="replace").strip()
                if not raw_response:
                    raise RuntimeError("Ollama returned an empty response. Check that Ollama is running and model '{}' is installed.".format(model))
                try:
                    result = json.loads(raw_response)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Ollama returned non-JSON output: {}".format(raw_response[:300])) from exc
            break
        except (TimeoutError, URLError, RuntimeError) as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError("Ollama scoring failed after 3 attempts: {}".format(exc)) from exc
            time.sleep(1)
    try:
        scored, result = _parse_ollama_score(result)
    except (json.JSONDecodeError, ValueError) as parse_error:
        # Retry malformed/truncated local-model output with a compact response.
        retry_body = dict(body)
        retry_body["options"] = {"temperature": 0, "num_predict": 768, "num_ctx": 2048}
        retry_body["messages"] = [{"role": "user", "content": prompt + " Return compact JSON with short strings only."}]
        last_result = result
        for _ in range(2):
            retry_request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(retry_body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(retry_request, timeout=180) as response:
                retry_result = json.loads(response.read())
            last_result = retry_result
            try:
                scored, result = _parse_ollama_score(retry_result)
                break
            except (json.JSONDecodeError, ValueError) as retry_error:
                parse_error = retry_error
                continue
        else:
            if not last_result.get("message", {}).get("content", "").strip() and last_result.get("done_reason") == "length":
                raise ValueError("Ollama reached its output token limit on every scoring attempt; the model did not return JSON.") from parse_error
            raise ValueError("Ollama returned invalid JSON on every scoring attempt; check the local model response.") from parse_error
    scored["score"] = int(float(scored["score"]))
    scored["confidence"] = max(0.0, min(1.0, float(scored["confidence"])))
    return scored
