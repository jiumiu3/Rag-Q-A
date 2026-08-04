# 系统架构与安全边界

```mermaid
flowchart LR
    PDF[3 份规范 PDF] --> OCR[M1 RapidOCR CPU]
    OCR --> STRUCT[M2 条款/表格/术语结构化]
    STRUCT --> INDEX[M3 SQLite + BM25 + 向量索引]
    INDEX --> TOOL[knowledge_search 工具]
    LLM[聊天模型 Agent] -->|工具调用| TOOL
    TOOL --> RET[M4 精确 + BM25 + 语义向量检索]
    RET --> GAP{证据充分?}
    GAP -->|否: 缺口/失败原因| PLAN[改写/扩展/拆分/切换路线]
    PLAN --> RET
    GAP -->|是| LLM
    LLM --> QA[M5 证据约束回答与来源]
    INPUT[用户设计描述] --> PARSE[M6 原子检查项]
    PARSE --> CONFIRM{人工确认}
    CONFIRM --> CLARIFY[M7 条件追问]
    CLARIFY --> CANDIDATE[候选规则提取]
    CANDIDATE --> REVIEW{人工审核}
    REVIEW -->|confirmed| RULE[M8 确定性规则执行]
    REVIEW -->|pending/rejected| SAFE[暂停或安全停止]
    QA --> FLOW[M9 单 Agent 工作流]
    RULE --> FLOW
    FLOW --> API[M10 FastAPI / Web 演示]
    API --> EVAL[M11 四类评测与版本绑定]
```

核心安全边界：问答模型必须通过 `knowledge_search` 获取规范 Evidence，生成的每条 claim 必须绑定工具返回的 `evidence_id`，引用详情由代码补齐并验证。模型（如启用）只生成经 Pydantic 校验的候选结构，最终数值、枚举、布尔和表格判断由确定性执行器完成。未解析表格、外部标准、缺失条件和未确认规则均安全降级，不输出确定性合规结论。

自适应检索由 `plan_retrieval`、`retrieve`、`evaluate_evidence` 三个职责独立的节点组成。状态持久化原始/当前/历史查询、路线、过滤条件、候选数、新增 Evidence 数和结构化充分性评估。明确条款号或表号始终只以 exact 命中作为目标证据，邻近上下文不能替代目标。

候选规则与审核记录使用增量 `CREATE TABLE IF NOT EXISTS` 迁移写入现有 SQLite 文件，不修改旧表。候选 ID 由规范、条款、Evidence、对象和属性稳定生成；审核后的正式规则按对象、属性及条件匹配，不依赖数组位置。

运行态状态存入 SQLite；恢复令牌只存哈希。原页接口只能使用已登记的 `document_id` 与页码定位。日志和 M11 版本绑定不保存 API Key。
