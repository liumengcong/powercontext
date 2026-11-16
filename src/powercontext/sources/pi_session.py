from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceMaterialization,
    TextEvidence,
)
from powercontext.sources.connectors import (
    ConnectorBinding,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import SourceDefinition


class PiSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _visible_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "toolCall":
            name = block.get("name", "")
            call_id = block.get("id", "")
            arguments = block.get("arguments", {})
            try:
                encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
            parts.append(f"[tool_call {name} {call_id}] {encoded}")
    return "\n".join(parts)


def _render_record(record: dict[str, Any], session_id: str) -> PiEventInput | None:
    event_id = record["id"]
    parent_id = record.get("parentId")
    event_type = record.get("type", "")
    role = ""
    body = ""
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role", "")
        if not isinstance(role, str) or not role:
            return None
        if role == "bashExecution":
            command = message.get("command", record.get("command", ""))
            output = message.get("output", record.get("output", ""))
            body = f"$ {command}" + (f"\n{output}" if output else "")
        elif role == "toolResult":
            tool_name = message.get("toolName", "")
            call_id = message.get("toolCallId", "")
            text = _visible_text(message.get("content"))
            body = f"[tool_result {tool_name} {call_id}] {text}" if text else ""
        else:
            body = _visible_text(message.get("content"))
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_text(record.get("content"))
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _parse_records(content: bytes) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    try:
        text = content.decode("utf-8-sig", errors="strict")
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Pi session must be valid UTF-8 JSONL") from error
    if not lines or not isinstance(lines[0], dict):
        raise ValueError("Pi session header is missing")
    header = lines[0]
    session_id = header.get("id")
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session must use version 3")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session id must be non-empty")
    nodes: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for record in lines[1:]:
        if not isinstance(record, dict):
            raise ValueError("Pi session records must be objects")
        event_id = record.get("id")
        parent_id = record.get("parentId")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in nodes:
            raise ValueError("Pi event ids must be unique non-empty strings")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError("Pi event parent must refer to an earlier event")
        nodes[event_id] = record
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digests[event_id] = hashlib.sha256(canonical).hexdigest()
    return session_id, list(nodes.values()), digests


def _events_from_records(
    session_id: str, records: list[dict[str, Any]], leaf_id: str | None
) -> tuple[PiEventInput, ...]:
    if not records:
        if leaf_id is not None:
            raise ValueError("Pi leaf id does not exist")
        return ()
    by_id = {record["id"]: record for record in records}
    leaf = records[-1]["id"] if leaf_id is None else leaf_id
    if leaf not in by_id:
        raise ValueError("Pi leaf id does not exist")
    path: list[dict[str, Any]] = []
    current: str | None = leaf
    while current is not None:
        record = by_id[current]
        path.append(record)
        current = record.get("parentId")
    path.reverse()
    visible = tuple(event for record in path if (event := _render_record(record, session_id)) is not None)
    return visible


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    session_id, records, _ = _parse_records(value.content)
    return _events_from_records(session_id, records, value.leaf_id)


def _event_source_id(session_id: str, event_id: str) -> str:
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    event_hash = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return f"pi:{session_hash}:{event_hash}"


class PiEventSourceAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = _event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
            materialization=SourceMaterialization.CAPTURED,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PiEventSourceAdapter(), version="1", projections=(PiEventTextEvidenceProjection(),)
)


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self.path.read_bytes()
        session_id, records, digests = _parse_records(raw)
        events = _events_from_records(session_id, records, self.leaf_id)
        previous = session.checkpoint
        imported: dict[str, str] = {}
        if previous is not None:
            if not isinstance(previous, dict) or previous.get("session_id") != session_id:
                raise ValueError("Pi connector checkpoint belongs to another session")
            old = previous.get("events")
            if not isinstance(old, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in old.items()):
                raise ValueError("Pi connector checkpoint is invalid")
            imported = dict(old)
            for event_id, digest in imported.items():
                if digests.get(event_id) != digest:
                    raise ValueError("an imported Pi event was changed or removed")
        updated = dict(imported)
        for event in events:
            digest = digests[event.event_id]
            old_digest = imported.get(event.event_id)
            if old_digest is not None:
                if old_digest != digest:
                    raise ValueError("an imported Pi event was changed")
                continue
            await session.submit(_event_source_id(session_id, event.event_id), "pi-event", event)
            updated[event.event_id] = digest
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"session_id": session_id, "events": updated},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
