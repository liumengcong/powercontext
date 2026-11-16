"""End-to-end API tests over the real PowerContext runtime (TestClient keeps one process)."""

from __future__ import annotations

import json


def test_status_reports_real_powercontext(client):
    status = client.get("/api/status").json()
    assert status["connected"] is True
    assert status["powercontext_package"].startswith("1.5")
    assert status["runtime_mode"].startswith("builtin-in-process")
    assert status["scope_id"], "应已解析出真实 PowerContext scope id"
    assert status["counts"]["total"] == 0, "干净环境不应有历史数据"


def test_status_explains_disconnection_and_blocks_writes(store_app):
    from fastapi.testclient import TestClient

    with TestClient(store_app) as local_client:
        assert local_client.get("/api/status").json()["connected"] is True
        # 真实的断开：关闭同进程内的 PowerContext runtime（故障演练开关默认关闭）
        disconnect = local_client.post("/api/dev/disconnect")
        assert disconnect.status_code == 404, "默认必须未启用故障演练端点"

    import os

    os.environ["STUDYFLOW_ALLOW_DESTRUCTIVE_DEV"] = "1"
    try:
        with TestClient(store_app) as local_client:
            assert local_client.post("/api/dev/disconnect").status_code == 200
            status = local_client.get("/api/status").json()
            assert status["connected"] is False
            assert "无法读取或写入" in status["message"]
            for path in ("/api/memories", "/api/continuation", "/api/tasks"):
                response = local_client.get(path)
                assert response.status_code == 503, f"{path} 在断开时必须失败而不是假装成功"
                assert response.json()["detail"]["code"] == "powercontext_unavailable"
            search = local_client.post("/api/search", json={"query": "幻读"})
            assert search.status_code == 503
            assert local_client.post("/api/dev/reconnect").status_code == 200
            assert local_client.get("/api/status").json()["connected"] is True
    finally:
        os.environ.pop("STUDYFLOW_ALLOW_DESTRUCTIVE_DEV", None)


def test_sample_import_is_idempotent(client):
    first = client.post("/api/samples/import").json()
    assert first["requested"] == 6
    assert first["created_now"] == 6
    assert first["workspace"] == "studyflow-demo"
    assert first["scope_id"]

    second = client.post("/api/samples/import").json()
    assert second["created_now"] == 0
    assert second["skipped_duplicate"] == 6
    assert second["total_memories"] == first["total_memories"] == 6

    listing = client.get("/api/memories", params={"states": "all"}).json()
    assert listing["count"] == 6
    assert listing["source_of_truth"] == "powercontext"
    kinds = sorted({item["kind"] for item in listing["items"]})
    assert kinds == ["note", "preference", "progress", "task", "weakness"]


def test_sample_search_and_empty_result(client):
    client.post("/api/samples/import")
    result = client.post("/api/search", json={"query": "幻读", "limit": 10}).json()
    kinds = {hit["record"]["kind"] for hit in result["hits"]}
    assert {"note", "weakness"} <= kinds
    for hit in result["hits"]:
        record = hit["record"]
        assert record["artifact_id"].startswith("art_")
        assert record["revision"] >= 1
        assert record["citation"]["quote"]
        assert "key=demo-" in (record["description"] or "")

    empty = client.post("/api/search", json={"query": "完全不存在的词组", "limit": 10}).json()
    assert empty["hits"] == []
    assert "不会编造" in empty["empty_message"]


def test_continuation_reads_preference_and_caps_tasks(client):
    client.post("/api/samples/import")
    payload = client.get("/api/continuation").json()
    assert payload["engine"].startswith("rule-based")
    assert payload["preference"]["available_minutes"] == 25
    assert payload["preference"]["max_tasks"] == 2
    assert len(payload["next_steps"]) <= 2
    assert payload["preference"]["source"]["artifact_id"].startswith("art_")
    assert payload["blocked"], "演示薄弱点应作为当前阻塞出现"
    assert payload["recorded_progress"], "演示进度原文应被照抄而不是改写"
    for step in payload["next_steps"]:
        assert step["citation"]["artifact_id"].startswith("art_")
        assert step["reason"]
    assert payload["references"]
    assert any("不是模型推断" in line for line in payload["explanation"])


def test_available_minutes_override_changes_the_plan(client):
    client.post("/api/samples/import")
    fifteen = client.get("/api/continuation", params={"available_minutes": 15}).json()
    assert fifteen["preference"]["max_tasks"] == 1
    assert len(fifteen["next_steps"]) <= 1
    long_session = client.get("/api/continuation", params={"available_minutes": 60}).json()
    assert long_session["preference"]["max_tasks"] == 3
    assert any("界面覆盖" in rule for rule in long_session["preference"]["rules"])


def test_task_status_persists_and_detects_stale_version(client):
    client.post("/api/samples/import")
    tasks = client.get("/api/tasks").json()
    assert len(tasks["items"]) == 2
    target = next(item for item in tasks["items"] if item["memory"]["text"].startswith("整理事务隔离级别"))
    assert target["status"] == "todo"
    stale_seq = target.get("event_payload") or {}
    assert stale_seq == {}

    updated = client.post(
        f"/api/tasks/{target['memory']['artifact_id']}/status",
        json={"status": "done", "expected_seq": 0},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["seq"] == 1
    assert body["event"]["artifact_id"].startswith("art_")
    assert body["task_memory"]["revision"] == 1, "任务正文 revision 未被伪称更新"

    conflict = client.post(
        f"/api/tasks/{target['memory']['artifact_id']}/status",
        json={"status": "todo", "expected_seq": 0},
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "version_conflict"
    assert detail["current_seq"] == 1

    ok_again = client.post(
        f"/api/tasks/{target['memory']['artifact_id']}/status",
        json={"status": "done", "expected_seq": 1},
    )
    assert ok_again.status_code == 200
    assert ok_again.json()["seq"] == 2

    after = {item["memory"]["artifact_id"]: item for item in client.get("/api/tasks").json()["items"]}
    assert after[target["memory"]["artifact_id"]]["status"] == "done"
    other = next(item for key, item in after.items() if key != target["memory"]["artifact_id"])
    assert other["status"] == "todo"


def test_memory_crud_validates_input(client):
    bad_kind = client.post("/api/memories", json={"kind": "secrets", "text": "不该写入"})
    assert bad_kind.status_code == 422

    empty_text = client.post("/api/memories", json={"kind": "note", "text": "   "})
    assert empty_text.status_code == 422

    control = client.post("/api/memories", json={"kind": "note", "text": "包含\x00控制字符"})
    assert control.status_code == 422

    long_text = client.post("/api/memories", json={"kind": "note", "text": "藏" * 4000})
    assert long_text.status_code == 422, "超过 8192 UTF-8 字节必须拒绝"

    created = client.post("/api/memories", json={"kind": "note", "text": "索引页码写在最后", "description": "自测笔记"})
    assert created.status_code == 201
    artifact_id = created.json()["memory"]["artifact_id"]
    detail = client.get(f"/api/memories/{artifact_id}").json()
    assert detail["state"]["state"] == "active"

    removed = client.delete(
        f"/api/memories/{artifact_id}",
        params={"expected_revision": 1, "expected_state_version": detail["state"]["state_version"]},
    )
    assert removed.status_code == 200
    assert removed.json()["memory"]["state"] == "forgotten"

    stale = client.delete(f"/api/memories/{artifact_id}", params={"expected_revision": 1, "expected_state_version": 0})
    assert stale.status_code == 409


def test_workspaces_do_not_leak(client):
    client.post("/api/samples/import")
    demo = client.get("/api/memories").json()
    assert demo["count"] == 6

    switched = client.post("/api/workspaces", json={"workspace": "全新工作区"})
    assert switched.status_code == 200
    assert switched.json()["scope_id"] != demo["scope_id"]

    blank = client.get("/api/memories").json()
    assert blank["count"] == 0
    assert blank["scope_id"] == switched.json()["scope_id"]

    client.post("/api/memories", json={"kind": "note", "text": "只属于新工作区"})
    search_in_new = client.post("/api/search", json={"query": "只属于新工作区"}).json()
    assert len(search_in_new["hits"]) == 1

    back = client.post("/api/workspaces", json={"workspace": "studyflow-demo"}).json()
    assert back["scope_id"] == demo["scope_id"]
    restored = client.get("/api/memories").json()
    assert restored["count"] == 6
    assert all("只属于新工作区" not in item["text"] for item in restored["items"])
    assert "不是用户认证" in client.get("/api/workspaces").json()["note"]


def test_review_export_is_utf8_and_only_uses_records(client):
    client.post("/api/samples/import")
    client.post("/api/samples/import")
    tasks = client.get("/api/tasks").json()["items"]
    target = next(item for item in tasks if item["memory"]["text"].startswith("整理事务隔离级别"))
    client.post(f"/api/tasks/{target['memory']['artifact_id']}/status", json={"status": "done", "expected_seq": 0})

    response = client.get("/api/review/export")
    assert response.status_code == 200
    assert "charset=utf-8" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert "学习复盘初稿" in text
    assert "已完成" in text and "仍未解决" in text and "下一步" in text
    assert "需要本人核对" in text
    assert "整理事务隔离级别" in text
    assert text.count("art_") >= 3, "导出必须包含真实 artifact 引用"
    assert "rule-based" in text
    for forbidden in ("提效", "论文数量"):
        for line in text.splitlines():
            if forbidden in line:
                assert "未记录" in line or "无法推断" in line, f"导出不该生成没有记录的数字：{line}"
    assert not any(word in text for word in ("100%", "300%", "提升 2 倍"))


def test_validation_log_records_real_operations(client):
    client.post("/api/samples/import")
    client.post("/api/memories", json={"kind": "note", "text": "记录一次真实写入"})
    client.post("/api/search", json={"query": "真实写入"})
    client.get("/api/continuation")
    entries = client.get("/api/validation-log").json()["items"]
    operations = {item["operation"] for item in entries}
    assert {"sample.import", "memory.create", "search", "continuation.build"} <= operations
    assert any(item.get("artifact_id", "").startswith("art_") for item in entries)
    assert all("password" not in json.dumps(item) and "token" not in json.dumps(item) for item in entries)


def test_imported_data_is_read_back_after_a_restart(settings):
    """验收样例 3：停止进程、重新打开后仍能读回 25 分钟偏好与未完成事项。"""
    from backend.app import create_app

    from fastapi.testclient import TestClient

    app_one = create_app(settings)
    with TestClient(app_one) as first:
        imported = first.post("/api/samples/import").json()
        scope_id = imported["scope_id"]
        tasks = first.get("/api/tasks").json()["items"]
        target = next(item for item in tasks if item["memory"]["text"].startswith("整理事务隔离级别"))
        first.post(f"/api/tasks/{target['memory']['artifact_id']}/status", json={"status": "done", "expected_seq": 0})
        first.get("/api/continuation")

    app_two = create_app(settings)
    with TestClient(app_two) as second:
        status = second.get("/api/status").json()
        assert status["scope_id"] == scope_id, "同一工作区必须解析到同一个 PowerContext scope"
        assert status["counts"]["total"] == 7, "6 条演示记忆 + 1 条状态事件"

        continuation = second.get("/api/continuation").json()
        assert continuation["preference"]["available_minutes"] == 25
        assert continuation["preference"]["max_tasks"] == 2
        assert len(continuation["next_steps"]) == 1
        assert continuation["next_steps"][0]["text"].startswith("复习数据库事务笔记")
        assert continuation["completed"][0]["status"] == "done"
        assert continuation["completed"][0]["evidence"]["artifact_id"].startswith("art_")

        tasks = second.get("/api/tasks").json()["items"]
        by_prefix = {item["memory"]["text"][:6]: item["status"] for item in tasks}
        assert by_prefix["整理事务隔离"] == "done"
        assert by_prefix["复习数据库事"] == "todo"

        # 以旧版本重复更新仍应给出冲突提示
        done_task = next(item for item in tasks if item["status"] == "done")
        stale = second.post(
            f"/api/tasks/{done_task['memory']['artifact_id']}/status",
            json={"status": "todo", "expected_seq": 0},
        )
        assert stale.status_code == 409
