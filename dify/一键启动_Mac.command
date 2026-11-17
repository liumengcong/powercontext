#!/bin/zsh
set -e
cd "${0:A:h}"
PYTHON=""
for candidate in "$PWD/.tools/python/bin/python3.12" python3.12 python3.11 python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,14)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  print '需要先安装 Python 3.12：https://www.python.org/downloads/macos/'
  print '安装完成后重新双击本文件。'
  read 'REPLY?按回车结束'
  exit 1
fi
[[ -x .venv/bin/python ]] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install -c requirements.lock -r requirements.txt
.venv/bin/python scripts/setup.py
print '服务启动后请保持此窗口开启；停止服务按 Control+C。'
print '本机检查地址：http://127.0.0.1:8787/healthz'
print 'Dify Cloud 需要公网 HTTPS 地址，下一步见《先看这里_Cloud导入与提交.md》。'
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8787 --workers 1 --env-file .env
