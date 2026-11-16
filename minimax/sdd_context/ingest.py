from __future__ import annotations

import ast
from pathlib import Path

from .common import digest, read_json


def documents(project: Path) -> list[dict]:
    """Parse first, then publish a full replacement in one SQL transaction."""
    result = []
    for path in sorted((project / "specs").glob("*.json")):
        data = read_json(path)
        for item in data["requirements"]:
            if item["status"] != "active":
                continue
            result.append(
                {
                    "source_id": item["id"],
                    "kind": "spec",
                    "path": path.relative_to(project).as_posix(),
                    "symbol": item["id"],
                    "start_line": 1,
                    "end_line": len(path.read_text().splitlines()),
                    "revision": data["revision"],
                    "file_sha256": digest(path.read_bytes()),
                    "content": item["text"],
                    "keywords": " ".join(item["keywords"]),
                }
            )
    for folder, kind in (("business", "code"), ("tests", "test")):
        for path in sorted((project / folder).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            relative = path.relative_to(project).as_posix()
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                result.append(
                    {
                        "source_id": f"{relative}::{node.name}",
                        "kind": kind,
                        "path": relative,
                        "symbol": node.name,
                        "start_line": start,
                        "end_line": node.end_lineno,
                        "revision": "sha256",
                        "file_sha256": digest(path.read_bytes()),
                        "content": "\n".join(text.splitlines()[start - 1 : node.end_lineno]),
                        "keywords": " ".join([relative, node.name, ast.get_docstring(node) or ""]),
                    }
                )
    ids = [doc["source_id"] for doc in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source identity")
    if not all(any(d["kind"] == kind for d in result) for kind in ("spec", "code", "test")):
        raise ValueError("Project must contain active specifications, code and tests")
    return result


def ingest(conn, project: Path) -> dict:
    docs = documents(project)
    fields = tuple(docs[0])
    sql = f"INSERT INTO sdd_documents ({','.join(fields)}) VALUES ({','.join(['%s'] * len(fields))})"
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM sdd_documents")
            cursor.executemany(sql, [tuple(d[f] for f in fields) for d in docs])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "operation": "replace-index",
        "documents": len(docs),
        "sources": [{k: d[k] for k in ("source_id", "file_sha256", "revision")} for d in docs],
    }
