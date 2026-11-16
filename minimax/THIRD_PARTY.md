# 开源来源与修改说明

- 基础仓库：`https://github.com/oceanbase/powercontext`，比赛分支 `2026-competition-ecosystem`，基线 commit `d647275c308e09b7b623f322a26f5c4e85734fb0`。本作品只新增 `minimax/` 下内容，不修改其他企业题目目录。
- PowerContext 1.5.1（Apache-2.0）：通过 PyPI 安装，使用其实际 Artifact、ArtifactDraft、ArtifactRepository、SQLiteProfile 和共享表实现任务持久化与修订。未将自行保存的 JSON 冒充 PowerContext。
- pylibseekdb 1.4.0.post2 / OceanBase seekdb：通过 PyPI 官方包安装并启动真实本地数据库，以 PyMySQL 执行参数化 SQL。源码与许可证见官方项目及安装包声明。本作品没有重分发数据库二进制。
- PyMySQL、pytest、python-dotenv 及传递依赖：按锁定清单安装，各自许可证和版权声明保留于原包。
- `sdd_context/`、订单业务、规格、测试、验证和打包脚本为本次任务编写；使用 Codex 辅助完成。参考代码不是 MiniMax 生成记录。完成真实 API 流程后，MiniMax 修改的确切文件和响应 ID 以 `reports/evidence/` 为准。

作品新增源码按根目录附带的 Apache License 2.0 授权。业务样例为人工构造数据，不含外部用户数据。
