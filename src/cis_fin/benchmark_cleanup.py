"""Resumable batch cleanup for discovered benchmark questions."""
import json
from pathlib import Path
from cis_fin.benchmark_questions import normalize_question


def clean_benchmark_questions(settings, source_path, output_path, checkpoint_path, batch_size=2):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    checkpoint = Path(checkpoint_path)
    saved = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"themes": {}}
    completed = saved.get("themes", {})
    themes = data.get("themes", [])
    for index in [i for i in range(len(themes)) if str(i) not in completed][:batch_size]:
        item = themes[index]
        try:
            question, valid = normalize_question(settings, item.get("question", ""), item.get("theme", ""), item.get("toe_dimension", ""))
            completed[str(index)] = {"question": question, "question_status": "validated" if valid else "needs_review"}
        except Exception as exc:
            completed[str(index)] = {"question": item.get("question", ""), "question_status": "needs_review", "error": type(exc).__name__}
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({"themes": completed}, indent=2), encoding="utf-8")
    for index, result in completed.items():
        themes[int(index)].update(result)
    cleaned = dict(data)
    cleaned["themes"] = themes
    cleaned["validated_theme_count"] = sum(item.get("question_status") == "validated" for item in themes)
    cleaned["cleanup_complete"] = len(completed) == len(themes)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"processed": len(completed), "total": len(themes), "complete": cleaned["cleanup_complete"]}
