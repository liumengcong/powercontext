"""Run acceptance through a real published Dify Workflow API. No fallback/mock."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)
key = os.getenv("DIFY_API_KEY", "")
if not key or key.startswith("REPLACE_"):
    sys.exit("请先在 .env 中填入已发布的 AI 正式版 Dify 应用 API Key；没有 Key 不会伪造端到端证据。")
base = os.getenv("DIFY_API_BASE", "https://api.dify.ai/v1").rstrip("/")
player = "acceptance-" + uuid4().hex[:16]
trace = {"time_utc": datetime.now(timezone.utc).isoformat(), "dify_engine_executed": True,
         "ai_workflow": True, "player_key": player, "steps": []}


def run(action, message="", identity=player):
    r = httpx.post(base + "/workflows/run", headers={"Authorization": "Bearer " + key},
        json={"inputs": {"player_key": identity, "action": action, "message": message},
              "response_mode": "blocking", "user": "lost-found-acceptance"}, timeout=180)
    r.raise_for_status()
    data = r.json()
    task = data.get("data", {})
    if task.get("status") != "succeeded":
        raise RuntimeError("Dify 执行失败：" + str(task.get("error", task)))
    out = task["outputs"]
    proof = json.loads(out["memory_proof"])
    trace["steps"].append({"action": action, "message": message, "workflow_run_id": task.get("id"),
                           "outputs": out, "proof": proof})
    print(f"{action}: PowerContext {proof['memory_before']} → {proof['memory_after']}")
    return out, proof


try:
    out, p = run("寄存", "会打嗝的月亮")
    assert p["memory_before"] == 0 and p["memory_after"] == 1 and "会打嗝的月亮" in out["answer"]
    run("A", "请记得我给月亮买了芋泥啵啵")
    out, p = run("回访", "上次那件物品怎么样了？")
    assert p["memory_before"] == p["memory_after"] == 2
    assert "会打嗝的月亮" in out["answer"]
    # Check persisted callback independently; LLM wording is open-ended.
    btoken = os.getenv("BACKEND_TOKEN", "")
    bbase = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
    rr = httpx.post(bbase + "/v1/inspect", headers={"Authorization": "Bearer " + btoken}, json={
        "player_key": player, "request_id": str(uuid4()), "action": "档案", "message": ""}, timeout=60)
    rr.raise_for_status()
    saved = rr.json()
    assert "芋泥啵啵" in json.dumps(saved["state"]["current"]["choices"], ensure_ascii=False)
    trace["powercontext_inspection"] = saved
    run("B")
    out, p = run("C")
    assert "结局" in out["answer"] and p["memory_after"] == 4
    out, p = run("档案", identity="isolated-" + uuid4().hex[:16])
    assert p["memory_after"] == 0 and "会打嗝的月亮" not in out["answer"]
    trace["status"] = "passed"
except Exception as exc:
    trace["status"] = "failed"
    trace["error"] = str(exc)
    raise
finally:
    target = ROOT / "evidence" / "dify_live_acceptance.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print("实际执行记录：" + str(target))
