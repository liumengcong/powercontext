#!/bin/zsh
set -e
cd "${0:A:h}"
if ! curl --http1.1 --fail --silent --connect-timeout 3 --max-time 5 http://127.0.0.1:8787/healthz >/dev/null; then
  print '请先双击“一键启动_Mac.command”，保持服务窗口开启。'
  read 'REPLY?按回车结束'
  exit 1
fi
if command -v cloudflared >/dev/null 2>&1; then
  TUNNEL_BIN="$(command -v cloudflared)"
elif [[ -x "$PWD/.tools/cloudflared" ]] && "$PWD/.tools/cloudflared" --version >/dev/null 2>&1; then
  TUNNEL_BIN="$PWD/.tools/cloudflared"
else
  ARCH="$(uname -m)"
  if [[ "$ARCH" == "arm64" ]]; then
    ASSET_ID='739392999'
    EXPECTED_SHA='8c2f90277ef06a6080bb40b964d33f09f1188102f8798f2e344174b6cf41accf'
  else
    ARCH='amd64'
    ASSET_ID='739393352'
    EXPECTED_SHA='8a59d51120cebbe902f0a8d6e1b7a7fd4f9a5bb4dcb3e0f05872483e75e7db6b'
  fi
  mkdir -p .tools
  ARCHIVE="$PWD/.tools/cloudflared-2026.11.0-${ARCH}.tgz.part"
  print '下载官方 cloudflared 2026.11.0；使用 HTTP/1.1 避开此次 HTTP/2 下载错误。'
  print '支持断点续传；每次连接有超时限制，可按 Control+C 取消，稍后运行同一命令继续。'
  DOWNLOAD_OK=0
  for DOWNLOAD_URL in "https://api.github.com/repos/cloudflare/cloudflared/releases/assets/${ASSET_ID}" "https://github.com/cloudflare/cloudflared/releases/download/2026.11.0/cloudflared-darwin-${ARCH}.tgz"; do
    if curl --http1.1 --fail --location --show-error --connect-timeout 12 --max-time 120 --speed-limit 1024 --speed-time 30 --continue-at - -H 'Accept: application/octet-stream' "$DOWNLOAD_URL" -o "$ARCHIVE"; then
      ACTUAL_SHA="$(shasum -a 256 "$ARCHIVE" | cut -d ' ' -f 1)"
      if [[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]]; then
        DOWNLOAD_OK=1
        break
      fi
      print '安装包校验未通过，丢弃本次不完整文件后改用另一官方入口。'
      rm -f "$ARCHIVE"
    else
      print '这个下载入口暂时失败，尝试另一个官方入口。'
    fi
  done
  if [[ "$DOWNLOAD_OK" != 1 ]]; then
    print '尚未下载完整，未安装或执行任何不完整文件。请检查网络后重新运行同一命令。'
    exit 1
  fi
  tar -xzf "$ARCHIVE" -C .tools cloudflared
  chmod +x .tools/cloudflared
  .tools/cloudflared --version
  TUNNEL_BIN="$PWD/.tools/cloudflared"
fi
print '接下来终端会显示 https://某个随机名字.trycloudflare.com。'
print '将整个 HTTPS 地址填入 Dify 的 BACKEND_BASE_URL；此窗口必须保持开启。'
print '临时地址只适合你自己联调；正式交付请按运行说明部署长期 HTTPS 服务。'
if [[ -x /usr/bin/caffeinate ]]; then
  print '联调期间防止电脑自动空闲休眠；关闭本窗口后恢复。请保持开盖、联网，建议接通电源。'
  exec /usr/bin/caffeinate -i "$TUNNEL_BIN" tunnel --protocol auto --url http://127.0.0.1:8787
fi
exec "$TUNNEL_BIN" tunnel --protocol auto --url http://127.0.0.1:8787
