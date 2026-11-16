"""Reproduce the competition scenario and save evidence; live mode needs a MiniMax key."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from sdd_context.agent import generate, validate_candidate
from sdd_context.common import ROOT, TASK, digest, project_fingerprint, read_json, utc_now, write_json
from sdd_context.context import prepare
from sdd_context.database import database
from sdd_context.ingest import ingest
from sdd_context.state import TaskState, load, save

TASK_ID = "orders-min-total"


def run_tests(project: Path, report: Path, name: str, test_file: str) -> dict:
    xml = report / f"{name}.xml"
    command = [sys.executable, "-m", "pytest", test_file, "-q", "--tb=short", f"--junitxml={xml}"]
    started = utc_now()
    result = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=90)
    output = (result.stdout + result.stderr).replace(str(project), "$PROJECT").replace(str(ROOT), "$MINIMAX")
    (report / f"{name}.log").write_text(output, encoding="utf-8")
    suites = ET.parse(xml).getroot()
    cases = suites.findall(".//testcase")
    failures = suites.findall(".//testcase/failure")
    errors = suites.findall(".//testcase/error")
    record = {
        "command": ["python", "-m", "pytest", test_file, "-q", "--tb=short", f"--junitxml={name}.xml"],
        "started": started,
        "finished": utc_now(),
        "exit_code": result.returncode,
        "source_fingerprint": project_fingerprint(project),
        "file_sha256": digest((project / "business/orders.py").read_bytes()),
        "tests": len(cases),
        "failures": len(failures),
        "errors": len(errors),
        "failure_types": sorted(set(item.get("type") for item in failures)),
        "failure_messages": [item.get("message") for item in failures],
    }
    if not cases or errors or result.returncode not in (0, 1):
        raise RuntimeError(f"Environment/collection error in {name}; inspect its log")
    if any(
        "AssertionError" not in (item.get("message") or "")
        and "DID NOT RAISE" not in (item.get("message") or "")
        and (item.get("type") or "") not in ("AssertionError",)
        for item in failures
    ):
        raise RuntimeError(f"Unexpected failure type in {name}; target behavior must cause the failure")
    write_json(report / f"{name}.json", record)
    return record


def tests_hashes(project: Path) -> dict:
    return {
        p.relative_to(project).as_posix(): digest(p.read_bytes())
        for p in sorted((project / "tests").rglob("*.py"))
    }


def restarted_context(project: Path, runtime: Path, report: Path, name: str) -> dict:
    target = report / f"{name}.json"
    command = [
        sys.executable,
        "-m",
        "sdd_context",
        "context",
        "--project",
        str(project),
        "--runtime",
        str(runtime),
        "--task-id",
        TASK_ID,
        "--output",
        str(target),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    (report / f"{name}.log").write_text(
        (result.stdout + result.stderr).replace(str(ROOT), "$MINIMAX"), encoding="utf-8"
    )
    write_json(
        report / f"{name}-process.json",
        {
            "command": [
                "python",
                "-m",
                "sdd_context",
                "context",
                "--project",
                "$PROJECT",
                "--runtime",
                "$RUNTIME",
                "--task-id",
                TASK_ID,
                "--output",
                f"{name}.json",
            ],
            "exit_code": result.returncode,
            "new_process": True,
        },
    )
    if result.returncode:
        raise RuntimeError(f"Restarted module failed; see {name}.log")
    return read_json(target)


def probe_spec_update(conn, version: str, project: Path, runtime: Path, report: Path) -> dict:
    path = project / "specs/orders.json"
    original = path.read_bytes()
    updated = read_json(path)
    updated["revision"] = "v2-probe"
    requirement = next(x for x in updated["requirements"] if x["id"] == "ORD-003")
    requirement["text"] = (
        "规格更新探针：金额下限改为严格大于 order.total > min_total，与 status 按 AND 组合。"
    )
    try:
        write_json(path, updated)
        write_json(report / "spec-update-input.json", updated)
        ingest(conn, project)
        context = asyncio.run(prepare(conn, version, project, runtime, TASK, TASK_ID))
        write_json(report / "context-spec-updated.json", context)
        actual = next(s for s in context["sources"] if s["source_id"] == "ORD-003")
        assert actual["revision"] == "v2-probe"
        assert actual["content"] == requirement["text"]
        assert not context["resumed_state"]["validation_current"]
        assert all(s["source_id"] != "ORD-000" for s in context["sources"])
        return {
            "passed": True,
            "source_id": "ORD-003",
            "revision": "v2-probe",
            "old_validation_invalidated": True,
            "scope": "retrieval probe only; implementation and final tests use restored v1",
        }
    finally:
        path.write_bytes(original)
        ingest(conn, project)


def verify(mode: str) -> Path:
    if mode == "live" and not os.getenv("MINIMAX_API_KEY", "").strip():
        raise ValueError("尚未配置 MiniMax。先运行 python configure.py；可先用 --reference 验证本地功能。")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    report = ROOT / "reports" / "runs" / run_id
    report.mkdir(parents=True, exist_ok=False)
    project = report / "project"
    runtime = ROOT / ".runtime" / run_id
    print(f"验证模式：{mode}；报告目录：{report}", flush=True)
    shutil.copytree(ROOT / "demo", project, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    shutil.copyfile(ROOT / "fixtures/before/orders.py", project / "business/orders.py")
    before = (project / "business/orders.py").read_text(encoding="utf-8")
    (report / "before.py").write_text(before, encoding="utf-8")
    unchanged_tests = tests_hashes(project)
    write_json(report / "test-manifest-before.json", unchanged_tests)
    write_json(
        report / "environment.json",
        {
            "python": platform.python_version(),
            "system": platform.platform(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("powercontext", "pylibseekdb", "PyMySQL", "pytest", "python-dotenv")
            },
            "mode": mode,
            "started": utc_now(),
        },
    )
    print("1/7 运行修改前的目标功能测试和原有回归测试", flush=True)
    before_feature = run_tests(project, report, "before-feature", "tests/test_feature.py")
    before_regression = run_tests(project, report, "before-regression", "tests/test_regression.py")
    assert before_feature["exit_code"] == 1 and before_feature["failures"] > 0
    assert before_regression["exit_code"] == 0
    print("2/7 导入工程资料，使用真实 seekdb 查询并构造上下文", flush=True)
    assert asyncio.run(load(runtime, TASK_ID)) is None
    with database(runtime) as (conn, version):
        write_json(report / "database-import.json", {"database_version": version, **ingest(conn, project)})
        context = asyncio.run(prepare(conn, version, project, runtime, TASK, TASK_ID))
        write_json(report / "context-before.json", context)
        expected = set(read_json(project / "task.json")["required_source_ids"])
        assert expected <= {item["source_id"] for item in context["sources"]}
        assert context["resumed_state"] is None
        assert all(item["source_id"] != "ORD-000" for item in context["sources"])
    initial = TaskState(
        task=TASK,
        done=["导入规格、业务代码与测试", "确认旧代码功能失败且原有回归通过"],
        todo=["使用上下文完成金额下限筛选", "运行相同目标测试与回归测试"],
        validation={"before_feature": before_feature, "before_regression": before_regression},
        source_fingerprint=project_fingerprint(project),
    )
    written = asyncio.run(save(runtime, TASK_ID, initial))
    write_json(report / "powercontext-write.json", written)
    print("3/7 关闭模块，在全新进程读取并使用 PowerContext 状态", flush=True)
    resumed = restarted_context(project, runtime, report, "context-resumed")
    assert resumed["resumed_state"]["content"] == initial.model_dump(mode="json")
    assert resumed["resumed_state"]["validation_current"]
    print("4/7 修改代码并保存代码快照与差异", flush=True)
    if mode == "live":
        code = generate(resumed, report)
        origin = "actual-minimax-api"
    else:
        code = validate_candidate((ROOT / "demo/business/orders.py").read_text(encoding="utf-8"))
        origin = "local-delivered-snapshot; MiniMax not called in this run"
        write_json(report / "reference-mode.json", {"origin": origin, "minimax_executed": False})
    assert code != before
    (project / "business/orders.py").write_text(code, encoding="utf-8")
    (report / "after.py").write_text(code, encoding="utf-8")
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            code.splitlines(keepends=True),
            fromfile="a/business/orders.py",
            tofile="b/business/orders.py",
        )
    )
    (report / "code.diff").write_text(patch, encoding="utf-8")
    print("5/7 运行相同测试；确认测试内容未被改变", flush=True)
    after_feature = run_tests(project, report, "after-feature", "tests/test_feature.py")
    after_regression = run_tests(project, report, "after-regression", "tests/test_regression.py")
    assert after_feature["exit_code"] == after_regression["exit_code"] == 0
    assert unchanged_tests == tests_hashes(project)
    write_json(report / "test-manifest-after.json", tests_hashes(project))
    print("6/7 验证重新导入规格生效，以及代码变更使旧验证失效", flush=True)
    with database(runtime) as (conn, version):
        ingest(conn, project)
        changed = asyncio.run(prepare(conn, version, project, runtime, TASK, TASK_ID))
        assert not changed["resumed_state"]["validation_current"]
        write_json(report / "context-code-changed.json", changed)
        spec_probe = probe_spec_update(conn, version, project, runtime, report)
    completed = TaskState(
        task=TASK,
        done=initial.done + ["完成代码修改", "目标与回归测试全部通过"],
        todo=[],
        validation={"feature": after_feature, "regression": after_regression},
        source_fingerprint=project_fingerprint(project),
    )
    write_json(report / "powercontext-completed.json", asyncio.run(save(runtime, TASK_ID, completed)))
    print("7/7 再次重启，确认已通过的验证和任务完成状态可复用", flush=True)
    final = restarted_context(project, runtime, report, "context-completed-resumed")
    assert final["resumed_state"]["content"]["todo"] == []
    assert final["resumed_state"]["validation_current"]
    summary = {
        "mode": mode,
        "finished": utc_now(),
        "code_origin": origin,
        "minimax_executed": mode == "live",
        "ready_for_submission": mode == "live",
        "checks": {
            "runnable": True,
            "minimax_actual": mode == "live",
            "context_correct": True,
            "database_actual": True,
            "state_reuse_after_restart": True,
            "code_change_effective": True,
        },
        "before": {"feature": before_feature, "regression": before_regression},
        "after": {"feature": after_feature, "regression": after_regression},
        "spec_update": spec_probe,
        "tests_unchanged": True,
        "after_source_fingerprint": project_fingerprint(project),
        "after_code_sha256": digest(code.encode()),
    }
    write_json(report / "summary.json", summary)
    print(
        json.dumps(
            {
                "report": str(report),
                "mode": mode,
                "ready_for_submission": mode == "live",
                "before_feature_failures": before_feature["failures"],
                "after_passed": after_feature["tests"] + after_regression["tests"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return report


def main():
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live", action="store_true", help="实际调用 MiniMax，生成可提交证据")
    modes.add_argument("--reference", action="store_true", help="本地参考实现验证，不计为 MiniMax 实际参与")
    args = parser.parse_args()
    try:
        verify("live" if args.live else "reference")
    except Exception as error:
        print(f"验证未完成：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
