# M11 评测数据与复现说明

本目录包含检索、问答、设计拆解、合规执行四类固定评测集。版本、样本数、标注方式和边界记录在 `MANIFEST.json`；这些数据仅用于评测，未参与训练。

## 数据集

| 文件 | 规模 | 用途 | 标签来源与限制 |
|---|---:|---|---|
| `retrieval_gold.jsonl` | 50 | Recall@1/5、MRR | 从当前 SQLite 精确条款键分层派生，不覆盖开放语义检索 |
| `qa_gold.jsonl` | 30 | 路由、状态、引用、数值、拒答 | 20 正例与 10 个拒答/降级案例；无领域专家自由文本评分 |
| `design_gold.jsonl` | 30 段/61 项 | 原子属性召回与顺序 | 实现阶段人工夹具，不是独立盲测集 |
| `design_detail_gold.jsonl` | 12 段/24 项 | 对象、参数、单位 | 待领域负责人签字确认 |
| `compliance_gold.jsonl` | 20 | 确定性执行、安全降级、trace | 人工规则夹具，只测执行器 |
| `demo_cases.json` | 3 | 查询、清单、完整预审演示 | 校验真实工作流终态 |

## 运行

本地已有环境：

```bash
python scripts/run_evaluation.py
python scripts/run_demo_cases.py
```

CPU Docker（源码已挂载时无需重建）：

```bash
docker compose run --rm --no-deps agent python scripts/run_evaluation.py
docker compose run --rm --no-deps agent python scripts/run_demo_cases.py
```

输出位于 `evaluation/reports/latest/`：

- `metrics.json`：指标、逐条失败、版本绑定和声明边界；
- `report.md`、`report.html`：人类可读误差报告；
- `demo_cases.json`、`demo_cases.md`：三类真实演示结果。

每次运行绑定代码内容哈希、Git commit（若存在）、索引版本/源/配置哈希、模型配置哈希、提示词与评测器哈希、数据集版本和逐文件 SHA256。模型配置哈希不含 API Key。

## 不得外推的结论

- `free_text_semantic_correctness`、`llm_rule_extraction_accuracy` 和 `production_end_to_end_compliance_accuracy` 当前为 `NOT_EVALUATED`。
- 27 张复杂表格仍强制人工复核；8 张 OCR 异常字符候选页不是本评测中的已确认错误。
- 当前目录没有独立优化前同口径基线，不报告性能提升比例。
