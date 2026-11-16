"""Generate distinct benchmark questions from representative cluster evidence."""
import json
import re
from urllib.request import Request, urlopen


def _question_key(question):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", question.casefold()).split())


def generate_distinct_question(settings, dimension, evidence, existing_questions):
    existing_keys = {_question_key(question) for question in existing_questions if question}
    rejected_questions = []
    for attempt in range(3):
        prompt = (
            "Create one distinct AI-readiness benchmark question from the evidence below. "
            "The question must belong to TOE dimension '{}', be answerable from annual reports, "
            "be company-agnostic, and describe the specific capability in the evidence. "
            "Do not repeat or paraphrase any existing question. Return JSON with only question. "
            "Existing questions: {}. Rejected duplicate candidates: {}. Evidence: {}"
        ).format(
            dimension,
            json.dumps(existing_questions),
            json.dumps(rejected_questions),
            evidence[:5000],
        )
        body = {
            "model": settings.ollama_model,
            "stream": False,
            # Qwen3.5 supports long chain-of-thought output by default; benchmark
            # questions need only a short direct answer, so disable thinking.
            "think": False,
            "format": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            "options": {"temperature": 0.2 + 0.2 * attempt, "num_predict": 96},
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
        question = json.loads(result["message"]["content"])["question"]
        question = " ".join(question.split()).rstrip(".!?") + "?"
        if _question_key(question) not in existing_keys:
            return question
        rejected_questions.append(question)

    raise ValueError("Ollama returned a duplicate benchmark question after 3 attempts.")
