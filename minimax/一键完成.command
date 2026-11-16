#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
task_python=""
for task_candidate in python3.12 python3; do
  if command -v "$task_candidate" >/dev/null 2>&1 && "$task_candidate" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
    task_python="$task_candidate"
    break
  fi
done
if [ -z "$task_python" ]; then
  echo '请先安装 Python 3.12，再重新运行本文件。详见 README.md。'
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  "$task_python" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python complete.py
echo '材料已生成。请按提交指南将代码推送到 GitHub minimax 分支。'
