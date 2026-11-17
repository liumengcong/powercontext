"""Initialize local-only configuration; never overwrite an existing secret."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if not target.exists():
    template = (root / ".env.example").read_text(encoding="utf-8")
    target.write_text(template.replace("REPLACE_WITH_GENERATED_TOKEN", secrets.token_urlsafe(36)), encoding="utf-8")
    target.chmod(0o600)
    print("已生成 .env。本文件有服务口令，请留在本机，不要上传 GitHub。")
else:
    print("沿用已有 .env，没有覆盖口令。")
print("请打开 .env，将 BACKEND_TOKEN 的值填入 Dify 同名 Secret 环境变量。")
