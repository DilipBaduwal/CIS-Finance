"""Generate distinct benchmark questions from representative cluster evidence."""
import json
from urllib.request import Request, urlopen


def generate_distinct_question(settings, dimension, evidence, existing_questions):
    prompt = (
        "Create one distinct AI-readiness benchmark question from the evidence below. "
        "The question must belong to TOE dimension '{}', be answerable from annual reports, "
        "be company-agnostic, and describe the specific capability in the evidence. "
        "Do not repeat or paraphrase any existing question. Return JSON with only question. "
        "Existing questions: {} Evidence: {}"
    ).format(dimension, json.dumps(existing_questions), evidence[:5000])
    body = {"model": settings.ollama_model, "stream": False, "format": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}, "options": {"temperature": 0.2}, "messages": [{"role": "user", "content": prompt}]}
    request = Request(settings.ollama_base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=180) as response:
        result = json.loads(response.read())
    question = json.loads(result["message"]["content"])["question"]
    return " ".join(question.split()).rstrip(".!?") + "?"
