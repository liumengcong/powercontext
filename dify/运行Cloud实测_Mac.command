#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print '请先运行“一键启动_Mac.command”安装依赖。'
  read 'REPLY?按回车结束'
  exit 1
fi
print '运行前：在 .env 填好正式版 DIFY_API_KEY 和正确的 BACKEND_BASE_URL。'
print '本脚本会在你的应用中进行 6 次真实运行，可能消耗模型额度。'
.venv/bin/python scripts/verify_dify.py
read 'REPLY?按回车结束'
