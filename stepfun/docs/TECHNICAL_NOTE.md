# 知屿：端侧 AI 推理优化与 seekdb 混合检索

**提交位置：** `stepfun/`；官方基线 `powercontext/2026-competition-ecosystem`，基线 SHA `d647275c308e09b7b623f322a26f5c4e85734fb0`。

**模型与量化。** 使用公开模型 `Xenova/bge-small-zh-v1.5`，固定版本 `75c43b069aac4d136ba6bc1122f995fedcfd2781`（上游 BAAI/BGE，512 维）。复用上游 INT8 ONNX，按上游配置为 per-channel、reduce_range、QInt8；未进行训练。实测同口径 ONNX 文件：FP32 **94,851,877 B**，INT8 **24,010,842 B**，压缩比 **3.9504**，体积减少 **74.69%**。共享 tokenizer 与配置 **549,873 B** 另列。来源、文件 SHA256、大小及准备耗时保存在 `model.lock.json`。

**端侧运行。** Transformers.js 4.3.2 在浏览器 Web Worker 内执行文档与查询向量化，CLS 池化并 L2 归一化，查询增加 BGE 中文检索指令。显式请求 `device: webgpu`、`dtype: q8`；无适配器或初始化/首次预热失败，释放实例并降级到 `wasm`，界面展示实际后端和原因。模型和 ORT 由本地服务器分发，远程模型访问关闭，后端 embedding function 显式为 null，没有云端推理 API。

**seekdb 使用。** seekdb-js 1.4.0 接入真实嵌入式 seekdb 1.4.0；原生集合保存 ID、512 维向量、原文及来源元数据，使用 HNSW/cosine 与 ngram(2) 全文索引。`hybridSearch` 将全文与 KNN 候选交给数据库，RRF 常数 60、窗口 50；客户端仅展示数据库排序。每 64 条 upsert，导入组完成刷新一次并验证可见性；完整数据库操作串行执行，防止 SDK 会话变量在并发请求中被覆盖。

**状态与优化。** 集合名称绑定知识库、模型 SHA、精度及预处理指纹；切换知识库清空旧结果。同内容用稳定 ID 去重，内容变更保留新来源版本。文档按 240 Unicode 字符分块、40 字符重叠，批大小 8，按长度组批减少 padding 并恢复顺序；模型实例复用。基准通过固定精度/后端分别比较量化、批处理和后端，每组 3 轮，记录原始样本及中位数。

**实测环境（2026-11-16）：** MacBook Air（Mac17,3），Apple M5，10 核 CPU / 10 核 GPU，24 GB；macOS 26.6（25G72），Safari 26.6。浏览器 hardwareConcurrency=8，deviceMemory 未提供。真实嵌入式 seekdb 1.4.0。

32 篇人工中文文档、16 条人工标注查询，4 配置各 3 轮，共 192 个查询样本；12/12 轮完成，实际后端均符合请求，无失败或降级。轮级加载/吞吐取 3 轮中位数；查询延迟取每配置 48 样本中位数，质量指标取均值。固定顺序、未随机化，未测温控/功耗；load 含本地文件/缓存、实例创建与首次预热，不含下载；e2e 计至评测预览文本显示。

|配置（各 3 轮）|加载 ms|文档/秒|seekdb ms|端到端 ms|Recall@5|nDCG@5|
|---|---:|---:|---:|---:|---:|---:|
|fp32_wasm_b1|768|12.432|6.914|67.0|0.8958|0.8921|
|q8_wasm_b1|549|11.478|8.396|66.0|0.8958|0.9102|
|q8_wasm_b8|560|10.211|7.054|67.0|0.8958|0.8931|
|q8_webgpu_b8|907|9.723|9.510|117.0|0.8958|0.8931|

**结论：** 同为 WASM/b1，INT8 加载中位数 768→549 ms（减少 28.52%），Recall@5 变化 0，nDCG@5 +0.0182。文档吞吐 12.432→11.478 篇/秒，未提速；b8 与 WebGPU 本轮也较慢，不能宣称加速。结果仅适用于此设备、浏览器和小型语料。Chrome/Edge、无 WebGPU 时的自动降级，以及自有文档完整交互验收仍待补测。

**复现。** 在 `stepfun` 执行 `npm ci && npm run models && npm run build && npm start`，访问 `http://127.0.0.1:3000`。加载模型、导入语料、查询；点击「运行完整评测」，执行 `npm run report -- reports/stepfun-benchmark.json` 汇总。模型来源：`huggingface.co/Xenova/bge-small-zh-v1.5`；官方题面见 README。

原始报告、汇总、CSV 与环境记录见 reports/stepfun-benchmark.*、reports/environment.json；原始时间与排名保留，不用最好一轮替代汇总。
