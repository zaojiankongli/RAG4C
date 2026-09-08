# RAG4C 桥服务并发性能报告（2026-08-17）

> 3 轮「并发优化 + 前端展示」迭代的性能基线固化。压测工具：`scripts/bench.py`（httpx 连接池）。

## 结论速览

| 项 | 结果 |
|---|---|
| 服务端真实处理延迟 | **2ms**（连接复用后，httpx 实测请求 2-6 全部 ≤3ms） |
| 连接复用 QPS（/api/metrics, c=16, n=200） | **42.2**（p50=47ms） |
| 每次新建连接 QPS（urllib 旧版） | 7.5（p50≈2s）→ **提升约 5.6 倍** |
| 单请求固定延迟（curl 新连接） | ~0.2s（Windows 本机 TCP 连接开销，非服务端） |
| 健康探测（/api/health） | 原每次实时探测远程 Milvus → **TTL 缓存 5s**，并发轮询不再反复打远程 |

## 关键发现

1. **瓶颈不在服务端**：所有端点（health/metrics/config）连接复用后毫秒级返回。
   Windows 本机回环的**新 TCP 连接**可能被安全软件（防病毒/防火墙）拖到 0.2~2s，
   这是客户端侧现象——真实用户（浏览器/桌面端 keep-alive）不受影响。

2. **并发保护已就位**（server/app.py）：
   - `QUERY_MAX_CONCURRENT=4`：同时执行的 RAG 链路上限（asyncio.Semaphore）
   - `QUERY_QUEUE_MAX=20`：排队上限，超出返回 429
   - `QUERY_CACHE_TTL_S=600`：相同 query+acl 10 分钟内 TTL 缓存（LRU 64 条）
   - `QUERY_TIMEOUT_S=240`：总闸超时（远程 embedding/rerank 慢时避免误杀，从 120 放宽）

3. **健康探测缓存**：`/api/health` 组件探活 TTL 5s，
   监控页 10s 轮询 + 前端探测不再每次都连远程 Milvus。

## 前端展示联动

监控页新增「并发负载与缓存」状态卡：
- 并发负载（pending / max_concurrent，超出队列上限会 429）
- 查询缓存（size / max）与缓存命中率（TTL 缓存收益可见）
- 队列上限

## 复测方法

```bash
# 连接复用（真实服务端性能）
python scripts/bench.py --path /api/metrics --n 200 --c 16

# 对比：每次新建连接（暴露本机 TCP 连接开销）
python scripts/bench.py --path /api/metrics --n 100 --c 8 --new
```

## 后续优化方向（2026-08-17 更新：部分已落地）

- **embedding 切本地：✅ 已完成**（.env 切 Ollama `http://localhost:11434/v1` + `bge-m3`，
  同模型 BAAI/bge-m3 向量空间一致，平滑切换）。实测热启动 600-800ms（远程 SiliconFlow 50s+，
  **快 60-80 倍**）。首次冷启动 ~15s 是 Ollama 加载模型。
- **reranker 切本地：❌ 不需要**（实测 span.rerank mean=382ms，远程也很快，
  之前以为的 50s+ 是首次冷启动，不是常态）。
- **真正的瓶颈：本地 Ollama LLM 推理（CPU）**：
  - `span.generate 86.5s`（qwen2.5 生成）、`span.verify_l3 57.4s`（judge 蕴含判定）、
    `span.gate 16.3s`（复杂度门控）。
  - 机器无 NVIDIA GPU，CPU 推理是固有的。提速可选：
    - LLM 槽位切远程 API（SiliconFlow/OpenAI 兼容，需改各槽位 base_url+model）
    - 换更小本地模型（qwen2.5:1.5b，质量略降但快）
    - 保留现状（本地隐私优先，接受 60-180s 的完整链路）
- **多 worker 横向扩容：`scripts/start_server.bat|sh`（可选 `RAG4C_WORKERS=N`，默认 1）**。
  ⚠️ 多 worker 时每 worker 独立 metrics/查询缓存/健康缓存/Semaphore：
  指标分裂（Prometheus 各报各）、缓存不共享、总并发=workers×4。
  单机控制台用 1 worker 是正确的；workers≥2 仅横向扩容且接受指标分裂时用。
- **连接层：已完成**（`scripts/bench.py` 连接池测量 + 本报告说明，
  客户端用 httpx/axios keep-alive 连接池即可）。
