"""Configure a local key, run the actual model, and collect final submission evidence."""

import os
import subprocess
import sys

from dotenv import load_dotenv

from sdd_context.common import ROOT

load_dotenv(ROOT / ".env", override=False)
if not os.getenv("MINIMAX_API_KEY", "").strip():
    if (ROOT / ".env").exists():
        raise SystemExit(".env 已存在但密钥为空，请在本地编辑后再运行。")
    subprocess.run([sys.executable, str(ROOT / "configure.py")], cwd=ROOT, check=True)
subprocess.run([sys.executable, str(ROOT / "verify.py"), "--live"], cwd=ROOT, check=True)
subprocess.run([sys.executable, str(ROOT / "finalize.py"), "--latest"], cwd=ROOT, check=True)
print("真实 MiniMax 验证和证据归档已完成。按 提交指南.md 推送仓库并提交提测。")
