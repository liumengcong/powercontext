"""Integration tests use real PowerContext 1.2.0 and disk persistence, no mocks."""
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app import create_app

TOKEN = "test-only-not-a-deployment-token-0000000000"
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN)) as client:
        yield client


def payload(action="寄存", message="会打嗝的月亮", player="sample-player-001", request_id=None):
    return {"player_key": player, "action": action, "message": message, "request_id": request_id or str(uuid4())}


def prepare(client, data):
    r = client.post("/v1/prepare", headers=HEADERS, json=data)
    assert r.status_code == 200, r.text
    return r.json()


def finish(client, p):
    r = client.post("/v1/commit", headers=HEADERS, json={"ticket": p["ticket"], "narration": p["fallback_narration"]})
    assert r.status_code == 200, r.text
    return r.json()


def turn(client, data):
    return finish(client, prepare(client, data))


def inspect(client, player="sample-player-001"):
    r = client.post("/v1/inspect", headers=HEADERS, json=payload("档案", "", player))
    assert r.status_code == 200, r.text
    return r.json()


def test_real_powercontext_and_source_citation(client):
    result = turn(client, payload())
    state = inspect(client)
    assert result["powercontext"] == "1.2.0"
    assert result["memory_before"] == 0 and result["memory_after"] == 1
    assert state["state"]["current"]["item"] == "会打嗝的月亮"
    assert state["memory_citation"]["entry_id"]
    assert state["state"]["history"][0]["message"] == "会打嗝的月亮"
    # The game-state request receipt points to the raw official Source by ID.
    assert any(x["source_id"] == result["source_id"] for x in state["recent_sources"])
    assert "会打嗝的月亮" in json.dumps(state["recent_sources"], ensure_ascii=False)


def test_preparation_does_not_advance_or_save(client):
    prepare(client, payload())
    state = inspect(client)
    assert state["revision"] == 0 and state["state"]["current"] is None


def test_new_session_reuses_exact_input_and_read_does_not_write(client):
    turn(client, payload())
    turn(client, payload("A", "让月亮先领一份芋泥啵啵"))
    p = prepare(client, payload("回访", ""))
    context = json.loads(p["director_context"])
    assert context["item"] == "会打嗝的月亮"
    assert "芋泥啵啵" in context["event"]["callback"]
    result = finish(client, p)
    assert result["memory_before"] == result["memory_after"] == 2
    assert "芋泥啵啵" in result["answer"]


def test_three_turns_create_one_ending_and_stamps(client):
    turn(client, payload())
    for choice in ("A", "B", "C"):
        result = turn(client, payload(choice, ""))
    state = inspect(client)["state"]
    assert state["current"]["round"] == 3
    assert state["current"]["ending"]
    assert len(state["current"]["stamps"]) == 3
    assert len(state["album"]) == 1
    assert "结局" in result["answer"]
    bad = client.post("/v1/prepare", headers=HEADERS, json=payload("A", ""))
    assert bad.status_code == 422


def test_player_scope_isolation(client):
    turn(client, payload())
    other = inspect(client, "a-different-player")
    assert other["revision"] == 0 and other["state"]["current"] is None


def test_recent_commit_retries_are_idempotent(client):
    data = payload()
    p = prepare(client, data)
    one = finish(client, p)
    two = finish(client, p)
    three = finish(client, prepare(client, data))
    assert one["answer"] == two["answer"] == three["answer"]
    assert two["replayed"] and three["replayed"]
    assert inspect(client)["revision"] == 1


def test_request_id_reuse_with_new_input_fails(client):
    data = payload()
    turn(client, data)
    data["message"] = "别的物品"
    r = client.post("/v1/prepare", headers=HEADERS, json=data)
    assert r.status_code == 409


def test_competing_prepared_actions_fail_closed(client):
    a, b = prepare(client, payload()), prepare(client, payload(message="会跳舞的影子"))
    finish(client, a)
    r = client.post("/v1/commit", headers=HEADERS, json={"ticket": b["ticket"], "narration": "第二个请求"})
    assert r.status_code == 409
    assert inspect(client)["state"]["current"]["item"] == "会打嗝的月亮"


def test_auth_validation_and_tampered_ticket(client):
    assert client.post("/v1/prepare", json=payload()).status_code == 401
    assert client.post("/v1/prepare", headers=HEADERS, json=payload(player="short")).status_code == 422
    assert client.post("/v1/prepare", headers=HEADERS, json=payload(message="")).status_code == 422
    assert client.post("/v1/prepare", headers=HEADERS, json=payload(message="字" * 241)).status_code == 422
    p = prepare(client, payload())
    r = client.post("/v1/commit", headers=HEADERS, json={"ticket": p["ticket"][:-1] + "Z", "narration": "篡改"})
    assert r.status_code == 400


def test_restart_reopens_actual_powercontext_database(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN)) as c:
        turn(c, payload())
        turn(c, payload("B", "柜员的名字叫星期八"))
        before = inspect(c)
    with TestClient(create_app(tmp_path, TOKEN)) as c:
        after = inspect(c)
        result = turn(c, payload("回访", ""))
    assert before == after
    assert "星期八" in result["answer"]
    assert (tmp_path / "powercontext.db").stat().st_size > 0


def test_reset_preserves_album_and_audit(client):
    turn(client, payload())
    for _ in range(3):
        turn(client, payload("A", ""))
    turn(client, payload("重开", ""))
    state = inspect(client)["state"]
    assert state["current"] is None and len(state["album"]) == 1
    turn(client, payload(message="忘记下班的闹钟"))
    state = inspect(client)["state"]
    assert state["current"]["item"] == "忘记下班的闹钟" and len(state["album"]) == 1


def test_dsl_code_and_http_contract_against_real_backend(client):
    root = Path(__file__).resolve().parents[1]
    for file in sorted((root / "dsl").glob("*.yml")):
        dsl = yaml.safe_load(file.read_text())
        ids = {n["id"] for n in dsl["workflow"]["graph"]["nodes"]}
        assert len(ids) == 9
        for edge in dsl["workflow"]["graph"]["edges"]:
            assert edge["source"] in ids and edge["target"] in ids
        for n in dsl["workflow"]["graph"]["nodes"]:
            if n["data"]["type"] == "code":
                compile(n["data"]["code"], file.name + ":" + n["id"], "exec")
    # Run actual Python node bodies and HTTP requests from the diagnostic DSL.
    dsl = yaml.safe_load((root / "dsl" / "00_无模型连通性自检.yml").read_text())
    values = {"100": {"player_key": "dsl-contract-player", "action": "寄存", "message": "拒绝内卷的订书机"}}
    for n in dsl["workflow"]["graph"]["nodes"][1:-1]:
        data = n["data"]
        if data["type"] == "code":
            ns = {}
            exec(data["code"], ns)
            kwargs = {v["variable"]: values[v["value_selector"][0]][v["value_selector"][1]] for v in data["variables"]}
            values[n["id"]] = ns["main"](**kwargs)
        else:
            source = "200" if n["id"] == "300" else "600"
            path = "/v1/prepare" if n["id"] == "300" else "/v1/commit"
            r = client.post(path, headers=HEADERS, json=json.loads(values[source]["request_body"]))
            values[n["id"]] = {"body": r.text, "status_code": r.status_code}
    assert "拒绝内卷的订书机" in values["800"]["answer"]
    assert json.loads(values["800"]["memory_proof"])["memory_after"] == 1
