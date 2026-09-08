"""桥服务离线冒烟：错误契约 / 端点结构 / 限流语义（进程内 TestClient）。

运行：python scripts/smoke_server.py
无论外部服务是否可达：对无据可依的问句一律弃权（离线走「检索失败」，
联网空库走「无相关内容」），验证响应仍为结构化 JSON 而非异常。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import isolate_redis_keyspace  # noqa: E402

# 必须在 import server.app 之前。本脚本会把「测试问题」这句问答写进答案缓存，
# 那是一句真人也会输入的中文——落到共用实例的生产前缀下，TTL 内就会被当成
# 正经答案发出去。
isolate_redis_keyspace("server")

from fastapi.testclient import TestClient  # noqa: E402

from server.app import _format_env_value, app  # noqa: E402
from server.config_writer import update_dotenv_text  # noqa: E402


@app.get("/api/_smoke/internal-error")
def _internal_error_probe() -> None:
    raise RuntimeError("private-smoke-detail")


client = TestClient(app, raise_server_exceptions=False)
passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


print("== 1. /api/health 深度探活 ==")
r = client.get("/api/health")
check("status 200", r.status_code == 200, f"got {r.status_code}")
data = r.json()
check("status 为 ok/degraded/down 之一", data.get("status") in ("ok", "degraded", "down"), str(data.get("status")))
check("components 含 milvus/embedder/reranker/llm", all(k in data.get("components", {}) for k in ("milvus", "embedder", "reranker", "llm")))
check("circuits 字段存在", "circuits" in data)

print("== 2. /api/query 无据可依时的弃权契约 ==")
r = client.post("/api/query", json={"query": "测试问题"})
check("status 200", r.status_code == 200, f"got {r.status_code}")
q = r.json()
_result = q.get("result", {})
_traces = _result.get("traces", [])
check("result 包装存在（前端 QueryResponse 契约）", "result" in q, str(q)[:200])
check("duration_ms / using_mock 存在", "duration_ms" in q and "using_mock" in q, str(sorted(q.keys())))
# 该问句无对应证据：离线时走「检索失败 -> 弃权」，联网空库时走「无相关内容 -> 弃权」，
# 两条路径都必须弃权，且必须在 traces 里说明原因——断言只认契约，不认具体成因，
# 否则这条冒烟会随外部服务可达与否而翻转。
check("abstained=True（无据可依）", _result.get("abstained") is True, str(_result.get("abstained")))
check(
    "traces 说明弃权原因",
    any(("检索失败" in t) or ("弃权" in t) for t in _traces),
    str(_traces),
)

print("== 3. 参数校验错误契约 ==")
r = client.post("/api/query", json={"query": ""})
check("422", r.status_code == 422, f"got {r.status_code}")
check("错误契约含 error.code", r.json().get("error", {}).get("code") == "validation_error", str(r.json())[:150])

print("== 4. 404 错误契约 ==")
r = client.get("/api/nonexistent")
check("404 JSON", r.status_code == 404 and r.json().get("error", {}).get("code") == "http_404", str(r.json())[:150])

print("== 5. /api/metrics 与衍生端点 ==")
r = client.get("/api/metrics")
check("metrics 200", r.status_code == 200)
m = r.json()
check("服务层字段（uptime_s/queue/cache/circuits）", all(k in m for k in ("uptime_s", "queue", "cache", "circuits")), str(sorted(m.keys())))
r = client.get("/api/metrics/prometheus")
check("prometheus 文本", r.status_code == 200 and r.headers.get("content-type", "").startswith("text/plain"), str(r.text)[:80])
r = client.get("/api/metrics/history")
check("history 200（空或列表）", r.status_code == 200 and "items" in r.json(), str(r.json())[:100])

print("== 6. /api/eval/run dry-run 互斥与结果 ==")
r = client.post("/api/eval/run", json={"dataset_spec": "eval/dataset_sample.py:SAMPLE_DATASET", "pipeline": "none"})
check("dry-run 200", r.status_code == 200, f"got {r.status_code}")
check("report 含 metrics/cases", all(k in r.json().get("report", {}) for k in ("metrics", "cases")), str(r.json())[:150])

print("== 7. /api/config 快照（circuit 段 + 解析引擎载荷） ==")
r = client.get("/api/config")
body = r.json()
check("config 200", r.status_code == 200)
check("sections 含 circuit 段", "circuit" in body.get("sections", {}), str(sorted(body.get("sections", {}).keys())))
cat_fields = body.get("sections", {}).get("catalog", {}).get("fields", [])
check("catalog 段 fields 非空（前缀修复防回归）", len(cat_fields) >= 4, str(cat_fields))
db_url_field = [f for f in cat_fields if f.get("path") == "catalog.db_url"]
check("catalog.db_url 脱敏展示", bool(db_url_field) and db_url_field[0].get("sensitive") is True and "•" in str(db_url_field[0].get("value", "")), str(db_url_field)[:120])
engines = body.get("engines")
check("engines 载荷存在", engines is not None, str(engines)[:120])
names = [p.get("name") for p in (engines or {}).get("plugins", [])]
check("插件清单含 mineru / docling", "mineru" in names and "docling" in names, str(names))
check("engine=auto 默认生效 mineru", engines is not None and engines.get("active") == "mineru", str(engines)[:120])
modes_map = {p.get("name"): p.get("modes") for p in (engines or {}).get("plugins", [])}
check("插件 modes 下发（free/paid、local/api）", modes_map == {"mineru": ["free", "paid"], "docling": ["local", "api"]}, str(modes_map))
bridge_paths = {
    field.get("path")
    for field in body.get("sections", {}).get("bridge", {}).get("fields", [])
}
check(
    "入库并发配置已下发",
    {"bridge.ingest_max_concurrent", "bridge.ingest_queue_max"} <= bridge_paths,
    str(sorted(bridge_paths)),
)

print("== 8. /api/config/update 白名单拒绝 ==")
r = client.post("/api/config/update", json={"updates": [{"path": "llm.generation.api_key", "value": "hacked"}]})
check("敏感字段拒绝", r.status_code == 200 and r.json().get("rejected"), str(r.json())[:150])

print("== 9. 管理端点安全约束 ==")
r = client.get("/api/_smoke/internal-error")
error_body = r.json()
check("500 使用通用错误消息", r.status_code == 500 and error_body.get("error", {}).get("code") == "internal_error", str(error_body))
check("500 不泄露内部异常详情", "private-smoke-detail" not in r.text, r.text)
r = client.post(
    "/api/eval/run",
    json={
        "dataset_spec": "eval/dataset_sample.py:SAMPLE_DATASET",
        "pipeline": "os:system",
    },
)
check("拒绝未授权评测管线", r.status_code == 400, f"got {r.status_code}: {r.text[:120]}")
r = client.post(
    "/api/eval/run",
    json={
        "dataset_spec": "eval/dataset_sample.py:SAMPLE_DATASET",
        "pipeline": "none",
        "out": "../outside.json",
    },
)
check("拒绝 eval 目录外输出", r.status_code == 400, f"got {r.status_code}: {r.text[:120]}")

print("== 10. dotenv 保真与注入防护 ==")
source = "# operator note\nRAG4C_A=old\n\nexport RAG4C_B = keep\n"
updated = update_dotenv_text(source, {"RAG4C_A": "new", "RAG4C_C": "3"})
check("保留注释、空行与未改字段", "# operator note\nRAG4C_A=new\n\nexport RAG4C_B = keep\n" in updated, repr(updated))
check("新字段追加到文件末尾", updated.endswith("RAG4C_C=3\n"), repr(updated))
try:
    _format_env_value("safe\nRAG4C_INJECTED=1")
except ValueError:
    rejected_newline = True
else:
    rejected_newline = False
check("拒绝 dotenv 换行注入", rejected_newline)

print("== 11. /api/datasets 知识库发现 ==")
# 这个端点是 /api/query 的 dataset_id 参数的**唯一取值来源**：没有它，
# 调用方只能靠猜库名。所以要保证它在 Milvus / MySQL 任一不可达时仍然
# 返回结构化结果，而不是 500——发现端点自己挂掉，等于整个多知识库能力
# 从外部看不见。
r = client.get("/api/datasets")
check("status 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
data = r.json()
check("含 datasets 与 count", "datasets" in data and "count" in data, str(list(data)))
check("count 与列表长度一致", data.get("count") == len(data.get("datasets", [])), str(data.get("count")))
if data.get("datasets"):
    first = data["datasets"][0]
    check("每项含 dataset_id", all("dataset_id" in d for d in data["datasets"]), str(first))
    check("每项含 origin", all("origin" in d for d in data["datasets"]), str(first))
    # chunk_count 现查 Milvus：可达时是 int，不可达时刻意给 None 而不是 0——
    # 0 会被读成「这个库是空的」，与「暂时数不出来」是完全不同的结论。
    check("chunk_count 为 int 或 None（不伪造 0）",
          all(d.get("chunk_count") is None or isinstance(d["chunk_count"], int)
              for d in data["datasets"]), str(first))
    check("按 dataset_id 排序稳定",
          [d["dataset_id"] for d in data["datasets"]]
          == sorted(d["dataset_id"] for d in data["datasets"]))
# 清单里声明的库必须出现，哪怕还没同步过（chunk_count=0）——
# 「声明了但还没抓」和「不存在」对使用者是两回事。
_manifest = _PROJECT_ROOT / "config" / "sources.json"
if _manifest.is_file():
    import json as _json

    declared = {
        s.get("dataset_id") or s["name"]
        for s in _json.loads(_manifest.read_text(encoding="utf-8"))["sources"]
    }
    listed = {d["dataset_id"] for d in data.get("datasets", [])}
    check("清单声明的知识库全部可见", declared <= listed, f"缺 {declared - listed}")

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
