> 最新状态与提交清单见 docs/最终提交清单.md。02_ 文件是本次真实Cloud导出；01_ 是初始通用模板。

# 离谱失物招领处

**一句话：把“不可能丢失的东西”寄存在 AI 柜员这里，用三次脑洞选择完成认领；回来时，柜员还记得你和失物的老梗。**

面向 OceanBase 2026 大赛「Just for Fun，用 Dify 做一个好玩的智能应用」。本项目采用 **Dify + PowerContext**，不声明使用 seekdb。

先读 [Cloud 导入与提交教程](先看这里_Cloud导入与提交.md)。完整复现见 [运行说明](docs/运行说明.md)，体验顺序见 [验证样例](docs/验证样例.md)。

## 当前完成状态

| 部分 | 状态 |
| --- | --- |
| 游戏规则、六种事件、三种结局、收藏册、回访 | 已实现 |
| 官方 PowerContext 1.2.0 持久化与玩家 Scope 隔离 | 本地真实运行验证通过 |
| 服务重启恢复、最近请求去重、冲突检测 | 本地真实运行验证通过 |
| 12 项后端/DSL 契约集成检查 | 已通过；见 evidence |
| Dify 正式版及无模型自检版 DSL | 已生成；Python 节点与 HTTP 契约通过本地检查 |
| 在参赛者 Dify Cloud 中导入、绑定模型、发布 | 已完成；2026-11-17发布第1版 |
| 真实 Dify Cloud + LLM + PowerContext 全链路验收 | 已有真实Cloud回访输出；完整API验收及重新导入复现待补 |
| 仓库交付 | 本作品使用私有仓库 liumengcong/powercontext-dify-2026；平台提测另行进行 |

这是一份可继续配置和验证的项目交付，不能把“本地检查通过”写成“已完成官方验收”或保证获得满分。

## 交付目录

```text
dify/
  dsl/00_无模型连通性自检.yml
  dsl/01_离谱失物招领处_AI正式版.yml
  backend/                   游戏规则、HTTP 服务、官方 PowerContext 接入
  docs/                      完整运行说明和验收样例
  scripts/                   初始化、后端实测、真实 Dify API 验收
  tests/                     使用真实 PowerContext 的集成检查
  evidence/                  本次真实本地记录；Cloud 记录须运行后补充
  requirements.txt           直接依赖
  requirements.lock          本次已测的完整依赖版本约束
  Dockerfile / compose.yml   后端容器方案，不包含 Dify 本身
  .env.example               仅占位配置
```

## 技术分工

Dify 负责输入校验、读取记忆、调用聊天模型生成旁白、成功后提交记忆，以及输出准确档案。它是作品正式体验入口和核心编排层。

配套 Python 服务负责固定规则、签名操作票据和对官方 PowerContext SDK 的调用。模型只能写旁白，不能修改得分、选项或持久化状态。

PowerContext 使用官方 `open_builtin_runtime`、`scopes.create`、`memory.for_scope(...).list/remember`、`sources.capture` 和 `records.get_source`。它保存真实玩家输入、当前关卡、历史选择、纪念章、收藏册和旁白回执。底层文件由 PowerContext 的 SQLite Profile 管理，本项目没有自行实现替代的 SQLite/JSON 存储层。

使用的是显式保存与读取的基础记忆；不声明自动 Topic Memory、Embedding、Rerank 或语义检索能力。唯一必需的模型在 Dify 的 AI 柜员节点中配置。

## 比赛基线

开发基础：`oceanbase/powercontext` 的 `2026-competition-ecosystem` 分支，基础提交为：

```text
d647275c308e09b7b623f322a26f5c4e85734fb0
```

**这是基础提交，不是你最终应该提测的提交。** 将本目录加入自己的比赛私有仓库，推送后，复制你自己新增提交的完整 SHA。

## 来源

- [题面详情](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/lhfslihtnnb8yqh6)
- [官方 FAQ](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/gp48z4pp0q3zg9b2)
- [GitHub 提交流程](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qs1povt8zbza6hts)
- [PowerContext 1.2.0 源码](https://github.com/oceanbase/powercontext/tree/powercontext-v1.2.0)
- [Dify 1.9.1 DSL 服务](https://github.com/langgenius/dify/blob/1.9.1/api/services/app_dsl_service.py)
- [Dify HTTP 节点文档](https://docs.dify.ai/en/cloud/use-dify/nodes/http-request)

核对日期：2026-11-17。Cloud 托管版本由平台更新，导入后必须在自己的实际环境运行样例，并将版本和模型配置记录进证据。

本项目源码、玩法文本和 DSL 为本次新编写；参考官方接口和数据结构，没有复制第三方工作流。PowerContext 为 Apache-2.0，保留其包内许可。项目新增代码采用 MIT 许可，见 LICENSE。

## 实际 Cloud 导出

02_ 的 yml 是平台包内 app.yaml，同名 ifpkg 是平台原始导出；无需重复导入。插件 langgenius/openai 1.1.0；账号模型显示名 gpt-6-luna。托管构建版本未显示，不据此推断上游模型身份。复现需重新绑定模型、服务地址及Secret。
