"""Runtime settings for StudyFlow, loaded from the environment."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent


def _load_dotenv() -> None:
    """Populate os.environ from qoder/.env without overriding real environment values."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    scheduler_path: Path
    host: str
    port: int
    default_workspace: str
    sample_data_path: Path

    @property
    def powercontext_url(self) -> str:
        # SQLAlchemy 的绝对路径写法：scheme + "//" + 以 / 开头的路径。
        return f"sqlite+aiosqlite:///{self.database_path}"


def build_settings() -> Settings:
    _load_dotenv()

    data_dir = Path(os.environ.get("STUDYFLOW_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(os.environ.get("STUDYFLOW_DB_PATH", data_dir / "powercontext.db")).expanduser()
    db_path = db_path if db_path.is_absolute() else (PROJECT_ROOT / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        data_dir=data_dir,
        database_path=db_path,
        scheduler_path=data_dir / "powercontext.scheduler.db",
        host=os.environ.get("STUDYFLOW_HOST", "127.0.0.1"),
        port=int(os.environ.get("STUDYFLOW_PORT", "8712")),
        default_workspace=os.environ.get("STUDYFLOW_WORKSPACE", "个人学习"),
        sample_data_path=Path(
            os.environ.get("STUDYFLOW_SAMPLE_DATA", PROJECT_ROOT / "sample-data.json")
        ).expanduser(),
    )


@dataclass
class WorkspaceRegistry:
    """Non-authoritative UI bookkeeping: which workspace names this machine opened, and the last one.

    The workspace→scope mapping itself is stored by PowerContext as a Scope external reference, so
    this file is only the picker state. Deleting it never loses memories.
    """

    path: Path
    names: list[str] = field(default_factory=list)
    last: str | None = None

    @classmethod
    def load(cls, data_dir: Path) -> "WorkspaceRegistry":
        path = data_dir / "workspaces.json"
        if not path.is_file():
            return cls(path=path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=path)
        names = payload.get("names") if isinstance(payload, dict) else None
        last = payload.get("last") if isinstance(payload, dict) else None
        return cls(
            path=path,
            names=[n for n in (names or []) if isinstance(n, str)],
            last=last if isinstance(last, str) and last else None,
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"schema": "studyflow.workspaces.v1", "names": self.names, "last": self.last},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def remember(self, name: str, *, last: bool = False) -> None:
        if name not in self.names:
            self.names.append(name)
        if last:
            self.last = name
        elif self.last is None:
            self.last = name
        self._save()
