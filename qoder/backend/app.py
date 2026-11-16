"""StudyFlow FastAPI application: local single-user tool on top of PowerContext builtin runtime."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend import domain
from backend.config import PROJECT_ROOT, Settings, WorkspaceRegistry, build_settings
from backend.store import (
    ACTIVE_STATES,
    ALL_STATES,
    InvalidMemoryInput,
    PowerContextStore,
    StoreError,
    StoreUnavailableError,
    ValidationLog,
    VersionConflictError,
    installed_version,
)

FRONTEND_DIR = PROJECT_ROOT / "frontend"

ALLOWED_KINDS = ("note", "preference", "weakness", "progress", "task")
DISCONNECTED_MESSAGE = "PowerContext 未连接：记忆无法读取或写入，界面不会显示成功结果。"
CONTROL_CHARS = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")


class MemoryCreate(BaseModel):
    kind: str
    text: str
    description: str | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if cleaned not in ALLOWED_KINDS:
            raise ValueError(f"kind 必须是 {', '.join(ALLOWED_KINDS)} 之一")
        return cleaned

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("text 不能为空")
        if CONTROL_CHARS.search(cleaned):
            raise ValueError("text 含控制字符，已拒绝")
        if len(cleaned.encode("utf-8")) > 8192:
            raise ValueError("text 超过 8192 UTF-8 字节")
        return cleaned

    @field_validator("description")
    @classmethod
    def _description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().replace("\n", " ").replace("\r", " ")
        if len(cleaned) > 200:
            raise ValueError("description 最长 200 字符")
        return cleaned or None


class TaskCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("待办内容不能为空")
        if CONTROL_CHARS.search(cleaned):
            raise ValueError("待办内容含控制字符，已拒绝")
        return cleaned


class StatusUpdate(BaseModel):
    status: str
    expected_seq: int = Field(ge=0)
    expected_task_revision: int | None = Field(default=None, ge=1)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned not in {"todo", "done"}:
            raise ValueError("status 只能是 todo 或 done")
        return cleaned


class WorkspaceCreate(BaseModel):
    workspace: str = Field(min_length=1, max_length=100)

    @field_validator("workspace")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("工作区名称不能为空")
        if CONTROL_CHARS.search(cleaned):
            raise ValueError("工作区名称含控制字符，已拒绝")
        return cleaned


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    kind: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


def error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Single error shape: ``{"code", "message", …}`` (FastAPI nests it under ``detail``)."""
    return {"code": code, "message": message, **extra}


class StudyFlowService:
    """Wires the store, import dedupe map and validation log together."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = PowerContextStore(
            database_url=settings.powercontext_url,
            scheduler_path=settings.scheduler_path,
        )
        self.log = ValidationLog(settings.data_dir / "validation-log.jsonl")
        self.imports = domain.ImportPlan.load(settings.data_dir / "import-map.json")
        self.workspaces = WorkspaceRegistry.load(settings.data_dir)
        self.workspace: str = self.workspaces.last or settings.default_workspace
        self.scope_id: str | None = None
        self.last_error: str | None = None
        # 单进程内的写锁：读任务正文 + 读全部状态事件 + 比较 expected_seq + 追加事件必须原子。
        # 因此后端只支持 `python -m backend.app` 的单进程 uvicorn，不做多 worker。
        self.state_lock = asyncio.Lock()

    async def startup(self) -> None:
        try:
            await self.store.start()
            self.last_error = None
            await self.use_workspace(self.workspace)
        except Exception as error:  # noqa: BLE001 - report, never fake success
            self.last_error = f"{type(error).__name__}: {error}"

    async def shutdown(self) -> None:
        await self.store.stop()

    async def use_workspace(self, name: str) -> str:
        self.scope_id = await self.store.ensure_scope(name)
        self.workspace = name
        self.workspaces.remember(name, last=True)
        return self.scope_id

    async def require_scope(self) -> str:
        if not self.store.connected:
            detail = self.last_error or "PowerContext builtin runtime 未连接"
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "powercontext_unavailable",
                    "message": f"无法读写记忆：{detail}",
                    "connected": False,
                },
            )
        if self.scope_id is None:
            await self.use_workspace(self.workspace)
        assert self.scope_id is not None
        return self.scope_id

    async def gather(self, scope_id: str) -> dict[str, list]:
        """Read every active memory in the scope.

        Only ``state=active`` is used for preferences/continuation: a forgotten memory must not keep
        influencing the plan. Full pagination is required because task status events are append-only.
        """
        records = await self.store.list_all(scope_id, states=ACTIVE_STATES)
        buckets: dict[str, list] = {kind: [] for kind in domain.KIND_ORDER}
        buckets["other"] = []
        for record in records:
            buckets.setdefault(record.kind, [])
            buckets[record.kind].append(record)
        return buckets

    async def continuation_payload(self, *, minutes_override: int | None = None) -> dict[str, Any]:
        scope_id = await self.require_scope()
        buckets = await self.gather(scope_id)
        prefs = domain.parse_preferences(buckets["preference"])
        if minutes_override is not None:
            prefs.available_minutes = minutes_override
            prefs.max_tasks = domain.task_budget(minutes_override)
            prefs.rules.append(
                f"本次由界面覆盖可用时长为 {minutes_override} 分钟，任务上限按分档规则重算为 {prefs.max_tasks} 项"
            )
        tasks = domain.build_task_states(buckets["task"], buckets[domain.TASK_STATUS_KIND])
        payload = domain.build_continuation(
            workspace=self.workspace,
            scope_id=scope_id,
            prefs=prefs,
            tasks=tasks,
            weaknesses=buckets["weakness"],
            notes=buckets["note"],
            progress=buckets["progress"],
        )
        await self.log.append(
            "continuation.build",
            workspace=self.workspace,
            scope_id=scope_id,
            read_from="powercontext",
            memories_read=sum(len(items) for items in buckets.values()),
            suggested=len(payload["next_steps"]),
            engine=domain.ENGINE,
        )
        return payload

    async def import_sample(self) -> dict[str, Any]:
        scope_id = await self.require_scope()
        raw = json.loads(self.settings.sample_data_path.read_text(encoding="utf-8"))
        workspace_name = str(raw.get("workspace") or "studyflow-demo")
        scope_id = await self.use_workspace(workspace_name)
        entries = domain.sample_entries(list(raw.get("memories") or []))
        already = self.imports.mapped(scope_id)
        existing_ids = {record.artifact_id for record in await self.store.list_all(scope_id, states=ALL_STATES)}

        pending = [entry for entry in entries if already.get(entry["key"]) not in existing_ids]
        created = await self.store.create(scope_id, pending) if pending else []
        mapping = dict(already)
        for entry, record in zip(pending, created, strict=True):
            mapping[entry["key"]] = record.artifact_id
        self.imports.record(scope_id, mapping)
        self.imports.save()

        after = [record for record in await self.store.list_all(scope_id, states=ALL_STATES)]
        await self.log.append(
            "sample.import",
            workspace=workspace_name,
            scope_id=scope_id,
            requested=len(entries),
            created_now=len(created),
            skipped_duplicate=len(entries) - len(pending),
            total_memories=len(after),
            artifacts={record.artifact_id: [record.revision, record.kind] for record in created},
        )
        return {
            "workspace": workspace_name,
            "scope_id": scope_id,
            "requested": len(entries),
            "created_now": len(created),
            "skipped_duplicate": len(entries) - len(pending),
            "total_memories": len(after),
            "created": [record.to_api() for record in created],
            "disclaimer": raw.get("disclaimer"),
        }

    async def change_task_status(
        self,
        artifact_id: str,
        *,
        status: str,
        expected_seq: int,
        expected_task_revision: int | None,
    ) -> dict[str, Any]:
        """Read the task, read *every* status event, compare and append — inside one process lock.

        PowerContext 1.5.1 gives real compare-and-set on ``forget`` but no public content-revise
        entry point for ``atomic-memory``, so a status change is an append. Two requests that both
        carry the same ``expected_seq`` would otherwise both pass the check and both write.
        """
        scope_id = await self.require_scope()
        async with self.state_lock:
            task = await self.store.get(scope_id, artifact_id)
            if task.kind != "task":
                raise HTTPException(status_code=422, detail=error_payload("invalid_input", f"{artifact_id} 不是 kind=task 的记忆"))

            status_records = await self.store.list_all(scope_id, kind=domain.TASK_STATUS_KIND)
            latest = domain.latest_events(status_records).get(artifact_id)
            current_seq = int((domain.decode_status_event(latest) or {}).get("seq") or 0) if latest else 0

            if expected_seq != current_seq:
                await self.log.append(
                    "task.status.conflict",
                    workspace=self.workspace,
                    scope_id=scope_id,
                    artifact_id=artifact_id,
                    expected_seq=expected_seq,
                    current_seq=current_seq,
                    outcome="rejected",
                )
                raise HTTPException(
                    status_code=409,
                    detail=error_payload(
                        "version_conflict",
                        f"待办状态已被更新（服务端最新事件 seq={current_seq}，请求携带 expected_seq={expected_seq}）。"
                        "为避免覆盖并发修改，本次写入已取消，请刷新后重试。",
                        current_seq=current_seq,
                        expected_seq=expected_seq,
                        task_revision=task.revision,
                    ),
                )

            if expected_task_revision is not None and expected_task_revision != task.revision:
                raise HTTPException(
                    status_code=409,
                    detail=error_payload(
                        "version_conflict",
                        f"任务正文 revision 已变化（服务端 {task.revision}，请求 {expected_task_revision}），本次写入已取消。",
                    ),
                )

            summary = f"待办「{task.text[:40]}」{'已完成' if status == 'done' else '恢复为待办'}"
            entry = domain.encode_status_event(artifact_id, status, current_seq + 1, summary)
            event = (await self.store.create(scope_id, [entry]))[0]
            await self.log.append(
                "task.status.change",
                workspace=self.workspace,
                scope_id=scope_id,
                task_artifact_id=artifact_id,
                task_revision=task.revision,
                status=status,
                seq=current_seq + 1,
                event_artifact_id=event.artifact_id,
                event_revision=event.revision,
                mechanism="append-only task_status memory in PowerContext, guarded by one in-process asyncio.Lock",
            )
            return {
                "task_artifact_id": artifact_id,
                "status": status,
                "seq": current_seq + 1,
                "event": event.to_api(),
                "task_memory": task.to_api(),
            }


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or build_settings()
    service = StudyFlowService(resolved)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.startup()
        try:
            yield
        finally:
            await service.shutdown()

    app = FastAPI(title="研习台 StudyFlow", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(InvalidMemoryInput)
    async def _invalid(_: Request, error: InvalidMemoryInput) -> JSONResponse:  # type: ignore[name-defined]
        return JSONResponse(status_code=422, content=error_payload("invalid_input", str(error)))

    @app.exception_handler(VersionConflictError)
    async def _conflict(_: Request, error: VersionConflictError) -> JSONResponse:  # type: ignore[name-defined]
        return JSONResponse(status_code=409, content=error_payload("version_conflict", str(error)))

    @app.exception_handler(StoreUnavailableError)
    async def _unavailable(_: Request, error: StoreUnavailableError) -> JSONResponse:  # type: ignore[name-defined]
        return JSONResponse(status_code=503, content=error_payload("powercontext_unavailable", str(error)))

    @app.exception_handler(StoreError)
    async def _store(_: Request, error: StoreError) -> JSONResponse:  # type: ignore[name-defined]
        return JSONResponse(status_code=500, content=error_payload("store_error", str(error)))

    # ------------------------------------------------------------------ status

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        connected = service.store.connected
        payload: dict[str, Any] = {
            "connected": connected,
            "powercontext_package": installed_version(),
            "runtime_mode": "builtin-in-process (SQLite)",
            "workspace": service.workspace,
            "scope_id": service.scope_id,
            "data_dir": str(resolved.data_dir),
            "database": str(resolved.database_path),
            "bound_to": f"{resolved.host}:{resolved.port}",
            "last_error": service.last_error,
            "search_note": "检索使用 PowerContext SQLite FTS5 全文索引；未配置 embedding 模型，因此不宣称语义检索。",
        }
        if not connected:
            payload["message"] = DISCONNECTED_MESSAGE
            return payload
        try:
            payload["powercontext"] = await service.store.describe()
            scope_id = await service.require_scope()
            payload["counts"] = {
                "total": len(await service.store.list_all(scope_id, states=ALL_STATES)),
                "active": len(await service.store.list_all(scope_id, states=ACTIVE_STATES)),
            }
        except HTTPException:
            payload["connected"] = False
            payload["message"] = DISCONNECTED_MESSAGE
        except Exception as error:  # noqa: BLE001 - 探测失败也不能显示“已连接”
            service.last_error = f"{type(error).__name__}: {error}"
            payload["connected"] = False
            payload["last_error"] = service.last_error
            payload["probe_error"] = service.last_error
            payload["message"] = "PowerContext 状态探测失败：无法确认读写，界面不会显示成功结果。"
        return payload

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        body = {"connected": service.store.connected, "powercontext_package": installed_version()}
        if not service.store.connected:
            return JSONResponse(status_code=503, content={**body, **error_payload("powercontext_unavailable", DISCONNECTED_MESSAGE)})
        return body

    # ------------------------------------------------------------------ workspaces

    @app.get("/api/workspaces")
    async def workspaces() -> dict[str, Any]:
        scope_id = await service.require_scope()
        return {
            "current": service.workspace,
            "current_scope_id": scope_id,
            "known": service.workspaces.names or [service.workspace],
            "scopes": await service.store.list_workspaces(),
            "note": "scope 只是本地数据选取边界，不是用户认证。",
        }

    @app.post("/api/workspaces")
    async def use_workspace(payload: WorkspaceCreate) -> dict[str, Any]:
        if not service.store.connected:
            raise HTTPException(status_code=503, detail=error_payload("powercontext_unavailable", "PowerContext 未连接"))
        scope_id = await service.use_workspace(payload.workspace)
        await service.log.append("workspace.use", workspace=payload.workspace, scope_id=scope_id)
        return {"workspace": payload.workspace, "scope_id": scope_id}

    # ------------------------------------------------------------------ memories

    @app.get("/api/memories")
    async def memories(
        kind: str | None = Query(default=None),
        states: str = Query(default="active"),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, Any]:
        scope_id = await service.require_scope()
        wanted = ALL_STATES if states == "all" else tuple(s.strip() for s in states.split(",") if s.strip())
        records = await service.store.list(scope_id, kind=kind, states=wanted, limit=limit + 1)
        has_more = len(records) > limit
        records = records[:limit]
        tasks = (
            domain.build_task_states(
                await service.store.list_all(scope_id, kind="task"),
                await service.store.list_all(scope_id, kind=domain.TASK_STATUS_KIND),
            )
            if kind == "task"
            else None
        )
        return {
            "workspace": service.workspace,
            "scope_id": scope_id,
            "count": len(records),
            "limit": limit,
            "has_more": has_more,
            "items": [record.to_api() for record in records],
            "tasks": None if tasks is None else [task.to_api() for task in tasks],
            "source_of_truth": "powercontext",
            "list_note": (
                "本页按 limit 截断显示；继续学习、待办状态与复盘导出始终读取 scope 内全部 active 记忆。"
                if has_more
                else "本页已返回该状态筛选下的全部记忆；核心推导只使用 state=active。"
            ),
        }

    @app.post("/api/memories", status_code=201)
    async def create_memory(payload: MemoryCreate) -> dict[str, Any]:
        scope_id = await service.require_scope()
        created = await service.store.create(
            scope_id,
            [{"kind": payload.kind, "text": payload.text, "description": payload.description}],
        )
        record = created[0]
        await service.log.append(
            "memory.create",
            workspace=service.workspace,
            scope_id=scope_id,
            artifact_id=record.artifact_id,
            revision=record.revision,
            kind=record.kind,
            state_version=record.state_version,
            source_ids=list(record.source_ids),
        )
        return {"memory": record.to_api()}

    @app.get("/api/memories/{artifact_id}")
    async def get_memory(artifact_id: str, revision: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
        scope_id = await service.require_scope()
        record = await service.store.get(scope_id, artifact_id, revision=revision)
        return {"memory": record.to_api(), "state": await service.store.state(scope_id, artifact_id)}

    @app.delete("/api/memories/{artifact_id}")
    async def forget_memory(
        artifact_id: str,
        expected_revision: int = Query(..., ge=1),
        expected_state_version: int = Query(..., ge=0),
    ) -> dict[str, Any]:
        scope_id = await service.require_scope()
        before = await service.store.state(scope_id, artifact_id)
        record = await service.store.forget(
            scope_id,
            artifact_id,
            expected_revision=expected_revision,
            expected_state_version=expected_state_version,
        )
        await service.log.append(
            "memory.forget",
            workspace=service.workspace,
            scope_id=scope_id,
            artifact_id=artifact_id,
            before=before,
            after_state=record.state,
            after_state_version=record.state_version,
            mechanism="PowerContext expected_revision + expected_state_version CAS",
        )
        return {"memory": record.to_api()}

    # ------------------------------------------------------------------ search

    @app.post("/api/search")
    async def search(payload: SearchRequest) -> dict[str, Any]:
        scope_id = await service.require_scope()
        result = await service.store.search(scope_id, payload.query, kind=payload.kind, limit=payload.limit)
        await service.log.append(
            "search",
            workspace=service.workspace,
            scope_id=scope_id,
            query=payload.query,
            mode=result["mode"],
            hits=len(result["hits"]),
            artifact_ids=[hit["record"]["artifact_id"] for hit in result["hits"]],
        )
        return {**result, "workspace": service.workspace, "scope_id": scope_id, "empty_message": None if result["hits"] else "PowerContext 未返回匹配记忆，应用不会编造结果。"}

    # ------------------------------------------------------------------ tasks

    @app.get("/api/tasks")
    async def tasks() -> dict[str, Any]:
        scope_id = await service.require_scope()
        task_records = await service.store.list_all(scope_id, kind="task")
        status_records = await service.store.list_all(scope_id, kind=domain.TASK_STATUS_KIND)
        states = domain.build_task_states(task_records, status_records)
        return {
            "workspace": service.workspace,
            "scope_id": scope_id,
            "items": [task.to_api() for task in states],
            "read_scope": {"task_memories": len(task_records), "status_events": len(status_records), "pagination": "full"},
            "status_model": {
                "schema": domain.TASK_STATUS_SCHEMA,
                "kind": domain.TASK_STATUS_KIND,
                "note": "完成/恢复都追加一条 task_status 事件记忆；任务正文 artifact 的 revision 保持不变。",
                "read_note": "本接口读取该 scope 全部分页的事件后再取最新，超过 500 条也不会复活旧状态。",
            },
        }

    @app.post("/api/tasks", status_code=201)
    async def create_task(payload: TaskCreate) -> dict[str, Any]:
        scope_id = await service.require_scope()
        record = (await service.store.create(scope_id, [{"kind": "task", "text": payload.text}]))[0]
        await service.log.append(
            "task.create",
            workspace=service.workspace,
            scope_id=scope_id,
            artifact_id=record.artifact_id,
            revision=record.revision,
        )
        return {"task": {"memory": record.to_api(), "status": "todo", "title": record.text[:80]}}

    @app.post("/api/tasks/{artifact_id}/status")
    async def update_task_status(artifact_id: str, payload: StatusUpdate) -> dict[str, Any]:
        return await service.change_task_status(
            artifact_id,
            status=payload.status,
            expected_seq=payload.expected_seq,
            expected_task_revision=payload.expected_task_revision,
        )

    # ------------------------------------------------------------------ continuation & export

    @app.get("/api/continuation")
    async def continuation(available_minutes: int | None = Query(default=None, ge=1, le=480)) -> dict[str, Any]:
        return await service.continuation_payload(minutes_override=available_minutes)

    @app.get("/api/review/export")
    async def review_export() -> Response:
        payload = await service.continuation_payload()
        markdown = domain.build_review_markdown(payload)
        await service.log.append(
            "review.export",
            workspace=service.workspace,
            scope_id=payload["scope_id"],
            bytes=len(markdown.encode("utf-8")),
            citations=len(payload["references"]),
        )
        filename = "studyflow-review.md"
        return Response(
            content=markdown.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/samples/import")
    async def import_sample() -> dict[str, Any]:
        return await service.import_sample()

    @app.get("/api/samples/validation-plan")
    async def validation_plan() -> dict[str, Any]:
        raw = json.loads(resolved.sample_data_path.read_text(encoding="utf-8"))
        return {"disclaimer": raw.get("disclaimer"), "validation": raw.get("validation"), "workspace": raw.get("workspace")}

    @app.get("/api/validation-log")
    async def validation_log(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        return {"items": service.log.tail(limit), "path": str(resolved.data_dir / "validation-log.jsonl")}

    # ------------------------------------------------------------------ dev-only fault drill

    @app.post("/api/dev/disconnect")
    async def dev_disconnect() -> dict[str, Any]:
        if os.environ.get("STUDYFLOW_ALLOW_DESTRUCTIVE_DEV") != "1":
            raise HTTPException(status_code=404, detail=error_payload("not_found", "未启用故障演练端点"))
        # 真的关闭 PowerContext runtime（关闭发生在它自己的宿主任务里），不是把界面临时切成离线状态。
        await service.store.stop()
        service.scope_id = None
        service.last_error = "手动断开（STUDYFLOW_ALLOW_DESTRUCTIVE_DEV=1 的故障演练）"
        await service.log.append("dev.disconnect", workspace=service.workspace)
        return {"connected": service.store.connected, "message": "PowerContext runtime 已关闭"}

    @app.post("/api/dev/reconnect")
    async def dev_reconnect() -> dict[str, Any]:
        if os.environ.get("STUDYFLOW_ALLOW_DESTRUCTIVE_DEV") != "1":
            raise HTTPException(status_code=404, detail=error_payload("not_found", "未启用故障演练端点"))
        await service.startup()
        return {"connected": service.store.connected, "scope_id": service.scope_id}

    # ------------------------------------------------------------------ frontend

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    return app


app = create_app()


def run(host: str | None = None, port: int | None = None, db_path: str | None = None) -> None:
    import uvicorn

    settings = build_settings()
    if db_path:
        target = Path(db_path).expanduser()
        os.environ["STUDYFLOW_DB_PATH"] = str(target)
        os.environ["STUDYFLOW_DATA_DIR"] = str(target.parent)
        settings = build_settings()
    uvicorn.run(
        create_app(settings),
        host=host or settings.host,
        port=port or settings.port,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    run()
