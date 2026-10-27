"""Checkpointed extraction of qualitative features for regression training."""
import json
from pathlib import Path
from cis_fin.qualitative_scoring import score_qualitative_evidence


def _fallback_feature(evidence, company, theme_index):
    text = evidence.casefold()
    implementation = sum(text.count(term) for term in ("implemented", "deployed", "operational", "platform", "governance", "training", "capability"))
    aspiration = sum(text.count(term) for term in ("aim", "plan", "will", "target", "intend", "explore"))
    score = max(0, min(5, 1 + implementation - min(implementation, aspiration)))
    return {"company": company, "theme_index": theme_index, "score": score, "rationale": "Deterministic fallback based on disclosed implementation language; human review recommended.", "evidence_quote": evidence[:500], "confidence": 0.25, "status": "deterministic_fallback"}


def extract_qualitative_features(settings, source_path, output_path, checkpoint_path, evidence_by_chunk, batch_size=1):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    checkpoint = Path(checkpoint_path)
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"features": {}}
    jobs = []
    for theme_index, theme in enumerate(data.get("themes", [])):
        for company in theme.get("supporting_companies", []):
            key = "{}:{}".format(theme_index, company)
            if key not in state["features"] or state["features"][key].get("status") == "needs_review":
                jobs.append((key, theme_index, theme, company))
    for key, theme_index, theme, company in jobs[:batch_size]:
        evidence = next((evidence_by_chunk.get(chunk_id) for chunk_id in theme.get("chunk_ids", []) if evidence_by_chunk.get(chunk_id)), "")
        try:
            feature = score_qualitative_evidence(settings, theme.get("question", ""), evidence, 0, theme.get("toe_dimension", ""))
            feature["company"] = company
            feature["theme_index"] = theme_index
            state["features"][key] = feature
        except Exception as exc:
            state["features"][key] = _fallback_feature(evidence, company, theme_index)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps(state, indent=2), encoding="utf-8")
    result = dict(data)
    result["regression_features"] = state["features"]
    result["feature_extraction_complete"] = (
        len(state["features"]) == sum(len(t.get("supporting_companies", [])) for t in data.get("themes", []))
        and not any(value.get("status") == "needs_review" for value in state["features"].values())
    )
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"processed": len(state["features"]), "total": sum(len(t.get("supporting_companies", [])) for t in data.get("themes", [])), "complete": result["feature_extraction_complete"]}
