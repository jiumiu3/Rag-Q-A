# M11 评测数据与复现说明

本目录包含检索、问答、设计拆解、合规执行四类固定评测集。版本、样本数、标注方式和边界记录在 `MANIFEST.json`；这些数据仅用于评测，未参与训练。

## 数据集

| 文件 | 规模 | 用途 | 标签来源与限制 |
|---|---:|---|---|
| `retrieval_gold.jsonl` | 50 | Recall@1/5、MRR | 从当前 SQLite 精确条款键分层派生，不覆盖开放语义检索 |
| `qa_gold.jsonl` | 30 | 路由、状态、引用、数值、拒答 | 20 正例与 10 个拒答/降级案例；自由文本评分未计入 |
| `design_gold.jsonl` | 30 段/61 项 | 原子属性召回与顺序 | 实现阶段人工夹具，不是独立盲测集 |
| `design_detail_gold.jsonl` | 12 段/24 项 | 对象、参数、单位 | 当前属于实现阶段基准 |
| `compliance_gold.jsonl` | 20 | 确定性执行、安全降级、trace | 人工规则夹具，只测执行器 |
| `datasets/semantic_retrieval.jsonl` | 4 | BM25、Vector、混合、自适应检索 | 自然表达与多属性组合，结果未计入正式指标 |
| `datasets/adversarial_qa.jsonl` | 5 | 精确错误拒答与安全动作 | 含错误前提和 Prompt Injection |
| `datasets/engineering_cases.jsonl` | 2/4项 | 多检查项工程预审 | 非敏感合成案例，结论未计入正式指标 |
| `datasets/rule_extraction.jsonl` | 4 | 候选规则结构化抽取 | 不与人工修改后规则混算 |
| `datasets/retrieval_realistic_small.jsonl` | 16 | 小规模分层真实检索 | 默认最多 12 次查询 Embedding 调用；标签尚未冻结 |
| `datasets/rag_compliance_business_small.jsonl` | 10 | RAG检索与合规判断业务金标 | 从3份规范PDF的OCR条款构造，含页码、原文、知识单元与安全降级标签 |
| `annotations/*.jsonl` | 待填写 | 可选逐条评分 | 未填写时报告 `NOT_EVALUATED` |
| `demo_cases.json` | 3 | 查询、清单、完整预审演示 | 校验真实工作流终态 |

## 运行

本地已有环境：

```bash
python scripts/run_evaluation.py
python scripts/run_demo_cases.py
python scripts/evaluate_realistic_retrieval.py
```

## 自动生成与冻结测试集

五类扩展测试集从 `data/knowledge.db` 的真实知识单元确定性生成，黄金标签不会传给
Agent、检索器、设计拆解器或合规执行器。先运行 3 条/类冒烟：

```bash
PYTHONPATH=. python scripts/generate_evaluation_datasets.py --limit 3
```

生成完整候选并校验：

```bash
PYTHONPATH=. python scripts/generate_evaluation_datasets.py
PYTHONPATH=. python scripts/validate_generated_datasets.py
```

首次创建 60% 开发集和 40% 冻结测试集：

```bash
PYTHONPATH=. python scripts/generate_evaluation_datasets.py --freeze
```

冻结集存在时命令会拒绝覆盖。只有确认升级 `dataset_version` 后才能显式传入
`--regenerate-frozen`。运行真实模块并生成 `metrics.json`、Markdown/HTML 报告和失败列表：

```bash
PYTHONPATH=. python scripts/run_frozen_evaluation.py
```

当前没有已确认的 `executable_rules` 基准，因此端到端案例保留为 `pending`，相关正式指标是
`NOT_EVALUATED`；自动候选规则不能替代可执行规则基准。远程 Embedding 不可用时 Vector 和
Hybrid 同样标记为 `NOT_EVALUATED`。

## 小规模RAG合规业务金标集

`datasets/rag_compliance_business_small.jsonl` 是首批10条独立业务案例，不复用执行器单元测试夹具。
每条案例包含自然语言设计描述、原子检查项、期望检索条款、确定性判断结构和PDF溯源信息。
案例覆盖三部分规范，以及合规、不合规、信息不足和人工复核四类终态；同时覆盖包含/排除边界、
单位换算、布尔及类别禁止、区间、条件和动作联锁、外部标准依赖。扫描版PDF通过项目OCR提取，
`source_image` 用于回看原页，`expected_unit_ids` 用于检索评测。该版本标记为
`assistant_verified`，适合作为开发集和首轮回归集；在取得独立领域专家复核前，不宣称为生产级盲测集。

只验证数据集规模、溯源字段、标签和覆盖面：

```bash
pytest -q tests/test_rag_compliance_business_dataset.py
```

当前生成集版本为 `generated-v1.3.0`。向量索引严格绑定模型、脱敏配置指纹、1024 维度、
知识单元源哈希和 contextual strategy；RRF 权重只在 dev 集选择，详细记录见
`reports/latest/fusion_dev.json`。端到端任务识别和无确认规则时的安全降级已由真实 Agent 运行并
标为 `PROVISIONAL`，最终合规状态、false compliant 等指标在没有可执行规则基准前继续保持
`NOT_EVALUATED`。

CPU Docker（源码已挂载时无需重建）：

```bash
docker compose run --rm --no-deps agent python scripts/run_evaluation.py
docker compose run --rm --no-deps agent python scripts/run_demo_cases.py
```

输出位于 `evaluation/reports/latest/`：

- `metrics.json`：指标、逐条失败、版本绑定和声明边界；
- `report.md`、`report.html`：人类可读误差报告；
- `demo_cases.json`、`demo_cases.md`：三类真实演示结果。
- `retrieval_realistic.json`：BM25/Hybrid 分层指标、目标覆盖率、延迟和逐条结果。

小规模真实检索评测默认执行 4 个 exact、12 个 BM25 和 12 个 Hybrid 任务。BM25 不调用模型，Hybrid 对每个非 exact 查询调用一次索引对应的真实 Embedding API，总调用上限为 12。脚本显示完成比例、耗时和 ETA；如索引使用 `local-hash`，会拒绝输出 Vector/Hybrid 指标。可先运行 `--limit 3` 冒烟，或通过 `--routes bm25` 完全离线检查词法召回。

`retrieval_realistic_review.jsonl` 中只有 `status=approved` 的案例进入正式指标；全部为 `pending` 时报告状态是 `PROVISIONAL`，不能对外宣称为生产真实效果。

每次运行绑定代码内容哈希、Git commit（若存在）、索引版本/源/配置哈希、模型配置哈希、提示词与评测器哈希、数据集版本和逐文件 SHA256。模型配置哈希不含 API Key。

## 不得外推的结论

- `free_text_semantic_correctness`、`llm_rule_extraction_accuracy` 和 `production_end_to_end_compliance_accuracy` 当前为 `NOT_EVALUATED`。
- 27 张复杂表格仍强制人工复核；8 张 OCR 异常字符候选页不是本评测中的已确认错误。
- 报告分别给出 BM25、Vector、混合和自适应检索；没有同口径历史基线时不报告提升比例。
- 直接模型回答基线不提供 Evidence，只允许离线定义，绝不进入生产默认路径。
