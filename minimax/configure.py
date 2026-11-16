"""Store the MiniMax key locally without echoing it or putting it in shell history."""

import getpass
import os

from sdd_context.common import ROOT

path = ROOT / ".env"
if path.exists():
    raise SystemExit(".env 已存在，请在本地编辑该文件；不会覆盖现有配置。")
key = getpass.getpass("在这里粘贴 MiniMax Key（输入不可见，回车保存）：").strip()
if not key or "\n" in key or "\r" in key or "'" in key:
    raise SystemExit("密钥为空或格式无效，未保存。")
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as file:
    file.write(f"MINIMAX_API_KEY='{key}'\nMINIMAX_BASE_URL=https://api.minimax.cn/v1\n")
    file.write("MINIMAX_MODEL=MiniMax-M3.1-Flash\n")
print("已保存到本地 .env，不会打印密钥；该文件已被 Git 忽略。")
