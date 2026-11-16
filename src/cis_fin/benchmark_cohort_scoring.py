"""Resumable Ollama scoring for one fixed benchmark theme set across a cohort."""

import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from cis_fin.automatic_features import build_features_from_document
from cis_fin.benchmark import normalize_fiscal_year, normalize_industry
from cis_fin.company_queries import CompanyLookupError, export_document_chunks, list_recent_documents
from cis_fin.postgres import secure_postgres_dsn


_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme_index": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 5},
                    "rationale": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["theme_index", "score", "rationale", "evidence_quote", "confidence"],
            },
        }
    },
    "required": ["scores"],
}


def _score_batch(settings, items):
    compact = [
        {
            "theme_index": item["theme_index"],
            "toe_dimension": item["toe_dimension"],
            "question": item["question"],
            "evidence": item["evidence"],
        }
        for item in items
    ]
    prompt = (
        "Score each annual-report evidence item independently for AI readiness from 0 to 5. "
        "Use only its evidence and its question; do not infer missing facts. "
        "0 means no evidence or explicit absence; 1 limited/aspirational; 2 early or isolated; "
        "3 established/repeated; 4 strong/scaled with outcomes or governance; "
        "5 integrated, leading capability with clear outcomes and continuous improvement. "
        "Return one result for every theme_index. Keep each rationale and evidence_quote concise. "
        "Items: {}"
    ).format(json.dumps(compact, ensure_ascii=False))
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "format": _SCORE_SCHEMA,
        "options": {"temperature": 0, "num_predict": 768, "num_ctx": 16384},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        settings.ollama_base_url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=300) as response:
                result = json.loads(response.read())
            content = result.get("message", {}).get("content", "")
            parsed = json.loads(content)
            received = {int(item["theme_index"]): item for item in parsed["scores"]}
            expected = {item["theme_index"] for item in items}
            if set(received) != expected:
                raise ValueError("Ollama returned an incomplete or unexpected theme-index set.")
            for index, scored in received.items():
                score = int(scored["score"])
                confidence = float(scored["confidence"])
                if not 0 <= score <= 5 or not 0.0 <= confidence <= 1.0:
                    raise ValueError("Ollama returned a score or confidence outside the allowed range.")
                scored["score"] = score
                scored["confidence"] = confidence
            return received
        except (TimeoutError, URLError, OSError, ValueError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError("Ollama theme-score batch failed after 3 attempts ({})".format(type(last_error).__name__)) from last_error


def score_company_themes(settings, themes, records, batch_size=5, progress_callback=None):
    """Score only these themes against one report; return scores and provenance."""
    if not 1 <= batch_size <= 10:
        raise ValueError("batch_size must be between 1 and 10")
    preliminary = build_features_from_document(themes, records)
    pending = []
    output = {}
    for index, theme in enumerate(themes):
        key = "theme_{}".format(index)
        base = dict(preliminary[key])
        selected_ids = set(base.get("supporting_chunk_ids", []))
        selected = [record for record in records if record.get("chunk_id") in selected_ids]
        if not selected:
            output[key] = dict(base, score=None, status="no_matching_evidence")
            continue
        evidence = "\n\n".join(
            "Pages {}–{}: {}".format(
                record.get("page_start", "?"),
                record.get("page_end", "?"),
                record.get("chunk_text", "")[:900],
            )
            for record in selected[:3]
        )
        pending.append(
            {
                "theme_index": index,
                "key": key,
                "base": base,
                "toe_dimension": theme.get("toe_dimension", "unknown"),
                "question": theme.get("question", theme.get("theme", "")),
                "evidence": evidence,
            }
        )

    batches = [pending[start : start + batch_size] for start in range(0, len(pending), batch_size)]
    for batch_number, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback(batch_number, len(batches), batch[0]["theme_index"] + 1, len(themes))
        scored = _score_batch(settings, batch)
        for item in batch:
            judge = scored[item["theme_index"]]
            output[item["key"]] = dict(
                item["base"],
                score=float(judge["score"]),
                status="ollama_reviewed",
                rationale=judge["rationale"],
                confidence=judge["confidence"],
                evidence_quote=judge["evidence_quote"],
            )
    return output


def _artifact_fingerprint(data):
    identity = {
        "industry": data.get("industry"),
        "fiscal_year": data.get("fiscal_year"),
        "companies": sorted(data.get("companies", [])),
        "themes": [
            (theme.get("question", ""), theme.get("toe_dimension", ""))
            for theme in data.get("themes", [])
        ],
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def score_healthcare_cohort(settings, source_path, matrix_path, checkpoint_path, batch_size=5):
    """Score only benchmark questions for each listed completed cohort report.

    Scores are checkpointed per company and per five-theme Ollama request. The
    generated regression matrix stores 0–5 `theme_N` values consumed by the
    client benchmark page.
    """
    source_path = Path(source_path)
    matrix_path = Path(matrix_path)
    checkpoint_path = Path(checkpoint_path)
    data = json.loads(source_path.read_text(encoding="utf-8"))
    themes = data.get("themes", [])
    companies = sorted({str(symbol).strip().upper() for symbol in data.get("companies", []) if symbol})
    if not themes or not companies:
        raise ValueError("Benchmark artifact must contain themes and companies before scoring.")

    fingerprint = _artifact_fingerprint(data)
    state = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists()
        else {"fingerprint": fingerprint, "companies": {}}
    )
    if state.get("fingerprint") != fingerprint:
        state = {"fingerprint": fingerprint, "companies": {}}
    try:
        documents = list_recent_documents(settings, limit=100)
    except CompanyLookupError:
        raise
    normalized_year = normalize_fiscal_year(data.get("fiscal_year", ""))
    company_documents = {}
    for row in documents:
        symbol = str(row[2] or "").strip().upper()
        if (
            symbol in companies
            and normalize_industry(str(row[3] or "")) == normalize_industry(data.get("industry", ""))
            and normalize_fiscal_year(str(row[4] or "")) == normalized_year
            and str(row[5] or "").casefold() == "completed"
        ):
            company_documents.setdefault(symbol, row)

    matrix = {
        "industry": data.get("industry", "Healthcare"),
        "fiscal_year": data.get("fiscal_year", "2025-2026"),
        "feature_names": ["theme_{}".format(index) for index in range(len(themes))],
        "rows": [],
        "excluded": [],
        "training_ready": False,
        "training_note": "Theme scores are evidence-based pilot values; review them before decision use.",
        "scoring_model": settings.ollama_model,
        "source_benchmark": str(source_path),
    }
    for symbol in companies:
        cached = state["companies"].get(symbol)
        if cached and cached.get("status") == "completed":
            row_data = cached
        else:
            document = company_documents.get(symbol)
            if document is None:
                row_data = {"status": "no_completed_report", "features": {}, "provenance": {}}
                matrix["excluded"].append({"company": symbol, "reason": "no_completed_report_in_cohort"})
            else:
                print("Loading {} report chunks for themed scoring...".format(symbol), flush=True)
                records = export_document_chunks(settings, uuid.UUID(str(document[0])))
                print("Scoring {}: {} chunks against {} benchmark questions only.".format(symbol, len(records), len(themes)), flush=True)
                scores = score_company_themes(
                    settings,
                    themes,
                    records,
                    batch_size=batch_size,
                    progress_callback=lambda batch, total_batches, question, total_questions, company=symbol: print(
                        "{}: score batch {}/{} (theme {} of {})".format(
                            company, batch, total_batches, question, total_questions
                        ),
                        flush=True,
                    ),
                )
                features = {key: item.get("score") for key, item in scores.items()}
                provenance = {key: item for key, item in scores.items()}
                row_data = {
                    "status": "completed",
                    "features": features,
                    "provenance": provenance,
                    "document_id": str(document[0]),
                    "document_title": str(document[1] or ""),
                }
            state["companies"][symbol] = row_data
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        feature_values = row_data.get("features", {})
        feature_vector = [feature_values.get(name) for name in matrix["feature_names"]]
        matrix["rows"].append(
            {
                "company": symbol,
                "features": feature_values,
                "feature_vector": feature_vector,
                "provenance": row_data.get("provenance", {}),
                "document_id": row_data.get("document_id"),
                "document_title": row_data.get("document_title"),
            }
        )
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
        finished = sum(1 for value in state["companies"].values() if value.get("status") == "completed")
        print("Cohort company progress: {}/{} scored.".format(finished, len(companies)), flush=True)

    return matrix
