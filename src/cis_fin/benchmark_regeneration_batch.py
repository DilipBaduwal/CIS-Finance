"""Checkpointed representative-evidence question regeneration."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from cis_fin.benchmark_regeneration import generate_distinct_question
from cis_fin.postgres import secure_postgres_dsn


def regenerate_batch(settings, source_path, output_path, checkpoint_path, batch_size=2):
    import psycopg
    data=json.loads(Path(source_path).read_text())
    state=json.loads(Path(checkpoint_path).read_text()) if Path(checkpoint_path).exists() else {"questions": {}}
    ids=[x.get("representative_chunk_id") for x in data.get("themes",[])]
    with psycopg.connect(secure_postgres_dsn(settings.database_url),connect_timeout=8) as c:
        with c.cursor() as q:
            q.execute("SELECT chunk_id::text,chunk_text FROM chunks WHERE chunk_id::text = ANY(%s)",(ids,))
            evidence=dict(q.fetchall())
    existing=[v["question"] for v in state["questions"].values()]
    for i,item in enumerate(data.get("themes",[])):
        if str(i) in state["questions"]: continue
        if i >= batch_size + len(state["questions"]): break
        try: question=generate_distinct_question(settings,item.get("toe_dimension",""),evidence.get(item.get("representative_chunk_id"),""),existing)
        except Exception: question=""
        state["questions"][str(i)]={"question":question,"question_status":"validated" if question and question.casefold() not in {x.casefold() for x in existing} else "needs_review"}
        if question: existing.append(question)
    Path(checkpoint_path).write_text(json.dumps(state,indent=2))
    for i,v in state["questions"].items(): data["themes"][int(i)].update(v)
    data["regeneration_complete"]=len(state["questions"])==len(data.get("themes",[]))
    Path(output_path).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    return len(state["questions"]),len(data.get("themes",[])),data["regeneration_complete"]
