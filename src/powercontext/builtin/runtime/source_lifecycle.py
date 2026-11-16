# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""Persisted source dependency graphs for Memory and Topic Memory eligibility."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """Invalid lifecycle identity, graph, or event."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: StrictStr
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be positive")  # noqa: TRY003
        return value


class SourceLifecycleApplication:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)
        database = getattr(runtime._provider, "database", None)
        if database is None:
            raise SourceLifecycleError("source lifecycle requires a relational Runtime")
        self._database = database

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("source revision must be a positive integer")
        await self._runtime.records.for_scope(self.scope_id).get_source(source_ref.source_type, source_ref.source_id)
        await self._register(node_id, "source", source_ref, (), "all", revision, True)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        await self._register(node_id, "memory", citation, parents, policy, None, None)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
            "topic-memory", artifact_ref.artifact_id, artifact_ref.revision
        )
        await self._register(node_id, "topic", artifact_ref, parents, policy, None, None)

    async def _register(
        self,
        node_id: str,
        kind: str,
        reference: SourceRef | MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
        source_revision: int | None,
        initial: bool | None,
    ) -> None:
        _identifier(node_id)
        if policy not in {"all", "any"} or (kind != "source" and not parents):
            raise SourceLifecycleError("invalid dependency policy or empty parents")
        if len(set(parents)) != len(parents):
            raise SourceLifecycleError("duplicate parents")
        for parent in parents:
            _identifier(parent)
        async with self._database.transaction() as connection:
            table = SOURCE_LIFECYCLE_NODES_TABLE
            rows = (await connection.execute(select(table).where(table.c.scope_id == self.scope_id))).mappings().all()
            existing = {str(row["node_id"]): row for row in rows}
            serialized_ref = _json(reference.model_dump(mode="json"))
            existing_node = existing.get(node_id)
            if existing_node is not None:
                same = (
                    existing_node["kind"] == kind
                    and existing_node["reference"] == serialized_ref
                    and existing_node["parents"] == _json(parents)
                    and existing_node["policy"] == policy
                    and existing_node["source_revision"] == source_revision
                )
                if same:
                    return
                raise SourceLifecycleError("node registration conflicts with an existing binding")
            if any(parent not in existing for parent in parents):
                raise SourceLifecycleError("parent must be registered before its child")
            parent_states = {key: bool(row["available"]) for key, row in existing.items()}
            available = bool(initial) if kind == "source" else _supported(parents, policy, parent_states)
            await connection.execute(
                insert(table).values(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind=kind,
                    reference=serialized_ref,
                    parents=_json(parents),
                    policy=policy,
                    available=available,
                    source_revision=source_revision,
                    last_sequence=0,
                    last_event=None,
                )
            )
        if kind == "memory" and not available:
            await self._set_memory_state(reference, False, "registered without active support")

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        payload = event.model_dump_json()
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            source = (
                await connection.execute(
                    select(table).where(table.c.scope_id == self.scope_id, table.c.node_id == event.node_id)
                )
            ).mappings().one_or_none()
            if source is None or source["kind"] != "source":
                raise SourceLifecycleError("event source is not registered")
            if int(source["source_revision"]) != event.revision:
                raise SourceLifecycleError("source revision does not match")
            last_sequence = int(source["last_sequence"])
            if event.sequence < last_sequence:
                raise SourceLifecycleError("event sequence must increase")
            if event.sequence == last_sequence:
                if source["last_event"] != payload:
                    raise SourceLifecycleError("event sequence was reused with different content")
            else:
                await connection.execute(
                    update(table)
                    .where(table.c.scope_id == self.scope_id, table.c.node_id == event.node_id)
                    .values(
                        available=event.available,
                        last_sequence=event.sequence,
                        last_event=payload,
                    )
                )
        await self._propagate()
        return await self.active_ids()

    async def _propagate(self) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            rows = (await connection.execute(select(table).where(table.c.scope_id == self.scope_id))).mappings().all()
        pending = {str(row["node_id"]): row for row in rows}
        states: dict[str, bool] = {}
        while pending:
            ready = [
                row
                for row in pending.values()
                if all(parent in states for parent in json.loads(row["parents"]))
            ]
            if not ready:
                raise SourceLifecycleError("dependency graph contains a cycle or missing parent")
            for row in sorted(ready, key=lambda item: str(item["node_id"])):
                node_id = str(row["node_id"])
                parents = tuple(json.loads(row["parents"]))
                desired = bool(row["available"]) if row["kind"] == "source" else _supported(
                    parents, str(row["policy"]), states
                )
                states[node_id] = desired
                if desired != bool(row["available"]):
                    if row["kind"] == "memory":
                        reference = MemoryCitation.model_validate_json(row["reference"])
                        await self._set_memory_state(reference, desired, "source lifecycle propagation")
                    async with self._database.transaction() as connection:
                        await connection.execute(
                            update(table)
                            .where(table.c.scope_id == self.scope_id, table.c.node_id == node_id)
                            .values(available=desired)
                        )
                pending.pop(node_id)

    async def _set_memory_state(self, citation: MemoryCitation, available: bool, reason: str) -> None:
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            service = context.artifacts.memory
            current = await service.head(citation.memory_ref.artifact_id)
            if current is None:
                raise SourceLifecycleError("registered Memory no longer exists")
            manifest = {item.entry_id: item for item in current.content.manifest.entries}
            item = manifest.get(citation.entry_id)
            if item is None:
                raise SourceLifecycleError("registered Memory entry no longer exists")
            if (item.state == "active") == available:
                return
            entry = next(
                (entry for entry in await service.entries(current) if entry.entry_id == citation.entry_id), None
            )
            if entry is None:
                raise SourceLifecycleError("registered Memory entry has no current version")
            if available:
                await service.reactivate(current, entries=(entry,), reason=reason)
            else:
                await service.forget(current, entries=(entry,), reason=reason)

    async def resolve(
        self,
        node_id: str,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        row = await self._get_node(node_id)
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown reference purpose")
        if purpose == "generation" and not bool(row["available"]):
            raise SourceLifecycleError("node is unavailable for generation")
        return _decode_reference(str(row["kind"]), str(row["reference"]))

    async def active_ids(self) -> list[str]:
        async with self._database.transaction() as connection:
            rows = await connection.scalars(
                select(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                       SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(True))
                .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
            )
            return list(rows)

    async def inactive_topic_artifact_ids(self) -> frozenset[str]:
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE.c.reference).where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                        SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                    )
                )
            ).scalars()
            return frozenset(str(json.loads(value)["artifact_id"]) for value in rows)

    async def _get_node(self, node_id: str):
        _identifier(node_id)
        async with self._database.transaction() as connection:
            row = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                    )
                )
            ).mappings().one_or_none()
        if row is None:
            raise SourceLifecycleError("unknown node")
        return row


def _supported(parents: tuple[str, ...], policy: str, states: dict[str, bool]) -> bool:
    values = [states[parent] for parent in parents]
    return all(values) if policy == "all" else any(values)


def _decode_reference(kind: str, value: str) -> SourceRef | MemoryCitation | ArtifactRef:
    model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[kind]
    return model.model_validate_json(value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identifier(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SourceLifecycleError("identifier must be non-empty and trimmed")


__all__ = ["SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
