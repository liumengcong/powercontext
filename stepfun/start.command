#!/bin/zsh
set -eu
cd "$(dirname "$0")"
if ! command -v node >/dev/null; then
  echo '请先安装 Node.js 22 LTS，再重新打开本文件。'
  read '?按回车退出'; exit 1
fi
npm ci
npm run models
npm run build
npm start
