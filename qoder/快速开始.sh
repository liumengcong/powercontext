#!/usr/bin/env bash
# 研习台 StudyFlow 本地启动脚本（macOS / Linux）
# 用法：./快速开始.sh            使用当前 python3，数据在 ./data
#       ./快速开始.sh /path/to/python   指定已安装 requirements.txt 的解释器
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${1:-python3}"
if [ ! -x "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "找不到可执行的 Python：$PYTHON" >&2
  echo "请先准备 Python 3.11/3.12 环境，例如：python3 -m venv .venv && source .venv/bin/activate" >&2
  exit 1
fi

echo "1/3 检查依赖（powercontext[builtin]==1.5.1 等，见 requirements.txt）"
"$PYTHON" - <<'PY'
import importlib.metadata as md
import sys

need = {"powercontext": "1.5.1", "fastapi": None, "uvicorn": None, "httpx": None, "aiosqlite": None}
missing = []
for name, want in need.items():
    try:
        got = md.version(name)
    except md.PackageNotFoundError:
        missing.append(name)
        continue
    if want and not got.startswith(want):
        print(f"  版本不一致：{name} 当前 {got}，本轮实测为 {want}（可继续，但接口可能有差异）")
if missing:
    print(f"  缺少依赖：{', '.join(missing)}")
    print("  安装：python -m pip install -r requirements.txt")
    sys.exit(1)
print("  依赖就绪")
PY

HOST="${STUDYFLOW_HOST:-127.0.0.1}"
PORT="${STUDYFLOW_PORT:-8712}"
echo "2/3 启动后端（单进程 uvicorn，仅监听 $HOST:$PORT）"
echo "    打开 http://$HOST:$PORT/ ，首次进入可点“导入演示数据”"
echo "3/3 数据位置：${STUDYFLOW_DATA_DIR:-$PWD/data}（PowerContext SQLite 与验证记录都在那里，勿提交到仓库）"
exec "$PYTHON" -m backend.app
