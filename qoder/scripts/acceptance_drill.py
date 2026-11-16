#!/usr/bin/env python
"""验收演练：用真实 HTTP 请求 + 真实进程重启，逐项执行 docs/验收样例.md 的第 1-6 节。

用法（在项目根目录的 qoder/ 下）：

    ../../work/pc-env/bin/python scripts/acceptance_drill.py

数据默认写到 `data/`（与 README 一致），脚本不会删除任何已有数据：重复运行时导入靠
去重映射保持不变，待办状态只追加事件。证据写入 `docs/evidence/<时间戳>/`，
失败同样记录并以非零状态退出。第 7 节（Qoder Sites 公开访问）必须人工在浏览器完成，
本脚本不声称已经发布。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

QODER_ROOT = Path(__file__).resolve().parents[1]
DEMO_WORKSPACE = "studyflow-demo"
ISOLATION_WORKSPACE = "隔离演练-临时工作区"


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object, dict]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, _decode(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, _decode(error.read()), dict(error.headers)

    def get(self, path: str) -> tuple[int, object]:
        status, payload, _ = self.request("GET", path)
        return status, payload

    def post(self, path: str, body: dict | None = None) -> tuple[int, object]:
        status, payload, _ = self.request("POST", path, body)
        return status, payload


def _decode(raw: bytes) -> object:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


class Server:
    """Starts a real backend process so a restart really means a new process."""

    def __init__(self, python: str, host: str, port: int, data_dir: Path, evidence: Path, index: int) -> None:
        env = dict(os.environ)
        env.update(
            STUDYFLOW_DATA_DIR=str(data_dir),
            STUDYFLOW_DB_PATH=str(data_dir / "powercontext.db"),
            STUDYFLOW_HOST=host,
            STUDYFLOW_PORT=str(port),
        )
        env.pop("STUDYFLOW_WORKSPACE", None)
        self.log_path = evidence / f"server-{index}.log"
        self.index = index
        command = [python, "-m", "backend.app"]
        self.process = subprocess.Popen(  # noqa: S603
            command,
            cwd=str(QODER_ROOT),
            env=env,
            stdout=open(self.log_path, "wb"),  # noqa: SIM115
            stderr=subprocess.STDOUT,
        )
        self.api = Api(f"http://{host}:{port}")

    def wait_ready(self) -> None:
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"后端进程启动失败，退出码 {self.process.returncode}，见 {self.log_path.name}")
            try:
                status, payload = self.api.get("/api/health")
                if status == 200 and payload.get("connected"):
                    return
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.5)
        raise RuntimeError("后端 60 秒内未就绪")

    def stop(self, *, graceful: bool = True) -> None:
        if self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGTERM if graceful else signal.SIGKILL)
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutError:  # pragma: no cover - defensive
            self.process.kill()
            self.process.wait(timeout=10)


class Recorder:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def check(self, name: str, *, ok: bool, detail: object) -> None:
        self.checks.append({"check": name, "passed": bool(ok), "detail": detail})
        flag = "PASS" if ok else "FAIL"
        preview = json.dumps(detail, ensure_ascii=False)
        if len(preview) > 300:
            preview = preview[:300] + " …"
        print(f"[{flag}] {name}\n       {preview}", flush=True)

    @property
    def failures(self) -> list[str]:
        return [item["check"] for item in self.checks if not item["passed"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="StudyFlow 验收演练")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8713)
    parser.add_argument("--data-dir", default=str(QODER_ROOT / "data"))
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    evidence = QODER_ROOT / "docs" / "evidence" / stamp
    evidence.mkdir(parents=True, exist_ok=True)

    rec = Recorder()
    servers: list[Server] = []
    launched: list[str] = []

    def start_server() -> Server:
        server = Server(args.python, args.host, args.port, data_dir, evidence, len(servers) + 1)
        servers.append(server)
        launched.append(
            f"STUDYFLOW_DATA_DIR={data_dir} {Path(args.python).name} -m backend.app  (http://{args.host}:{args.port})"
        )
        server.wait_ready()
        return server

    try:
        # ---------------------------------------------------------- 1. 干净环境启动
        server = start_server()
        status, payload = server.api.get("/api/status")
        first = payload if isinstance(payload, dict) else {}
        rec.check(
            "1. 干净环境启动并报告真实 PowerContext 状态",
            ok=status == 200 and first.get("connected") is True and str(first.get("powercontext_package", "")).startswith("1.5"),
            detail={
                "connected": first.get("connected"),
                "powercontext_package": first.get("powercontext_package"),
                "runtime_mode": first.get("runtime_mode"),
                "database": first.get("database"),
                "bound_to": first.get("bound_to"),
                "scope_id": first.get("scope_id"),
                "counts": first.get("counts"),
                "search_note": first.get("search_note"),
            },
        )
        if first.get("connected") is not True:
            raise RuntimeError("PowerContext 未连接，后续步骤无法执行")

        # ---------------------------------------------------------- 2. 导入、检索与引用
        status, imported_a = server.api.post("/api/samples/import")
        status, imported_b = server.api.post("/api/samples/import")
        a, b = _as_dict(imported_a), _as_dict(imported_b)
        status, listed = server.api.get("/api/memories?states=all")
        listing = _as_dict(listed)
        rec.check(
            "2a. 导入 sample-data.json 且重复导入不新增",
            ok=status == 200 and a.get("requested") == 6 and b.get("total_memories") == a.get("total_memories") == listing.get("count"),
            detail={
                "workspace": a.get("workspace"),
                "scope_id": a.get("scope_id"),
                "requested": a.get("requested"),
                "created_now_first": a.get("created_now"),
                "created_now_second": b.get("created_now"),
                "skipped_duplicate_second": b.get("skipped_duplicate"),
                "total_memories": listing.get("count"),
                "source_of_truth": listing.get("source_of_truth"),
                "artifacts": {item["kind"]: item["artifact_id"] for item in listing.get("items", []) if item["kind"] != "task"},
            },
        )

        status, found = server.api.post("/api/search", {"query": "幻读", "limit": 10})
        search = _as_dict(found)
        descriptions = " ".join((hit["record"].get("description") or "") for hit in search.get("hits", []))
        must = ["key=demo-note-isolation", "key=demo-weak-phantom"]
        diagnostics = search.get("diagnostics", {})
        rec.check(
            "2b. 检索“幻读”返回真实原文与 PowerContext 引用",
            ok=all(token in descriptions for token in must) and search.get("hits"),
            detail={
                "query": "幻读",
                "mode": search.get("mode"),
                "hit_count": len(search.get("hits", [])),
                "referenced_keys": [token for token in must if token in descriptions],
                "embedding_calls": diagnostics.get("embedding_calls"),
                "first_hit": {
                    "artifact_id": search["hits"][0]["record"]["artifact_id"],
                    "revision": search["hits"][0]["record"]["revision"],
                    "kind": search["hits"][0]["record"]["kind"],
                    "matched_by": search["hits"][0].get("matched_by"),
                    "quote": search["hits"][0]["record"]["citation"]["quote"][:60],
                },
            },
        )

        status, nothing = server.api.post("/api/search", {"query": "紫色长颈鹿开飞机", "limit": 10})
        empty = _as_dict(nothing)
        rec.check(
            "2c. 检索不存在的词返回空结果且不编造",
            ok=empty.get("hits") == [] and "不会编造" in (empty.get("empty_message") or ""),
            detail={"hits": empty.get("hits"), "empty_message": empty.get("empty_message")},
        )

        # ---------------------------------------------------------- 3. 真正跨会话继续
        status, before_restart = server.api.get("/api/continuation")
        pre = _as_dict(before_restart)
        server.stop()
        server = start_server()
        status, after_restart = server.api.get("/api/continuation")
        post = _as_dict(after_restart)
        status, restarted_status_raw = server.api.get("/api/status")
        restarted = _as_dict(restarted_status_raw)
        pref = post.get("preference", {})
        rec.check(
            "3. 杀掉进程重启后从 PowerContext 读回偏好/进度/薄弱点",
            ok=(
                restarted.get("scope_id") == pre.get("scope_id") == post.get("scope_id")
                and pref.get("available_minutes") == 25
                and pref.get("max_tasks") == 2
                and len(post.get("next_steps", [])) <= 2
                and bool(post.get("blocked"))
                and bool(post.get("recorded_progress"))
                and bool(pref.get("source"))
            ),
            detail={
                "scope_before": pre.get("scope_id"),
                "scope_after": post.get("scope_id"),
                "process": "旧进程已 SIGTERM 退出，新进程 PID 不同",
                "pid_change": {"old_stopped": True, "new_pid": server.process.pid},
                "available_minutes": pref.get("available_minutes"),
                "max_tasks": pref.get("max_tasks"),
                "preference_source": pref.get("source"),
                "rules": pref.get("rules"),
                "next_steps": [{"text": s["text"][:40], "reason": s["reason"], "artifact_id": s["citation"]["artifact_id"]} for s in post.get("next_steps", [])],
                "blocked_count": len(post.get("blocked", [])),
                "recorded_progress_quote": (post.get("recorded_progress") or [{}])[0].get("text", "")[:60],
                "reference_count": len(post.get("references", [])),
                "explanation": post.get("explanation"),
                "counts_after_restart": restarted.get("counts"),
            },
        )

        # ---------------------------------------------------------- 4. 状态更新与持久化 + 冲突
        status, tasks_payload = server.api.get("/api/tasks")
        tasks = _as_dict(tasks_payload)
        target = next((item for item in tasks.get("items", []) if item["memory"]["text"].startswith("整理事务隔离级别")), None)
        other = next((item for item in tasks.get("items", []) if target and item is not target), None)
        if target is None:
            raise RuntimeError("演示待办缺失，无法执行第 4 节")

        seq_now = int((target.get("event_payload") or {}).get("seq") or 0)
        if target["status"] != "done":
            status, updated_raw = server.api.post(f"/api/tasks/{target['memory']['artifact_id']}/status", {"status": "done", "expected_seq": seq_now})
            updated = _as_dict(updated_raw)
        else:
            status, reopen = server.api.post(f"/api/tasks/{target['memory']['artifact_id']}/status", {"status": "done", "expected_seq": seq_now})
            updated = _as_dict(reopen)
        rec.check(
            "4a. 标记完成写入真实 task_status 事件",
            ok=status == 200 and updated.get("seq") == seq_now + 1 and updated.get("task_memory", {}).get("revision") == 1,
            detail={
                "task_artifact_id": target["memory"]["artifact_id"],
                "task_body_revision": updated.get("task_memory", {}).get("revision"),
                "event_artifact_id": updated.get("event", {}).get("artifact_id"),
                "event_revision": updated.get("event", {}).get("revision"),
                "seq_before": seq_now,
                "seq_after": updated.get("seq"),
                "status_model_note": tasks.get("status_model", {}).get("note"),
                "honesty_note": "任务正文 revision 仍为 1：状态是追加的事件，不声称内容被改写",
            },
        )

        status, conflict_raw = server.api.post(
            f"/api/tasks/{target['memory']['artifact_id']}/status", {"status": "todo", "expected_seq": 0}
        )
        conflict = _as_dict(conflict_raw)
        rec.check(
            "4b. 用旧 expected_seq 更新被拒绝，不静默覆盖",
            ok=status == 409 and conflict.get("detail", {}).get("current_seq") == seq_now + 1,
            detail={"http_status": status, "code": conflict.get("detail", {}).get("code"), "message": conflict.get("detail", {}).get("message"), "current_seq": conflict.get("detail", {}).get("current_seq")},
        )

        # 两个真实并发请求携带同一个 expected_seq：应用锁必须只放行一次写入。
        concurrent_seq = seq_now + 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    server.api.request,
                    "POST",
                    f"/api/tasks/{target['memory']['artifact_id']}/status",
                    {"status": "done", "expected_seq": concurrent_seq},
                )
                for _ in range(2)
            ]
            concurrent_results = [future.result() for future in futures]
        codes = sorted(code for code, _, _ in concurrent_results)
        _, tasks_now = server.api.get("/api/tasks")
        now_payload = _as_dict(tasks_now)
        current_task = next(
            (item for item in now_payload.get("items", []) if item["memory"]["artifact_id"] == target["memory"]["artifact_id"]),
            {},
        )
        rec.check(
            "4b2. 同一 expected_seq 的并发写入只成功一次",
            ok=codes == [200, 409] and (current_task.get("event_payload") or {}).get("seq") == concurrent_seq + 1,
            detail={
                "expected_seq_sent_by_both": concurrent_seq,
                "http_status_codes": codes,
                "rejected_message": next(( _as_dict(payload).get("detail", {}).get("message") for code, payload, _ in concurrent_results if code == 409), None),
                "event_seq_after": (current_task.get("event_payload") or {}).get("seq"),
                "status_events_total": now_payload.get("read_scope", {}).get("status_events"),
                "lock_scope": "单进程 uvicorn 内的 asyncio.Lock；跨进程不保证，README 已写明",
            },
        )

        server.stop()
        server = start_server()
        status, tasks_after = server.api.get("/api/tasks")
        after = _as_dict(tasks_after)
        by_id = {item["memory"]["artifact_id"]: item for item in after.get("items", [])}
        rec.check(
            "4c. 再次重启后完成状态仍在、未完成的仍为待办",
            ok=(
                by_id.get(target["memory"]["artifact_id"], {}).get("status") == "done"
                and (other is None or by_id.get(other["memory"]["artifact_id"], {}).get("status") == other["status"])
            ),
            detail={
                "target_status": by_id.get(target["memory"]["artifact_id"], {}).get("status"),
                "target_event_seq": (by_id.get(target["memory"]["artifact_id"], {}).get("event_payload") or {}).get("seq"),
                "other_status": None if other is None else by_id.get(other["memory"]["artifact_id"], {}).get("status"),
                "other_expected": None if other is None else other["status"],
                "items": [{"text": i["memory"]["text"][:24], "status": i["status"]} for i in after.get("items", [])],
            },
        )

        # ---------------------------------------------------------- 5. 工作区隔离
        isolation_name = f"{ISOLATION_WORKSPACE}-{stamp}"
        status, demo_listing = server.api.get("/api/memories")
        demo = _as_dict(demo_listing)
        status, switched_raw = server.api.post("/api/workspaces", {"workspace": isolation_name})
        switched = _as_dict(switched_raw)
        status, blank = server.api.get("/api/memories")
        blank_listing = _as_dict(blank)
        marker = f"仅隔离演练可见 {stamp}"
        status, created_raw = server.api.post("/api/memories", {"kind": "note", "text": marker})
        created = _as_dict(created_raw)
        status, back_raw = server.api.post("/api/workspaces", {"workspace": DEMO_WORKSPACE})
        back = _as_dict(back_raw)
        status, restored_raw = server.api.get("/api/memories")
        restored = _as_dict(restored_raw)
        status, leak = server.api.post("/api/search", {"query": marker, "limit": 10})
        leak_result = _as_dict(leak)
        rec.check(
            "5. 切换工作区互不可见，scope 只是数据边界",
            ok=(
                switched.get("scope_id") != demo.get("scope_id")
                and blank_listing.get("count") == 0
                and back.get("scope_id") == demo.get("scope_id")
                and all(marker not in item["text"] for item in restored.get("items", []))
                and leak_result.get("hits") == []
            ),
            detail={
                "isolation_workspace": isolation_name,
                "demo_scope": demo.get("scope_id"),
                "demo_count": demo.get("count"),
                "isolation_scope": switched.get("scope_id"),
                "isolation_count_before_write": blank_listing.get("count"),
                "written_in_isolation": created.get("memory", {}).get("artifact_id"),
                "back_to_demo_scope": back.get("scope_id"),
                "demo_count_after_switch_back": restored.get("count"),
                "cross_scope_search_hits_for_marker": leak_result.get("hits"),
                "workspaces_note": _as_dict(server.api.get("/api/workspaces")[1]).get("note"),
            },
        )

        # ---------------------------------------------------------- 6. 导出复盘
        export_status, export_body, export_headers = server.api.request("GET", "/api/review/export")
        markdown = export_body if isinstance(export_body, str) else json.dumps(export_body, ensure_ascii=False)
        review_path = evidence / "studyflow-review.md"
        review_path.write_text(markdown, encoding="utf-8")
        forbidden_hits = [
            line
            for line in markdown.splitlines()
            if any(word in line for word in ("提效", "论文数量", "使用时长"))
            and not any(qualifier in line for qualifier in ("未记录", "无法推断", "需要本人核对"))
        ]
        rec.check(
            "6. 导出 UTF-8 Markdown 复盘初稿，只使用真实记录",
            ok=(
                export_status == 200
                and "charset=utf-8" in _header(export_headers, "content-type")
                and "attachment" in _header(export_headers, "content-disposition")
                and "学习复盘初稿" in markdown
                and markdown.count("art_") >= 3
                and not forbidden_hits
            ),
            detail={
                "http_status": export_status,
                "content_type": _header(export_headers, "content-type"),
                "content_disposition": _header(export_headers, "content-disposition"),
                "bytes": len(markdown.encode("utf-8")),
                "citation_count": markdown.count("art_"),
                "saved_to": str(review_path.relative_to(QODER_ROOT)),
                "sections": [line for line in markdown.splitlines() if line.startswith("## ")],
                "invented_number_lines": forbidden_hits,
                "head": markdown.splitlines()[:6],
            },
        )

        # ---------------------------------------------------------- 验证记录本身
        status, log_payload = server.api.get("/api/validation-log?limit=500")
        log = _as_dict(log_payload)
        operations = sorted({item.get("operation", "") for item in log.get("items", [])})
        secrets = [item for item in log.get("items", []) if any(word in json.dumps(item, ensure_ascii=False) for word in ("password", "token", "api_key"))]
        rec.check(
            "验证记录只记操作、不含凭据",
            ok={"sample.import", "memory.create", "search", "continuation.build", "task.status.change", "task.status.conflict", "review.export"} <= set(operations) and not secrets,
            detail={"log_path": log.get("path"), "operations": operations, "entries": len(log.get("items", [])), "secret_like_entries": secrets},
        )
    except Exception as error:  # noqa: BLE001 - failures must still be written out
        rec.check("演练异常中断", ok=False, detail={"error": f"{type(error).__name__}: {error}"})
    finally:
        for server in servers:
            server.stop()

        bundle = {
            "schema": "studyflow.acceptance.v1",
            "run_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            "environment": {
                "python": _run([args.python, "--version"]),
                "powercontext": _installed_package("powercontext"),
                "fastapi": _installed_package("fastapi"),
                "uvicorn": _installed_package("uvicorn"),
                "command": " ".join([str(Path(args.python).name), "scripts/acceptance_drill.py", f"--port {args.port}"]),
                "data_dir": str(data_dir),
                "server_launches": launched,
                "platform": sys.platform,
            },
            "note": "全部结果来自本机真实进程与真实 PowerContext SQLite 数据；第 7 节站点公开发布需人工在浏览器完成，本文件不做声称。",
            "checks": rec.checks,
        }
        (evidence / "evidence.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        (evidence / "run-commands.txt").write_text(
            "\n".join(
                [
                    f"验收演练命令：cd qoder && {args.python} scripts/acceptance_drill.py --port {args.port}",
                    *launched,
                    "",
                    "生成的服务器日志：",
                    *[str(server.log_path.relative_to(QODER_ROOT)) for server in servers],
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n证据目录：{evidence.relative_to(QODER_ROOT)}")
        print(f"检查项：{len(rec.checks)}，失败：{len(rec.failures)}")
        for name in rec.failures:
            print(f"  失败：{name}")

    return 1 if rec.failures else 0


def _as_dict(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}


def _header(headers: dict, name: str) -> str:
    """HTTP header names are case-insensitive; urllib lower-cases them."""
    lowered = {key.lower(): value for key, value in headers.items()}
    return lowered.get(name.lower(), "")


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:  # pragma: no cover
        return f"unavailable: {error}"
    return (result.stdout or result.stderr).strip()


def _installed_package(name: str) -> str:
    return _run([sys.executable, "-c", f"import importlib.metadata as m; print(m.version({name!r}))"])


if __name__ == "__main__":
    sys.exit(main())
