# 前端 React key 整类扫描（Round 11 Phase 2，2026-09-29）

> 背景：Round 10 §5.3 的实机教训——`RetrievalTrace` 用非唯一的 `chunkId` 当 key，
> 上线后浏览器刷了 363 次 `two children with the same key` 警告而所有读数全绿。
> 当时只修了那一处；本轮做整类清点，把「`.map` 里用非唯一业务字段当 key」的
> 同类形状一次盘完，并留下可复用的核对结论。

## 扫描方法（可复跑）

```
grep -rnE '\.map\(\(?[a-zA-Z_]+,? *[^)]*\)? *=>.*key=\{[a-zA-Z_]+\.' frontend/src --include='*.tsx'
grep -rnE 'key=\{[a-zA-Z_]+\.(chunkId|docId|documentId|type|name|rank|status)\}' frontend/src --include='*.tsx'
```

两轮共命中 12 个候选点，逐一对照数据源的语义判定（判据：**在同一个
`.map` 的数组里是否可能重复**，而不是字段名好不好听）。

## 清点结论

| 落点 | key | 判定 | 依据 |
|---|---|---|---|
| `components/RunEventTable.tsx:70` | `type`（筛选项） | ✅ 唯一 | 下拉选项来自事件类型的去重集合 |
| `components/RunEventTable.tsx:70` | `node.id` / `attempt` | ✅ 唯一 | 拓扑节点 id / attempt 号各自唯一 |
| `components/RunEventTable.tsx:94` | `event.seq` | ✅ 唯一 | seq 是运行内事件序号（账本主键语义） |
| `retrieval-quality/…/EvidenceComparisonTable.tsx` | `variant.experimentId` / `rank` | ✅ 唯一 | 行=排名、列=实验，两个方向都唯一 |
| `components/EngineBoard.tsx:132` | `p.name` | ✅ 唯一 | 引擎插件按名字注册（registry 键） |
| `monitor/DocumentIngestMonitor.tsx:205/218` | `item.name` | ✅ 唯一 | 分布统计按 name 聚合（同名必然已合并） |
| `pages/KnowledgeTaxonomyPage.tsx:751` | `tag.name` | ✅ 唯一 | 分面标签按 name 聚合 |
| `storage-backends/…/StorageBackendsPanel.tsx:617` | `field.name` | ✅ 唯一 | 表单字段定义按 name 声明 |
| `parse-intervention/…/ChunkMergedView.tsx:60/79` | `chunkId` | ⚠️ **加固** | 模型层「父/子去重」若回归或来源混杂（深链钉入的头部重复载入），同 id 的 body/tombstone 会成对出现——key 加 `:kind` 后缀 |
| `parse-intervention/…/ChunkListPane.tsx:13` | `chunk_id` | ⚠️ **加固** | 同上，且本条是**新用例真实抓到的**：写防御用例时 React 直接报了同 key 警告——列表模式自己也用裸 chunkId。改 `${chunk_id}:${start+i}`（窗口基址+槽位，虚拟滚动的 key 本就应随窗口） |

## 交付物

1. `ChunkMergedView` key 加 `:body` / `:tombstone` 后缀；
2. `ChunkListPane` 列表行 key 改 `${chunk_id}:${start+i}`；
3. 防御用例「同一 chunkId 以正文和墓碑两种形态出现时不撞 React key」——
   写它的时候先红（抓到列表模式的真实缺口），修后绿，不是摆设。

## 门禁读数（本片新跑）

- `vitest run src/parse-intervention` → **15 files / 98 tests 全过**；
- `tsc --noEmit` → 退出码 0。

## 边界与残留

- 扫描覆盖 `.map` + `key={x.field}` 形状与 index-keyed map（仅 1 处，语义正确）；
  JSX 里解构后再用 key 的写法（`{items.map(({id}) => <X key={id}/>）`）不在第一轮
  grep 射程，探针的控制台判红（Round 10 §5.3）仍是兜底。
- `key={name}` 家族全部成立的前提是「聚合/注册发生在上游」；若未来某个
  distribution 端点改为按 (name, scope) 分桶，这些点会变成真缺陷——届时本表
  是第一核对清单。
