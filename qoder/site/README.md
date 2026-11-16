# Qoder Sites 展示站点

公开地址：<https://studyflow-uyxsdatsqos.qoder.zone>。2026-11-16 已发布，public，已通过 Chrome 无痕窗口和无认证 HTTP 访问验证。

`dist/index.html` 与 `dist/screenshots/` 为 Qoder 编写的静态作品展示和真实截图。完整应用按项目 README 在本机运行。

本地预览：在 `qoder/` 执行 `python3 -m http.server 4173 --bind 127.0.0.1 --directory site/dist`，再打开 `http://127.0.0.1:4173/`。

发布使用官方 Qoder CN CLI Sites，`projectRoot=qoder/`，`webDirectory=site/dist`，无云后端。生成的 `.qoder.site` 是本机发布入口，不手工修改。发布凭据的公开摘要在 `docs/evidence/site-publication.json`。评审结束前保持站点在线。
