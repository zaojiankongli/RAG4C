"""端到端核验：LLM 槽位缓存在**真实请求路径**上确实命中。

    python scripts/e2e_llm_cache.py --url http://127.0.0.1:8020

## 为什么单独有这么一个脚本

``scripts/smoke_llm_cache.py`` 用的是桩，它能证明"缓存这段代码是对的"，但证明
不了"缓存这段代码会被跑到"。这个项目已经栽过一次：SSE 路径的答案缓存写得好好
的，接线断了一截，命中率恒为 0，所有单元断言照样全绿。槽位缓存这次又差点重演
——``cache_ttl_s`` 的默认值被 pydantic 在配置重建时丢掉，实测六个槽位全是 0。

所以这里只做一件事：对着**跑起来的服务**发两次请求，然后看 ``/api/metrics``
里的数字有没有动。故意不引入任何项目内部模块（除了标准库），因为它要验证的
恰恰是"另一个进程里的那份代码"。

## 两次请求为什么不是简单地问两遍

问两遍同样的问题，第二次会被**答案缓存**在最外层拦下，整条管线根本不会重跑，
槽位缓存自然一次也不会被查——测出来的 0 命中会被误读成"槽位缓存坏了"。

用的办法是第二次把 ``retry`` 从 ``true`` 改成 ``false``：
- 答案缓存的键里含 ``retry``（见 core/query_cache.py 的键构成），所以键变了，
  管线**会**真的重跑一遍；
- 而 ``retry`` 不参与任何 prompt 的渲染，改写槽位收到的消息**逐字节相同**，
  所以槽位缓存**必须**命中。

这就把"答案缓存没拦住"和"槽位缓存命中了"这两件事同时钉死了。

## 判定

只有一个硬条件：两次请求之间 ``llm_cache.hits`` 至少 +1。其余（省下的 token、
L2 是否启用）打印出来供人看，不作为判据——例如目标端点不返回 usage 时省量就
是 0，那不是缺陷。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# 本脚本只对着自己拉起的本机服务发请求：仅接受 http + 数字环回地址
# （不解析 DNS，即无 rebinding 面），且禁止重定向。这不是任意 URL 的请求器。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect not allowed", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _assert_loopback_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in _LOOPBACK_HOSTS:
        raise ValueError("e2e_llm_cache only talks to the loopback http service it targets")


def _post(url: str, payload: dict, timeout: float) -> dict:
    _assert_loopback_url(url)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, timeout: float) -> dict:
    _assert_loopback_url(url)
    with _OPENER.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _saved_total(metrics: dict) -> float:
    """metrics 里"缓存省下的 total token"之和。

    取 ``sum`` 而不是 ``count``：``incr(name, value=N)`` 记的是一次值为 N 的
    采样，count 只是调用次数。拿 count 当 token 数会得到一条恒等于命中次数的
    曲线——看着像在测量，其实什么也没测。
    """
    counters = metrics.get("counters") or metrics.get("metrics") or {}
    total = 0.0
    for key, val in counters.items():
        if not key.startswith("llm.cache.saved.total"):
            continue
        if isinstance(val, dict):
            total += float(val.get("sum", 0.0))
        elif isinstance(val, (int, float)):
            total += float(val)
    return total


def _describe(resp: dict) -> str:
    """把一次 /api/query 的回包压成一行。

    ``answer`` 嵌在 ``result`` 里而不在顶层；``cached`` 在顶层，且**未命中时
    这个键可能压根不出现**（所以判定一律走 falsy，不要判 ``is False``）。
    """
    result = resp.get("result") or {}
    answer = str(result.get("answer", "") or "")
    return (f"cached={bool(resp.get('cached'))} "
            f"答案长度={len(answer)} "
            f"耗时={resp.get('duration_ms', 0):.0f}ms")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="服务地址")
    ap.add_argument(
        "--url2", default="",
        help="第二次请求打到的服务地址（默认与 --url 相同）。指到**另一个共用同一"
             "个 Redis 的进程**上，就能顺带验证跨副本那一层：那个进程的 L1 是空的，"
             "命中只可能来自 L2。这正是 L2 存在的全部理由，也是单进程测不出来的。",
    )
    ap.add_argument(
        "--query",
        default="什么是 Spring Boot 的自动配置？它是如何生效的？",
        help="用来提问的句子。必须能通过改写槽位的 _is_concrete 门槛"
             "（含疑问词或 ≥40 字），否则改写根本不调 LLM，本脚本无从观测。",
    )
    ap.add_argument("--dataset", default="", help="数据集 id（留空 = 全局检索）")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    base = args.url.rstrip("/")
    base2 = (args.url2 or args.url).rstrip("/")
    cross = base2 != base
    body: dict = {"query": args.query}
    if args.dataset:
        body["dataset_id"] = args.dataset

    print(f"服务：{base}" + (f"  ->  第二次打到 {base2}（跨副本）" if cross else ""))
    print(f"问题：{args.query}")

    try:
        before = _get(f"{base2}/api/metrics", args.timeout)
    except urllib.error.URLError as exc:
        print(f"[FAIL] 连不上服务：{exc}")
        print("       先起一个：python -m uvicorn server.app:app "
              "--host 127.0.0.1 --port 8020 --workers 1")
        return 1

    # 基线读的是**第二次请求要打的那个进程**：命中必须发生在它身上。
    # 跨副本时它和 --url 是两个进程，各有各的计数器，读错一个就永远看不到增量。
    lc_before = before.get("llm_cache") or {}
    if "error" in lc_before:
        print(f"[FAIL] /api/metrics 里的 llm_cache 取不到：{lc_before['error']}")
        return 1
    saved_before = _saved_total(before)
    print(f"\n请求前（{base2}）llm_cache={json.dumps(lc_before, ensure_ascii=False)}")
    print(f"        已记账省下的 token={saved_before:g}")
    # 这里要问的是"连上了吗"，不是"配了吗"：配了 url 但 Redis 挂着的时候，
    # 两个进程之间照样没有共享层。老版本服务只报 l2_enabled（= 已配置），
    # 取不到 l2_reachable 时退回它，至少不比从前差。
    if cross and not lc_before.get("l2_reachable", lc_before.get("l2_enabled")):
        print("[FAIL] 跨副本模式下第二个进程的 L2 连不上 —— 它没连上 Redis，")
        print("       两个进程之间没有任何共享层，这次跨副本验证不可能成立。")
        print("       给两个进程都设上 RAG4C_REDIS_URL 再来。")
        return 1

    # 第一次：把管线跑热，槽位缓存这时应该是写入而不是命中。
    print(f"\n[1/2] {base} retry=true  …")
    try:
        r1 = _post(f"{base}/api/query", {**body, "retry": True}, args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 第一次请求失败：{exc}")
        return 1
    print(f"      {_describe(r1)}")

    mid = _get(f"{base}/api/metrics", args.timeout)
    lc_mid = mid.get("llm_cache") or {}
    print(f"      写入方（{base}）llm_cache={json.dumps(lc_mid, ensure_ascii=False)}")

    # 第二次：换掉 retry —— 答案缓存的键变了（管线真的重跑），
    # 但改写槽位收到的 prompt 一个字节没变（槽位缓存必须命中）。
    print(f"\n[2/2] {base2} retry=false （答案缓存键变了，改写 prompt 没变）…")
    try:
        r2 = _post(f"{base2}/api/query", {**body, "retry": False}, args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 第二次请求失败：{exc}")
        return 1
    print(f"      {_describe(r2)}")

    after = _get(f"{base2}/api/metrics", args.timeout)
    lc_after = after.get("llm_cache") or {}
    saved_after = _saved_total(after)
    print(f"\n请求后（{base2}）llm_cache={json.dumps(lc_after, ensure_ascii=False)}")
    print(f"        已记账省下的 token={saved_after:g}")

    hits_before = int(lc_before.get("hits", 0) or 0)
    hits_after = int(lc_after.get("hits", 0) or 0)
    delta = hits_after - hits_before
    l2_delta = int(lc_after.get("l2_hits", 0) or 0) - int(lc_before.get("l2_hits", 0) or 0)

    print("\n" + "=" * 60)
    if r2.get("cached"):
        # 答案缓存把第二次拦下了，管线没重跑，这次观测无效——报"没测成"，
        # 而不是报"缓存不命中"。把这两件事混为一谈会让人去修没坏的东西。
        print("[INCONCLUSIVE] 第二次被答案缓存拦下了（cached=true），管线没有重跑，")
        print("               槽位缓存这次根本没被查。换个没问过的问题再来一次。")
        return 2
    if delta >= 1:
        print(f"[PASS] 槽位缓存在真实路径上命中了：hits {hits_before} -> {hits_after}"
              f"（+{delta}）")
        print(f"       省下的 token：{saved_before:g} -> {saved_after:g}"
              f"（+{saved_after - saved_before:g}）")
        if saved_after == saved_before:
            print("       注：省量为 0 通常是端点没返回 usage，不影响命中判定。")
        if cross:
            # 跨副本时命中**必须**来自 L2：另一个进程的 L1 里从来没写过这条。
            # 只看总命中数会把"其实是本进程自己之前写的"也算进来。
            if l2_delta >= 1:
                print(f"       跨副本成立：l2_hits +{l2_delta}，命中来自 Redis 而非本进程 L1。")
            else:
                print(f"[FAIL] 命中了，但 l2_hits 没涨（+{l2_delta}）——这条命中来自该进程")
                print("       自己的 L1，没有跨进程。跨副本这一层仍未得到验证。")
                return 1
        elif not lc_after.get("l2_reachable", lc_after.get("l2_enabled")):
            print("       注：L2 不可达 —— 这次只验证了进程内的 L1。"
                  "配上 RAG4C_REDIS_URL 并用 --url2 指到第二个进程，才能验证跨副本那一层。")
        return 0
    print(f"[FAIL] 槽位缓存没有命中：hits {hits_before} -> {hits_after}")
    print("       管线重跑了（cached=false）却一次都没命中，说明接线断了。查：")
    print("       1) 六个检索期槽位的 cache_ttl_s 是不是真的 > 0（配置重建会吞掉字段默认值）")
    print("       2) 改写槽位这次到底调没调 LLM（_is_concrete 会把短句直接放行）")
    print("       3) 服务进程是不是跑的旧代码（改完没重启）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
