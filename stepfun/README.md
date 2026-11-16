# 知屿：浏览器端向量化与 seekdb 混合检索

2026 OceanBase 数据库大赛 / 阶跃星辰题目（800510，20 分）。
本项目基于 `oceanbase/powercontext` 的 `2026-competition-ecosystem` 分支，所有应用资产位于 `stepfun/`。

## 当前验证状态（2026-11-16）

- TypeScript 检查、生产构建：通过。
- 5 项逻辑测试：通过（Unicode 分块、质量指标去重、中位数、稳定哈希、数据库会话串行队列）。
- **真实嵌入式 seekdb 1.4.0** 的 6 项基础设施测试：通过（包含全文/混合检索可见性、去重、隔离、并发和重启恢复）。这些测试仅使用合成向量验证数据库链路，**不属于端侧推理或检索质量实测**。
- HTTP 应用、模型清单、ORT 运行时资源：返回 200；数据库健康检查通过。
- FP32 / INT8 模型文件：已下载并记录 SHA256，ONNX 体积分别为 94,851,877 / 24,010,842 字节，压缩比 **3.9504**；共享配置与 tokenizer 共 549,873 字节，另列不混入 ONNX 比较。
- **尚未在 Chrome / Edge 实际运行文档与查询 embedding、WebGPU/WASM 切换和浏览器基准。** 当前执行环境关闭了浏览器操作。不能据此声称端到端验收通过、质量无损或已获得比赛分数。完整评测入口已经实现，运行方法见下。

## 启动（推荐：嵌入式，无需 Docker）

要求：Node.js 22+、Chrome / Edge 当前稳定版。模型加载和推理需要内存；FP32 测试明显高于 INT8 的使用量。首次启动嵌入式 seekdb 会从 OceanBase 官方地址下载原生运行库。

在本项目 `stepfun` 文件夹打开终端：

```sh
npm ci
npm run models
npm run build
npm start
```

打开 `http://127.0.0.1:3000`。等状态栏显示数据库已连接，然后点击「加载本地模型」。Mac 也可双击 `start.command`；Windows 可在 PowerShell 执行 `./start.ps1`。

交付包如果附带了模型文件，`npm run models` 会校验本地文件并直接复用。源码仓库不提交大模型；其他机器可按 `model.lock.json` 下载相同不可变版本。默认先使用 Hugging Face，访问失败后尝试镜像。也可以显式设置：

```sh
MODEL_ENDPOINT=https://hf-mirror.com npm run models
```

Windows PowerShell 对应 `$env:MODEL_ENDPOINT='https://hf-mirror.com'; npm run models`。

如数据库启动失败，页面显示实际错误，请查看终端。没有回放数据、内存检索或其他数据库兜底。

## 演示顺序

1. 保持知识库 `personal`，精度 INT8，后端「自动 · 优先 WebGPU」，加载模型。
2. 点击「示例语料」或导入自己的 TXT/MD 文件；等待向量化、写入和索引刷新完成。
3. 查询「浏览器不支持 WebGPU 怎么办？」。结果显示原文、来源、片段号和分阶段计时。
4. 重复导入相同文档，验证计数不变；新增文档后立即查询；将知识库改为 `second` 验证隔离，再切回 `personal`。
5. 停止并重新启动应用，加载同一配置模型后验证资料仍存在。
6. 使用无 WebGPU 的浏览器环境验证自动降级；手动 WASM 仅覆盖执行路径，不能替代自动降级实测。

内容以稳定哈希生成来源与片段 ID。相同来源与内容重复导入采用 upsert；内容变更保存新版本，不会自动删除旧版本。精度或模型版本改变后进入独立集合，需要重新导入，避免向量空间混用。

## 一键评测与提交结果

点击「运行完整评测」，应用会执行 4 组配置，每组重复 3 轮：

|配置|精度|请求后端|文档批大小|用于对比|
|---|---|---|---|---|
|fp32_wasm_b1|FP32|WASM|1|量化前基线|
|q8_wasm_b1|INT8|WASM|1|仅改变精度|
|q8_wasm_b8|INT8|WASM|8|仅改变批大小|
|q8_webgpu_b8|INT8|WebGPU|8|仅改变后端|

语料：`data/eval.json`，32 篇人工中文文档、16 个查询、人工相关性等级。仅用于小规模工程验收，不代表通用中文检索质量。评测不缓存查询向量；文档按长度组批并恢复原顺序。每轮使用同一模型/精度的独立实例，记录加载与预热。操作日志中的首次/后续交互耗时也可以单独导出。

模型若不能执行 WebGPU INT8，会自动降级 WASM，报告保留原因和实际后端；汇总时不会把降级样本计入 GPU 成绩。此配置并不保证所有设备都支持；如果评审环境仅能用 WASM，必须据实报告。

评测报告保存到 `reports/benchmark-时间.json`，也可点击导出。生成中位数、质量对比和 CSV：

```sh
npm run report -- reports/benchmark-具体时间.json
```

检查生成的 `.summary.json`、`.csv`、`.md`，把真实表格补入 `docs/TECHNICAL_NOTE.md`，并附原始报告；如果模型或代码改动，重跑相应测试。补记设备准确型号、系统版本、浏览器版本和显卡。**不要把未测字段改成估算数值。** 本地模型准备耗时单列于 `model.lock.json`，它包含下载、读写与校验，不能当作纯网络下载速度。浏览器 load 包含本地文件/缓存读取、session 创建和首次预热；benchmark e2e 计至评测结果文本显示，交互卡片耗时见操作日志。

## 数据库结构与检索

- seekdb-js `1.4.0`，集合名为 `edge_{知识库}_{模型配置指纹前32位}`。
- 原生集合保存 `_id`、`document`、512 维 `embedding`、JSON `metadata`。元数据包括来源 ID、文件名、片段号及完整配置指纹。
- 向量索引：HNSW，cosine；全文索引：二元 ngram，适合中文输入。
- 服务器将浏览器向量传给 `collection.hybridSearch`，全文与 KNN 均由 seekdb 执行，RRF 窗口 50、常数 60。结果不在客户端二路融合。
- `embeddingFunction: null`，没有默认 embedding provider，也没有任何服务器 embedding 路由。
- 每 64 条进行一次 upsert，同一导入请求结束后刷新一次（前端每组至多 256 片段）。刷新完成验证向量索引可见性；全文可见性由真实数据库集成用例验证。
- seekdb-js 混合检索使用会话变量，完整数据库操作串行化，防止并发查询覆盖会话。
- 默认仅绑定回环地址，凭证留在服务器 `.env`。所有原文通过 `textContent` 展示。

## 模型与量化

`Xenova/bge-small-zh-v1.5`，固定 commit `75c43b069aac4d136ba6bc1122f995fedcfd2781`，来源与 SHA256 见 `model.lock.json`。其上游为 `BAAI/bge-small-zh-v1.5`。使用上游已经公开的 INT8 ONNX 文件 `model_quantized.onnx`，**未自行训练或冒称自行量化**；上游量化配置（per-channel、reduce_range、QInt8）归档于 `docs/upstream-quantize-config.json`。

Transformers.js `4.3.2` 在浏览器 Web Worker 内推理，CLS 池化并归一化，查询添加中文检索前缀，文档不加前缀。240 Unicode 字符分块、40 字符重叠。模型 tokenizer 按配置自动截断到其最大长度；输入的字符数与 token 数并不等价。

端侧向量均在 worker 计算，服务器只做数据库操作。模型文件和 ORT 资源由本地服务器静态分发，禁用远程模型加载；没有云端推理 API。

## 测试

```sh
npm test
npm run test:integration
```

集成测试默认嵌入式 seekdb，使用 `smoke_a` / `smoke_b` 独立集合，写入合成向量验证真实数据库行为。它不会伪装为模型质量测试。

## 已有 server / Docker（可选）

使用 `.env.example` 创建 `.env`，设置 `SEEKDB_MODE=server`、host、port、user、password。应用可自动创建业务数据库，连接账户需要相应权限。

也提供 `compose.yaml`，需要安装并启动 Docker；执行 `docker compose up -d`，再使用 server 配置启动应用。Docker 部署文件尚未在本机验证（本机未安装 Docker）。默认嵌入式路径已经用真实 seekdb 测试。

## 官方参考

- 题目：<https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/kw9wfx4bu9pyvat9>
- 官方基线：<https://github.com/oceanbase/powercontext/tree/2026-competition-ecosystem>
- SDK：<https://github.com/oceanbase/seekdb-js>（本应用按 npm 1.4.0 的类型与实现核对）
- 模型：<https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781>
- 提交说明：<https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qs1povt8zbza6hts>
