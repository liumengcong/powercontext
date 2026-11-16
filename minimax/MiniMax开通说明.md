# MiniMax 开通与本地配置

核对日期：2026 年 11 月 16 日。赛题要求真实使用 MiniMax 参与代码修改，安装 SDK 或使用其他模型不能替代。

## 先领取赛事权益

题面说明参赛选手可获赠 1 个月 MiniMax Token Plan，但该题面没有公开具体领取表单。先在赛事通知群/助教处询问 MiniMax 权益领取方式和所需参赛身份，领取成功后在本人账号检查可用资源。不要把“已有 Key”当作“已有额度”。

如果未能领取赛事权益，是否购买按量资源或 M Plan 由你自行决定；本项目不替你购买或充值。

## 注册并取得 Key

1. 打开 MiniMax 官方前置准备说明：`https://platform.minimax.cn/docs/guides/quickstart-preparation`，按页面完成注册/登录。
2. 按账号资源选择 Key：按量付费使用“接口密钥”中的 API Key；M Plan 使用“订阅管理”中的订阅 Key。具体位置以控制台页面为准。
3. 确认账号可使用文本模型；Key 即使存在，没有有效资源也可能调用失败。
4. 在本项目 `minimax/` 目录执行 `.venv/bin/python complete.py`，按提示在本地粘贴 Key 并回车。输入不显示，正常。程序会把 Key 保存到 `.env`，不会把它写入提交记录。

不用把 Key 发到聊天、代码或公开仓库。`.env` 已列入 `.gitignore`；示例文件 `.env.example` 只含空值。

## 默认接口配置

本项目核对后的默认配置：

```text
MINIMAX_BASE_URL=https://api.minimax.cn/v1
MINIMAX_MODEL=MiniMax-M3.1-Flash
```

来源为官方 `https://platform.minimax.cn/docs/api-reference/text-openai-api`。可在本地 `.env` 调整成本人账号可用的官方模型和区域端点；程序只向列出的 MiniMax 官方 HTTPS 域名发送 Key。

官方模型列表说明：M2.5/M2.7 系列已从 2026 年 10 月 19 日停止向新用户开放，并安排 2026 年 11 月 18 日正式下线；官方当前文档列出 M3.1-Flash 和 M3。本项目使用 M3.1-Flash，避免新账号沿用历史示例造成调用失败。最终以本人控制台授权与接口响应为准。

## 运行结果与问题定位

- 成功：生成 `reports/evidence/`，`reports/submission-status.json` 显示 `materials_complete=true`；包含真实响应 ID、生成代码、差异和所有测试记录。
- 401/403：检查 Key 类型、账号区域、模型权限和资源状态。
- 429：检查额度、并发和速率限制；程序不自动重试。
- 模型返回内容或测试不通过：查看该次 `reports/runs/<编号>/` 的记录；不会自动归档成通过状态。
- 没有任何真实 API 记录：不能满足该题“MiniMax 实际参与”验收，不要把参考日志当作这项证据。

官方资料：

- `https://platform.minimax.cn/docs/guides/quickstart-preparation`
- `https://platform.minimax.cn/docs/m-plan/intro`
- `https://platform.minimax.cn/docs/api-reference/text-openai-api`
- `https://platform.minimax.cn/docs/faq/models`
- 赛事题面：`https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qlqnoryzrs7gtqbw`
