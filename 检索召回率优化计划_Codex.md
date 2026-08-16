# 检索召回率优化计划

> 目标：在不破坏当前稳定能力的前提下，按优先级逐项优化检索效果。  
> 当前基线参考：BM25 Recall@5 ≈ 95.45%，Vector Recall@5 ≈ 77.27%，Hybrid Recall@5 ≈ 86.36%。  
> 核心原则：**一次只改一类变量，跑同一冻结测试集，记录前后指标，再决定是否保留。**

## 0. 执行规则

Codex 每次开始优化前都必须：

1. 阅读当前检索实现、评测脚本和冻结测试集说明。
2. 记录当前基线：Recall@1、Recall@5、MRR、nDCG@5、P50/P95 latency。
3. 一次只实施一个优化点，不同时修改多个核心变量。
4. 修改后运行同一冻结测试集，输出前后对比。
5. 若指标下降，回退本轮修改，不继续叠加。
6. 不允许针对冻结测试集中的具体问题写硬编码规则。
7. 不允许修改冻结测试集答案来“提高指标”。
8. 优先避免重新调用全量 Embedding；只有明确修改了文档向量输入、Chunk 或 Embedding 模型时才重建向量索引。
9. 每轮实验结果追加到 `docs/RETRIEVAL_OPTIMIZATION_LOG.md`。
10. 完成一个优先级后再进入下一个，不要跳步。

---

# P0：失败案例诊断

## 当前不足

当前最大问题不是单纯“召回率低”，而是：

- BM25 Recall@5 已较高；
- Vector 明显弱于 BM25；
- Hybrid 反而低于纯 BM25；
- 还无法明确每个失败案例究竟属于召回、排序、融合还是标签问题。

因此必须先做诊断，再优化算法。

## 任务

为冻结测试集生成逐案例诊断报告，至少记录：

```text
case_id
query
gold_unit_ids
BM25 gold rank
Vector gold rank
Hybrid gold rank
BM25 Top20
Vector Top20
Hybrid Top10
失败分类
```

失败分类统一为：

```text
A. BM25/Vector 已召回，但未进入最终 Top5 -> 排序问题
B. BM25 未召回，Vector 已召回 -> 词法检索问题
C. Vector 未召回，BM25 已召回 -> 语义检索问题
D. 两路均召回，但 Hybrid 丢失 -> 融合问题
E. 两路 Top20 都没有 -> 真正召回问题
F. Gold 标签与真实知识单元不一致 -> 标签问题
```

## 验收

生成：

```text
evaluation/reports/latest/retrieval_failure_analysis.md
evaluation/reports/latest/retrieval_failure_analysis.json
```

并总结各类失败数量。

## 是否重建向量

**否。**

---

# P1：动态检索路由

## 当前不足

当前检索路由主要是：

```text
明确条款号/表号 -> Exact
其他自然语言问题 -> BM25 + Vector
```

分类过粗。对于专业术语、数字、参数明确的问题，Vector 可能反而干扰 BM25 排序。

## 优化目标

增加更细的检索类型：

```text
Exact 型 -> Exact
专业关键词 / 数值参数型 -> BM25 主导
明显自然语言改写 / 同义表达型 -> BM25 + Vector
复杂多目标问题 -> 拆分子查询后分别检索
```

不要让所有非 Exact 问题默认进入相同 Hybrid 路线。

## 建议实现

新增或完善 QueryAnalyzer / RetrievalPlanner 的分类字段，例如：

```text
exact
keyword
numeric
semantic
multi_goal
table
```

每种类型选择不同检索器和候选策略。

## 评测重点

比较：

```text
原始固定 Hybrid
vs
动态路由
```

重点观察：Recall@5 是否 >= 当前 BM25 基线、Recall@1/MRR 是否提高、Vector 是否只在真正需要时参与。

## 是否重建向量

**否。**

## 已执行结果

已实现 `route_type` 动态分类：

- 明确条款号/表号：`exact`
- 多目标复杂问题：`bm25 + vector`
- 数值参数或上下文限定专业关键词：`bm25`
- 默认自然语言问题：`bm25 + vector`

冻结集 44 条可检索案例，固定 Hybrid 与动态路由对比如下：

| 指标 | 修改前固定 Hybrid | 修改后动态路由 |
|---|---:|---:|
| Recall@1 | 65.91% | 84.09% |
| Recall@5 | 86.36% | 97.73% |
| MRR | 74.89% | 90.23% |
| nDCG@5 | 73.83% | 88.16% |

动态路由分布：`bm25` 24 条，`bm25 + vector` 10 条，`exact` 10 条。

结论：`KEEP`。

---

# P1：领域 Query Rewrite 与同义词扩展

## 当前不足

当前领域同义词覆盖较少，例如只有 UPS、供电时间、间距、照度等少量表达，无法覆盖三份规范中的大量工程术语。

## 优化目标

从项目已有结构化数据中自动构建领域别名词典：

- 术语
- 缩写
- 条款标题
- 设备名称
- 常见口语表达

例如：

```text
信号线 -> 仪表电缆
动力线 -> 电力电缆
急停 -> 紧急停车 / ESD
控制柜 -> 机柜 / 控制机柜
PLC -> 可编程逻辑控制器
RTU -> 远程终端单元
```

## 实现原则

原 Query 不删除，只扩展：

```text
原始 Query + 规范术语 + 同义表达
```

禁止生成过长 Query。优先使用确定性词典；LLM Query Rewrite 作为后备路线。

## 评测重点

单独比较：

- BM25 原始 Query
- BM25 + 同义词
- Hybrid 原始 Query
- Hybrid + Query Rewrite

记录 Query Rewrite 真正新增召回的案例数。

## 是否重建向量

**否。** 查询时只需实时生成新 Query 的 Embedding。

## 已执行结果

尝试扩展领域同义词词典并复用统一 `expand_query`。冻结集对比结果：

| 指标 | BM25 原始 | BM25 扩展 | Hybrid 原始 | Hybrid 扩展 |
|---|---:|---:|---:|---:|
| Recall@1 | 68.18% | 65.91% | 65.91% | 61.36% |
| Recall@5 | 95.45% | 95.45% | 86.36% | 84.09% |
| MRR | 80.11% | 77.16% | 74.89% | 70.80% |
| nDCG@5 | 79.65% | 77.38% | 73.83% | 70.72% |

新增召回：BM25 0 条，Hybrid 0 条。丢失案例：Hybrid 1 条，`retrieval-098`。

结论：`REVERT`。本轮词典扩展没有带来新增召回，并降低 MRR 与 Hybrid Recall@5。

---

# P1：BM25 领域分词优化

## 当前不足

当前中文主要使用连续 bigram，例如：

```text
仪表电缆 -> 仪表 / 表电 / 电缆
```

优点是比单字分词稳定，但没有完整保留领域实体。

## 优化目标

改成：

```text
领域完整词 + 普通词 + bigram
```

例如：

```text
仪表电缆 -> 仪表电缆 / 仪表 / 电缆 / 仪表 / 表电 / 电缆
```

## 建议

优先从现有规范自动提取领域词表，而不是完全手工维护。重点保留：设备名、系统名、仪表名、缩写、单位、标准号、条款号、表号。

## 评测重点

主要观察 Recall@1、MRR、nDCG@5。

## 是否重建向量

**否。** 但需要重新构建 BM25 索引。

> 如果当前 `build_index.py` 会顺带重新生成所有 Vector，优先增加 `--bm25-only` 或等价能力，避免无意义调用 Embedding API。

---

# P1：BM25 字段权重

## 当前不足

当前 `retrieval_text` 中标准号、文件名、章节路径、父条款、当前条款和正文基本作为一整段文本参与 BM25，没有明显字段重要性区别。

## 优化目标

测试类似 BM25F 的字段权重：

```text
条款标题：3.0
表格标题：3.0
当前条款号：2.5
专业实体：2.0
正文：1.0
父章节：1.0
文件名：0.2
固定类型标签：0.2
```

不要直接把以上权重当最终答案。必须在 dev 集调参，在 frozen_test 只做最终验证。

## 评测重点

Recall@1、MRR、nDCG@5。

## 是否重建向量

**否。** 需要重新构建 BM25 索引。

---

# P1：接入真正的 Reranker

## 当前不足

当前项目已经预留 Reranker 接口，但检索效果主要仍依赖 BM25、Vector 和 RRF。

BM25 Recall@5 已高，但 Recall@1 仍有提升空间，说明很多正确条款已经找到了，只是排序不够靠前。

## 优化目标

```text
BM25 Top20
+
Vector Top20
        ↓
候选去重
        ↓
Reranker
        ↓
最终 Top5
```

优先使用轻量 Cross-Encoder 或其他可复现 Reranker。若使用 LLM rerank，必须记录延迟、成本和稳定性。

## 评测重点

Recall@1、MRR、nDCG@5、P50/P95 latency。

如果 Recall@5 不变但 Recall@1 和 MRR 明显提升，可以保留。

## 是否重建向量

**否。**

---

# P2：Embedding 输入文本消融实验

## 当前不足

当前 Vector 索引使用：

```text
context_prefix + 原始 content
```

作为 Embedding 输入。Context 可能帮助，也可能因为固定 Metadata 太多而稀释正文语义。

## 优化目标

至少比较以下方案：

### A
```text
正文
```

### B
```text
条款标题 + 正文
```

### C
```text
章节标题 + 条款标题 + 正文
```

### D
```text
当前完整 context_prefix + 正文
```

每种方案使用相同 Embedding 模型和相同冻结测试集。

## 评测重点

Vector Recall@1、Vector Recall@5、Vector MRR、Hybrid Recall@5。

不要默认“上下文越多越好”。

## 是否重建向量

**是。** 每个实验方案都需要重新生成文档向量，因此这一阶段放在 P2，只有 P0/P1 完成后再做。

---

# P2：Parent-Child / 多粒度检索

## 当前不足

目前主要是：

```text
先检索 Knowledge Unit -> 命中后再扩展父条款和相邻条款
```

对于宽泛问题和多条款问题，单一粒度可能不足。

## 优化目标

构建多粒度检索表示：

```text
Child：具体子条款
Parent：完整小节
Title：章节 / 条款主题
```

查询路由：

```text
具体参数问题 -> Child 优先
宽泛总体要求 -> Parent 优先
复杂问题 -> 多粒度联合召回
```

最终回答仍必须绑定到可追溯的原始条款 Evidence。

## 评测重点

单独分析宽泛问题、多条款问题、参数问题，不只看总体 Recall。

## 是否重建向量

**是。** 因为新增了新的向量索引表示。

---

# P3：Embedding 模型对比

## 当前不足

只有在完成前面的输入文本、Query 和路由优化后，才能判断 Vector 的瓶颈是否来自 Embedding 模型本身。

## 优化目标

选择 2-3 个候选 Embedding 模型，在完全相同的 Chunk、Context、Query、测试集、Top-K 条件下比较。

## 评测指标

- Recall@1
- Recall@5
- MRR
- nDCG@5
- Embedding 延迟
- 单次查询成本
- 建库耗时

## 是否重建向量

**是。** 每换一个 Embedding 模型必须完整重建 Vector 索引。

---

# 最终目标

不单纯追求 Recall@5 从 95% 刷到 100%。最终更关注：

```text
最终 Recall@5 >= 当前 BM25 基线
Recall@1 明显提高
MRR / nDCG@5 提高
Vector 能补回 BM25 漏召回案例
Hybrid 不再拖累 BM25
延迟与 API 成本保持可接受
```

最终实验应能够回答：

1. 哪类问题适合 BM25？
2. 哪类问题 Vector 能真正补充？
3. 哪类问题应该 Exact？
4. Query Rewrite 能挽回多少失败案例？
5. Reranker 提升了多少首位排序能力？
6. Contextual Embedding 是否真的有效？
7. Hybrid 的收益是否经过消融实验验证？

---

# Codex 每轮实验输出格式

每完成一个优化项，在 `docs/RETRIEVAL_OPTIMIZATION_LOG.md` 追加：

```markdown
## 实验 X：名称

### 修改原因
原本存在的问题。

### 修改内容
修改了哪些文件和逻辑。

### 是否重建索引
- BM25：
- Vector：
- Embedding API 调用：

### 修改前
- Recall@1:
- Recall@5:
- MRR:
- nDCG@5:
- P95 latency:

### 修改后
- Recall@1:
- Recall@5:
- MRR:
- nDCG@5:
- P95 latency:

### 失败案例变化
- 修复：
- 新增失败：
- 无变化：

### 结论
- KEEP / REVERT
- 原因：
```

---

# Codex 执行顺序

严格按照以下顺序：

```text
1. P0 失败案例诊断
2. P1 动态检索路由
3. P1 Query Rewrite / 领域同义词
4. P1 BM25 领域分词
5. P1 BM25 字段权重
6. P1 Reranker
7. P2 Embedding 输入文本消融
8. P2 Parent-Child 多粒度检索
9. P3 Embedding 模型对比
```

每完成一项后先停止，输出本轮实验结果和 KEEP / REVERT 结论，再继续下一项。
