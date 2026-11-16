from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field
from powercontext.artifacts import Artifact, ArtifactDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES


class TaskState(BaseModel):
    task: str
    done: list[str] = Field(default_factory=list)
    todo: list[str] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    source_fingerprint: str = ""


class TaskArtifact(Artifact[TaskState]):
    family: ClassVar[str] = "sdd-task"


class TaskDraft(ArtifactDraft[TaskState]):
    family: ClassVar[str] = "sdd-task"


@asynccontextmanager
async def repository(runtime: Path):
    runtime.mkdir(parents=True, exist_ok=True)
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{(runtime / 'powercontext.db').resolve()}")
    async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
        yield profile, ArtifactRepository((TaskArtifact,))


async def load(runtime: Path, task_id: str) -> dict | None:
    async with repository(runtime) as (profile, repo):
        async with profile.database.transaction() as connection:
            try:
                record = await repo.latest(connection, "sdd-demo", "sdd-task", task_id)
            except RepositoryNotFoundError:
                return None
            return {
                "engine": "PowerContext ArtifactRepository/SQLiteProfile",
                "revision": record.revision,
                "artifact_id": record.artifact_id,
                "content": record.content.model_dump(mode="json"),
            }


async def save(runtime: Path, task_id: str, state: TaskState) -> dict:
    async with repository(runtime) as (profile, repo):
        async with profile.database.transaction() as connection:
            draft = TaskDraft(content=state)
            try:
                previous = await repo.latest(connection, "sdd-demo", "sdd-task", task_id)
            except RepositoryNotFoundError:
                record = await repo.create(connection, "sdd-demo", task_id, draft)
            else:
                record = await repo.revise(connection, "sdd-demo", previous, draft)
            return {
                "engine": "PowerContext ArtifactRepository/SQLiteProfile",
                "revision": record.revision,
                "artifact_id": record.artifact_id,
                "content": record.content.model_dump(mode="json"),
            }
