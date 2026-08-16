# 检索失败诊断

- 数据集：`evaluation/frozen_test/retrieval.jsonl`
- 评测案例：44
- 排除不可回答案例：4

## 路由指标

| 路由 | Recall@1 | Recall@5 | MRR | nDCG@5 | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 0.6818 | 0.9545 | 0.8011 | 0.7965 | 13.8ms | 32.2ms |
| vector | 0.4318 | 0.7727 | 0.5591 | 0.5771 | 16.8ms | 33.9ms |
| hybrid | 0.6591 | 0.8636 | 0.7489 | 0.7383 | 30.3ms | 65.9ms |

## 失败分类

| 分类 | 数量 |
|---|---:|
| A | 2 |
| B | 1 |
| C | 7 |
| E | 3 |
| OK | 31 |

## 逐案例诊断

### retrieval-027 · term · OK
- Query：`规范中“孔板流量计”具体指什么？ 请限定在Q/GGW 02005.3-2022第5.3.5.7条的上下文内回答。`
- Gold：`['chunk_e065d0844c9c9434029c']`
- BM25 gold rank：`{'chunk_e065d0844c9c9434029c': 1}`
- Vector gold rank：`{'chunk_e065d0844c9c9434029c': 1}`
- Hybrid gold rank：`{'chunk_e065d0844c9c9434029c': 1}`

### retrieval-024 · term · OK
- Query：`规范中“每条计量支路设置一台流量计算机，进行工况和标况条件下体积”具体指什么？ 请限定在Q/GGW 02005.3-2022第5.3.4.3条的上下文内回答。`
- Gold：`['chunk_8f6a84215f67c84f2a06']`
- BM25 gold rank：`{'chunk_8f6a84215f67c84f2a06': 1}`
- Vector gold rank：`{'chunk_8f6a84215f67c84f2a06': 1}`
- Hybrid gold rank：`{'chunk_8f6a84215f67c84f2a06': 1}`

### retrieval-093 · multi_clause · OK
- Query：`请综合说明规范性引用文件与规范性引用文件两方面的规范要求，范围限定为Q/GGW 02005.2-2022和Q/GGW 02005.3-2022。`
- Gold：`['chunk_13d97808a6993afacdd5', 'chunk_5ffd4081fccc2d41d21c']`
- BM25 gold rank：`{'chunk_5ffd4081fccc2d41d21c': 1, 'chunk_13d97808a6993afacdd5': 2}`
- Vector gold rank：`{'chunk_13d97808a6993afacdd5': 1, 'chunk_5ffd4081fccc2d41d21c': 3}`
- Hybrid gold rank：`{'chunk_5ffd4081fccc2d41d21c': 1, 'chunk_13d97808a6993afacdd5': 2}`

### retrieval-067 · numeric · A
- Query：`计量系统有哪些数值或精度限制？ 请限定在Q/GGW 02005.3-2022第5条的上下文内回答。`
- Gold：`['chunk_0f3b688d89cdaf4a93d9']`
- BM25 gold rank：`{'chunk_0f3b688d89cdaf4a93d9': 20}`
- Vector gold rank：`{'chunk_0f3b688d89cdaf4a93d9': 11}`
- Hybrid gold rank：`{'chunk_0f3b688d89cdaf4a93d9': 7}`

### retrieval-083 · table · OK
- Query：`Q/GGW 02005.3-2022 表3的适用条件是什么？`
- Gold：`['table_8fdbf6c8ecb89daef24a']`
- BM25 gold rank：`{'table_8fdbf6c8ecb89daef24a': 3}`
- Vector gold rank：`{'table_8fdbf6c8ecb89daef24a': 2}`
- Hybrid gold rank：`{'table_8fdbf6c8ecb89daef24a': 1}`

### retrieval-050 · semantic · OK
- Query：`工程设计涉及输油管道控制阀结构要求如下时需要满足哪些要求？ 请限定在Q/GGW 02005.2-2022第7.1.6条的上下文内回答。`
- Gold：`['chunk_cd9cd1e16f54dae8ea40']`
- BM25 gold rank：`{'chunk_cd9cd1e16f54dae8ea40': 1}`
- Vector gold rank：`{'chunk_cd9cd1e16f54dae8ea40': 5}`
- Hybrid gold rank：`{'chunk_cd9cd1e16f54dae8ea40': 1}`

### retrieval-071 · numeric · OK
- Query：`仪表信号有哪些数值或精度限制？ 请限定在Q/GGW 02005.2-2022第5.7条的上下文内回答。`
- Gold：`['chunk_17f0bb05047009c7cf28']`
- BM25 gold rank：`{'chunk_17f0bb05047009c7cf28': 1}`
- Vector gold rank：`{'chunk_17f0bb05047009c7cf28': 1}`
- Hybrid gold rank：`{'chunk_17f0bb05047009c7cf28': 1}`

### retrieval-095 · multi_clause · OK
- Query：`请综合说明计量系统 metering system与枢纽站 hub station两方面的规范要求，范围限定为Q/GGW 02005.3-2022和Q/GGW 02005.1-2022。`
- Gold：`['chunk_0322e2ddba4943a2e1bd', 'chunk_db431ba00f7981256704']`
- BM25 gold rank：`{'chunk_db431ba00f7981256704': 2, 'chunk_0322e2ddba4943a2e1bd': 5}`
- Vector gold rank：`{'chunk_0322e2ddba4943a2e1bd': 6, 'chunk_db431ba00f7981256704': 12}`
- Hybrid gold rank：`{'chunk_db431ba00f7981256704': 1, 'chunk_0322e2ddba4943a2e1bd': 5}`

### retrieval-028 · term · OK
- Query：`规范中“设备序列编号不足两位数时，第一位数字”具体指什么？ 请限定在Q/GGW 02005.1-2022第5.1.2.4.7条的上下文内回答。`
- Gold：`['chunk_471274490d7ef724b1ec']`
- BM25 gold rank：`{'chunk_471274490d7ef724b1ec': 1}`
- Vector gold rank：`{'chunk_471274490d7ef724b1ec': 1}`
- Hybrid gold rank：`{'chunk_471274490d7ef724b1ec': 1}`

### retrieval-030 · term · OK
- Query：`规范中“流量计算机”具体指什么？ 请限定在Q/GGW 02005.3-2022第5.4.3条的上下文内回答。`
- Gold：`['chunk_df9efb207b9557ec3be7']`
- BM25 gold rank：`{'chunk_df9efb207b9557ec3be7': 1}`
- Vector gold rank：`{'chunk_df9efb207b9557ec3be7': 2}`
- Hybrid gold rank：`{'chunk_df9efb207b9557ec3be7': 1}`

### retrieval-002 · exact_clause · OK
- Query：`Q/GGW 02005.2-2022 第3.6条的原文要求是什么？`
- Gold：`['chunk_1a2b327267db7b3d4795']`
- BM25 gold rank：`{'chunk_1a2b327267db7b3d4795': 2}`
- Vector gold rank：`{'chunk_1a2b327267db7b3d4795': 13}`
- Hybrid gold rank：`{'chunk_1a2b327267db7b3d4795': 2}`

### retrieval-041 · semantic · OK
- Query：`工程设计涉及界面检测仪时需要满足哪些要求？ 请限定在Q/GGW 02005.2-2022第5.6.5条的上下文内回答。`
- Gold：`['chunk_e345d63028288bb87781']`
- BM25 gold rank：`{'chunk_e345d63028288bb87781': 1}`
- Vector gold rank：`{'chunk_e345d63028288bb87781': 1}`
- Hybrid gold rank：`{'chunk_e345d63028288bb87781': 1}`

### retrieval-007 · exact_clause · OK
- Query：`Q/GGW 02005.1-2022 第3.17条的原文要求是什么？`
- Gold：`['chunk_45930e0320ad7f45e182']`
- BM25 gold rank：`{'chunk_45930e0320ad7f45e182': 1}`
- Vector gold rank：`{'chunk_45930e0320ad7f45e182': 2}`
- Hybrid gold rank：`{'chunk_45930e0320ad7f45e182': 1}`

### retrieval-037 · semantic · OK
- Query：`工程设计涉及仪表管阀件 instrument valve and p时需要满足哪些要求？ 请限定在Q/GGW 02005.1-2022第3.25条的上下文内回答。`
- Gold：`['chunk_6a2c1a12d86b8a7f5576']`
- BM25 gold rank：`{'chunk_6a2c1a12d86b8a7f5576': 1}`
- Vector gold rank：`{'chunk_6a2c1a12d86b8a7f5576': 3}`
- Hybrid gold rank：`{'chunk_6a2c1a12d86b8a7f5576': 1}`

### retrieval-091 · multi_clause · B
- Query：`请综合说明规范性引用文件与范围.两方面的规范要求，范围限定为Q/GGW 02005.1-2022和Q/GGW 02005.2-2022。`
- Gold：`['chunk_0fb8e162ef9db7e79bd4', 'chunk_7f04581c9a4f209d154c']`
- BM25 gold rank：`{'chunk_0fb8e162ef9db7e79bd4': 1}`
- Vector gold rank：`{'chunk_0fb8e162ef9db7e79bd4': 5, 'chunk_7f04581c9a4f209d154c': 6}`
- Hybrid gold rank：`{'chunk_0fb8e162ef9db7e79bd4': 1}`

### retrieval-015 · exact_clause · C
- Query：`Q/GGW 02005.3-2022 第5.3.6.1条的原文要求是什么？`
- Gold：`['chunk_246d4e121220c18741ba']`
- BM25 gold rank：`{'chunk_246d4e121220c18741ba': 1}`
- Vector gold rank：`{}`
- Hybrid gold rank：`{'chunk_246d4e121220c18741ba': 7}`

### retrieval-069 · numeric · OK
- Query：`B型井口安全控制系统，适用于在采气树设置井下安全阀及地面有哪些数值或精度限制？ 请限定在Q/GGW 02005.1-2022第9.8.2条的上下文内回答。`
- Gold：`['chunk_fc365760cbf15e24d4db']`
- BM25 gold rank：`{'chunk_fc365760cbf15e24d4db': 1}`
- Vector gold rank：`{'chunk_fc365760cbf15e24d4db': 1}`
- Hybrid gold rank：`{'chunk_fc365760cbf15e24d4db': 1}`

### retrieval-029 · term · OK
- Query：`规范中“仪表信号”具体指什么？ 请限定在Q/GGW 02005.2-2022第5.7条的上下文内回答。`
- Gold：`['chunk_17f0bb05047009c7cf28']`
- BM25 gold rank：`{'chunk_17f0bb05047009c7cf28': 1}`
- Vector gold rank：`{'chunk_17f0bb05047009c7cf28': 2}`
- Hybrid gold rank：`{'chunk_17f0bb05047009c7cf28': 1}`

### retrieval-049 · semantic · OK
- Query：`工程设计涉及油气藏型地下储气库分为井场、集注站、集输系统、联络线、分时需要满足哪些要求？ 请限定在Q/GGW 02005.1-2022第6.1.3.2条的上下文内回答。`
- Gold：`['chunk_4306606f93e8e49c50e8']`
- BM25 gold rank：`{'chunk_4306606f93e8e49c50e8': 1}`
- Vector gold rank：`{'chunk_4306606f93e8e49c50e8': 1}`
- Hybrid gold rank：`{'chunk_4306606f93e8e49c50e8': 1}`

### retrieval-098 · multi_clause · E
- Query：`请综合说明计量系统与液化天然气 liquefied natural gas两方面的规范要求，范围限定为Q/GGW 02005.3-2022和Q/GGW 02005.1-2022。`
- Gold：`['chunk_0f3b688d89cdaf4a93d9', 'chunk_1afecd8ce572cda733df']`
- BM25 gold rank：`{'chunk_1afecd8ce572cda733df': 3}`
- Vector gold rank：`{}`
- Hybrid gold rank：`{'chunk_1afecd8ce572cda733df': 5}`

### retrieval-101 · multi_clause · OK
- Query：`请综合说明检定 verification与安全仪表系统safety instrumented sy两方面的规范要求，范围限定为Q/GGW 02005.3-2022和Q/GGW 02005.1-2022。`
- Gold：`['chunk_6185f3457354500ee294', 'chunk_a0fcdf4d9822e86900b3']`
- BM25 gold rank：`{'chunk_6185f3457354500ee294': 2, 'chunk_a0fcdf4d9822e86900b3': 8}`
- Vector gold rank：`{'chunk_6185f3457354500ee294': 1, 'chunk_a0fcdf4d9822e86900b3': 3}`
- Hybrid gold rank：`{'chunk_6185f3457354500ee294': 2, 'chunk_a0fcdf4d9822e86900b3': 5}`

### retrieval-043 · semantic · OK
- Query：`工程设计涉及不同安装位置通用仪表的图形符号见表时需要满足哪些要求？ 请限定在Q/GGW 02005.1-2022第5.2.1条的上下文内回答。`
- Gold：`['chunk_e966c334dbbdd52aedcd']`
- BM25 gold rank：`{'chunk_e966c334dbbdd52aedcd': 1}`
- Vector gold rank：`{'chunk_e966c334dbbdd52aedcd': 1}`
- Hybrid gold rank：`{'chunk_e966c334dbbdd52aedcd': 1}`

### retrieval-022 · term · OK
- Query：`规范中“等电位连接 equipotential bonding”具体指什么？ 请限定在Q/GGW 02005.1-2022第3.19条的上下文内回答。`
- Gold：`['chunk_dcf02a5d5596f37535e8']`
- BM25 gold rank：`{'chunk_dcf02a5d5596f37535e8': 1}`
- Vector gold rank：`{'chunk_dcf02a5d5596f37535e8': 1}`
- Hybrid gold rank：`{'chunk_dcf02a5d5596f37535e8': 1}`

### retrieval-006 · exact_clause · C
- Query：`Q/GGW 02005.3-2022 第5.3.3.5条的原文要求是什么？`
- Gold：`['chunk_442df02b19bf8218c983']`
- BM25 gold rank：`{'chunk_442df02b19bf8218c983': 1}`
- Vector gold rank：`{}`
- Hybrid gold rank：`{'chunk_442df02b19bf8218c983': 4}`

### retrieval-105 · multi_clause · E
- Query：`请综合说明液化天然与考虑到国内现有检定中心的能力，天然气贸易计量系统中单台流两方面的规范要求，范围限定为Q/GGW 02005.2-2022和Q/GGW 02005.3-2022。`
- Gold：`['chunk_2b648c864304beabd561', 'chunk_45e2b614428cce4db94f']`
- BM25 gold rank：`{'chunk_2b648c864304beabd561': 1}`
- Vector gold rank：`{'chunk_2b648c864304beabd561': 4}`
- Hybrid gold rank：`{'chunk_2b648c864304beabd561': 1}`

### retrieval-068 · numeric · OK
- Query：`压力检测仪表选型有哪些数值或精度限制？ 请限定在Q/GGW 02005.2-2022第5.3.2条的上下文内回答。`
- Gold：`['chunk_a83a08a1e93117bf2145']`
- BM25 gold rank：`{'chunk_a83a08a1e93117bf2145': 1}`
- Vector gold rank：`{'chunk_a83a08a1e93117bf2145': 2}`
- Hybrid gold rank：`{'chunk_a83a08a1e93117bf2145': 1}`

### retrieval-059 · numeric · OK
- Query：`压力仪表安装要求压力仪表安装有哪些数值或精度限制？ 请限定在Q/GGW 02005.2-2022第8.3.3.2条的上下文内回答。`
- Gold：`['chunk_f7feac7e5aca5e7266e1']`
- BM25 gold rank：`{'chunk_f7feac7e5aca5e7266e1': 1}`
- Vector gold rank：`{'chunk_f7feac7e5aca5e7266e1': 4}`
- Hybrid gold rank：`{'chunk_f7feac7e5aca5e7266e1': 1}`

### retrieval-055 · semantic · OK
- Query：`工程设计涉及液化天然气接收站DCS按照就近原则时需要满足哪些要求？ 请限定在Q/GGW 02005.1-2022第6.2.1.6条的上下文内回答。`
- Gold：`['chunk_7e23ddcce160e3353179']`
- BM25 gold rank：`{'chunk_7e23ddcce160e3353179': 1}`
- Vector gold rank：`{'chunk_7e23ddcce160e3353179': 1}`
- Hybrid gold rank：`{'chunk_7e23ddcce160e3353179': 1}`

### retrieval-035 · semantic · OK
- Query：`工程设计涉及压力检测仪表时需要满足哪些要求？ 请限定在Q/GGW 02005.2-2022第5.3.1条的上下文内回答。`
- Gold：`['chunk_8c7dcfccfc1aa628e509']`
- BM25 gold rank：`{'chunk_8c7dcfccfc1aa628e509': 1}`
- Vector gold rank：`{'chunk_8c7dcfccfc1aa628e509': 5}`
- Hybrid gold rank：`{'chunk_8c7dcfccfc1aa628e509': 1}`

### retrieval-104 · multi_clause · C
- Query：`请综合说明天然气贸易计量系统与基本过程控制系统 basic processcontro两方面的规范要求，范围限定为Q/GGW 02005.3-2022和Q/GGW 02005.1-2022。`
- Gold：`['chunk_1138ce83273675efeb82', 'chunk_93edf39162ff0a2fb975']`
- BM25 gold rank：`{'chunk_1138ce83273675efeb82': 2, 'chunk_93edf39162ff0a2fb975': 6}`
- Vector gold rank：`{'chunk_1138ce83273675efeb82': 2}`
- Hybrid gold rank：`{'chunk_1138ce83273675efeb82': 2, 'chunk_93edf39162ff0a2fb975': 10}`

### retrieval-060 · numeric · OK
- Query：`仪表管阀件材质有哪些数值或精度限制？ 请限定在Q/GGW 02005.1-2022第11.6.1.4条的上下文内回答。`
- Gold：`['chunk_29fd8b107dce5a1b4f95']`
- BM25 gold rank：`{'chunk_29fd8b107dce5a1b4f95': 1}`
- Vector gold rank：`{'chunk_29fd8b107dce5a1b4f95': 3}`
- Hybrid gold rank：`{'chunk_29fd8b107dce5a1b4f95': 1}`

### retrieval-087 · table · C
- Query：`Q/GGW 02005.3-2022 表5的各行参数要求是什么？`
- Gold：`['table_fdd9b3db369e2bf69d73']`
- BM25 gold rank：`{'table_fdd9b3db369e2bf69d73': 4}`
- Vector gold rank：`{}`
- Hybrid gold rank：`{'table_fdd9b3db369e2bf69d73': 9}`

### retrieval-005 · exact_clause · C
- Query：`Q/GGW 02005.2-2022 第3.8条的原文要求是什么？`
- Gold：`['chunk_45e2b614428cce4db94f']`
- BM25 gold rank：`{'chunk_45e2b614428cce4db94f': 2}`
- Vector gold rank：`{}`
- Hybrid gold rank：`{'chunk_45e2b614428cce4db94f': 6}`

### retrieval-075 · numeric · OK
- Query：`当直埋电缆穿越道路时有哪些数值或精度限制？ 请限定在Q/GGW 02005.1-2022第11.4.4.4条的上下文内回答。`
- Gold：`['chunk_65e9bd0f38ea5e1ac931']`
- BM25 gold rank：`{'chunk_65e9bd0f38ea5e1ac931': 1}`
- Vector gold rank：`{'chunk_65e9bd0f38ea5e1ac931': 1}`
- Hybrid gold rank：`{'chunk_65e9bd0f38ea5e1ac931': 1}`

### retrieval-100 · multi_clause · E
- Query：`请综合说明-与检测元件 sensor两方面的规范要求，范围限定为Q/GGW 02005.1-2022和Q/GGW 02005.2-2022。`
- Gold：`['chunk_540d54e02591c6a6bd0d', 'chunk_e97f550d57d5ff741bda']`
- BM25 gold rank：`{'chunk_e97f550d57d5ff741bda': 2}`
- Vector gold rank：`{'chunk_e97f550d57d5ff741bda': 2}`
- Hybrid gold rank：`{'chunk_e97f550d57d5ff741bda': 2}`

### retrieval-099 · multi_clause · OK
- Query：`请综合说明变送器 transmitter与缩略语两方面的规范要求，范围限定为Q/GGW 02005.2-2022和Q/GGW 02005.3-2022。`
- Gold：`['chunk_5fb4d1109782a3848a56', 'chunk_b09aade4702ad2b22a35']`
- BM25 gold rank：`{'chunk_5fb4d1109782a3848a56': 2, 'chunk_b09aade4702ad2b22a35': 4}`
- Vector gold rank：`{'chunk_b09aade4702ad2b22a35': 1, 'chunk_5fb4d1109782a3848a56': 16}`
- Hybrid gold rank：`{'chunk_b09aade4702ad2b22a35': 2, 'chunk_5fb4d1109782a3848a56': 3}`

### retrieval-082 · table · OK
- Query：`Q/GGW 02005.1-2022 表3的表头与参数单位是什么？`
- Gold：`['table_496abb837d73946c6fb1']`
- BM25 gold rank：`{'table_496abb837d73946c6fb1': 3}`
- Vector gold rank：`{'table_496abb837d73946c6fb1': 1}`
- Hybrid gold rank：`{'table_496abb837d73946c6fb1': 2}`

### retrieval-092 · multi_clause · C
- Query：`请综合说明范围与监控阀室 monitoring valve statio两方面的规范要求，范围限定为Q/GGW 02005.3-2022和Q/GGW 02005.1-2022。`
- Gold：`['chunk_1b18c8a0a7aeaab6ba7f', 'chunk_5aa2b526b5aa993ade95']`
- BM25 gold rank：`{'chunk_5aa2b526b5aa993ade95': 2, 'chunk_1b18c8a0a7aeaab6ba7f': 9}`
- Vector gold rank：`{'chunk_5aa2b526b5aa993ade95': 3}`
- Hybrid gold rank：`{'chunk_5aa2b526b5aa993ade95': 2, 'chunk_1b18c8a0a7aeaab6ba7f': 10}`

### retrieval-078 · table · A
- Query：`Q/GGW 02005.3-2022 表1的各行参数要求是什么？`
- Gold：`['table_5b1154de9a13b318914b']`
- BM25 gold rank：`{'table_5b1154de9a13b318914b': 7}`
- Vector gold rank：`{'table_5b1154de9a13b318914b': 8}`
- Hybrid gold rank：`{'table_5b1154de9a13b318914b': 6}`

### retrieval-004 · exact_clause · C
- Query：`Q/GGW 02005.1-2022 第3.9条的原文要求是什么？`
- Gold：`['chunk_6185f3457354500ee294']`
- BM25 gold rank：`{'chunk_6185f3457354500ee294': 1}`
- Vector gold rank：`{}`
- Hybrid gold rank：`{'chunk_6185f3457354500ee294': 8}`

### retrieval-046 · semantic · OK
- Query：`工程设计涉及信号处理功能图形符号见表时需要满足哪些要求？ 请限定在Q/GGW 02005.1-2022第5.2.8条的上下文内回答。`
- Gold：`['chunk_6acbe61e1d877980193d']`
- BM25 gold rank：`{'chunk_6acbe61e1d877980193d': 1}`
- Vector gold rank：`{'chunk_6acbe61e1d877980193d': 1}`
- Hybrid gold rank：`{'chunk_6acbe61e1d877980193d': 1}`

### retrieval-054 · semantic · OK
- Query：`工程设计涉及分输站场未设置在线分析系统时时需要满足哪些要求？ 请限定在Q/GGW 02005.3-2022第6.3.1.3条的上下文内回答。`
- Gold：`['chunk_0084c1e902ad47fd31d9']`
- BM25 gold rank：`{'chunk_0084c1e902ad47fd31d9': 1}`
- Vector gold rank：`{'chunk_0084c1e902ad47fd31d9': 1}`
- Hybrid gold rank：`{'chunk_0084c1e902ad47fd31d9': 1}`

### retrieval-036 · semantic · OK
- Query：`工程设计涉及超声流量计（插入式）时需要满足哪些要求？ 请限定在Q/GGW 02005.3-2022第5.3.5.2条的上下文内回答。`
- Gold：`['chunk_6e36a0745f525f46faec']`
- BM25 gold rank：`{'chunk_6e36a0745f525f46faec': 1}`
- Vector gold rank：`{'chunk_6e36a0745f525f46faec': 1}`
- Hybrid gold rank：`{'chunk_6e36a0745f525f46faec': 1}`

### retrieval-047 · semantic · OK
- Query：`工程设计涉及控制阀上游安装普通不锈钢压力表，下游安装不锈钢耐振压力表时需要满足哪些要求？ 请限定在Q/GGW 02005.2-2022第6.2.3条的上下文内回答。`
- Gold：`['chunk_bd2a2e1ac67a35b7129c']`
- BM25 gold rank：`{'chunk_bd2a2e1ac67a35b7129c': 1}`
- Vector gold rank：`{'chunk_bd2a2e1ac67a35b7129c': 1}`
- Hybrid gold rank：`{'chunk_bd2a2e1ac67a35b7129c': 1}`

