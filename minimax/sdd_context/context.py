from __future__ import annotations

import re
from pathlib import Path

from .common import digest, project_fingerprint
from .state import load


async def prepare(
    conn, version: str, project: Path, runtime: Path, task: str, task_id: str, budget: int = 40000
) -> dict:
    terms = sorted(set(re.findall(r"[\w]+", task.lower())))[:32]
    if not terms:
        raise ValueError("Task needs searchable terms")
    clauses = " OR ".join(["LOWER(CONCAT(keywords, ' ', content)) LIKE %s ESCAPE '='"] * len(terms))
    sql = f"SELECT * FROM sdd_documents WHERE ({clauses}) ORDER BY kind, source_id"
    params = ["%" + t.replace("=", "==").replace("%", "=%").replace("_", "=_") + "%" for t in terms]
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall())
    # Check every selected source against its exact on-disk file before showing it to the assistant.
    for row in rows:
        path = (project / row["path"]).resolve()
        if not path.is_relative_to(project.resolve()):
            raise ValueError("Source path escapes the project")
        if not path.is_file() or digest(path.read_bytes()) != row["file_sha256"]:
            raise ValueError(f"Source changed or disappeared; re-import required: {row['path']}")
    rows.sort(
        key=lambda d: (-sum(t in (d["keywords"] + " " + d["content"]).lower() for t in terms), d["source_id"])
    )
    selected = []
    used = 0
    for kind in ("spec", "code", "test"):
        candidates = [r for r in rows if r["kind"] == kind]
        if not candidates:
            raise ValueError(f"No relevant {kind} sources; broaden task or re-import")
        for row in candidates:
            cost = len(row["content"]) + len(row["source_id"]) + 300
            if used + cost > budget:
                raise ValueError("Budget cannot hold all matching evidence; narrow the task or increase it")
            used += cost
            selected.append(row)
    state = await load(runtime, task_id)
    fingerprint = project_fingerprint(project)
    if state:
        state["validation_current"] = state["content"]["source_fingerprint"] == fingerprint
        if not state["validation_current"]:
            state["next_action"] = "Sources changed; rerun target and regression tests"
    return {
        "task_id": task_id,
        "task": task,
        "source_fingerprint": fingerprint,
        "database": {
            "version": version,
            "sql": sql,
            "parameters": params,
            "returned_source_ids": [r["source_id"] for r in rows],
        },
        "sources": selected,
        "resumed_state": state,
        "budget": {"characters_used_with_metadata_reserve": used, "maximum": budget},
    }
