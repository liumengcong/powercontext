"""Thin adapter over the PowerContext builtin runtime (installed version 1.5.1).

Only the public Python API of the installed package is used:

    open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=...)))
    runtime.scopes.list()/create()/get()
    runtime.atomic_memory.for_scope(scope_id).create()/list()/get()/search()/state()/forget()
    runtime.capabilities()

Deliberately *not* used: `revise()`/`change()` on an Artifact body. powercontext[builtin]==1.5.1
does not expose a public content-revision entry point for the ``atomic-memory`` family, so StudyFlow
records task state as append-only ``task_status`` event memories instead of pretending a body
revision was updated. See docs/README "状态模型".
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope.models import ScopeDescriptor, ScopeDraft, ScopeExternalReference

SCOPE_REFERENCE_KIND = "studyflow-workspace"
MEMORY_FAMILY = "atomic-memory"

MAX_TEXT_BYTES = 8192
MAX_DESCRIPTION_CHARS = 200
MAX_KIND_CHARS = 128

ACTIVE_STATES = ("active",)
ALL_STATES = ("active", "forgotten", "merged", "retired")


class StoreError(RuntimeError):
    """Base class for storage failures surfaced to the API layer."""


class StoreUnavailableError(StoreError):
    """PowerContext runtime is not open; callers must not report success."""


class VersionConflictError(StoreError):
    """A caller supplied a stale expected revision / state version."""


class InvalidMemoryInput(StoreError):
    """The request cannot be represented by the atomic-memory model."""


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


@dataclass(frozen=True)
class MemoryRecord:
    """One atomic-memory artifact as read back from PowerContext."""

    artifact_id: str
    revision: int
    family: str
    kind: str
    text: str
    description: str | None
    state: str
    state_version: int
    source_ids: tuple[str, ...]
    content_schema: str

    def citation(self, quote_chars: int = 120) -> dict[str, Any]:
        quote = self.text.strip().replace("\n", " ")
        if len(quote) > quote_chars:
            quote = quote[:quote_chars] + "…"
        return {
            "artifact_id": self.artifact_id,
            "revision": self.revision,
            "family": self.family,
            "kind": self.kind,
            "description": self.description,
            "quote": quote,
            "state": self.state,
            "state_version": self.state_version,
        }

    def to_api(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "revision": self.revision,
            "family": self.family,
            "kind": self.kind,
            "text": self.text,
            "description": self.description,
            "state": self.state,
            "state_version": self.state_version,
            "source_ids": list(self.source_ids),
            "content_schema": self.content_schema,
            "citation": self.citation(),
        }


def _to_record(record: Any) -> MemoryRecord:
    artifact = record.artifact
    content = artifact.content
    state = record.state
    return MemoryRecord(
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        family=artifact.family,
        kind=content.kind,
        text=content.text,
        description=content.description,
        state=str(getattr(state.state, "value", state.state)),
        state_version=int(state.state_version),
        source_ids=tuple(ref.source_id for ref in artifact.lineage.sources),
        content_schema=str(content.model_dump()["schema_"]),
    )


def _page_cursor(page: Any) -> str | None:
    return getattr(page, "next_cursor", None)


@dataclass
class PowerContextStore:
    """Owns one in-process builtin runtime bound to a SQLite file."""

    database_url: str
    scheduler_path: Path
    _runtime: Any = None
    _owner_task: asyncio.Task | None = None
    _release: asyncio.Event | None = None
    _lock: asyncio.Lock = None  # type: ignore[assignment]
    _scope_cache: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()
        self._scope_cache = {}

    @property
    def connected(self) -> bool:
        return self._runtime is not None

    async def start(self) -> None:
        async with self._lock:
            if self._runtime is not None:
                return
            self._release = asyncio.Event()
            ready = asyncio.Event()
            startup_error: BaseException | None = None
            task = asyncio.create_task(self._own_runtime(ready))
            await ready.wait()
            if not task.done():
                self._owner_task = task
                return
            try:
                await task
            except BaseException as error:  # noqa: BLE001 - reported as StoreError below
                startup_error = error
            self._owner_task = None
            if startup_error is not None:
                raise StoreError(f"PowerContext runtime 启动失败：{_describe(startup_error)}") from startup_error

    async def _own_runtime(self, ready: asyncio.Event) -> None:
        """Own the runtime context manager inside one dedicated task.

        ``open_builtin_runtime`` binds context-local observers, so entering and exiting it must
        happen in the same task. Request handlers only read/write through the runtime object;
        ``stop()`` signals this task instead of unwinding the stack from a request.
        """
        config = BuiltinConfig(
            database=SQLiteConfig(url=self.database_url),
            runtime=RuntimeConfig(
                dream_enabled=False,
                profile_schedule_enabled=False,
                memory_schedule_seconds=None,
                experience_schedule_seconds=None,
                topic_memory_schedule_seconds=None,
            ),
        )
        async with AsyncExitStack() as stack:
            try:
                self._runtime = await stack.enter_async_context(
                    open_builtin_runtime(config, scheduler_path=self.scheduler_path, dispatch_processing=False)
                )
            finally:
                ready.set()
            await self._release.wait()

    async def stop(self) -> None:
        async with self._lock:
            task, self._owner_task = self._owner_task, None
            self._runtime = None
            self._scope_cache.clear()
            if task is not None:
                if self._release is not None:
                    self._release.set()
                try:
                    await asyncio.wait_for(task, timeout=30)
                except asyncio.TimeoutError:  # pragma: no cover - defensive
                    task.cancel()
                except BaseException:  # noqa: BLE001 - shutdown must not raise through the API
                    pass

    def _require(self) -> Any:
        if self._runtime is None:
            raise StoreUnavailableError("PowerContext runtime 未连接，无法读写记忆")
        return self._runtime

    # ------------------------------------------------------------------ workspaces

    async def list_workspaces(self) -> list[dict[str, Any]]:
        runtime = self._require()
        out: list[dict[str, Any]] = []
        for descriptor in await runtime.scopes.list():
            name = next((ref.value for ref in descriptor.external_references if ref.kind == SCOPE_REFERENCE_KIND), None)
            if name is None:
                continue
            out.append({"workspace": name, "scope_id": descriptor.scope_id, "scope_version": descriptor.version})
        out.sort(key=lambda item: item["workspace"])
        return out

    async def ensure_scope(self, workspace: str) -> str:
        """Resolve (or register) the PowerContext scope that isolates one workspace name."""
        runtime = self._require()
        name = (workspace or "").strip()
        if not name:
            raise InvalidMemoryInput("工作区名称不能为空")
        if len(name) > 100:
            raise InvalidMemoryInput("工作区名称过长（最多 100 字符）")
        cached = self._scope_cache.get(name)
        if cached:
            return cached
        for descriptor in await runtime.scopes.list():
            for ref in descriptor.external_references:
                if ref.kind == SCOPE_REFERENCE_KIND and ref.value == name:
                    self._scope_cache[name] = descriptor.scope_id
                    return descriptor.scope_id
        created: ScopeDescriptor = await runtime.scopes.create(
            ScopeDraft(
                title=f"研习台工作区 {name}",
                summary="StudyFlow 本地工作区，仅用于隔离记忆数据，不代表用户认证。",
                external_references=(ScopeExternalReference(kind=SCOPE_REFERENCE_KIND, value=name),),
                idempotency_key=f"{SCOPE_REFERENCE_KIND}:{name}",
            )
        )
        self._scope_cache[name] = created.scope_id
        return created.scope_id

    # ------------------------------------------------------------------ memories

    async def create(self, scope_id: str, entries: list[dict[str, str]]) -> list[MemoryRecord]:
        runtime = self._require()
        drafts: list[AtomicMemoryContent] = []
        for entry in entries:
            kind = (entry.get("kind") or "").strip()
            text = (entry.get("text") or "").strip()
            description = entry.get("description")
            if not kind or not text:
                raise InvalidMemoryInput("kind 与 text 均为必填")
            if len(kind) > MAX_KIND_CHARS:
                raise InvalidMemoryInput(f"kind 最长 {MAX_KIND_CHARS} 字符")
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise InvalidMemoryInput(f"text 最长 {MAX_TEXT_BYTES} UTF-8 字节")
            if description is not None:
                description = str(description).strip().replace("\n", " ").replace("\r", " ")
                description = description or None
                if description and len(description) > MAX_DESCRIPTION_CHARS:
                    raise InvalidMemoryInput(f"description 最长 {MAX_DESCRIPTION_CHARS} 字符")
            drafts.append(AtomicMemoryContent(kind=kind, text=text, description=description))
        if not drafts:
            return []
        memory = runtime.atomic_memory.for_scope(scope_id)
        result = await memory.create(drafts)
        return [_to_record(record) for record in result.records]

    async def list(
        self,
        scope_id: str,
        *,
        kind: str | None = None,
        states: tuple[str, ...] = ACTIVE_STATES,
        limit: int = 200,
    ) -> list[MemoryRecord]:
        runtime = self._require()
        memory = runtime.atomic_memory.for_scope(scope_id)
        collected: list[MemoryRecord] = []
        cursor: str | None = None
        while len(collected) < limit:
            page = await memory.list(states=states, kind=kind, limit=min(50, limit - len(collected)), cursor=cursor)
            collected.extend(_to_record(item) for item in page.items)
            cursor = _page_cursor(page)
            if not cursor:
                break
        return collected[:limit]

    async def list_all(
        self,
        scope_id: str,
        *,
        kind: str | None = None,
        states: tuple[str, ...] = ACTIVE_STATES,
        max_pages: int = 1000,
    ) -> list[MemoryRecord]:
        """Follow the PowerContext cursor until the scope is exhausted.

        Task state is append-only, so truncating this read would resurrect an old status once more
        than ``limit`` events exist. Callers that show a list to a human may still page; core state
        derivation must not.
        """
        runtime = self._require()
        memory = runtime.atomic_memory.for_scope(scope_id)
        collected: list[MemoryRecord] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page = await memory.list(states=states, kind=kind, limit=50, cursor=cursor)
            collected.extend(_to_record(item) for item in page.items)
            cursor = _page_cursor(page)
            if not cursor:
                return collected
        raise StoreError(f"记忆分页在 {max_pages} 页后仍未读完，请检查 scope {scope_id}")

    async def get(self, scope_id: str, artifact_id: str, *, revision: int | None = None) -> MemoryRecord:
        runtime = self._require()
        memory = runtime.atomic_memory.for_scope(scope_id)
        try:
            record = await memory.get(artifact_id, revision=revision)
        except LookupError as error:
            raise StoreError(f"记忆不存在：{artifact_id}") from error
        return _to_record(record)

    async def state(self, scope_id: str, artifact_id: str) -> dict[str, Any]:
        runtime = self._require()
        memory = runtime.atomic_memory.for_scope(scope_id)
        read = await memory.state(artifact_id)
        return {
            "artifact_id": read.ref.artifact_id,
            "revision": read.ref.revision,
            "state": str(getattr(read.state, "value", read.state)),
            "state_version": int(read.state_version),
        }

    async def forget(self, scope_id: str, artifact_id: str, *, expected_revision: int, expected_state_version: int) -> MemoryRecord:
        runtime = self._require()
        memory = runtime.atomic_memory.for_scope(scope_id)
        try:
            result = await memory.forget(
                artifact_id,
                expected_revision=expected_revision,
                expected_state_version=expected_state_version,
            )
        except Exception as error:  # noqa: BLE001 - translate CAS failures into a typed conflict
            if _is_conflict(error):
                raise VersionConflictError(
                    f"该记忆已被其它操作更新（期望 revision={expected_revision}，state_version={expected_state_version}），"
                    "已停止写入以免覆盖。请刷新后重试。"
                ) from error
            raise
        return _to_record(result.primary)

    async def search(
        self,
        scope_id: str,
        query: str,
        *,
        kind: str | None = None,
        limit: int = 20,
        mode: str = "text",
    ) -> dict[str, Any]:
        """Real PowerContext retrieval. Returns [] and an explicit note when nothing matches."""
        runtime = self._require()
        normalised = (query or "").strip()
        if not normalised:
            raise InvalidMemoryInput("检索词不能为空")
        memory = runtime.atomic_memory.for_scope(scope_id)
        page = await memory.search(normalised, mode=mode, limit=limit, kind=kind)
        hits: list[dict[str, Any]] = []
        for item in page.hits:
            index_hit = item.hit
            ref = index_hit.artifact_ref
            record = await self.get(scope_id, ref.artifact_id, revision=ref.revision)
            hits.append(
                {
                    "record": record.to_api(),
                    "matched_by": list(item.matched_by),
                    "score": float(index_hit.score) if index_hit.score is not None else None,
                    "channel_scores": {
                        name: float(score.raw) if getattr(score, "raw", None) is not None else None
                        for name, score in (index_hit.channel_scores or {}).items()
                    },
                }
            )
        return {
            "query": normalised,
            "mode": str(page.mode),
            "requested_mode": mode,
            "hits": hits,
            "diagnostics": {
                "embedding_calls": int(page.embedding_calls),
                "embedding_fallback": bool(page.embedding_fallback),
                "generation_calls": int(page.generation_calls),
                "rerank": str(page.rerank),
                "recoverable": bool(page.recoverable),
            },
        }

    # ------------------------------------------------------------------ status

    async def describe(self) -> dict[str, Any]:
        runtime = self._require()
        capabilities = await runtime.capabilities()
        payload = capabilities.model_dump(mode="json")
        return {
            "connected": True,
            "package": "powercontext[builtin]",
            "installed_version": installed_version(),
            "database": {"kind": "sqlite", "url": self.database_url},
            "runtime_mode": "builtin-in-process",
            "memory_search_modes": payload.get("memory_search_modes", []),
            "model_generation_enabled": bool(payload.get("memory_extraction")),
            "prompt_statuses": {
                name: str(status.get("status")) for name, status in (payload.get("prompts") or {}).items()
            },
            "artifact_dreaming": bool(payload.get("artifact_dreaming")),
        }


_CONFLICT_CODES = {"atomic_memory_changed", "preview_stale", "revision_conflict", "invalid_memory_state"}


def _is_conflict(error: BaseException) -> bool:
    from powercontext.errors import PowerContextError, RevisionConflictError

    if isinstance(error, RevisionConflictError):
        return True
    code = str(getattr(error, "code", ""))
    if code in _CONFLICT_CODES:
        return True
    if isinstance(error, PowerContextError):
        text = f"{type(error).__name__}: {error}".lower()
        return "stale" in text or "conflict" in text
    return False


def installed_version() -> str:
    try:
        from importlib.metadata import version

        return version("powercontext")
    except Exception:  # noqa: BLE001 - version reporting must never break the status endpoint
        return "unknown"


class ValidationLog:
    """Append-only local evidence trail of real PowerContext operations (no credentials)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def append(self, operation: str, **fields: Any) -> None:
        entry = {"recorded_at": _now_iso(), "operation": operation}
        entry.update({key: value for key, value in fields.items() if value is not None})
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                rows.append({"operation": "unparsed_line", "raw": line[:200]})
        return rows[-limit:][::-1]
