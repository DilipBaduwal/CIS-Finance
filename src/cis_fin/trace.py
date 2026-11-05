"""Immutable, timestamped Markdown score traces."""

from datetime import datetime, timezone
from pathlib import Path
import re

def write_score_trace(directory: Path, trace: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = trace.get("timestamp") or datetime.now(timezone.utc).isoformat()
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", timestamp)
    path = directory / (safe + "_score_trace.md")
    if path.exists():
        raise FileExistsError(str(path))
    fields = ["company", "industry", "fiscal_year", "question", "dimension", "model", "prompt_version", "score", "confidence", "rationale", "evidence_quote", "chunk_ids", "page_numbers", "source_checksum", "timestamp", "application_version", "fallback_used", "errors"]
    body = ["# AI-readiness score trace", "", "| Field | Value |", "|---|---|"]
    for field in fields:
        value = str(trace.get(field, "")).replace("|", "\\|").replace("\n", "<br>")
        body.append("| {} | {} |".format(field, value))
    path.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    return path

