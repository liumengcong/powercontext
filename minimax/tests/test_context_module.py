import asyncio
import shutil

import pytest

from sdd_context.agent import validate_candidate
from sdd_context.common import ROOT, TASK, read_json, write_json
from sdd_context.context import prepare
from sdd_context.database import database
from sdd_context.ingest import ingest
from sdd_context.state import TaskState, load, save


@pytest.fixture(scope="module")
def real_database(tmp_path_factory):
    runtime = tmp_path_factory.mktemp("real-seekdb")
    with database(runtime) as result:
        yield result


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    shutil.copytree(ROOT / "demo", path, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return path


def context(conn, version, project, tmp_path, task=TASK, budget=40000):
    return asyncio.run(prepare(conn, version, project, tmp_path / "state", task, "test-task", budget))


def test_real_query_has_required_sources_and_no_retired_spec(real_database, project, tmp_path):
    conn, version = real_database
    ingest(conn, project)
    result = context(conn, version, project, tmp_path)
    required = read_json(project / "task.json")["required_source_ids"]
    actual = {r["source_id"] for r in result["sources"]}
    assert set(required) <= actual
    assert "ORD-000" not in actual
    assert "OceanBase" in result["database"]["version"]
    assert set(result["database"]["returned_source_ids"]) == actual


def test_updated_spec_replaces_content_and_deleted_spec_disappears(real_database, project, tmp_path):
    conn, version = real_database
    ingest(conn, project)
    path = project / "specs/orders.json"
    value = read_json(path)
    value["revision"] = "updated-test"
    value["requirements"] = [r for r in value["requirements"] if r["id"] != "ORD-004"]
    item = next(r for r in value["requirements"] if r["id"] == "ORD-003")
    item["text"] = "orders min_total：测试更新内容，strict boundary"
    write_json(path, value)
    ingest(conn, project)
    result = context(conn, version, project, tmp_path)
    docs = {r["source_id"]: r for r in result["sources"]}
    assert docs["ORD-003"]["content"] == item["text"]
    assert docs["ORD-003"]["revision"] == "updated-test"
    assert "ORD-004" not in docs


def test_changed_file_requires_reimport(real_database, project, tmp_path):
    conn, version = real_database
    ingest(conn, project)
    file = project / "business/orders.py"
    file.write_text(file.read_text() + "\n# modified after import\n")
    with pytest.raises(ValueError, match="re-import"):
        context(conn, version, project, tmp_path)
    ingest(conn, project)
    assert context(conn, version, project, tmp_path)["sources"]


def test_invalid_import_does_not_publish_partial_index(real_database, project, tmp_path):
    conn, version = real_database
    ingest(conn, project)
    path = project / "specs/orders.json"
    original = path.read_bytes()
    value = read_json(path)
    value["requirements"].append(value["requirements"][0])
    write_json(path, value)
    with pytest.raises(ValueError, match="Duplicate"):
        ingest(conn, project)
    path.write_bytes(original)
    assert context(conn, version, project, tmp_path)["sources"]


def test_irrelevant_query_does_not_return_fixed_answer(real_database, project, tmp_path):
    conn, version = real_database
    ingest(conn, project)
    with pytest.raises(ValueError, match="No relevant"):
        context(conn, version, project, tmp_path, task="unrelated_kitchen_sink_xzq")


def test_too_small_budget_fails_instead_of_losing_constraints(real_database, project, tmp_path):
    conn, version = real_database
    ingest(conn, project)
    with pytest.raises(ValueError, match="Budget"):
        context(conn, version, project, tmp_path, budget=10)


def test_powercontext_revisions_are_durable_and_tasks_are_isolated(tmp_path):
    async def scenario():
        runtime = tmp_path / "pc"
        assert await load(runtime, "one") is None
        first = await save(runtime, "one", TaskState(task="task", done=["imported"], todo=["modify"]))
        assert first["revision"] == 1
        assert (await load(runtime, "one"))["content"]["todo"] == ["modify"]
        second = await save(runtime, "one", TaskState(task="task", done=["modified"], todo=[]))
        assert second["revision"] == 2
        assert (await load(runtime, "one"))["content"]["todo"] == []
        assert await load(runtime, "other") is None

    asyncio.run(scenario())


def test_model_output_cannot_change_signature_or_add_module_side_effects():
    valid = (ROOT / "demo/business/orders.py").read_text()
    assert validate_candidate(valid) == valid
    with pytest.raises(ValueError):
        validate_candidate("import os\n" + valid)
    with pytest.raises(ValueError, match="signature"):
        validate_candidate(valid.replace("min_total=None", "min_total=0"))
    with pytest.raises(ValueError):
        validate_candidate(valid + "\nopen('unexpected', 'w')\n")
