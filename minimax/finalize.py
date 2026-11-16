"""Promote a successful live run into tracked, self-contained submission evidence."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from sdd_context.common import ROOT, digest, read_json, write_json


def finalize(report: Path) -> None:
    report = report.resolve()
    allowed = (ROOT / "reports/runs").resolve()
    if not report.is_relative_to(allowed):
        raise ValueError("Report must be a verify.py run inside reports/runs")
    summary = read_json(report / "summary.json")
    if not (
        summary["mode"] == "live"
        and summary["minimax_executed"]
        and summary["ready_for_submission"]
        and all(summary["checks"].values())
    ):
        raise ValueError("只接受全部通过的真实 MiniMax 运行；reference 记录不能作为最终提交。")
    request = read_json(report / "minimax-request.json")
    response = read_json(report / "minimax-response.json")
    application = read_json(report / "minimax-application.json")
    assert response["http_status"] == 200 and response["finish_reason"] == "stop"
    assert json.loads(request["payload"]["messages"][1]["content"])["resumed_state"]
    assert read_json(report / "test-manifest-before.json") == read_json(report / "test-manifest-after.json")
    after = (report / "after.py").read_bytes()
    assert digest(after) == summary["after_code_sha256"] == application["sha256"]
    evidence = ROOT / "reports/evidence"
    if evidence.exists():
        raise ValueError("reports/evidence 已存在，请先检查并移走旧证据，再生成新的提交证据。")
    shutil.copytree(report, evidence, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (ROOT / "demo/business/orders.py").write_bytes(after)
    manifest = {
        p.relative_to(ROOT).as_posix(): digest(p.read_bytes())
        for p in sorted(evidence.rglob("*"))
        if p.is_file()
    }
    manifest["demo/business/orders.py"] = digest(after)
    write_json(ROOT / "reports/evidence-manifest.json", manifest)
    write_json(
        ROOT / "reports/submission-status.json",
        {
            "materials_complete": True,
            "platform_submitted": False,
            "score_confirmed": False,
            "actual_minimax_model": response.get("model"),
            "minimax_response_id": response.get("id"),
            "code_sha256": digest(after),
            "source_report": report.name,
            "next": "Commit minimax, push to your private repository, submit the resulting branch and commit id",
        },
    )
    print("已生成可提交证据：reports/evidence/，已更新 demo/business/orders.py。")
    print("下一步提交这些文件到 Git，并将完整 Commit ID 填写到大赛平台。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path)
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    if args.latest:
        runs = sorted((ROOT / "reports/runs").glob("*/summary.json"))
        successful_live = [p.parent for p in runs if read_json(p).get("ready_for_submission")]
        if not successful_live:
            parser.error("没有通过的 live 记录，请先运行 python verify.py --live")
        report = successful_live[-1]
    elif args.report:
        report = args.report
    else:
        parser.error("提供运行目录，或使用 --latest")
    try:
        finalize(report)
    except Exception as error:
        parser.exit(1, f"未生成提交材料：{error}\n")


if __name__ == "__main__":
    main()
