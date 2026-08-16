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
2. 每个检查项检索 Top5 直接条款，模型综合判断，不强制要求唯一条款。
3. 模型输出必须是结构化结果，不允许自由文本直接决定合规状态。
4. 模型输出的所有 `evidence_id`、数值、条款号必须由代码校验。
5. 证据不足、冲突、表格未结构化、条件缺失时，必须安全降级，不能强行通过或拒绝。
6. 不建立“每条规范对应一条确认规则”的规则数据库。

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

检索子查询只使用：

```text
控制室 照度 无人值守
```

不把 `500` 和 `lx` 作为检索词。实际值只在最终判断阶段使用。

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
standard_code
clause_id
table_id
page_number
quote
```

Top5 用于让模型综合判断，避免只依赖单一条款导致误判。

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
    limitations: list[str]
```

说明：

- `reasoning` 必须解释为什么得出当前状态。
- `evidence_ids` 必须来自当前检查项对应的检索 Evidence。
- `applied_conditions` 表示模型实际使用的适用条件。
- `limitations` 表示表格未结构化、条件不完整、外部标准依赖等限制。

## 6. 代码校验规则

在模型输出后增加 `ComplianceJudgementValidator`，至少校验：

1. `evidence_ids` 是本轮检索返回的 Evidence 子集。
2. `check_item_id` 属于当前检查项。
3. `status` 是合法枚举。
4. 模型理由中的数值必须能在绑定 Evidence 原文中找到。
5. 模型引用的条款号或表号必须与绑定 Evidence 的 citation 一致。
6. 如果 Evidence 中存在冲突，模型不能输出 `COMPLIANT`。
7. 如果表格 Evidence 是 `PARTIAL` 或 `NEEDS_MANUAL_ANNOTATION`，必须降级为 `MANUAL_REVIEW_REQUIRED`。

## 7. 汇总策略

对所有检查项判断结果汇总：

```text
所有强制项均为 COMPLIANT
  -> COMPLIANT

存在至少一个 NON_COMPLIANT
  -> NON_COMPLIANT

存在 INSUFFICIENT_INFORMATION、CONFLICT、MANUAL_REVIEW_REQUIRED
  -> 对应状态
```

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
app/workflow/nodes.py
app/workflow/graph.py
```

具体变化：

- `AdaptiveRetrievalPlanner` 为每个 `CheckItem` 生成独立检索计划。
- 新增 `judge_compliance` 节点，调用 RAG 判断器。
- `evaluate_compliance` 改为消费模型判断结果，不再依赖规则库。
- 保留 `RuleEvaluator` 作为可选的后验校验层，但不作为唯一判断路径。

## 9. 实现顺序

1. 先定义 `ComplianceJudgement` 和 `ComplianceJudgementList`。
2. 实现 `ComplianceJudge`，输入检查项和 Top5 Evidence。
3. 实现 `ComplianceJudgementValidator`。
4. 修改 `AdaptiveRetrievalPlanner`，按检查项生成子查询。
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
