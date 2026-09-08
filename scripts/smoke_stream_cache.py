"""SSE 流式问答的缓存接线冒烟（server/app.py 的 /api/query/stream）。

运行：python scripts/smoke_stream_cache.py

**这个脚本为什么存在。** 两级缓存、跨进程 single-flight、语料代次失效全都
建好了，却只接在 `/api/query` 上；而前端默认走的是 `/api/query/stream`
（`QueryPage` 里 `streamAnswer` 是主路径，`fetchAnswer` 只在它抛异常时兜底）。
于是真实流量的缓存命中率恒为 0，整套东西在生产上等于不存在，压测量到的也是
一条用户不走的路。这类"建好了但没接上"的缺陷不会报错、不会变慢、任何单元
测试都照过——只有一条端到端断言拦得住它。

覆盖：
1. 流式回放契约：命中后仍发 phase / token / done，前端不必为缓存开分支
2. 信封不套娃：命中的 done 是 {result, using_mock, duration_ms, cached}，
   而不是把它再包一层
3. 两条端点共用同一个缓存键：/api/query 与 /api/query/stream 互相命中
4. L2（跨副本）在流式路径上真的会被查到——只清 L1 后仍能命中
5. 语料代次一涨，流式缓存跟着失效（不能只有非流式路径受代次约束）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ruff: noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import isolate_redis_keyspace

# 必须早于 import server.app：配置是 lru_cache 单例，晚了就切不动前缀了。
# 本脚本会把真人也可能输入的中文问句写进答案缓存，落到共用实例的生产前缀下
# 就是测试数据污染线上应答。
isolate_redis_keyspace("stream-cache")

import uuid

from fastapi.testclient import TestClient

from core import cache_epoch
from core.query_cache import get_query_cache
from server.app import app

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


def stream(question: str) -> list[dict]:
    """打一次流式问答，返回解析后的事件列表。

    TestClient 会把 StreamingResponse 整个读完，所以直接切 text 即可——
    这里测的是事件序列与载荷形状，不是流式的时序。
    """
    r = client.post("/api/query/stream", json={"query": question})
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    events = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        events.append(json.loads(body))
    return events


def done_of(events: list[dict]) -> dict:
    return next((e for e in events if e.get("type") == "done"), {})


# 每次运行换一个问句：同一个 Redis / 同一个进程里跑第二遍时，上一轮写进
# L2 的答案会让第一次请求就命中，"首次未命中"那条断言随即误报。
Q = f"流式缓存冒烟问题-{uuid.uuid4().hex[:8]}"

# 预热代次，理由与服务启动时那次（server.app._prime_cache_epoch）完全一样：
# 冷启动的 current() 先返回 0、几毫秒后后台刷新才补上真值，而第一次问答要跑
# 好几秒——比 3 秒的刷新窗口还长。于是第 1 问用 epoch=0 算键、第 2 问用真值
# 算键，两个不同的键，"再问同一句应该命中"必然翻车。
#
# 这里必须自己调：TestClient 只有当成上下文管理器用（with TestClient(app)）
# 时才会跑 lifespan，而本脚本要在模块级发请求，用不上那个写法。
cache_epoch.prime()

# ===========================================================================
print("== 1. 首次流式问答：未命中，走完整链路 ==")
# ===========================================================================
ev1 = stream(Q)
types1 = [e.get("type") for e in ev1]
d1 = done_of(ev1)
check("有 done 事件", bool(d1), str(types1))
check("首次不带 cached 标记", not d1.get("result", {}).get("cached"), str(d1)[:200])
check("done 载荷是前端 QueryResponse 契约",
      all(k in d1.get("result", {}) for k in ("result", "using_mock", "duration_ms")),
      str(sorted(d1.get("result", {}).keys())))

# ===========================================================================
print("== 2. 再问同一句：命中缓存，且仍是完整的事件序列 ==")
# ===========================================================================
ev2 = stream(Q)
types2 = [e.get("type") for e in ev2]
d2 = done_of(ev2)
check("命中被标记为 cached", d2.get("result", {}).get("cached") is True, str(d2)[:200])
check("命中后仍发 phase 事件（前端进度条靠它）", "phase" in types2, str(types2))
inner1 = d1.get("result", {}).get("result", {})
if inner1.get("answer"):
    check("命中后仍发 token 事件（前端正文靠它）", "token" in types2, str(types2))
else:
    # 弃权答案正文为空，没有 token 可发——这不是缺陷，断言不该硬要求。
    check("弃权答案无正文时不发空 token", "token" not in types2, str(types2))

inner2 = d2.get("result", {}).get("result", {})
check("信封没有套娃（result.result 是答案本体，不是又一层信封）",
      "using_mock" not in inner2 and ("answer" in inner2 or "abstained" in inner2),
      str(sorted(inner2.keys()))[:200])
check("命中返回的答案与首次一致",
      inner2.get("answer") == inner1.get("answer")
      and inner2.get("abstained") == inner1.get("abstained"))

# 离线冒烟里的答案必然是弃权（正文为空），上面那条 token 断言因此走不到
# 有正文的分支。直接对回放函数补一刀：真有答案时必须发得出 token，否则
# 界面在命中缓存时会是一片空白，而这条路径永远不会在离线冒烟里暴露。
from server.app import _replay_events  # noqa: E402

_evs = _replay_events(
    {"result": {"answer": "有正文的答案", "abstained": False},
     "using_mock": False, "duration_ms": 1.0}
)
_kinds = [e["type"] for e in _evs]
check("有正文时回放出 phase/token/done 三种事件",
      _kinds == ["phase", "token", "done"], str(_kinds))
check("token 里是完整正文（整段一次发，不切片）",
      _evs[1]["text"] == "有正文的答案", str(_evs[1]))
check("回放不改动原载荷（cached 只写在副本上）",
      _evs[2]["result"]["cached"] is True and _evs[2]["result"]["duration_ms"] == 1.0)

# ===========================================================================
print("== 3. 两条端点共用同一个缓存键 ==")
# ===========================================================================
# 键不一致的话，同一个问题会按"用没用流式"算两遍——而且不会有任何报错。
r = client.post("/api/query", json={"query": Q})
check("/api/query 命中了流式写进去的缓存", r.json().get("cached") is True,
      str(sorted(r.json().keys())))

Q2 = f"反向共用-{uuid.uuid4().hex[:8]}"
client.post("/api/query", json={"query": Q2})
check("反过来：流式命中非流式写进去的缓存",
      done_of(stream(Q2)).get("result", {}).get("cached") is True)

# ===========================================================================
print("== 4. L2（跨副本）在流式路径上会被查到 ==")
# ===========================================================================
cache = get_query_cache()
if not cache.l2_reachable():
    print("  [SKIP] L2 不可达（无 Redis 或连不上）——与缓存本身一样，这是被支持的形态")
else:
    before = cache.stats()
    cache.clear_l1()          # 只清 L1，模拟"另一个副本的进程内缓存是冷的"
    d3 = done_of(stream(Q))
    after = cache.stats()
    check("L1 清空后流式仍然命中（只能来自 L2）",
          d3.get("result", {}).get("cached") is True, str(d3)[:200])
    check("l2_hits 确实涨了",
          after.get("l2_hits", 0) > before.get("l2_hits", 0),
          f"{before.get('l2_hits')} -> {after.get('l2_hits')}")
    check("命中的 L2 结果被回填进 L1",
          after.get("promotions", 0) > before.get("promotions", 0),
          f"{before.get('promotions')} -> {after.get('promotions')}")

# ===========================================================================
print("== 5. 语料代次一涨，流式缓存跟着失效 ==")
# ===========================================================================
# 只有非流式路径受代次约束的话，用户传完文档再问一次（走的是流式）依然会
# 拿到入库前的旧答案——正是代次机制要根治的那条链路。
cache_epoch.bump(None, reason="流式缓存冒烟")
d4 = done_of(stream(Q))
check("代次递增后同一问句不再命中", not d4.get("result", {}).get("cached"),
      str(d4)[:200])
check("失效后重算的答案仍然完整",
      "result" in d4.get("result", {}) and "duration_ms" in d4.get("result", {}))

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
