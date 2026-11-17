"""All durable gameplay data lives in official PowerContext, not a custom DB."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from powercontext.builtin.runtime import (
    BuiltinConfig, CaptureSource, MemoryEntryInput, RememberMemoryRequest, open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

from .game import fresh_state


class MemoryStore:
    def __init__(self, runtime):
        self.runtime = runtime
        # One process, one lock: bounded memory use and serialized state transitions.
        # Durable expected_revision additionally protects the PowerContext write.
        self.lock = asyncio.Lock()

    @classmethod
    @asynccontextmanager
    async def open(cls, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        config = BuiltinConfig.model_validate({
            "database": {"kind": "sqlite", "url": "sqlite+aiosqlite:///" + str((data_dir / 'powercontext.db').resolve())},
            "runtime": {"dream_enabled": False},
        })
        async with open_builtin_runtime(config, scheduler_path=data_dir / "scheduler.db") as runtime:
            yield cls(runtime)

    async def read(self, player_hash: str):
        scope = await self.runtime.scopes.create(ScopeDraft(
            title="离谱失物招领处 / " + player_hash[:12], summary="虚构失物游戏的玩家独立存档", idempotency_key="lost-found:" + player_hash,
        ))
        app = self.runtime.memory.for_scope(scope.scope_id)
        page = await app.list()
        states = [x for x in page.entries if x.entry.kind == "game-state"]
        if len(states) > 1:
            raise RuntimeError("Multiple game-state entries: refusing ambiguous memory")
        entry = states[0] if states else None
        state = fresh_state() if entry is None else json.loads(entry.entry.text)
        revision = 0 if page.memory_ref is None else page.memory_ref.revision
        return scope.scope_id, app, entry, state, revision

    async def write(self, scope_id: str, app, entry, state: dict, revision: int, source_id: str, event: dict):
        source_content = json.dumps(event, ensure_ascii=False, sort_keys=True)
        metadata = {"application": "lost-found", "schema": 1}
        source_receipt = await self.runtime.sources.for_scope(scope_id).capture(CaptureSource(
            source_id=source_id, content=source_content, metadata=metadata,
        ))
        result = await app.remember(RememberMemoryRequest(expected_revision=revision or None, entries=(MemoryEntryInput(
            entry=None if entry is None else entry.entry,
            kind="game-state", text=json.dumps(state, ensure_ascii=False, sort_keys=True),
            reason="玩家完成一次真实游戏操作，保存可跨会话复用的状态与原始输入",
        ),)))
        return result, source_receipt
