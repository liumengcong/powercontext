# 规有所依：SDD 工程上下文管理系统

作品对应 OceanBase × MiniMax 赛题 `800514`。代码基于官方 PowerContext `2026-competition-ecosystem` 分支，所有作品资产位于 `minimax/`。参赛工作分支为 `minimax`。

模块为开发助手选择当前有效的规格、Python 代码符号与测试来源，使用真实 seekdb SQL 查询结果组装上下文；通过 PowerContext Artifact 持久化任务进展，并在新进程中将读取的进展用于下一次上下文构造。开发助手通过 MiniMax 官方 API 接收上下文，生成订单金额筛选功能的业务代码。

**当前交付状态**：本地参考实现已通过 18 项业务测试和 8 项模块测试。真实 seekdb、PowerContext 持久化与新进程恢复已实测。尚未执行 MiniMax API；没有伪造其响应或使用记录。必须完成下述 live 步骤并归档记录，再提交最终比赛版本。当前记录不代表已获比赛分数。

## 1. 环境与安装

实测环境：macOS ARM64，Python 3.12，PowerContext 1.5.1，pylibseekdb 1.4.0.post2，数据库 `OceanBase seekdb-v1.4.0.0`。精确依赖见 `requirements.lock.txt`。推荐 macOS 15+ ARM64 或 Linux glibc 2.28+ x86_64/ARM64，Python 3.12；Linux 未在本机实测。Windows 未验证。

安装 Python 3.12 后，在仓库的 `minimax/` 目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
```

`pylibseekdb` 从包内启动真实本地 seekdb，无需 Docker、云数据库或模型密钥。数据库文件保存在 `.runtime/`，不会随提交上传。首次启动通常需要数秒；请留出足够磁盘与内存。不要同时运行多个使用相同运行目录的进程。

如使用已有 seekdb/OceanBase MySQL 模式实例，在 `.env` 设置 `SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_USER`、`SEEKDB_PASSWORD`、`SEEKDB_DATABASE`，示例见 `.env.example`。远程必须使用专用测试数据库，运行过程会重建其中的 `sdd_documents` 索引内容；不会访问业务库的订单数据。默认本地模式最容易复现。

## 2. 不需要密钥的完整验证

```bash
.venv/bin/python verify.py --reference
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest demo/tests -q
```

第一条命令为每次运行创建全新项目副本、seekdb 数据目录和 PowerContext 状态，执行：旧代码功能失败与回归通过 → 导入/查询 → 保存状态 → 新进程恢复 → 应用交付的代码快照 → 同一组测试通过 → 规格更新重新导入 → 旧验证失效 → 保存完成状态 → 再次新进程恢复。

命令结束打印报告路径。`reference` **不会调用 MiniMax，不能作为 MiniMax 实际参与的证据**。已实测参考记录见 `reports/reference/`，当前状态见 `reports/submission-status.json`。

## 3. 必须完成的真实 MiniMax 开发流程

先按 `MiniMax开通说明.md` 获得可调用文本模型的 Key 和额度，然后：

```bash
.venv/bin/python complete.py
```

首次运行会提示在本地输入 Key，终端不显示输入内容，保存到仅当前用户可读的 `.env`。命令会从旧代码开始，构造包含数据库来源和恢复状态的上下文，向 MiniMax 请求实际代码修改，然后运行同一组测试。只有全部通过，才将真实响应、前后代码、差异、测试日志、数据库查询和状态恢复记录归档到 `reports/evidence/`，并把 MiniMax 生成的业务实现写入 `demo/business/orders.py`。

也可分步执行：

```bash
.venv/bin/python configure.py
.venv/bin/python verify.py --live
.venv/bin/python finalize.py --latest
```

MiniMax 接口默认 `https://api.minimax.cn/v1/chat/completions`，模型默认 `MiniMax-M3.1-Flash`；可通过 `.env` 调整为账号实际可用的官方端点/模型。记录包括请求内容、响应 ID、返回模型、用量、生成代码和应用哈希，不包含 Authorization 或模型私有思考内容。单次运行调用一次 API；失败时保留记录，由使用者决定是否重新运行，不自动重试计费。

没有 Key 会明确报错，不会换用其他模型或伪造调用。API 通道尚待真实 Key 验证。模型若返回不合法结果、改动签名、修改允许范围以外的内容或功能测试不通过，流程终止，不会生成“材料已完成”状态。

## 4. 上下文模块入口

以下命令均从 `minimax/` 执行，输入输出为真实文件或 JSON：

```bash
.venv/bin/python -m sdd_context ingest --project demo --runtime .runtime/manual --output import.json
.venv/bin/python -m sdd_context context --project demo --runtime .runtime/manual --output context.json
.venv/bin/python -m sdd_context state-write --runtime .runtime/manual --state-file examples/state.json
.venv/bin/python -m sdd_context state-read --runtime .runtime/manual
.venv/bin/python -m sdd_context context --project demo --runtime .runtime/manual --output resumed-context.json
```

`--task` 指定任务文字，默认验证任务；`--task-id` 指定状态身份；`--budget` 为来源正文与元数据预留的字符预算，默认 40000。预算不足会明确失败，不静默丢弃约束；不将字符预算宣称为精确 token 预算。

上下文 JSON 包含 `sources`、`database.sql`/`parameters`/查询返回身份、`resumed_state`、`source_fingerprint`。每条来源包含真实相对路径、符号、行号、规格版本和文件 SHA-256。重新导入后采用新规格；所选文件导入后发生变化会要求重新导入。状态中保存的验证指纹与当前项目不同时，`validation_current=false` 并明确要求重新测试。

## 5. 提交材料

| 材料 | 位置 |
| --- | --- |
| 模块与 Agent 源码 | `sdd_context/` |
| 初始化、验证、真实开发流程 | `verify.py`、`complete.py`、`finalize.py` |
| 任务、有效规格、初始数据、必要来源 | `demo/task.json`、`demo/specs/orders.json`、`demo/tests/conftest.py`、`验证样例.md` |
| 修改前代码 | `fixtures/before/orders.py` |
| 交付的修改后代码与业务测试 | `demo/business/orders.py`、`demo/tests/` |
| 模块测试 | `tests/test_context_module.py` |
| 本机参考验证结果 | `reports/reference/`、`reports/module-tests.*` |
| 真实 MiniMax 与整体验证记录 | 完成 live 后生成的 `reports/evidence/` |
| 技术说明与复用声明 | `技术说明.md`、`THIRD_PARTY.md` |
| 大赛提交操作 | `提交指南.md` |

评审从干净环境按第 1 节安装，执行第 2 节，核对已归档真实 MiniMax 记录；核对 `reports/evidence/after.py` 与 `demo/business/orders.py` 完全一致。无需重新请求模型生成相同代码。若需要重新演示模型流程，可配置自己的 Key 执行第 3 节。

## 6. 约束与已知限制

当前是单用户、单项目 CLI，使用专用索引表；完整导入对该表进行事务替换，查询为参数化 SQL 关键词匹配及应用层重排，适用于本验证样例的小型工程，未进行大规模性能测试。工程适配器支持 JSON 规格和 Python 顶层符号；不解析整个多语言依赖图。输入业务订单的结构视为调用方已校验，不新增原接口未声明的数据清洗规则。

PowerContext 使用官方内置 `ArtifactRepository`、`SQLiteProfile` 和版本表保存自定义 `sdd-task` Artifact；不要求额外配置 PowerContext Server 或第二个生成模型。此选择满足重启后读取并使用任务状态的基础要求；没有宣称恢复完整 Agent 会话。

模型结果仅允许修改一份业务文件，进行语法、签名和基本构造检查，再在生成的项目副本中执行测试。这些检查不是任意不可信代码的完整安全隔离。验证样例不使用外部订单数据。

每次验证自动创建新目录，无需删除上次记录。重置业务代码可复制 `fixtures/before/orders.py` 到自己的演示副本；不要将它覆盖到已归档的提交证据。既有 `reports/evidence/` 不会被自动覆盖，重复归档需先检查并手动移走旧记录。
