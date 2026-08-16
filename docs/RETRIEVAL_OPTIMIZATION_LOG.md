# 检索优化实验日志

## 基线/P0：冻结集检索失败诊断

### 修改原因

优化前缺少逐案例失败分类，无法判断后续应优先修复召回、排序、语义、融合还是标签问题。

### 修改内容

- 新增 `scripts/diagnose_retrieval_failures.py`。
- 使用已有冻结查询向量缓存生成逐案例 BM25/Vector/Hybrid 排名。
- 输出 `evaluation/reports/latest/retrieval_failure_analysis.json` 和 `.md`。

### 是否重建索引

- BM25：否
- Vector：否
- Embedding API 调用：否，全部复用 `query_embedding_cache.json`

### 修改前

冻结集 44 条可检索案例：

- BM25：Recall@1 68.18%，Recall@5 95.45%，MRR 80.11%，nDCG@5 79.65%
- Vector：Recall@1 43.18%，Recall@5 77.27%，MRR 55.91%，nDCG@5 57.71%
- Hybrid：Recall@1 65.91%，Recall@5 86.36%，MRR 74.89%，nDCG@5 73.83%

### 修改后

指标与修改前一致，因为本轮仅新增诊断，不改检索链路。

### 失败案例变化

失败分类：

- A 排序问题：2
- B 词法检索问题：1
- C 语义检索问题：7
- D 融合问题：0
- E 真正召回问题：3
- F 标签问题：0
- OK：31

### 结论

- KEEP
- 原因：诊断报告能支持后续实验；未改变生产检索行为，也未调用全量 Embedding。

## 实验 1：P1 动态检索路由

### 修改原因

原先所有非 Exact 查询统一进入 `bm25 + vector`，但数值参数和带上下文的专业关键词问题经常被 Vector 排序干扰。

### 修改内容

- `app/retrieval/models.py` 给 `QueryAnalysis` 增加 `route_type`。
- `app/retrieval/service.py` 的 `QueryAnalyzer` 增加通用分类逻辑。
- 路由规则：
  - 明确条款号：`exact`
  - 明确表号：`exact`
  - 多目标复杂问题：`bm25 + vector`
  - 数值参数或上下文限定专业关键词：`bm25`
  - 默认自然语言问题：`bm25 + vector`
- 新增对应单元测试。

### 是否重建索引

- BM25：否
- Vector：否
- Embedding API 调用：否，评测复用冻结查询向量缓存

### 修改前

固定 Hybrid 基线：

- Recall@1 65.91%
- Recall@5 86.36%
- MRR 74.89%
- nDCG@5 73.83%

### 修改后

动态路由：

- Recall@1 84.09%
- Recall@5 97.73%
- MRR 90.23%
- nDCG@5 88.16%

动态路由分布：

- `bm25`：24
- `bm25 + vector`：10
- `exact`：10

### 失败案例变化

- 固定 Hybrid 有 7 条案例未进入 Top5。
- 动态路由下未进入 Top5 的案例减少到 1 条。

### 结论

- KEEP
- 原因：Recall@5 从 86.36% 提升到 97.73%，同时超过 BM25 基线 95.45%；Recall@1 和 MRR 均明显提高，且未重建任何索引。
