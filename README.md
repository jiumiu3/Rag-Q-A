# 油气管网规范 RAG 问答与合规预审 Agent

这是一个把技术规范转换为可检索、可引用、可执行知识的 RAG 应用。输入规范 PDF 后，系统完成
OCR 与条款结构化，建立 BM25 和 Embedding 索引，并提供带条款、页码和原文证据的问答；对于
设计描述，还能由模型拆分检查项，按项动态检索规范 Evidence，并由模型结合完整设计上下文和来源条款生成合规预审报告。

## 一分钟了解

```text
规范 PDF → OCR/结构化 → SQLite + BM25 + Vector
                              ↓
用户问题 → 动态检索规划 → Exact/BM25/Vector → Evidence → 带来源回答
设计描述 → 模型原子拆解 → 确认 → 动态分项检索 → 模型综合判断 → 合规预审报告
```

项目的核心约束是“先取证，再回答”：模型只能依据检索得到并绑定到当前目标或检查项的 Evidence
生成回答和合规结论。查询与合规审查共用动态检索规划：明确条款号或表号走 `exact`，数值参数或
专业关键词优先走 `bm25`，复杂或开放语义问题走 `bm25 + vector`，证据确实缺失时再有限改写和切换路线。

### 当前测试结果

当前公开结果来自自动生成并冻结的可复现测试集 `generated-v1.3.0`。知识库包含三份规范、
1092 个知识单元；向量索引 `index_e2d07010e6a742afe128` 使用
`qwen3.7-text-embedding`，包含 1092 个 1024 维向量，版本与内容一致性检查通过。

| 检索方式 | Recall@1 | Recall@5 | 说明 |
| --- | ---: | ---: | --- |
| BM25 | 68.18% | 95.45% | 词法召回基线 |
| Vector | 43.18% | 77.27% | 语义向量基线 |
| 固定 Hybrid | 65.91% | 86.36% | 当前融合结果低于纯 BM25 |
| 动态路由 | 84.09% | 97.73% | 根据查询类型选择 Exact、BM25 或混合路线 |
| Agentic 首轮规划 | 84.09% | 100.00% | 按检索目标规划子查询后的首轮结果 |

当前真实完整工作流的合规审查准确度为 **90%**，`false compliant` 为 **0**。工程静态检查、类型检查和
全量自动化回归均通过。该结果属于当前小规模业务金标集上的开发评测，不代表生产场景泛化保证。

复现方式见[评测说明](evaluation/README.md)，已经确认的问题与处理记录见
[问题与解决记录](docs/问题与解决记录.md)。

## 主要能力

- 中文规范 PDF 页面化和 CPU OCR，保留坐标、置信度、页码与原页图像。
- 条款层级、术语、图注、候选表格和知识单元结构化。
- SQLite 精确检索、BM25 和 Embedding 语义向量检索。
- 明确条款号/表号使用 `exact`；数值参数和专业关键词使用 `bm25`；复杂或开放语义问题使用 `bm25 + vector`。
- 证据不足时记录缺口并采用保留标识符改写、领域同义词、问题拆分或路线切换重试；无新增证据即安全停止。
- 问答和合规判断仅依据动态检索返回的 Evidence 组织中文结果。
- Claim-Evidence 绑定、引用真实性校验和证据不足安全降级。
- 轻量模型 Schema 拆解设计描述，代码生成稳定 ID、字符位置和追溯字段。
- 检查项确认、处理状态反馈、会话恢复和报告导出。
- 每个检查项独立检索 Top5 直接 Evidence，显式保存检查项与证据归属。
- 模型结合完整原始设计描述与每项 Evidence 输出合规状态、总体总结、逐项原因和来源条款。
- 代码只验证 Evidence 归属及来源条款真实性，不用确定性业务规则覆盖模型结论。
- 规范 Evidence 可提取为 SQLite 持久化候选规则；确认后的版本才能进入正式规则库。
- FastAPI、SQLite 会话、响应式 Web 问答页面和 Markdown/JSON 报告。
- 检索、问答、拆解、合规四类可复现评测及版本哈希绑定。

## 架构

```mermaid
flowchart LR
    PDF[规范 PDF] --> OCR[RapidOCR CPU]
    OCR --> STRUCT[条款/术语/表格结构化]
    STRUCT --> STORE[SQLite + BM25]
    STRUCT --> VECTOR[Embedding 向量索引]
    STORE --> ROUTER[统一动态检索规划]
    VECTOR --> ROUTER
    USER[用户问题] --> ROUTER
    ROUTER -->|Evidence| AGENT[聊天模型]
    AGENT --> VERIFY[引用真实性校验]
    VERIFY --> ANSWER[带来源回答]
    DESIGN[设计描述] --> ITEMS[原子检查项]
    ITEMS --> CONFIRM[确认/追问]
    CONFIRM --> SEARCH[动态分项检索 Evidence]
    SEARCH --> JUDGE[模型结构化判断]
    JUDGE --> VALIDATE[Evidence 与来源条款校验]
```

详细组件和安全边界见 [系统架构说明](docs/ARCHITECTURE.md)。

## 技术栈

- Python 3.12、FastAPI、Pydantic、SQLite
- RapidOCR、ONNX Runtime CPU、PyMuPDF、Pillow
- BM25、OpenAI-compatible Embeddings、FAISS CPU（可选）
- OpenAI-compatible Chat Completions、LangGraph（原生图入口可选）
- 原生 HTML/CSS/JavaScript，无 npm 构建链

## 目录结构

```text
app/
  api/             API 路由
  clarification/  条件追问与恢复
  compliance/     规则和确定性执行器
  core/           配置、模型客户端、日志和异常
  design/         设计描述拆解
  evaluation/     统一评测器
  ingestion/      PDF 与 OCR
  knowledge/      SQLite、BM25、向量索引
  qa/             RAG Agent 与回答校验
  retrieval/      查询分析和 Evidence
  web/            问答页面
  workflow/       单 Agent 状态工作流
scripts/          OCR、建库、评测和重放工具
evaluation/       固定评测集与报告
docs/             架构和补充文档
data/             本地规范、OCR、数据库和索引，不提交 Git
```

## 环境要求

推荐使用 Docker Engine 和 Docker Compose v2。默认镜像为 CPU 环境，不需要 NVIDIA GPU、CUDA 或 cuDNN；建议至少 4 CPU、8 GB 内存。本地运行要求 Python 3.12+。

## 快速启动

### 1. 准备配置与目录

```bash
cp .env.example .env
mkdir -p data/raw data/pages data/ocr data/processed data/indexes
```

将规范 PDF 放入 `data/raw/`。规范原文、OCR、数据库和向量文件均不会提交到 Git。

Linux 用户如果 UID/GID 不是 `1000`，请根据 `id -u`、`id -g` 修改 `.env` 中的 `LOCAL_UID`、`LOCAL_GID`。

### 2. 配置 RAG 模型

```dotenv
APP_MODEL__PROVIDER=compatible
APP_MODEL__CHAT_BASE_URL=https://api.deepseek.com
APP_MODEL__CHAT_API_KEY_ENV=DEEPSEEK_API_KEY
APP_MODEL__CHAT_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=你的DeepSeek密钥
APP_MODEL__EMBEDDING_BASE_URL=https://你的Embedding服务地址/v1
APP_MODEL__EMBEDDING_API_KEY_ENV=LLM_API_KEY
APP_MODEL__EMBEDDING_MODEL=Embedding模型名称
APP_MODEL__EMBEDDING_BATCH_SIZE=16
APP_MODEL__EMBEDDING_MAX_CHARS=6000
APP_MODEL__REQUEST_RETRIES=3
APP_MODEL__API_KEY_ENV=LLM_API_KEY
LLM_API_KEY=你的密钥
APP_AGENT__RAG_LLM_ENABLED=true
APP_RETRIEVAL__CONTEXTUAL_ENABLED=true
APP_RETRIEVAL__CONTEXTUAL_STRATEGY=deterministic
APP_RETRIEVAL__CONTEXTUAL_VERSION=v1
```

`.env` 不得提交。远程建库会把知识单元发送给 Embedding 服务，问答会把检索 Evidence 发送给聊天模型，启用前应确认数据授权和服务方的数据政策。

离线后备模式可设置：

```dotenv
APP_AGENT__RAG_LLM_ENABLED=false
```

离线模式使用确定性哈希向量和抽取式回答，适合测试或故障降级，不代表完整语义 RAG 能力。

### 3. 构建与启动

```bash
docker compose build agent
docker compose up --detach agent
docker compose ps
docker compose logs --follow agent
```

访问地址：

- Web 问答：`http://127.0.0.1:8000/api/v1/demo`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`
- 工作流图：`http://127.0.0.1:8000/api/v1/workflow/graph`

停止服务：

```bash
docker compose down
```

`data/` 通过 Volume 持久化，删除容器不会删除正式数据。

## 构建知识库

首次导入规范时依次执行。

### PDF 元数据检查

```bash
docker compose run --rm --no-deps agent \
  python scripts/ingest_pdf.py --inspect-only
```

### OCR

先进行单页检查：

```bash
docker compose run --rm --no-deps agent \
  python scripts/ingest_pdf.py --max-pages 1
```

确认正常后执行完整 OCR：

```bash
docker compose run --rm --no-deps agent \
  python scripts/ingest_pdf.py
```

OCR 支持断点缓存。只有明确需要重新生成时才增加 `--force`。

### 条款结构化

```bash
docker compose run --rm --no-deps agent \
  python scripts/build_structure.py
```

OCR 修正可参考 `corrections.example.yaml`。修订只作用于结构化副本，不覆盖原始 OCR。

### 索引构建

```bash
docker compose run --rm --no-deps agent \
  python scripts/build_index.py
```

该命令生成 SQLite、BM25、Embedding 向量、可选 FAISS 文件和索引版本清单。检查一致性：

BM25 和 Embedding 使用确定性生成的 `context_prefix + 原始 content`，引用仍只使用原始内容。构建同时生成 `data/indexes/contextual_units.jsonl` 审计文件。修改上下文策略或版本后必须重新运行本命令；配置变化会触发全量上下文重算。

```bash
docker compose run --rm --no-deps agent \
  python scripts/build_index.py --check
```

数据库知识单元、BM25 文档、向量 ID 和向量数量必须一致。

## 使用问答 Agent

Web 首页以问答为主视图，证据、检查项、合规结果和运行 Trace 默认收在可展开侧栏。

直接调用 API：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/qa \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Q/GGW 02005.2-2022 第8.3.4.4条有什么安装要求？",
    "top_k": 5
  }'
```

响应包含：

- `answer_text`：模型组织的回答，末尾固定附证据来源；
- `claims`：逐条结论及绑定的 `evidence_ids`；
- `citations`：规范号、条款/表号、页码和原文摘录；
- `limitations`：证据不足、模型降级或人工复核说明；
- `trace`：检索路线和候选结果。

明确编号查询采用确定性精确检索，数值参数和专业关键词优先 BM25，复杂或开放语义问题使用
BM25 与语义向量，避免相似内容冒充指定条款。模型输出必须通过结构化 Schema、Evidence ID、
引用真实性校验；失败时系统明确降级或拒答，不使用模型记忆补写规范。

## 设计描述与合规预审

设计描述预览：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/design/preview \
  -H 'Content-Type: application/json' \
  -d '{"description":"新建输气站控制室照度为500lx，UPS持续供电时间为2h。"}'
```

模型通过轻量结构将描述拆成原子检查项，代码补充稳定 ID、字符位置和追溯字段。用户确认后，
统一动态规划器按检查项生成子查询并选择检索路线；已有直接 Evidence 时进入模型判断，确实没有
绑定 Evidence 时才有限重试。

合规执行接口：

```text
POST /api/v1/compliance/evaluate
```

模型根据完整原始设计描述及当前检查项绑定的直接 Evidence，输出总体总结、逐项合规状态、判断原因和
来源条款。代码验证 Evidence ID 与条款/表号真实归属，但不使用确定性数值、单位、枚举或布尔规则覆盖
模型的业务结论。信息不足、证据冲突或未解析表格由模型明确降级。

候选规则审核接口作为独立管理能力保留，但不是当前 RAG 合规工作流的必经路径。

候选规则审核接口：

```text
POST /api/v1/candidate-rules/extract
GET  /api/v1/candidate-rules
GET  /api/v1/candidate-rules/{candidate_rule_id}
POST /api/v1/candidate-rules/{candidate_rule_id}/confirm
POST /api/v1/candidate-rules/{candidate_rule_id}/reject
POST /api/v1/candidate-rules/{candidate_rule_id}/revise
GET  /api/v1/rules
GET  /api/v1/rules/{rule_id}
```

候选规则默认 `pending_review`，确认过程会保存原始 JSON、修改后版本、操作信息和备注。`rejected` 规则不会进入正式规则库；匹配存在缺条件、歧义或冲突时不会执行确定性判断。

外部标准、缺失条件、未解析表格和非唯一查表不会输出确定性结论。

## 会话与证据接口

```text
POST /api/v1/sessions
POST /api/v1/sessions/{session_id}/messages
POST /api/v1/sessions/{session_id}/confirm
POST /api/v1/sessions/{session_id}/clarify
GET  /api/v1/sessions/{session_id}
POST /api/v1/sessions/{session_id}/replay
GET  /api/v1/sessions/{session_id}/report?format=markdown|json

GET /api/v1/knowledge/documents
GET /api/v1/knowledge/index-versions
GET /api/v1/knowledge/status
GET /api/v1/pages/{document_id}/{page_number}
```

消息接口使用 `idempotency_key` 防止重复提交。恢复令牌只在 SQLite 保存哈希，原页接口只允许访问已登记文档和页码。

## 复现评测

```bash
docker compose run --rm --no-deps agent \
  python scripts/run_evaluation.py

docker compose run --rm --no-deps agent \
  python scripts/run_demo_cases.py

docker compose run --rm --no-deps agent \
  python scripts/run_rag_compliance_business_evaluation.py
```

输出位于本地 `evaluation/reports/latest/`，包括 JSON、Markdown、HTML 指标报告和业务审查结果。
评测记录代码哈希、索引版本、Embedding/Chat 模型、运行配置和数据集 SHA256，便于确认两次结果是否
在同一版本上产生。公开仓库只保留评测框架、清单和聚合结果，不提交规范原文、生成样本、失败案例
明细或查询向量缓存。

## 问题与解决记录

已确认的架构、检索、合规判断和交互问题统一维护在
[问题与解决记录](docs/问题与解决记录.md)，README 不再保留历史调试过程。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,ocr,index,agent]'
cp .env.example .env
python -m app.main
```

代码检查：

```bash
ruff format --check .
ruff check .
mypy app
pytest
```

## 数据与安全边界

- `.env`、规范 PDF、OCR、页面图像、SQLite 和向量索引默认不提交 Git。
- 远程模型只应在得到规范数据外传授权后启用。
- 回答只在已导入规范范围内提供证据，不替代正式工程审查。
- 复杂表格可能标记为 `NEEDS_MANUAL_ANNOTATION`，不得自动查值。
- 合规结论由模型基于当前检查项绑定 Evidence 生成，输出前必须通过 Evidence 归属和来源条款真实性校验。
- 没有充分证据时必须拒答、返回部分结果或标记待复核。
