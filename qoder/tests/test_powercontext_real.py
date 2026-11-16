"""Real PowerContext integration tests — no mocks. These open the builtin runtime on SQLite."""

from __future__ import annotations

import json

import pytest

from backend.store import (
    ALL_STATES,
    PowerContextStore,
    VersionConflictError,
)

pytestmark = pytest.mark.integration


async def _reopen(database_dir):
    return PowerContextStore(
        database_url=f"sqlite+aiosqlite:///{database_dir / 'powercontext.db'}",
        scheduler_path=database_dir / "powercontext.scheduler.db",
    )


async def test_create_list_and_read_back_survive_a_process_lifecycle(tmp_path):
    """写入 → 关闭 runtime → 重新打开 → 读回：证明事实来源在 PowerContext，不在页面内存。"""
    store = PowerContextStore(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'powercontext.db'}",
        scheduler_path=tmp_path / "powercontext.scheduler.db",
    )
    await store.start()
    assert store.connected

    scope_id = await store.ensure_scope("studyflow-demo")
    created = await store.create(
        scope_id,
        [
            {
                "kind": "preference",
                "text": "我的单次学习时间为 25 分钟，优先复习薄弱知识点，每次最多安排两个任务。",
                "description": "演示学习偏好",
            },
            {
                "kind": "note",
                "text": "数据库事务隔离复习笔记：幻读涉及符合条件的行集合变化。",
                "description": "演示课程笔记 01",
            },
        ],
    )
    assert len(created) == 2
    assert all(record.revision == 1 for record in created)
    assert all(record.state == "active" for record in created)
    assert all(record.source_ids for record in created), "每条记忆都应有真实 PowerContext source lineage"
    await store.stop()
    assert store.connected is False

    reopened = await _reopen(tmp_path)
    await reopened.start()
    try:
        same_scope = await reopened.ensure_scope("studyflow-demo")
        assert same_scope == scope_id, "scope 绑定必须稳定，否则工作区隔离不成立"
        records = await reopened.list(same_scope, limit=50)
        assert {record.artifact_id for record in records} == {record.artifact_id for record in created}
        preference = next(record for record in records if record.kind == "preference")
        assert "25 分钟" in preference.text
        assert preference.content_schema == "powercontext.atomic-memory.v1"
    finally:
        await reopened.stop()


async def test_unknown_scope_operations_are_isolated(tmp_path):
    store = await _reopen(tmp_path)
    await store.start()
    try:
        demo = await store.ensure_scope("演示工作区")
        fresh = await store.ensure_scope("全新工作区")
        assert demo != fresh

        created = await store.create(fresh, [{"kind": "note", "text": "只属于全新工作区的一条笔记"}])
        assert len(await store.list(demo, limit=50)) == 0
        demo_records = await store.list(fresh, limit=50)
        assert [record.artifact_id for record in demo_records] == [created[0].artifact_id]

        hits = await store.search(demo, "笔记", limit=5)
        assert hits["hits"] == []
    finally:
        await store.stop()


async def test_chinese_full_text_search_returns_real_citations(tmp_path):
    store = await _reopen(tmp_path)
    await store.start()
    try:
        scope_id = await store.ensure_scope("studyflow-demo")
        await store.create(
            scope_id,
            [
                {
                    "kind": "note",
                    "text": "数据库事务隔离复习笔记：脏读是读到另一个事务尚未提交的数据；不可重复读是在同一事务中多次读取同一行得到不同结果；幻读涉及符合条件的行集合变化。",
                    "description": "演示课程笔记 01",
                },
                {
                    "kind": "weakness",
                    "text": "我还不能用例子解释幻读与不可重复读的区别，需要完成两组 SQL 事务执行顺序练习。",
                    "description": "演示自评 01",
                },
                {"kind": "progress", "text": "已完成脏读和不可重复读的概念整理。", "description": "演示进度 01"},
            ],
        )
        result = await store.search(scope_id, "幻读", limit=10)
        kinds = {hit["record"]["kind"] for hit in result["hits"]}
        assert {"note", "weakness"} <= kinds, f"中文检索“幻读”应命中笔记与薄弱点，实际 {result}"
        assert result["mode"] in {"text", "fts", "auto"}
        assert result["diagnostics"]["embedding_calls"] == 0, "未配置 embedding 模型时不应调用模型"
        for hit in result["hits"]:
            assert hit["record"]["citation"]["quote"] in hit["record"]["text"] or hit["record"]["citation"]["quote"].endswith("…")
            assert hit["matched_by"], "每个命中都应说明匹配通道"

        missing = await store.search(scope_id, "量子色谱", limit=10)
        assert missing["hits"] == []
    finally:
        await store.stop()


async def test_forget_uses_powercontext_compare_and_set(tmp_path):
    """真实的预期版本冲突：PowerContext 自身的 expected_revision / expected_state_version。"""
    store = await _reopen(tmp_path)
    await store.start()
    try:
        scope_id = await store.ensure_scope("studyflow-demo")
        record = (await store.create(scope_id, [{"kind": "task", "text": "整理事务隔离级别对照表"}]))[0]
        assert record.state_version == 0

        with pytest.raises(VersionConflictError):
            await store.forget(scope_id, record.artifact_id, expected_revision=99, expected_state_version=0)

        still_there = await store.get(scope_id, record.artifact_id)
        assert still_there.state == "active", "冲突后不得静默改动数据"

        forgotten = await store.forget(
            scope_id, record.artifact_id, expected_revision=record.revision, expected_state_version=record.state_version
        )
        assert forgotten.state == "forgotten"
        assert forgotten.state_version > record.state_version
        assert await store.list(scope_id, limit=10) == []
        everything = await store.list(scope_id, states=ALL_STATES, limit=10)
        assert [item.artifact_id for item in everything] == [record.artifact_id]
    finally:
        await store.stop()


async def test_task_status_events_are_append_only_and_ordered(tmp_path):
    store = await _reopen(tmp_path)
    await store.start()
    try:
        from backend.domain import build_task_states, decode_status_event, encode_status_event, latest_events

        scope_id = await store.ensure_scope("studyflow-demo")
        task = (await store.create(scope_id, [{"kind": "task", "text": "整理事务隔离级别对照表，并为幻读写一个两事务并发的示例。"}]))[0]

        first = (await store.create(scope_id, [encode_status_event(task.artifact_id, "done", 1, "待办已完成")]))[0]
        events = await store.list(scope_id, kind="task_status", limit=50)
        states = build_task_states([task], events)
        assert states[0].status == "done"
        assert states[0].event.artifact_id == first.artifact_id
        assert task.revision == 1, "正文 revision 不因状态事件而变化（界面不得声称已修订正文）"

        await store.create(scope_id, [encode_status_event(task.artifact_id, "todo", 2, "待办恢复为待办")])
        events = await store.list(scope_id, kind="task_status", limit=50)
        latest = latest_events(events)[task.artifact_id]
        assert decode_status_event(latest)["seq"] == 2
        assert build_task_states([task], events)[0].status == "todo"

        search = await store.search(scope_id, "整理事务隔离级别", limit=10)
        assert search["hits"], "状态事件的中文摘要也应能被全文检索到"

        text = json.loads(first.text)
        assert text["schema"] == "studyflow.task-status.v1"
        assert text["task_artifact_id"] == task.artifact_id
    finally:
        await store.stop()
