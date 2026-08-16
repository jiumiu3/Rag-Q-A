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
    CLARIFY --> RETRIEVE[按检查项检索 Top5 Evidence]
    RETRIEVE --> JUDGE[结构化 RAG 判断]
    JUDGE --> VALIDATE[引用、数值、单位与状态校验]
    VALIDATE --> REPORT[合规预审报告]
    QA --> FLOW[M9 单 Agent 工作流]
    RULE --> FLOW
    FLOW --> API[M10 FastAPI / Web 演示]
    API --> EVAL[M11 四类评测与版本绑定]
```

核心安全边界：问答模型必须通过 `knowledge_search` 获取规范 Evidence，生成的每条 claim 必须绑定工具返回的 `evidence_id`。合规判断模型只能消费当前 CheckItem 绑定的 Top5 直接 Evidence，输出必须符合 Pydantic Schema；代码再验证 Evidence 归属、条款/表号、实际值来源，并重新执行数值、单位、枚举和布尔比较。未解析表格、冲突证据、外部标准和缺失条件均安全降级。

自适应检索由 `plan_retrieval`、`retrieve`、`evaluate_evidence` 三个职责独立的节点组成。状态持久化原始/当前/历史查询、路线、过滤条件、候选数、新增 Evidence 数和结构化充分性评估。明确条款号或表号始终只以 exact 命中作为目标证据，邻近上下文不能替代目标。

规范问答使用独立但语义一致的受控循环：`analyze_question` 将复杂问题拆成可验证目标，`plan_qa_retrieval` 根据未覆盖目标规划最多三轮、每轮最多五个查询，`evaluate_qa_evidence` 逐目标记录 `SUPPORTED`、`PARTIAL`、`UNSUPPORTED` 或 `CONFLICT`。只有全部目标充分时才进入 `generate_qa_answer`；精确编号未命中、未解析表格、冲突证据、无新增 Evidence 或达到轮次上限均安全停止。回答生成器只消费工作流已批准的 Evidence，不自行检索。

候选规则与审核记录仍使用增量 `CREATE TABLE IF NOT EXISTS` 迁移写入现有 SQLite 文件，作为独立管理和后验复核能力，不再是合规主工作流的前置依赖。

运行态状态存入 SQLite；恢复令牌只存哈希。原页接口只能使用已登记的 `document_id` 与页码定位。日志和 M11 版本绑定不保存 API Key。
