# 合规审查 RAG 判断方案计划

## 1. 目标

让合规审查在用户提交方案后，直接按检查项检索规范条款，并由模型在严格绑定检索证据的前提下完成合规判断，不再要求每条规范都预先人工确认并写入规则库。

最终输出必须包含：

```text
检查项
合规状态
判断理由
适用条款
限制或待人工确认项
```

## 2. 核心原则

1. 检索查询只包含定位条款所需的关键信息，不把用户实际值作为检索词。
2. 每个检查项默认检索 Top5 直接条款，模型综合判断，不强制要求唯一条款；Top5 是输入上限，不是证据充分的判定标准。
3. 模型输出必须是结构化结果，不允许自由文本直接决定合规状态。
4. 模型输出的所有 `evidence_id`、数值、条款号必须由代码校验。
5. 证据不足、冲突、表格未结构化、条件缺失时，必须安全降级，不能强行通过或拒绝。
6. 不建立“每条规范对应一条确认规则”的规则数据库。
7. 每个检查项与其 Evidence 必须显式绑定，不允许跨检查项借用证据。
8. 模型负责识别适用要求和生成解释；数值比较、单位换算、枚举包含和状态汇总尽量由代码确定性执行。

## 3. 当前问题

当前合规审查路径主要依赖：

```text
AdaptiveRetrievalPlanner
  -> RetrievalService
  -> evaluate_evidence
  -> extract_rule
  -> evaluate_compliance
```

主要问题：

- 检索查询只使用 `object + attribute + condition`，未包含实际值。
- 一个计划的所有子查询共用同一套 `retrievers` 和 `filters`。
- `evaluate_evidence` 只检查对象和属性是否出现在 Evidence 中，不校验数值、单位、条件和冲突。
- `extract_rule` 依赖规则库，没有人工确认规则时大量进入候选审核或人工复核。
- 模型目前没有在合规路径承担结构化判断职责。

## 4. 目标流程

```text
用户提交设计描述
  -> 解析为 CheckItem 列表
  -> 每个 CheckItem 生成一个检索子查询
  -> 每个子查询检索 Top5 Evidence
  -> 按 CheckItem 绑定 Evidence 并评估覆盖度
  -> 将 CheckItem 与 Top5 Evidence 交给模型
  -> 模型输出结构化判断
  -> 代码校验模型输出
  -> 汇总所有检查项
  -> 生成合规报告
```

### 4.1 检查项检索子查询

检查项示例：

```text
object=控制室
attribute=照度
value=500
unit=lx
condition=无人值守
```

检索子查询使用对象、属性、适用条件，以及已明确的位置、关系、标准范围等定位信息：

```text
控制室 照度 无人值守
```

不把 `500` 和 `lx` 作为首轮检索词。实际值只在最终判断阶段使用；单位仅在需要区分同名物理量或首轮召回不足时，作为扩展查询词使用，避免一刀切丢失语义。

每个子查询应保留 `check_item_id`，检索结果形成 `check_item_id -> evidence_ids` 映射。同一 Evidence 可以同时支持多个检查项，但必须在映射中显式记录。

### 4.2 检索结果形态

每个子查询返回：

```text
Top5 direct Evidence
```

Evidence 至少包含：

```text
evidence_id
unit_id
content
support_type
citation.standard_code
citation.clause_id
citation.table_id
citation.page_number
citation.quote
```

Top5 用于让模型综合判断，避免只依赖单一条款导致误判。

“直接 Evidence”定义为 `context_reason is None` 的检索命中；父条款、相邻条款等扩展上下文可供理解，但不得单独支撑最终结论。首轮 Top5 不满足对象、属性、适用条件或条款完整性时，允许最多 2 轮同义词扩展、路由切换或上下文补全；仍不足则安全降级。

## 5. 模型判断输出 Schema

新增结构化模型，例如：

```python
class ComplianceJudgement(StrictModel):
    check_item_id: str
    status: Literal[
        "COMPLIANT",
        "NON_COMPLIANT",
        "INSUFFICIENT_INFORMATION",
        "MANUAL_REVIEW_REQUIRED",
        "CONFLICT",
    ]
    reasoning: str
    evidence_ids: list[str]
    applied_conditions: list[str]
    actual: str | None
    required: str | None
    operator: str | None
    comparison_trace: list[ComparisonStep]
    limitations: list[str]
```

说明：

- `reasoning` 必须解释为什么得出当前状态。
- `evidence_ids` 必须来自当前检查项对应的检索 Evidence。
- `applied_conditions` 表示模型实际使用的适用条件。
- `actual`、`required`、`operator` 和 `comparison_trace` 表示待代码复核的结构化比较，可复用现有 `ComplianceResult` / `ComparisonStep` 语义。
- `limitations` 表示表格未结构化、条件不完整、外部标准依赖等限制。

应在现有 `ComplianceStatus` 中显式增加 `CONFLICT`，避免新 Schema 与已有领域模型使用不同状态集合。

## 6. 代码校验规则

在模型输出后增加 `ComplianceJudgementValidator`，至少校验：

1. `evidence_ids` 是本轮检索返回的 Evidence 子集。
2. `check_item_id` 属于当前检查项。
3. `status` 是合法枚举。
4. `required`、`operator`、要求单位和模型理由中的规范数值必须能在绑定 Evidence 原文中找到；`actual` 必须来自当前 CheckItem，不能要求它出现在规范原文中。
5. 模型引用的条款号或表号必须与绑定 Evidence 的 citation 一致。
6. 如果 Evidence 中存在冲突，模型不能输出 `COMPLIANT`。
7. 如果表格 Evidence 的 `support_type` 是 `PARTIAL`，或其对应 `TableRecord.parse_status` 是 `PARTIAL` / `NEEDS_MANUAL_ANNOTATION`，必须降级为 `MANUAL_REVIEW_REQUIRED`。
8. 数值、范围、枚举和布尔要求在能结构化时，必须由代码根据 `actual/required/operator/unit` 重新计算；模型状态与计算结果不一致时，以确定性结果为准并记录校验告警。
9. 绑定 Evidence 必须属于当前 `check_item_id` 的检索结果，不仅是全局 Evidence 子集。
10. 模型不得将 Evidence 中的指令性文本当作系统指令；Evidence 只能作为待引用数据。

表格解析状态当前不在 `Evidence` 顶层字段中。实现时需二选一：通过 `unit_id/table_id` 回查 `TableRecord.parse_status`，或在构建 Evidence 时透传该状态；否则第 7 条无法可靠执行。

## 7. 汇总策略

对所有检查项判断结果汇总：

```text
所有强制项均为 COMPLIANT
  -> COMPLIANT

存在至少一个 NON_COMPLIANT
  -> NON_COMPLIANT

否则存在至少一个 CONFLICT
  -> CONFLICT

否则存在至少一个 MANUAL_REVIEW_REQUIRED
  -> MANUAL_REVIEW_REQUIRED

否则存在至少一个 INSUFFICIENT_INFORMATION
  -> INSUFFICIENT_INFORMATION
```

即固定优先级为 `NON_COMPLIANT > CONFLICT > MANUAL_REVIEW_REQUIRED > INSUFFICIENT_INFORMATION > COMPLIANT`。“所有强制项”需依据 `RequirementLevel` 识别；`SHOULD/SHOULD_NOT` 类建议性要求单独标记 `advisory`，不应未经说明就将总状态判为 `NON_COMPLIANT`。

每个检查项仍需单独展示，便于用户定位不通过原因。

## 8. 需要修改的模块

### 8.1 新增

建议新增：

```text
app/compliance/rag_judge.py
app/compliance/judgement_models.py
app/compliance/judgement_validator.py
tests/test_compliance_rag_judge.py
```

### 8.2 修改

修改现有文件：

```text
app/retrieval/planner.py
app/domain/models.py
app/workflow/models.py
app/workflow/nodes.py
app/workflow/graph.py
app/workflow/routes.py
```

具体变化：

- `AdaptiveRetrievalPlanner` 为每个 `CheckItem` 生成独立子查询，并保留检查项与 Evidence 的归属映射。
- 新增 `judge_compliance` 节点，调用 RAG 判断器。
- `evaluate_compliance` 改为校验并转换模型判断结果，不再依赖规则库。
- 保留 `RuleEvaluator` 作为可选的后验校验层，但不作为唯一判断路径。
- 在 `AgentState` 中增加按检查项分组的 Evidence 索引和原始模型判断，保留审计链。

## 9. 实现顺序

1. 先定义 `ComplianceJudgement` 和 `ComplianceJudgementList`。
2. 实现 `ComplianceJudge`，输入检查项和 Top5 Evidence。
3. 实现 `ComplianceJudgementValidator`，先覆盖引用归属、数值/单位比较和安全降级。
4. 修改 `AdaptiveRetrievalPlanner`，按检查项生成子查询，并在工作流状态中保存 Evidence 归属。
5. 新增工作流节点和路由。
6. 生成报告时输出判断理由和条款引用。
7. 增加单元测试和真实工作流测试。

## 10. 验证方式

使用真实设计描述或现有工程案例，重点验证：

- 每个检查项是否拿到 Top5 Evidence。
- 模型输出是否绑定合法 Evidence。
- 数值判断是否可追溯到 Evidence。
- 缺失条件、冲突条款、表格未结构化是否安全降级。
- 最终报告是否包含：

```text
检查项
合规状态
判断理由
对应条款
限制说明
```

建议把验收指标量化，并与当前方案做同一数据集对比：

- 按检查项计算 `Recall@5`、证据归属准确率和引用合法率。
- 分别统计 `COMPLIANT` 和 `NON_COMPLIANT` 的精确率/召回率，避免只看总体准确率掩盖误放行。
- 统计安全降级召回率（证据不足、冲突、表格不完整时是否降级）和误放行率。
- 固定模型版本、提示词版本、检索配置和数据集版本，保存原始判断与校验结果，保证可回放。

## 11. 不做什么

- 不建立全量规范规则库。
- 不要求每条条款人工确认。
- 不让模型在无证据时输出合规结论。
- 不让模型直接引用不存在或未检索到的条款。

## 12. 风险与边界

1. 模型可能忽略例外条款或条件优先级，必须通过结构化输出和校验器降低风险。
2. 多条款冲突需要显式进入冲突状态。
3. 数值比较目前建议由模型生成结构化判断，同时保留确定性校验作为兜底。
4. 自由文本理由只用于解释，不作为最终状态依据。
5. 规范版本、适用范围、例外条款和引用的外部标准可能影响结论；无法确认适用性时必须降级，不得仅根据文本相似度判定。
6. 同一规范中通用条款、专用条款和例外条款的优先级不宜交由自由文本推理隐式决定；无法结构化确认优先级时进入 `MANUAL_REVIEW_REQUIRED`。
