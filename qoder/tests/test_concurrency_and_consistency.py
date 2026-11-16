"""Regression tests for the three real concurrency/consistency boundaries.

1. Two simultaneous status writes must not both succeed (append-only events need an application lock).
2. Status resolution must read *every* task_status event: truncating the read would resurrect an
   older status once more than the page limit exists.
3. A forgotten memory must stop influencing the preference/continuation derivation.

Everything here runs against the real powercontext[builtin]==1.5.1 runtime; no mocks.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest

from backend import domain
from tests.conftest import make_settings


@pytest.fixture()
def concurrency_settings():
    return make_settings("concurrency")


async def _open_app(settings):
    """Run the real lifespan and an in-process async client inside the same event loop."""
    from httpx import ASGITransport, AsyncClient

    from backend.app import create_app

    app = create_app(settings)
    stack = AsyncExitStack()
    client = await stack.enter_async_context(
        AsyncClient(transport=ASGITransport(app), base_url="http://studyflow.test", timeout=120)
    )
    await stack.enter_async_context(app.router.lifespan_context(app))
    return app, client, stack


async def test_two_concurrent_status_writes_do_not_both_succeed(concurrency_settings) -> None:
    app, client, stack = await _open_app(concurrency_settings)
    try:
        assert (await client.post("/api/samples/import")).status_code == 200
        tasks = (await client.get("/api/tasks")).json()["items"]
        target = next(item for item in tasks if item["memory"]["text"].startswith("整理事务隔离级别"))
        url = f"/api/tasks/{target['memory']['artifact_id']}/status"

        first, second = await asyncio.gather(
            client.post(url, json={"status": "done", "expected_seq": 0}),
            client.post(url, json={"status": "done", "expected_seq": 0}),
        )
        codes = sorted([first.status_code, second.status_code])
        assert codes == [200, 409], f"同一 expected_seq 的并发写入必须只成功一次，实际 {codes}"

        conflict = second if second.status_code == 409 else first
        assert conflict.json()["detail"]["current_seq"] == 1

        after = (await client.get("/api/tasks")).json()
        current = next(item for item in after["items"] if item["memory"]["artifact_id"] == target["memory"]["artifact_id"])
        assert current["status"] == "done"
        assert current["event_payload"]["seq"] == 1, "只能追加一条状态事件"
        assert after["read_scope"]["status_events"] == 1
        assert current["memory"]["revision"] == 1, "任务正文 revision 不变"

        ok = await client.post(url, json={"status": "todo", "expected_seq": 1})
        assert ok.status_code == 200
        assert ok.json()["seq"] == 2
    finally:
        await stack.aclose()
        assert app.state.service.store.connected is False


async def test_status_resolution_is_not_truncated_at_the_page_limit(concurrency_settings) -> None:
    app, client, stack = await _open_app(concurrency_settings)
    try:
        await client.post("/api/samples/import")
        tasks = (await client.get("/api/tasks")).json()["items"]
        target = next(item for item in tasks if item["memory"]["text"].startswith("整理事务隔离级别"))
        service = app.state.service
        scope_id = await service.require_scope()

        total = 520
        entries = [
            domain.encode_status_event(
                target["memory"]["artifact_id"],
                "done" if index <= 500 else "todo",
                index,
                f"演练第 {index} 条状态事件，用于验证分页读取不被截断",
            )
            for index in range(1, total + 1)
        ]
        for start in range(0, len(entries), 50):
            await service.store.create(scope_id, entries[start : start + 50])

        payload = (await client.get("/api/tasks")).json()
        current = next(item for item in payload["items"] if item["memory"]["artifact_id"] == target["memory"]["artifact_id"])
        assert payload["read_scope"]["status_events"] == total, "必须读完全部状态事件，不能只取前 500 条"
        assert current["event_payload"]["seq"] == total
        assert current["status"] == "todo", "第 501 条之后才是最新状态，截断读取会复活 done"

        continuation = (await client.get("/api/continuation")).json()
        assert not any(item.get("task_artifact_id") == target["memory"]["artifact_id"] for item in continuation["completed"]), (
            "已完成清单同样不能使用被截断的旧事件"
        )

        stale = await client.post(
            f"/api/tasks/{target['memory']['artifact_id']}/status",
            json={"status": "done", "expected_seq": 500},
        )
        assert stale.status_code == 409, "旧事件号必须被拒绝"
        assert stale.json()["detail"]["current_seq"] == total

        fresh = await client.post(
            f"/api/tasks/{target['memory']['artifact_id']}/status",
            json={"status": "done", "expected_seq": total},
        )
        assert fresh.status_code == 200
        assert fresh.json()["seq"] == total + 1

        listing = (await client.get("/api/memories", params={"limit": 10})).json()
        assert listing["count"] == 10
        assert listing["has_more"] is True, "列表页截断必须显式告知，而不是假装读全了"
    finally:
        await stack.aclose()


def test_forgotten_preference_stops_shaping_the_plan(client) -> None:
    """state=forgotten 的记忆不得继续参与偏好/续学推导。"""
    client.post("/api/samples/import")
    before = client.get("/api/continuation").json()
    assert before["preference"]["source"], "演示偏好应被读到"

    listed = client.get("/api/memories", params={"kind": "preference"}).json()
    preference = listed["items"][0]
    removed = client.delete(
        f"/api/memories/{preference['artifact_id']}",
        params={"expected_revision": preference["revision"], "expected_state_version": preference["state_version"]},
    )
    assert removed.status_code == 200
    assert removed.json()["memory"]["state"] == "forgotten"

    after = client.get("/api/continuation").json()
    assert after["preference"]["source"] is None
    assert any("默认值" in rule for rule in after["preference"]["rules"])
    assert all(item["artifact_id"] != preference["artifact_id"] for item in after["references"]), "已遗忘记忆不应再被引用"

    search = client.post("/api/search", json={"query": "单次学习时间", "limit": 10}).json()
    assert all(hit["record"]["artifact_id"] != preference["artifact_id"] for hit in search["hits"])

    listing = client.get("/api/memories", params={"states": "all"}).json()
    assert listing["has_more"] is False
    assert "active" in listing["list_note"]
    assert any(item["artifact_id"] == preference["artifact_id"] and item["state"] == "forgotten" for item in listing["items"])
