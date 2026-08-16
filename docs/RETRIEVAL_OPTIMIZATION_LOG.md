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
