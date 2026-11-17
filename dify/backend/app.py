from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import zlib
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .game import RuleError, canonical_receipt, fallback_narration, presentation, transition
from .memory import MemoryStore


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    player_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    request_id: str = Field(min_length=8, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal["寄存", "A", "B", "C", "回访", "档案", "重开"]
    message: str = Field(default="", max_length=240)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=20, max_length=80000)
    narration: str = Field(min_length=1, max_length=2000)


def create_app(data_dir: Path | None = None, token: str | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service_token = token if token is not None else os.getenv("BACKEND_TOKEN", "")
        if len(service_token) < 32 or service_token.startswith("REPLACE_"):
            raise RuntimeError("BACKEND_TOKEN must be a random value of at least 32 characters; run scripts/setup.py")
        app.state.token = service_token
        app.state.signing_key = hmac.new(service_token.encode(), b"lost-found/ticket/v1", hashlib.sha256).digest()
        async with MemoryStore.open(data_dir or Path(os.getenv("DATA_DIR", "data"))) as store:
            app.state.store = store
            yield

    app = FastAPI(title="离谱失物招领处 · PowerContext", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def require_auth(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + app.state.token):
            raise HTTPException(401, "服务口令错误；请检查 Dify 的 BACKEND_TOKEN。")

    def sign(data: dict) -> str:
        raw = base64.urlsafe_b64encode(zlib.compress(encode_json(data).encode(), 9)).decode().rstrip("=")
        sig = hmac.new(app.state.signing_key, raw.encode(), hashlib.sha256).hexdigest()
        return raw + "." + sig

    def verify(ticket: str) -> dict:
        try:
            raw, sig = ticket.rsplit(".", 1)
            expected = hmac.new(app.state.signing_key, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                raise ValueError("signature")
            data = json.loads(zlib.decompress(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))))
            if data["expires_at"] < time.time():
                raise HTTPException(410, "本次预备操作已过期，请重新运行。")
            return data
        except HTTPException:
            raise
        except (ValueError, TypeError, KeyError, zlib.error):
            raise HTTPException(400, "无效的操作票据。") from None

    @app.get("/healthz")
    async def health():
        return {"status": "ready", "powercontext": version("powercontext"), "app": "lost-found", "schema": 1}

    @app.post("/v1/prepare", dependencies=[Depends(require_auth)])
    async def prepare(body: PrepareRequest):
        player_hash = hashlib.sha256(body.player_key.encode()).hexdigest()
        request_hash = hashlib.sha256(encode_json(body.model_dump(exclude={"player_key"})).encode()).hexdigest()
        store = app.state.store
        async with store.lock:
            _, _, _, state, revision = await store.read(player_hash)
            previous = next((x for x in state["requests"] if x["id"] == body.request_id), None)
            if previous:
                if previous["hash"] != request_hash:
                    raise HTTPException(409, "同一个 request_id 不能用于不同的输入。")
                return {"ticket": sign({"player": player_hash, "request_id": body.request_id, "request_hash": request_hash,
                                        "revision": revision, "cached": True, "expires_at": time.time() + 900}),
                        "director_context": encode_json({"already_committed": True, "instruction": "这是一次安全重试，将返回已保存的回执。"}), "fallback_narration": "该操作已处理，正在返回原回执。",
                        "memory_before": revision, "cached": True}
            try:
                next_state, event, mutated = transition(state, body.action, body.message, player_hash)
            except RuleError as exc:
                raise HTTPException(422, str(exc)) from None
            context = presentation(next_state, event)
            payload = {"player": player_hash, "request_id": body.request_id, "request_hash": request_hash, "revision": revision,
                       "state": next_state, "event": event, "context": context, "mutated": mutated,
                       "expires_at": time.time() + 900, "cached": False}
            return {"ticket": sign(payload), "director_context": encode_json(context),
                    "fallback_narration": fallback_narration(context), "memory_before": revision, "cached": False}

    @app.post("/v1/commit", dependencies=[Depends(require_auth)])
    async def commit(body: CommitRequest):
        ticket = verify(body.ticket)
        store = app.state.store
        async with store.lock:
            scope, memory, entry, state, revision = await store.read(ticket["player"])
            previous = next((x for x in state["requests"] if x["id"] == ticket["request_id"]), None)
            if previous:
                if previous["hash"] != ticket["request_hash"]:
                    raise HTTPException(409, "请求身份冲突。")
                return {**previous["response"], "replayed": True}
            if ticket["cached"] or revision != ticket["revision"]:
                raise HTTPException(409, "存档已被另一次操作更新。请重新运行当前操作。")
            context = ticket["context"]
            narration = body.narration.strip()
            if not narration:
                raise HTTPException(422, "旁白不能为空。")
            response = {"answer": narration + "\n\n---\n\n" + canonical_receipt(context),
                        "memory_before": revision, "memory_after": revision, "scope_id": scope,
                        "replayed": False, "state_changed": ticket["mutated"], "powercontext": version("powercontext")}
            after = revision + 1 if ticket["mutated"] else revision
            response["answer"] += f"\n\n> PowerContext 记忆版本：{revision} → {after}；" + ("本次已保存。" if ticket["mutated"] else "本次只读回访。")
            if ticket["mutated"]:
                source_id = "turn-" + hashlib.sha256(ticket["request_id"].encode()).hexdigest()
                response.update(memory_after=revision + 1, source_id=source_id)
                next_state = ticket["state"]
                next_state["requests"] = (next_state["requests"] + [{"id": ticket["request_id"], "hash": ticket["request_hash"],
                    "response": response}])[-4:]
                event = {"request_id": ticket["request_id"], "player_scope": scope, "input": ticket["event"],
                         "narration": narration, "context": context, "expected_revision": revision}
                result, source = await store.write(scope, memory, entry, next_state, revision, source_id, event)
                if result.memory_ref.revision != response["memory_after"]:
                    raise RuntimeError("Unexpected PowerContext revision")
            return response

    @app.post("/v1/inspect", dependencies=[Depends(require_auth)])
    async def inspect(body: PrepareRequest):
        player_hash = hashlib.sha256(body.player_key.encode()).hexdigest()
        store = app.state.store
        async with store.lock:
            scope, _, entry, state, revision = await store.read(player_hash)
            recent_sources = []
            for request in state["requests"]:
                sid = request["response"].get("source_id")
                if sid:
                    record = await store.runtime.records.for_scope(scope).get_source("content", sid)
                    recent_sources.append(record.model_dump(mode="json"))
            return {"scope_id": scope, "revision": revision, "state": state,
                    "recent_sources": recent_sources,
                    "memory_citation": None if entry is None else entry.citation.model_dump(mode="json"),
                    "powercontext": version("powercontext")}

    return app


app = create_app()
