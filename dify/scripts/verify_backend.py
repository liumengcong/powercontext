"""Exercise an actual HTTP server, real SDK and restart; clearly excludes Dify/LLM."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "status": "running", "real_powercontext": True,
              "real_http_server": True, "dify_engine_executed": False, "llm_executed": False,
              "note": "真实后端/官方SDK证据，旁白使用规则自检文本；不作为 Dify Cloud 或模型调用的完成证明。", "steps": []}
    with tempfile.TemporaryDirectory(prefix="lost-found-check-") as directory:
        token = secrets.token_urlsafe(36)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        headers = {"Authorization": "Bearer " + token}
        env = {**os.environ, "BACKEND_TOKEN": token, "DATA_DIR": directory}
        client = httpx.Client(base_url=base, headers=headers, timeout=30)

        def start():
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
                                       cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError("后端未能启动")
                try:
                    r = client.get("/healthz")
                    if r.status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError("等待服务就绪超时")

        def stop(process):
            process.terminate()
            process.wait(timeout=15)

        player = "backend-evidence-player"

        def body(action, message="", identity=player):
            return {"player_key": identity, "request_id": str(uuid4()), "action": action, "message": message}

        def inspect(identity=player):
            r = client.post("/v1/inspect", json=body("档案", identity=identity))
            r.raise_for_status()
            return r.json()

        def turn(action, message="", identity=player):
            data = body(action, message, identity)
            r = client.post("/v1/prepare", json=data)
            r.raise_for_status()
            p = r.json()
            r = client.post("/v1/commit", json={"ticket": p["ticket"], "narration": p["fallback_narration"]})
            r.raise_for_status()
            out = r.json()
            report["steps"].append({"input": data, "context_recalled": json.loads(p["director_context"]), "actual_output": out})
            return out

        process = start()
        try:
            turn("寄存", "会打嗝的月亮")
            turn("A", "请记得我给月亮买了芋泥啵啵")
            out = turn("回访", "新会话，回来看看")
            assert "芋泥啵啵" in out["answer"] and out["memory_before"] == out["memory_after"] == 2
            before = inspect()
        finally:
            stop(process)
        process = start()
        try:
            after = inspect()
            assert after == before
            report["restart_check"] = {"passed": True, "revision": after["revision"], "memory_citation": after["memory_citation"]}
            out = turn("回访", "服务重启后的回访")
            assert "芋泥啵啵" in out["answer"]
            turn("B")
            out = turn("C")
            assert "结局" in out["answer"]
            isolated = inspect("isolated-backend-player")
            assert isolated["state"]["current"] is None and isolated["revision"] == 0
            report["isolation_check"] = {"passed": True, "revision": isolated["revision"]}
            end = inspect()
            report["actual_powercontext_records"] = {key: end[key] for key in ["scope_id", "revision", "memory_citation", "recent_sources"]}
            report["status"] = "passed"
        finally:
            stop(process)
            client.close()
    output = ROOT / "evidence" / "backend_actual_run.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("真实后端及重启验证通过：" + str(output))


if __name__ == "__main__":
    main()
