from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from .common import ROOT, TASK, read_json, write_json
from .context import prepare
from .database import database
from .ingest import ingest
from .state import TaskState, load, save


def main():
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="SDD 工程上下文 CLI")
    parser.add_argument("action", choices=["ingest", "context", "state-read", "state-write"])
    parser.add_argument("--project", type=Path, default=ROOT / "demo")
    parser.add_argument("--runtime", type=Path, default=ROOT / ".runtime" / "manual")
    parser.add_argument("--task-id", default="orders-min-total")
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--budget", type=int, default=40000)
    args = parser.parse_args()
    if args.action == "state-read":
        result = asyncio.run(load(args.runtime.resolve(), args.task_id))
    elif args.action == "state-write":
        if args.state_file is None:
            parser.error("--state-file required")
        result = asyncio.run(
            save(args.runtime.resolve(), args.task_id, TaskState.model_validate(read_json(args.state_file)))
        )
    else:
        with database(args.runtime.resolve()) as (conn, version):
            if args.action == "ingest":
                result = {"database_version": version, **ingest(conn, args.project.resolve())}
            else:
                result = asyncio.run(
                    prepare(
                        conn,
                        version,
                        args.project.resolve(),
                        args.runtime.resolve(),
                        args.task,
                        args.task_id,
                        args.budget,
                    )
                )
    if args.output:
        write_json(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
