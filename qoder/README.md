# 研习台 StudyFlow

作品网站：<https://studyflow-uyxsdatsqos.qoder.zone> · [评审材料入口](docs/提交材料.md)

让下一次学习，从上次的进展继续。

本地单用户的学习记忆台：把笔记、偏好、薄弱点、进度和待办**显式**保存进
[PowerContext](https://github.com/oceanbase/powercontext) 开源组件，重新打开应用时从
PowerContext 读回，生成有出处的继续学习清单与复盘初稿。

- 中文名：研习台　英文名：StudyFlow
- 核心记忆持久化与跨会话召回：**真实使用 `powercontext[builtin]==1.5.1`**，不是 localStorage、自写 SQLite 表或假接口。
- 演示数据是合成资料（`sample-data.json`），不代表真实用户或真实学习成果。
- 没有使用人数、提效比例等效果数字：这些没有被记录，就不能被生成。

---

## 1. 技术路线（与赛事要求的对应关系）

| 要求 | 本项目实际情况 |
| --- | --- |
| 必须真实使用 PowerContext 组件 | 记忆正文、类别、状态、检索、遗忘全部走 PowerContext 公开 Python API；`data/powercontext.db` 是 PowerContext 自己建表与写的数据文件 |
| 允许 builtin runtime 进程内使用 | 采用 `open_builtin_runtime(...)` + SQLite 持久化（`runtime_mode = builtin-in-process (SQLite)`） |
| 不声称独立接入 seekdb | 未接入 seekdb，README 与界面都不做该声称 |
| 锁定实际可安装版本 | `requirements.txt` 锁 `powercontext[builtin]==1.5.1`；接口按**已安装 1.5.1 包**实测，不照抄主分支 1.5.2 文档 |
| 单进程 | 必须单进程 uvicorn（写锁是进程内 `asyncio.Lock`），默认只监听 `127.0.0.1:8712` |

## 2. 干净环境安装与启动

要求 Python 3.11 或 3.12（本轮实测 Python 3.12）。

```bash
cd qoder
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt      # 或 -r requirements.lock.txt 完全复现实测环境
cp .env.example .env                            # 可选：改端口/数据目录；默认值已可用
./快速开始.sh                                   # 等价于 python -m backend.app
```

打开 <http://127.0.0.1:8712/> ，首次进入点左侧**导入演示数据**。

- 端口：`STUDYFLOW_PORT`，默认 `8712`；绑定地址：`STUDYFLOW_HOST`，默认 `127.0.0.1`。
- 不用脚本时：`python -m backend.app`，或 `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8712`。
- 不要加 `--workers >1`：并发保护只在单进程内成立。

### 数据位置

| 文件 | 作用 | 是否提交 |
| --- | --- | --- |
| `data/powercontext.db` | PowerContext builtin runtime 的 SQLite 数据（记忆、scope、全文索引） | 否（`.gitignore` 已排除） |
| `data/powercontext.scheduler.db` | PowerContext 调度器文件（本项目关闭了定时任务） | 否 |
| `data/workspaces.json` | 界面选择器状态：本机打开过哪些工作区名、最后一次是哪个 | 否 |
| `data/import-map.json` | 演示数据导入去重映射（`key → artifact_id`） | 否 |
| `data/validation-log.jsonl` | 应用对 PowerContext 的真实操作记录（不含凭据） | 否 |

`data/` 里没有任何密钥；工作区→scope 的映射由 PowerContext 以 Scope external reference 保存，
删掉 `workspaces.json` 不会丢记忆。

## 3. 页面与流程

| 页面 | 行为 | 事实来源 |
| --- | --- | --- |
| 总览 | 本次要继续的任务、最近学习记录、仍待解决的薄弱点；空状态给“导入演示数据”入口 | PowerContext 读回的 active 记忆 |
| 记忆库 | 新建笔记/偏好/薄弱点/进度/待办；按状态与类别筛选；卡片显示原文、artifact_id、revision、类别与“查看出处” | PowerContext atomic-memory |
| 继续学习 | 已完成 / 当前阻塞 / 下一步 / 参考记忆；偏好真实影响建议（25 分钟 ⇒ 最多 2 项，并写明规则） | 每次请求都重新读 PowerContext |
| 检索 | 调用 PowerContext 全文检索；命中显示原文与精确引用；无结果就显示无结果 | `atomic_memory.search` |
| 待办 | 创建、标记完成、恢复；状态以追加事件持久化；旧版本号会被拒绝 | `kind=task` + `kind=task_status` |
| 复盘导出 | 生成 UTF-8 Markdown 初稿，含引用清单与“需要本人核对” | 同上 |
| 验证记录 | 列出真实写入/读回/检索/状态变更操作 | `data/validation-log.jsonl` |

顶部右上角的连接状态来自 `/api/status` 的真实探测：PowerContext 断开时页面显示“无法读取或写入”，
所有读写接口返回 503，不会继续显示成功。

## 4. PowerContext 调用点

全部集中在 `backend/store.py`（其余模块只调用这个适配器）：

| 能力 | 调用 | 位置 |
| --- | --- | --- |
| 启动/关闭 runtime | `open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=...)), scheduler_path=..., dispatch_processing=False)` | `store.py:194` |
| 工作区→scope | `runtime.scopes.list()` / `runtime.scopes.create(ScopeDraft(..., external_references=(ScopeExternalReference(kind="studyflow-workspace", value=name),), idempotency_key=...))` | `store.py:225,244,249` |
| 写入记忆 | `runtime.atomic_memory.for_scope(scope).create([AtomicMemoryContent(kind, text, description)])` | `store.py:280,284` |
| 分页读取 | `...list(states=, kind=, limit=, cursor=)`（`list_all()` 沿 cursor 读完，用于核心推导） | `store.py:300,326` |
| 单条读取/状态 | `...get(artifact_id, revision=)` / `...state(artifact_id)` | `store.py:337,345` |
| 遗忘（CAS） | `...forget(artifact_id, expected_revision=, expected_state_version=)` | `store.py:357` |
| 全文检索 | `...search(query, mode="text", limit=, kind=)` | `store.py:386` |
| 能力探测 | `runtime.capabilities()` | `store.py:421` |

## 5. 状态模型（为什么是追加事件）

1.5.1 的 `atomic-memory` **没有公开的正文改写（revise）入口**。因此待办状态这样实现：

- 任务正文是一条 `kind=task` 记忆，创建后不再修改，`revision` 恒为 1；
- 每次“标记完成/恢复待办”追加一条 `kind=task_status` 事件记忆，正文是带模式版本的 JSON：

```json
{"schema":"studyflow.task-status.v1","task_artifact_id":"art_…","status":"done",
 "seq":6,"recorded_at":"2026-11-16T19:28:31+08:00",
 "summary":"待办「整理事务隔离级别对照表…」已完成",
 "note":"应用层追加事件，不修改任务正文 revision"}
```

- 取最新状态：按 `seq`（其次 `recorded_at`、`artifact_id`）排序取最大，**读取时沿 cursor 读完全部事件**
  （`store.list_all`）。只读前 500 条会在事件超过 500 时复活旧状态，所以核心推导不允许截断；
  列表接口 `GET /api/memories` 可以按 `limit` 截断，但会返回 `has_more` 与 `list_note` 明示；
- JSON 包装里保留中文 `summary`，PowerContext 的词法投影仍能命中中文词；
- 界面与文档都不声称“内容 revision 已更新”。

### 并发边界

- `POST /api/tasks/{id}/status` 的“读任务 → 读全部状态事件 → 比较 `expected_seq` → 追加事件”
  在**同一把进程内 `asyncio.Lock`** 中原子完成（`backend/app.py` 的 `StudyFlowService.change_task_status`）。
  两个携带同一 `expected_seq` 的并发请求只有一个能写入，另一个收到 409。
- 这把锁只在单进程内成立：跨进程/多 worker 不提供保证，所以本项目要求单进程运行。
- 遗忘走 PowerContext 真正的 compare-and-set（`expected_revision` + `expected_state_version`），
  版本过期返回 409，不会静默覆盖。

## 6. 检索行为（中文实测）

- 检索走 PowerContext 的 SQLite FTS5 全文索引 + BM25，`mode="text"`，返回真实 `artifact_ref` 与命中通道；
- 未配置任何 embedding 模型：`/api/search` 的 `diagnostics.embedding_calls` 实测恒为 `0`，
  因此**只宣称全文检索，不宣称语义检索**；
- 中文可命中：查询“幻读”在演示 scope 实测返回演示笔记与薄弱点（`matched_by=["text"]`），
  由 PowerContext 的词法投影（含随包安装的 jieba）完成分词；
- 查不到就返回空列表并显示“应用不会编造结果”，不会生成答案。

## 7. 测试与验收演练

```bash
python -m pytest tests/                    # 20 项，含真实 PowerContext 集成测试
python scripts/acceptance_drill.py         # 端到端验收演练（真实进程重启）
```

- `tests/test_powercontext_real.py`：真实 runtime、真实 SQLite 文件，覆盖写入→停止→重开→读回、
  scope 隔离、中文全文检索与空结果、`forget` 的 CAS 冲突、状态事件顺序。
- `tests/test_api.py`：接口层（含断开演练、导入幂等、偏好影响建议、并发前的版本冲突、输入安全、
  工作区隔离、UTF-8 导出、重启读回）。
- `tests/test_concurrency_and_consistency.py`：两个真实并发状态写入只成功一次；
  520 条状态事件不被截断；已遗忘的偏好不再影响推导。
- `scripts/acceptance_drill.py` 会真实启动后端进程、SIGTERM 杀掉再重启两次，逐项执行
  `docs/验收样例.md` 第 1–6 节，把结果写入 `docs/evidence/<时间戳>/evidence.json`（含服务器日志与导出的 Markdown）。
  失败也会写入并以非零退出。第 7 节（Qoder Sites 公开访问）必须人工在浏览器完成，脚本不做该声称。

实测结果与失败记录见 `docs/验证结果.md`。

## 8. 功能边界（明确不做什么）

- 本地单用户工具：scope 只是**数据选取边界**，不是用户认证，不支持公开多租户。
- 不接入 seekdb，不宣称独立部署的 PowerContext 服务。
- 不调用任何生成式模型：继续学习清单和复盘初稿由规则整理生成（`engine = rule-based-v1`），界面与导出都注明。
- 不生成没有记录过的内容：使用时长、论文数量、提效比例等一律不出现在导出里。
- 演示数据是合成资料；`docs/试用记录表.md` 是空白表，等真实试用后填写。
- 站点 `site/` 只做展示，不承载后端；互动演示必须本地运行本仓库代码。

## 9. 故障排查

| 现象 | 处理 |
| --- | --- |
| `Address already in use` | 换端口：`STUDYFLOW_PORT=8720 python -m backend.app` |
| 顶部显示“未连接”、接口 503 | 看 `/api/status` 的 `last_error`；确认 `data/powercontext.db` 可写、没有其他进程正占用同一数据文件（本工具按单进程设计） |
| `ModuleNotFoundError: powercontext` | 依赖没装进当前解释器：`python -m pip install -r requirements.txt`，或启动时传解释器路径 `./快速开始.sh /path/to/venv/bin/python` |
| 检索中文无结果 | 先确认该词确实写在某条 active 记忆里；这是全文检索，不是语义检索，近义改写不会命中 |
| 待办状态更新返回 409 | 正常保护：界面会刷新到服务端最新事件；说明有另一次更新先写入了事件 |
| `database is locked` | 不要同时跑两个后端进程指向同一 `data/`；停掉多余进程后重试 |
| 想验证断开行为 | `STUDYFLOW_ALLOW_DESTRUCTIVE_DEV=1 python -m backend.app`，再 `curl -X POST localhost:8712/api/dev/disconnect`；默认不开启该端点 |

## 10. 目录结构

```
qoder/
├── backend/         # FastAPI 应用：config / store(PowerContext 适配) / domain(规则) / app(路由)
├── frontend/        # 原生 HTML/CSS/JS 界面（无构建步骤）
├── tests/           # pytest：真实集成 + 接口 + 并发一致性
├── scripts/         # acceptance_drill.py 验收演练
├── docs/            # 开发记录、验证结果、验收样例、证据目录、讲稿与试用表
├── site/            # 项目展示站点（静态，Qoder Sites 用）
├── data/            # 运行时数据（已 gitignore，不提交）
├── requirements.txt / requirements.lock.txt
├── .env.example / .gitignore / LICENSE / 快速开始.sh
└── sample-data.json / START_IN_QODER.md（需求稿）
```

### 展示站本地预览

`site/dist/` 是纯静态页面（截图来自本机真实界面），不承载任何后端：

```bash
cd site && python3 -m http.server 4173 --bind 127.0.0.1 --directory dist
# http://127.0.0.1:4173/
```

已通过 Qoder Sites 发布：<https://studyflow-uyxsdatsqos.qoder.zone>，任何人可访问，已用 Chrome 无痕窗口与无认证 HTTP 请求核对。发布使用 `webDirectory: "site/dist"`（相对 `qoder/`）。完整应用仍在本机运行。

开发记录、账号标识、CLI 会话导出及真实验证证据见 `docs/开发记录.md`、`docs/验证结果.md` 与 `docs/evidence/`。

## 11. 许可与来源

- 本项目以 Apache License 2.0 发布，见 `LICENSE`；与上游 PowerContext 许可一致，详见 `docs/开源来源与许可.md`。
- 基础仓库：`oceanbase/powercontext` 分支 `2026-competition-ecosystem`，基础提交 `d647275c308e09b7b623f322a26f5c4e85734fb0`。
- 源码入口（本项目提交位置）：<https://github.com/liumengcong/powercontext/tree/studyflow-qoder-20261116/qoder>
- 赛题：<https://open.oceanbase.com/train/TopicDetails?questionId=600078&subQesitonId=800515>
