"""Immutable benchmark-version metadata for cohort question sets."""
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path


def _question_key(question):
    value = re.sub(r"[^a-z0-9 ]", "", (question or "").casefold()).strip()
    value = re.sub(r"\b(in this evidence|based on this evidence|from the evidence)\b", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _deduplicate_themes(themes):
    """Keep the first canonical question and drop near-duplicate variants."""
    kept = []
    keys = []
    for item in themes:
        question = item.get("question", "")
        key = _question_key(question)
        if not key:
            kept.append(item)
            continue
        words = set(key.split())
        if any(
            key == existing
            or SequenceMatcher(None, key, existing).ratio() >= 0.88
            or (words and set(existing.split()) and len(words & set(existing.split())) / max(len(words), len(existing.split())) >= 0.85)
            for existing in keys
        ):
            continue
        keys.append(key)
        kept.append(item)
    return kept


def create_benchmark_version(industry, fiscal_year, companies, themes, version_number, output_path):
    normalized_themes = [
        {
            "theme": item.get("theme", ""),
            "question": item.get("question", ""),
            "toe_dimension": item.get("toe_dimension", ""),
        }
        for item in _deduplicate_themes(themes)
    ]
    identity = {
        "industry": industry,
        "fiscal_year": fiscal_year,
        "companies": sorted(companies),
        "themes": normalized_themes,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    result = {
        "version": "{}-{}".format(industry.lower().replace(" ", "-"), version_number),
        "version_number": version_number,
        "benchmark_id": digest,
        "industry": industry,
        "fiscal_year": fiscal_year,
        "companies": sorted(companies),
        "themes": normalized_themes,
        "immutable": True,
        "status": "active",
    }
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def create_next_benchmark_version(previous_path, companies, output_path, themes=None):
    """Create an immutable next version without mutating the previous version.

    Existing themes/questions are carried forward by default.  ``themes`` should
    only contain reviewed recurring themes when the cohort discovery process has
    found a genuine addition or revision.
    """
    previous = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    next_themes = previous.get("themes", []) if themes is None else themes
    return create_benchmark_version(
        industry=previous["industry"],
        fiscal_year=previous["fiscal_year"],
        companies=companies,
        themes=next_themes,
        version_number=int(previous.get("version_number", 1)) + 1,
        output_path=output_path,
    )
