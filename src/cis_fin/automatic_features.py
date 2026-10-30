"""Build transparent benchmark features from an already processed document."""
import re
from cis_fin.qualitative_scoring import score_qualitative_evidence


_STOPWORDS = {"what", "which", "does", "this", "that", "the", "and", "for", "with", "from", "how", "are", "its", "into", "their", "organization", "company", "evidence"}


def _terms(text):
    return {word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (text or "").lower()) if word not in _STOPWORDS}


def build_features_from_document(themes, records):
    """Return theme scores and provenance from exported document chunks.

    This is intentionally deterministic for the pilot: evidence relevance is
    based on term overlap, and the 0–5 score reflects breadth of supporting
    chunks. Human/LLM review can replace the score before a benchmark version
    is promoted.
    """
    output = {}
    for index, theme in enumerate(themes):
        query_terms = _terms("{} {}".format(theme.get("theme", ""), theme.get("question", "")))
        matches = []
        for record in records:
            text_terms = _terms(record.get("chunk_text", ""))
            overlap = len(query_terms & text_terms)
            if overlap >= 2:
                matches.append((overlap, record))
        matches.sort(key=lambda item: item[0], reverse=True)
        selected = [record for _, record in matches[:5]]
        # 0 = no evidence, then breadth up to 5. This is a pilot score, not a claim.
        score = min(5.0, float(len(selected)))
        output["theme_{}".format(index)] = {
            "score": score,
            "status": "automatic_pilot",
            "supporting_chunk_ids": [record.get("chunk_id") for record in selected],
            "evidence_previews": [record.get("chunk_text", "")[:300] for record in selected],
        }
    return output


def score_document_themes_with_llm(settings, themes, records, model=None, strict=False):
    """Score matched themes with local Ollama, falling back per theme on failure."""
    preliminary = build_features_from_document(themes, records)
    output = {}
    for index, theme in enumerate(themes):
        key = "theme_{}".format(index)
        item = preliminary[key]
        selected = [record for record in records if record.get("chunk_id") in item["supporting_chunk_ids"]]
        evidence = "\n\n".join(record.get("chunk_text", "")[:900] for record in selected[:3])
        page = selected[0].get("page_start", "unknown") if selected else "unknown"
        if not evidence:
            output[key] = item
            continue
        try:
            judged = score_qualitative_evidence(
                settings,
                theme.get("question", theme.get("theme", "")),
                evidence,
                page,
                theme.get("toe_dimension", "unknown"),
                model=model,
            )
            output[key] = dict(item, score=float(judged["score"]), status="ollama_reviewed", rationale=judged.get("rationale", ""), confidence=judged.get("confidence", 0.0), evidence_quote=judged.get("evidence_quote", ""))
        except Exception as exc:
            if strict:
                raise RuntimeError("Strict scoring failed for {}: {}".format(key, exc)) from exc
            output[key] = dict(item, status="automatic_pilot_fallback", llm_error=str(exc))
    return output
