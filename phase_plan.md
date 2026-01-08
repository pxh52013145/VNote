# RAG 视频知识库（基于 BiliNote 改造）阶段计划 & 项目规划

## 1. 项目简介

### 1.1 背景
BiliNote 现有能力是「视频/音频 → 下载/抽取 → 语音转写（带时间段）→ LLM 生成结构化 Markdown 笔记」，并支持时间戳跳转标记（`*Content-[mm:ss]`）与截图占位（`*Screenshot-[mm:ss]`）等增强理解的功能。

本项目计划在保留“可选生成 Markdown 笔记”能力的前提下，引入 **RAG（Retrieval-Augmented Generation）视频知识库**：将视频转写与结构化片段写入 **Dify 自建知识库（Dataset）**，通过 **Dify 智能体（Chat/Agent）** 实现“对话式视频检索与问答”，并在结果中直接给出 **时间戳、命中片段、关联视频**，支持点击跳转定位到对应视频位置（包含本地视频）。

### 1.2 目标用户与使用场景
- 学习/培训：在多个课程视频中快速定位某个概念出现的时间点与讲解片段
- 项目复盘：跨多个会议录屏/讲解视频检索决策与上下文
- 内容运营：从大量视频素材中按主题查找引用片段、生成摘要或笔记

### 1.3 核心目标（MVP）
1. 通过 Dify 构建视频知识库（Dataset），并能自动增量写入视频转写内容
2. 用户在对话页面输入问题，智能体返回：
   - 相关视频（标题/来源/可播放链接）
   - 命中片段（Top-K）与片段文本摘要
   - 命中时间戳（start/end）（必做）；（可选加分）可点击跳转
3. 输出时间戳（start/end）用于定位；（可选加分）实现“点击跳转到时间点”（网络视频 `t=` / 本地 `<video>.currentTime`）
4. 保留“生成 Markdown 笔记”的可选功能（原有 BiliNote 流程可复用）

### 1.4 非目标（本阶段不做或延后）
- 复杂多租户计费系统
- 大规模分布式转写/队列/弹性伸缩（先做单机/小规模可运行）
- 复杂权限体系（先做单工作区/单管理员/简单 token）

### 1.5 课程任务书对齐（`1.pdf` 重点）
#### 1.5.1 日程节点（课程安排）
- 12/29：选题/组队确认
- 12/30~1/3：自由研发
- 1/4：中期报告（20%）
- 1/5：测试与交付理论简介（建议这天做回归、打包、演示彩排）
- 1/6~1/8：自由研发
- 1/9：结题报告/答辩（80%）

#### 1.5.2 PPT/报告必须包含的内容
任务书要求 PPT 和大作业报告至少包含：
- 项目目标：最终目的与预期成果
- 实现方案：是否使用虚拟化、云计算、AI/ML、大数据等；算法原理与实现细节
- 产品展示：可视化界面截图 + 现场演示
- 成员分工：每位成员承担角色与贡献

#### 1.5.3 交付与打包要求
- 源代码打包：建议 `7z` 或 `tar.gz`（不要用 zip/rar）
- 演讲讲义：PPT 转 PDF
- 大作业报告：PDF
- 三份文件按要求发送到任务书指定邮箱（dingye@dgut.edu.cn）
- 核心要求：必须有可视化界面，不能做纯算法项目

#### 1.5.4 加分指标（优先覆盖）
任务书明确的加分点（成绩不超过 100）：
- ✅ 使用云计算平台，且现场演示可直接访问互联网地址
- ✅ 使用 Docker / Kubernetes / Serverless 部署
- ✅ 支持移动端或仅移动端开发
- ✅ 使用大数据处理技术
- ✅ 使用人工智能、机器学习技术
- ✅ 使用先进互联网框架（如 Actix、Svelte 等）

本项目建议优先拿下前 3 项：
- 一键 Docker Compose：把 Dify + 本项目（前后端）整合为“一条命令启动”
- 云上可访问：部署到云服务器（VPS）并提供公网 URL（可选绑定域名 + HTTPS）
- 移动端适配：前端对话/检索/播放页面做 responsive（可选 PWA）

### 1.6 推荐工具/模型（对齐老师推荐）
> 原则：**转写（Whisper）→ 向量化（Embedding）→ Dify Dataset 入库 → 智能体对话检索**。LLM 只负责“回答与格式化输出”，向量检索由 embedding + 向量库完成。

- Dify：自建知识库（Dataset）+ 应用（Chat/Agent）；提供 API 便于本项目“入库/对话”集成
- Whisper：用于将视频音频转成带时间戳文本；本项目沿用 BiliNote 已集成的 `fast-whisper` 路线（等价满足）
- `mxbai-embed-large`：用于 embedding（RAG 检索核心）；可用 Ollama 在 CPU 上运行（仅 embedding，不跑大 LLM）
- `qwen3-vl:30b-a3b-instruct`：用于“截图理解/画面问答”等加分项；30B 本地跑对算力要求高，建议作为 **可选**（优先先跑通“转写 RAG”主链路）

### 1.7 模型与成本策略（推荐默认组合）
- Chat/Agent LLM：优先用 **DeepSeek 官方 API**（成本低、效果稳定）；或其他 OpenAI-compatible 供应商
- Embedding：优先用 `mxbai-embed-large`（Ollama 自建，成本≈0）；如果不想自建 embedding，则改用云 embedding（会有额外费用）
- 说明：部分便宜 LLM API **不提供 embeddings**，因此常见组合是“LLM 用 API + embedding 本地/独立供应商”

### 1.8 云服务器（免费或超低价）建议
- 0 成本优先：Oracle Cloud Free Tier（需要信用卡/可能抢不到资源；成功后可长期免费）
- 超低价 VPS：任选 2C4G（或 2C2G+swap）即可跑 Dify + 本项目 demo；优先选网络到 GitHub/模型 API 稳定的机房
- 备用方案：本机部署 + Cloudflare Tunnel 暴露公网地址（成本≈0，但“云计算平台”加分可能不如真实云服务器稳妥）

### 1.9 0 成本公网演示（推荐你当前路线）
- 目标：老师现场可直接访问一个公网 `https://...` 地址进行演示
- 做法：本机运行 Dify + 本项目；用 Cloudflare Quick Tunnel 把本项目前端端口暴露出去（Dify 控制台不必暴露）
- 命令（Windows）：
  - `cloudflared tunnel --url http://localhost:3015`
  - 说明：Quick Tunnel 地址每次启动会变化；如需稳定域名可用 Cloudflare 账号创建 named tunnel

## 2. 现有项目能力盘点（BiliNote 现状）

### 2.1 后端（FastAPI）
- 任务系统：`task_id` 贯穿下载/转写/总结/保存，并输出 `note_results/{task_id}.status.json` 给前端轮询
- 下载器：支持 B 站/YouTube/抖音/快手/本地文件等（`backend/app/downloaders/*`）
- 转写：支持 fast-whisper/bcut/kuaishou/mlx/groq 等（`backend/app/transcriber/*`），产出 `segments(start,end,text)`
- 生成笔记：对转写文本做 prompt 组装后调用 LLM，输出 Markdown；支持时间戳链接与截图替换
- 本地上传：`/api/upload` 保存到 `uploads/`，并静态挂载 `/uploads`

### 2.2 前端（React + Vite）
- 生成笔记表单：选择平台/模型/风格/格式（link/screenshot/toc/summary）
- 任务队列与历史记录：轮询后端任务状态并展示 Markdown 预览

### 2.3 可复用点
- **转写段落（segments）** 是构建视频知识库的最佳基础（天然带时间轴）
- **本地上传 + 静态服务** 可作为本地视频可播放 URL 的基础（便于前端跳转）
- 任务状态管理与缓存结构可延伸到“入库状态/索引状态”

## 3. 总体方案（自建 Dify + 官方/中转模型）

### 3.1 总体架构（推荐）
```
用户前端(React)
  ├─ 生成笔记（可选，复用原流程）
  └─ RAG 对话检索（新）
        │
后端(FastAPI)
  ├─ 下载/转写/抽帧（复用）
  ├─ 分块（chunking）与元信息组织（新）
  ├─ 入库：调用 Dify Knowledge API（新）
  └─ 对话：代理调用 Dify Chat API（新，建议后端代理以保护 API Key）
        │
Dify（自建）
  ├─ Dataset（知识库：视频转写 chunks）
  └─ Chat/Agent App（检索+生成，返回引用）
        │
模型服务（官方/中转 API）
  ├─ Chat LLM（回答生成）
  ├─ Embedding（知识库向量化）
  └─ Rerank（可选，提升命中质量）
```

### 3.2 关键设计：chunk 与可跳转时间戳
因为 Dify Knowledge API 的 chunk 结构本身不存“自定义 metadata 字段”（至少在公开 API Schema 中未体现），因此建议将可跳转信息直接编码进 chunk 的 `content` 文本中，保证被检索到后可解析：

**chunk content 约定（建议统一格式）**
```
[VIDEO_ID=BVxxxx][PLATFORM=bilibili][TITLE=...]
[TIME=00:01:23-00:02:05]
转写内容……
```
前端/后端拿到检索结果（Dify 引用 content）后，解析 `[TIME=...]` 即可生成跳转按钮。

> 如果后续确认 Dify 支持对 segment/document 自定义 metadata 字段（或通过 external knowledge provider 自定义），可以把上述信息迁移到 metadata，实现更强过滤与更干净的正文。

### 3.3 知识库组织策略（单库全局 + 文档分组）
推荐在 MVP 采用 **单知识库（全局）**：
- 一个 Dataset 存放所有视频的 chunks（跨视频检索效果最好）
- 每个视频对应一个 Document（Document name = 视频标题/ID），便于管理与删除/更新
- 后续可增加标签（tag）或多 Dataset 实现项目隔离

### 3.4 对话结果展示策略（引用/关联处）
Dify 默认可能不会在 API 返回引用列表，需要在 App 的「功能」里开启 **引用和归属** 并重新发布；否则 `metadata.retriever_resources` 可能为空。

Dify Chat API 的 blocking 返回中包含 `metadata.retriever_resources`（引用列表），字段包含：
- `dataset_id / dataset_name`
- `document_id / document_name`
- `segment_id`
- `score`
- `content`（命中片段文本）

前端可以基于 `retriever_resources` 渲染：
- “命中片段 Top-K 列表”
- 每条展示时间戳与跳转按钮（解析 `content`）
- 点击跳转：若是网络视频，打开带 t= 的 url；若是本地视频，前端播放器 seek 到秒数

## 4. 接口与配置规划（建议）

### 4.1 新增环境变量（后端）
建议在 `.env` / `backend/.env` 增加：
- `DIFY_BASE_URL=http://localhost`（自建 Dify base url，不用带 `/v1`）
- `DIFY_DATASET_ID=...`（目标 Dataset UUID）
- `DIFY_SERVICE_API_KEY=...`（Service API Key：用于写入知识库/查询 indexing-status，仅后端保存）
- `DIFY_APP_API_KEY=...`（App API Key：用于 `/v1/chat-messages` 对话，仅后端保存）
- `DIFY_APP_USER=bilinote`（Dify chat body 必填 `user` 字段；任意稳定字符串即可）

> Key 不要下发到前端；前端只调用后端接口，后端再代理 Dify。

### 4.2 新增后端 API（建议）
- `POST /api/rag/index`：输入 `task_id` 或 `video_url`，触发“下载/转写/分块/入库”全流程
- `GET /api/rag/index_status/{task_id}`：返回入库状态（含 Dify indexing status）
- `POST /api/rag/chat`：代理调用 Dify Chat API（blocking/streaming），并把引用结构化返回给前端
- `POST /api/rag/retrieve`（可选）：直接调用 Knowledge retrieve，用于“纯检索模式/调试”

### 4.3 数据持久化（建议最小化）
为保证可维护性，建议在本地 sqlite 中新增一张映射表：
- `rag_documents`：`task_id/video_id/platform` → `dataset_id/document_id`，便于更新/删除/重新入库

## 5. 开发阶段计划（Phase Plan）

> 时间仅为建议，可按课程/项目周期调整。每个阶段都有“可验收产物”。

### Phase 0：需求澄清与技术预研（0.5～1 天）
**目标**
- 明确“跨视频检索 + 视频内定位 + 本地跳转”的验收标准
- 确定 Dify 自建方式、API Key 流程、模型供应商（官方/中转）可用性

**产物**
- 本文档定稿
- 关键参数确认（模型名、base_url、embedding/rerank 选择）

**验收**
- 能在本机访问 Dify Web（规划端口）与确认可创建 Dataset/App

---

### Phase 1：自建 Dify 可用（1 天）
**任务**
- 使用 Docker Compose 部署 Dify
- 完成管理员初始化
- 配置模型供应商（Chat LLM + Embedding，必要时 Rerank）
- 创建 Dataset（Knowledge Base）
- 创建 Chat/Agent App 并绑定 Dataset

**产物**
- Dify 可登录
- 拿到 `DIFY_DATASET_ID`、`DIFY_SERVICE_API_KEY`、`DIFY_APP_API_KEY`

**验收**
- Dify 后台中能手工上传一个文本到知识库，能在 App 中问答并看到引用

---

### Phase 2：后端入库管线（转写 → 分块 → 写入 Dify）（2～3 天）
**目标**
- 复用 BiliNote 转写产物，自动写入 Dify Dataset

**任务拆分**
1. 分块策略（chunking）
   - 输入：`TranscriptResult(segments[])`
   - 输出：chunks（每个 chunk 有 start/end、text、video_id、title、platform）
   - 初版建议：按“时间窗口”聚合（如 30～90 秒）或按 token 近似长度聚合
2. Dify Knowledge API 接入
   - 通过 `/datasets/{dataset_id}/document/create-by-text` 创建 Document（每视频一个）
   - 通过 `/datasets/{dataset_id}/documents/{document_id}/segments` 写入 chunks（可控）
   - 轮询 `/datasets/{dataset_id}/documents/{batch}/indexing-status` 获取索引状态
3. 建立映射表（可选但强烈建议）
   - 记录视频与 Dify document_id 关系，支持更新/删除

**产物**
- 新增后端 service：`DifyKnowledgeClient`
- 新增 API：`/api/rag/index`、`/api/rag/index_status/{task_id}`

**验收**
- 输入一个视频（含本地上传），完成转写后自动入库，Dify 中可看到对应 Document 与 chunks

---

### Phase 3：对话检索（前端 UI + 后端代理 Dify Chat）（2～3 天）
**目标**
- 用户可以在前端对话窗口提问并得到答案 + 引用片段列表

**任务**
- 后端新增 `DifyChatClient`，封装 `/chat-messages`
- 统一返回结构：`answer` + `retriever_resources[]`（命中片段）
- 前端新增页面：`ChatPage`
  - 输入框 + 消息列表
  - 引用区（Top-K）展示 `document_name/score/content`

**产物**
- 前端可对话，能看到“关联处/引用”

**验收**
- 对同一 Dataset 中多视频提问，能返回跨视频命中片段

---

### Phase 4：时间戳展示（可选跳转）（1～2 天）
**目标**
- 引用片段显示时间戳；可选实现点击跳转

**任务**
- 统一解析规则：从引用 `content` 解析 `[TIME=mm:ss-mm:ss]`
- 网络视频跳转：
  - bilibili：`https://www.bilibili.com/video/{BV}?t=秒`
  - youtube：`https://www.youtube.com/watch?v={id}&t=秒s`
  - 其他平台按可用方式降级处理
- 本地视频跳转：
  - 前端增加播放器（`<video src="/uploads/xxx.mp4">`）
  - 点击引用：`videoRef.currentTime = startSeconds; video.play()`

**产物**
- 引用区展示时间戳（必做）
- （可选）“跳到 01:23”按钮 + 本地视频定位播放

**验收**
- 任意检索结果都能展示命中片段的 start/end 时间戳
- （可选）随机点击 3 个引用片段，能跳转到对应时间位置（±2 秒以内）

---

### Phase 5：与“生成 Markdown 笔记”并存（1～2 天）
**目标**
- 保留原有笔记生成作为可选产物，并与 RAG 入库共存

**任务**
- UI 上提供两种模式：
  - “生成笔记（MD）”
  - “入库并开启对话检索（RAG）”
- 后端统一：一次转写可同时触发“入库 + 生成笔记”（可配置）

**验收**
- 同一个任务既能产出 MD，又能在对话中检索到该视频内容

---

### Phase 6：质量优化与工程化（2～4 天）
**内容**
- 检索质量：
  - 调整 chunk 大小、top_k、hybrid search、rerank
  - 提示词约束输出格式（必须列出引用与时间戳）
- 成本与性能：
  - 只对转写文本做 embedding（避免将截图 base64 入库）
  - 增量更新与去重（避免重复入库）
- 可观测性：
  - 入库/对话日志
  - Dify indexing status 失败重试
- 安全：
  - API Key 仅后端保存
  - 基础限流、防刷

**验收**
- 常见问题命中率提升（通过自定义测试集/用例列表回归）

---

### Phase 7：交付与文档（1 天）
**产物**
- 部署文档（Dify + 本项目）
- 用户使用手册（入库、对话、跳转、笔记导出）
- 演示脚本与样例视频/问题集

**验收**
- 新环境从 0 到可用，按文档 30 分钟内完成部署与跑通 demo

## 6. 里程碑验收清单（Checklist）
- [ ] Dify 自建可访问、可创建 Dataset 与 App
- [ ] 后端可将视频转写写入 Dataset（可见 Document 与 chunks）
- [ ] 前端对话能返回答案 + 引用片段（Top-K）
- [ ] 引用片段含时间戳，支持点击跳转
- [ ] 本地视频可播放并跳转到时间点
- [ ] 可选生成 MD 笔记仍可用

## 7. 风险与对策
- **成本风险（embedding/chat/rerank）**：先用 economy/较小 embedding；对长视频做分块与去重；测试阶段控制入库样本量
- **检索不准**：加入 rerank；chunk 以时间窗口为主；提示词强制引用与时间范围；必要时“视频内检索”模式附带视频限定词
- **本地视频不可播放**：浏览器无法直接访问磁盘路径，必须上传到 `/uploads` 或走 Tauri 文件协议；MVP 优先上传
- **索引延迟**：前端显示 indexing 状态；后端轮询 Dify indexing-status 并允许重试

## 8. 待确认问题（请在启动 Phase 2 前确定）
1. 视频入库的 Dataset：单库全局（推荐）还是按课程/项目拆分多库？
2. 是否需要用户体系（不同用户各自知识库隔离）？
3. 目标模型组合（Chat/Embedding/Rerank 的 provider 与 model_name）
4. 演示范围：只做 Web 还是需要 Tauri 桌面版（本地文件体验更好）

## 9. 两人小组分工建议（可直接写入 PPT/报告）
> 原则：一人主后端/部署，一人主前端/产品与文档；关键链路互为备份，避免单点。

### 成员1：后端/数据管线/部署（Owner）
- Dify 自建部署与运维（docker compose、端口、备份/升级记录）
- 模型供应商接入（中转/官方 API）与 Dify 侧配置跑通
- BiliNote 后端改造：`/api/rag/*`、chunking、写入 Dify Dataset、索引状态轮询
- 云上部署（加分项）：Dify + 本项目一键部署到 VPS，提供公网可访问 URL
- 安全与配置：API Key 仅后端保存；基础限流与日志

### 成员2：前端/UI/交互/测试（Owner）
- 前端新增 RAG 对话页（消息流、引用/关联处列表、错误与 loading 状态）
- 引用片段解析与跳转（网络视频 t=、本地 `<video>.currentTime`）
- 移动端适配（加分项）：responsive 布局；可选 PWA
- 测试与演示脚本：用例集（10~20 个问题）、回归检查、现场演示路径设计

### 共同产出（两人都要写/都要能讲）
- 中期/结题汇报 PPT（含：目标、方案、架构图、关键算法/流程、界面、分工、演示）
- 大作业报告（含：实现细节、关键接口、部署方式、结果与反思）
- 最终交付打包（源代码 7z/tar.gz + PPT PDF + 报告 PDF）

## 10. 建议的阶段性验收（按日期对齐）
- 12/30：Dify 自建完成；BiliNote 本地跑通；确定模型/中转可用
- 1/2：完成“转写→分块→入库→可检索”闭环（后端为主，能在 Dify App 里问到结果）
- 1/4（中期）：前端对话页可用，返回引用片段与时间戳；准备 3 分钟 demo
- 1/8：云上可访问 URL + 一键 docker compose；移动端适配基本可用
- 1/9（结题）：完整演示 + 打包提交

---

## 11. 本地 <-> Dify 知识库对账/同步（MinIO 原文真源）（已落地进度）

### 11.1 背景
- 每次启动/切换 Dify Profile，需要对“本地库 vs 当前 Dify 数据集”做一次对账，否则“已入库”标记会串库。
- 多人共用服务器 Dify 时，Dify 可能多于任一本地；需要支持从远端拉取，做到多端一致。
- 仅靠向量库分段无法 100% 还原原始笔记结构，因此选用 MinIO 作为“原文真源”，Dify 负责索引与检索。

### 11.2 核心设计（稳定可对账）
- 标识：`source_key = platform:video_id:created_at_ms`；`sync_id = sha256(source_key)`
  - 同一 `platform:video_id` 允许多次生成，用 `created_at_ms` 区分版本/批次（满足“同一视频多份笔记”）。
- 存储隔离：MinIO 全局一套服务，但按 Dify Profile 分 Bucket：bucket 由 profile 名称派生（S3 安全 slug + hash）+ `MINIO_BUCKET_PREFIX`
- Dify 文档命名：`<title> [platform:video_id:created_at_ms] (note|transcript)`（便于 `list_documents` 直接解析出 `source_key`）

### 11.3 对账状态（UI 标签）
- `LOCAL_ONLY`：本地有 / Dify 无 -> 显示“本地”，提供“入库”入口（上传原文包 + 写入当前 Dify）
- `DIFY_ONLY`：Dify 有 / 本地无 -> 显示“DIFY”，提供“获取”入口（从 MinIO 拉取原文包到本地）
- `DIFY_ONLY_NO_BUNDLE`：Dify 有 / MinIO 缺原文包 -> 显示“DIFY(缺包)”，无法获取；需在有本地原文的设备上点一次“入库/补传”
- `PARTIAL`：两边都有但不完整 -> 显示“部分”，可“入库”补齐远端缺项，或“补全”拉取本地缺项（默认只补缺失文件）
- `CONFLICT`：两边都有且都完整，但内容 hash 不一致 -> 显示“冲突”，提供“本地覆盖/云端覆盖/另存副本”
- `SYNCED`：两边都有 -> 显示“已同步”
- `DELETED`：远端已写入 tombstone -> 显示“已删除”，用于多端同步删除（可在有本地原文的设备重新入库恢复）
- `DIFY_ONLY_LEGACY`：Dify 旧格式文档（无 `created_at_ms` tag）-> 显示“DIFY(旧)”，暂不支持自动获取

### 11.4 Phase Plan & 进度
- Phase 0（0.5 天）定规范（MVP 前置）— Completed  
  - `source_key = platform:video_id:created_at_ms`；`sync_id = sha256(source_key)`  
  - bundle：`meta.json + note.md + transcript.json + transcript.srt (+ audio.json)`，zip 构建为“确定性输出”，用于稳定 hash  
  - MinIO object key：`MINIO_OBJECT_PREFIX + <sync_id>.zip`（不依赖 Dify metadata）  
  - DB：`sync_items` 表按 `dify_profile` 维度落库（避免 dify1/dify2 串库）
- Phase 1（1 天）打通 MinIO「原文真源」— Completed  
  - `/api/sync/push`：本地→MinIO（幂等：同 `bundle_sha256` 不重复上传）+ 写入/更新 Dify 文档  
  - `/api/sync/pull`：MinIO→本地（校验 `bundle_sha256`；默认只补缺失文件，`overwrite=true` 可强制覆盖）
- Phase 2（1 天）实现对账扫描 — Completed  
  - `/api/sync/scan`：扫本地 + 扫 Dify 文档列表 + 查 MinIO bundle/tombstone → 计算状态（含 `CONFLICT`/`DIFY_ONLY_NO_BUNDLE`）  
  - 扫描结果落 SQLite（`sync_items`），前端可直接渲染
- Phase 3（0.5–1 天）前端渲染 + 入口 — Completed  
  - 启动/切换 profile 自动 scan；列表/详情展示标签与入口（入库/获取/补全）
- Phase 4（可选，0.5–1 天）冲突与删除策略 — Completed  
  - 冲突：本地覆盖 / 云端覆盖 / 另存为副本  
  - 删除：支持“删本地 / 删远端（tombstone + 尝试删 Dify）”，多人共用场景用 tombstone 防误删

> 服务器部署与配置说明见 `doc/library_sync.md`
