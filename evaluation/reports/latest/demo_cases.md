# M11 三类演示案例

## 基础条款查询

- 终态：`COMPLETED`（期望 `COMPLETED`）
- 通过：`True`
- 意图：`CLAUSE_LOOKUP`
- 检查项：0，证据：18
- 工作流：`normalize_input -> classify_intent -> answer_query -> generate_report`

## 检查清单

- 终态：`COMPLETED`（期望 `COMPLETED`）
- 通过：`True`
- 意图：`CHECKLIST_GENERATION`
- 检查项：2，证据：0
- 工作流：`normalize_input -> classify_intent -> identify_scenario -> generate_checklist -> generate_report`

## 完整设计描述预审

- 终态：`WAITING_CONFIRMATION`（期望 `WAITING_CONFIRMATION`）
- 通过：`True`
- 意图：`COMPLIANCE_REVIEW`
- 检查项：2，证据：0
- 工作流：`normalize_input -> classify_intent -> identify_scenario -> extract_check_items -> confirm`
