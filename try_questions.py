"""Try the question prompt on extracted chunks. Needs only local Ollama: no database, Qdrant or S3.

Usage (from the project folder, venv active):
  PYTHONPATH=src python try_questions.py artifacts/ril-test3.chunks.jsonl 3
"""
import json
import os
import re
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from cis_fin.questions import OllamaQuestionGenerator, QuestionGenerationError

# Words that should NOT appear in a company-agnostic question. Edit for your report.
COMPANY_TERMS = ["reliance", "jio", "ril", "rjil", "ambani", "o2c", "jiomart"]

path = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

generator = OllamaQuestionGenerator(base_url, model, timeout_seconds=300)
rows = [json.loads(line) for line in open(path, encoding="utf-8")][:count]
print("Model:", model, "| chunks:", len(rows), "\n")

flagged = total = 0
for row in rows:
    print("=== chunk", row["chunk_index"], "pages", row["page_start"], "-", row["page_end"])
    try:
        candidates = generator.generate(row["text"])
    except QuestionGenerationError as exc:
        print("  FAILED:", exc, "\n")
        continue
    if not candidates:
        print("  (no questions: model abstained)\n")
    for c in candidates:
        total += 1
        hit = [t for t in COMPANY_TERMS if re.search(r"\b" + re.escape(t) + r"\b", c.question.lower())]
        flagged += bool(hit)
        print("  Q:", c.question)
        print("     dimension:", c.toe_dimension, "| ai_specific:", c.ai_specific,
              "| quote_in_source:", c.quote_matches_source)
        if hit:
            print("     !! names company terms:", hit)
        print("     quote:", c.evidence_quote[:160].replace("\n", " "))
    print()
print("Summary: {} questions, {} name the company.".format(total, flagged))
