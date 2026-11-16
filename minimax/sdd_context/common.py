from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = "Implement list_orders min_total filter; keep status, order, inputs unchanged"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def project_fingerprint(project: Path) -> str:
    entries = []
    for name in ("business", "specs", "tests"):
        for path in sorted((project / name).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json"}:
                entries.append((path.relative_to(project).as_posix(), digest(path.read_bytes())))
    return digest(json.dumps(entries, ensure_ascii=False).encode())
