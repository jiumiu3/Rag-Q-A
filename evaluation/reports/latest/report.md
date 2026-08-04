# M11 可复现评测报告

生成时间：2026-08-04T09:15:48.316778+00:00
代码哈希：`131bd14c483627740d9fd0c8aff13dff29cbe8d9c028a84aca9ec9aa4f56ccf0`
索引版本：`index_2d8fa99bdc0f110e0087`
索引源哈希：`b5e2f7510d3f2e816b4df786f5460b794bb820dfaca34cad8158877a961af375`
数据集版本：`m11-datasets-v1`
模型配置哈希：`23e2b00c905a2787974b41a3e815471b443254a846107a9e732df2090aabec46`（不包含 API Key）
提示词与评测器哈希：`c64610c62cb2771b79ed10cab8a64d36e5a5ea07055526300b9eb995329a5b32`

## 指标

### retrieval
数据量：50

| 指标 | 值 | 分母 | 状态 | 适用范围 |
|---|---:|---:|---|---|
| recall_at_1 | 1.0000 | 50 | MEASURED | 50条真实索引精确条款号基准 |
| recall_at_5 | 1.0000 | 50 | MEASURED | 50条真实索引精确条款号基准 |
| mrr | 1.0000 | 50 | MEASURED | 50条真实索引精确条款号基准 |

- 限制：标签由 SQLite 精确条款键分层派生，不代表同义改写或开放语义检索性能。

### qa
数据量：30

| 指标 | 值 | 分母 | 状态 | 适用范围 |
|---|---:|---:|---|---|
| route_accuracy | 1.0000 | 30 | MEASURED | 30条条款/表格查询 |
| evidence_status_accuracy | 1.0000 | 30 | MEASURED | 30条可程序验证状态 |
| citation_validity | 1.0000 | 30 | MEASURED | 引用存在且来自 Evidence |
| numeric_consistency | 1.0000 | 30 | MEASURED | 回答数值不得脱离绑定 Evidence |
| refusal_accuracy | 1.0000 | 10 | MEASURED | 10条错误条款/复杂表格/错误表号 |
| evidence_supported_answer_accuracy | 1.0000 | 30 | MEASURED | 状态、期望证据与引用联合校验 |
| agent_tool_call_rate | 1.0000 | 30 | MEASURED | 回答对象包含经代码执行的 knowledge_search 调用审计 |
| agent_generation_success_rate | 1.0000 | 30 | MEASURED | 模型基于工具证据生成结构化回答且未降级为抽取式回答 |
| free_text_semantic_correctness | N/A | 0 | NOT_EVALUATED | 没有领域专家逐条语义评分，不报告自由文本正确率 |

- 限制：20条正例由精确条款键派生；自由文本语义正确性尚需领域专家评分。

### design
数据量：30

| 指标 | 值 | 分母 | 状态 | 适用范围 |
|---|---:|---:|---|---|
| item_recall | 1.0000 | 61 | MEASURED | 30段、每段2-3项的属性多重集召回 |
| item_sequence_exact_match | 1.0000 | 30 | MEASURED | 30段属性顺序完全匹配 |
| object_binding_accuracy | 1.0000 | 24 | MEASURED | 12段24项细粒度人工夹具 |
| parameter_accuracy | 1.0000 | 24 | MEASURED | 12段24项细粒度人工夹具 |
| unit_accuracy | 1.0000 | 24 | MEASURED | 12段24项细粒度人工夹具 |

- 限制：细粒度夹具由项目实现阶段人工编写，标注人字段仍待领域负责人签字确认。

### compliance
数据量：20

| 指标 | 值 | 分母 | 状态 | 适用范围 |
|---|---:|---:|---|---|
| deterministic_status_accuracy | 1.0000 | 20 | MEASURED | 20条人工规则夹具的确定性执行结果 |
| trace_or_limitation_coverage | 1.0000 | 20 | MEASURED | 每项必须有比较 trace 或安全降级限制 |
| advisory_strength_accuracy | 1.0000 | 1 | MEASURED | 带期望 advisory 标签的规则 |
| missing_condition_safety_accuracy | 1.0000 | 2 | MEASURED | 缺少必要输入时必须安全降级，不得猜测结论 |
| llm_rule_extraction_accuracy | N/A | 0 | NOT_EVALUATED | 未建立真实条款到规则的领域专家黄金集 |
| production_end_to_end_compliance_accuracy | N/A | 0 | NOT_EVALUATED | 尚无人工确认生产规则库，不能报告端到端准确率 |

- 限制：规则均为人工夹具，只测执行器；不代表真实条款规则抽取或工程审查准确率。

## 误差分类

- agent_generation: 0
- agent_tool_call: 0
- binding: 0
- citation: 0
- item_extraction: 0
- numeric: 0
- ocr: 0
- parameter: 0
- qa_status: 0
- retrieval: 0
- routing: 0
- rule_extraction: 0
- structure: 0
- table: 0
- unit: 0

### 逐案例误差

| 套件 | 案例 | 分类 | 期望 | 实际 | 说明 |
|---|---|---|---|---|---|
| - | - | - | - | - | 当前固定夹具无失败 |

## 已知数据质量

- ocr_suspicious_pages: 8
- manual_review_tables: 27
- production_rules_confirmed: 0
- note: 来自 M1/M2 真实质量报告与当前规则库状态

## 声明边界

- 检索指标仅适用于精确条款号基准，不能外推为开放语义检索准确率。
- 问答未进行领域专家自由文本语义评分。
- 拆解细粒度夹具尚待领域负责人签字确认。
- 合规仅测人工规则夹具执行器，未测真实规则抽取和生产端到端准确率。
- 没有优化前同口径基线，因此不报告性能提升百分比。
