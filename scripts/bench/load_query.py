#!/usr/bin/env python
"""并发压测：对 /api/query 打固定并发，产出 QPS / P50 / P95 / P99 / 错误分布。

判据是**改动前后的同一组数字**，所以这个脚本本身必须是可复现的：固定问题集、
固定并发、固定总量，结果写成 JSON 落盘，改完代码再跑一遍直接 diff。

配合 ``mock_upstream.py`` 使用——真实上游的延迟方差会淹没被测信号，
详见那个文件的开头。

三种负载各自回答一个不同的问题，缺一不可：

- ``distinct``  每个请求问不同的问题 → 全部 miss 缓存，测的是**真实吞吐上限**
- ``hotspot``   所有请求问同一个问题 → 全部撞 single-flight，测的是**缓存击穿
  路径会不会把服务掐死**（实测这里最容易出事：等待者若占着执行槽，热点问题
  会让整个服务对其它请求也不可用）
- ``mixed``     八成热点两成长尾 → 最接近真实流量形态

用法::

    python scripts/bench/load_query.py --url http://127.0.0.1:8010 \\
        --concurrency 32 --total 200 --pattern distinct --out output/bench/before-distinct.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

# 压测只打自己拉起的本机服务：仅接受 http + 数字环回地址（不解析 DNS，即无
# rebinding 面），且禁止重定向。这不是任意 URL 的请求器。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect not allowed", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _assert_loopback_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in _LOOPBACK_HOSTS:
        raise ValueError("load_query only talks to the loopback service it measures")


_QUESTIONS = [
    "What is a Spring Boot starter and why would I use one?",
    "How do I configure a DataSource in Spring Boot?",
    "Milvus 里的 collection 和 index 是什么关系？",
    "What is a checkpointer in LangGraph?",
    "How does Redis handle key expiration?",
    "What is the difference between HNSW and IVF in Milvus?",
    "How do I write a custom LangChain retriever?",
    "Spring Boot 的自动配置是怎么生效的？",
]


def _post(url: str, payload: dict, timeout: float) -> tuple[int, float, str, bool | None]:
    """发一次请求，返回 ``(状态码, 耗时秒, 错误标签, abstained)``。

    非 2xx 不抛异常而是当数据返回：压测里 429/503 是**被测行为**而不是脚本
    故障，它们恰恰是要统计的东西。区分 429（限流拒绝，服务还活着）和 503
    （排队超时，服务已经堵了）对判断瓶颈在哪很关键。

    ``abstained`` 是为了回答一个不问就会误读的问题：**这批请求里有多少走了
    弃权？** 弃权路径不调生成与 L3 裁判，延迟只有完整链路的零头（实测真实
    上游：完整链路 10~30s、弃权 0.26~0.33s，差 30 倍）。不报这个比例，
    QPS 117 / P50 262ms 会被当成「系统能扛 117 QPS 的完整问答」——而实际上
    它扛的是「检索 + 快速拒答」。

    解析不出该字段时返回 ``None``（而不是 False）：契约变了要看得见，
    静默当成"没弃权"会把这个缺口盖掉。
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    _assert_loopback_url(url)
    t0 = time.perf_counter()
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, time.perf_counter() - t0, "", _extract_abstained(raw)
    except urllib.error.HTTPError as e:
        try:
            e.read()
        except Exception:  # noqa: BLE001
            pass
        return e.code, time.perf_counter() - t0, f"http_{e.code}", None
    except Exception as e:  # noqa: BLE001
        return 0, time.perf_counter() - t0, type(e).__name__, None


def _extract_abstained(raw: bytes) -> bool | None:
    """从响应体里取 ``result.abstained``；取不到返回 ``None``。

    契约是 ``{result, using_mock, duration_ms}``（见 ``server/app.py`` 的
    ``_serialize``），abstained 在 ``result`` 里。这里刻意吞掉一切解析异常
    并返回 None——压测脚本不该因为响应体形状变了而崩掉，但那个形状变化必须
    体现在 ``abstained_known_rate`` 上。
    """
    try:
        data = json.loads(raw)
        inner = data.get("result")
        if not isinstance(inner, dict) or "abstained" not in inner:
            return None
        return bool(inner["abstained"])
    except Exception:  # noqa: BLE001
        return None


def _pct(sorted_vals: list[float], p: float) -> float:
    """最近秩百分位。样本量小时线性插值会造出没人经历过的数字，这里不插值。"""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round(p / 100.0 * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[k]


def run(args: argparse.Namespace) -> dict:
    url = args.url.rstrip("/") + "/api/query"
    results: list[tuple[int, float, str]] = []
    lock = threading.Lock()
    counter = {"n": 0}

    # 每轮压测都要**冷缓存**，否则测的是缓存而不是链路。
    #
    # 这个坑真踩了：第一版没有 nonce，问题集只有 8 条而总量 96，于是第 9 个
    # 请求起全是缓存命中，distinct 测出 P50 = 1073ms、hotspot 测出 119 QPS
    # ——两个数字都漂亮，也都毫无意义，因为服务端根本没干活。更隐蔽的是，
    # 之前手工发的那一次验证查询已经把 hotspot 用的问题写进了缓存，所以
    # hotspot 连 single-flight 都没触发到。
    #
    # nonce 按「每轮一个」而不是「每个请求一个」：hotspot 要的正是同一轮内
    # 所有请求撞同一个 key（才能压到 single-flight），只需与**上一轮**隔离。
    nonce = args.nonce or f"{time.time():.3f}"

    # 预热：丢弃前几个请求的计时。
    #
    # 第二个坑，和 nonce 那个同一类：重启服务后的第一轮 distinct 测出 7.49 QPS，
    # 而紧接着连测三轮稳定在 18~22 QPS。差的不是代码，是**冷启动**——首个请求
    # 才会去惰性建 openai 客户端、建连接池、加载配置。96 个请求里混进几个
    # 一次性的秒级开销，均值和 P95 就全废了，还会被误读成"改动引入了退化"。
    #
    # 预热请求走**独立 nonce**，否则它们会把正式轮次的 key 写进缓存。
    if args.warmup > 0:
        print(f"  预热 {args.warmup} 个请求（不计入统计）…", flush=True)
        for w in range(args.warmup):
            # warmup 忽略返回值：它的作用是填缓存/热身，不进统计。
            _post(url, {"query": f"{_QUESTIONS[w % len(_QUESTIONS)]} [warmup-{nonce}-{w}]",
                        "dataset_id": args.dataset or None}, args.timeout)

    def pick(i: int) -> str:
        if args.pattern == "hotspot":
            return f"{_QUESTIONS[0]} [{nonce}]"
        if args.pattern == "mixed":
            # 八成打同一个热点，两成散开——真实流量里"大家都在问同一件事"
            # 是常态，而纯 distinct 会低估缓存路径的重要性。
            if i % 5:
                return f"{_QUESTIONS[0]} [{nonce}]"
            return f"{_QUESTIONS[i % len(_QUESTIONS)]} [{nonce}-{i}]"
        # distinct：每个请求都要有独一无二的 key，不能靠问题集轮转
        return f"{_QUESTIONS[i % len(_QUESTIONS)]} [{nonce}-{i}]"

    def worker() -> None:
        while True:
            with lock:
                i = counter["n"]
                if i >= args.total:
                    return
                counter["n"] = i + 1
            r = _post(
                url,
                {"query": pick(i), "dataset_id": args.dataset or None},
                args.timeout,
            )
            with lock:
                results.append(r)
                done = len(results)
            if done % 20 == 0:
                print(f"  {done}/{args.total}", flush=True)

    print(
        f"压测 {url}\n  并发 {args.concurrency}  总量 {args.total}  "
        f"负载 {args.pattern}  超时 {args.timeout}s"
    )
    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    ok = [d for code, d, _, _ in results if code == 200]
    codes = Counter(code for code, _, _, _ in results)
    errs = Counter(tag for _, _, tag, _ in results if tag)
    ok_sorted = sorted(ok)

    # 弃权率：只统计**成功**请求（失败请求没有 answer 可言）。
    #
    # 分母用「成功里 abstained 字段已知的那部分」，另报 known_rate —— 若
    # known_rate < 1 说明响应形状变了、这个比例本身就不可信，两个数要一起看。
    ok_rows = [(code, abst) for code, _, _, abst in results if code == 200]
    known = [abst for _, abst in ok_rows if abst is not None]
    abstained = sum(1 for v in known if v)

    # QPS 只算成功请求。把秒回的 429 算进吞吐会得出「越过载越快」的荒谬结论
    # ——那正是过载时最容易自欺的一个数字。
    summary = {
        "url": url,
        "pattern": args.pattern,
        "concurrency": args.concurrency,
        "total": args.total,
        "warmup": args.warmup,
        "wall_s": round(wall, 2),
        "ok": len(ok),
        "qps_ok": round(len(ok) / wall, 3) if wall else 0.0,
        "success_rate": round(len(ok) / len(results), 4) if results else 0.0,
        "latency_ms": {
            "p50": round(_pct(ok_sorted, 50) * 1000, 1),
            "p95": round(_pct(ok_sorted, 95) * 1000, 1),
            "p99": round(_pct(ok_sorted, 99) * 1000, 1),
            "mean": round(statistics.fmean(ok) * 1000, 1) if ok else 0.0,
            "max": round(max(ok) * 1000, 1) if ok else 0.0,
        },
        "status_codes": dict(sorted(codes.items())),
        "errors": dict(errs),
        # 弃权率：这个数字决定上面那些 QPS/延迟该怎么读。全 0 = 测的是完整
        # 问答链路；接近 1 = 测的是「检索 + 快速拒答」，与完整链路差一个数量级。
        "abstained_rate": round(abstained / len(known), 4) if known else None,
        "abstained_known_rate": round(len(known) / len(ok_rows), 4) if ok_rows else None,
    }

    print("\n" + "=" * 60)
    print(f"  成功 {summary['ok']}/{args.total}   成功率 {summary['success_rate']:.1%}")
    print(f"  QPS  {summary['qps_ok']}   墙钟 {summary['wall_s']}s")
    lat = summary["latency_ms"]
    print(f"  P50 {lat['p50']}ms   P95 {lat['p95']}ms   P99 {lat['p99']}ms   max {lat['max']}ms")
    print(f"  状态码 {summary['status_codes']}")
    ar = summary["abstained_rate"]
    kr = summary["abstained_known_rate"]
    if ar is None:
        print("  弃权率   未知（响应体里没有 result.abstained）")
    else:
        print(f"  弃权率   {ar:.1%}（字段可见 {kr:.0%}）")
        if ar >= 0.5:
            print("           ⚠ 过半请求走了弃权：上面的 QPS/延迟主要反映「检索 + 快速拒答」，"
                  "不是完整问答链路。")
        if kr is not None and kr < 1.0:
            print(f"           ⚠ 只有 {kr:.0%} 的响应带 result.abstained，弃权率本身不可信。")
    if errs:
        print(f"  错误   {dict(errs)}")
    print("=" * 60)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG4C /api/query 并发压测")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--total", type=int, default=200)
    ap.add_argument("--pattern", choices=["distinct", "hotspot", "mixed"], default="distinct")
    ap.add_argument("--dataset", default="springboot")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument(
        "--nonce", default="",
        help="附加到问句上的一次性后缀，用来绕开服务端查询缓存；留空则用时间戳",
    )
    ap.add_argument(
        "--warmup", type=int, default=4,
        help="正式计时前先发几个不计入统计的请求，抵消冷启动开销；0 表示不预热",
    )
    ap.add_argument("--out", default="", help="结果写入的 JSON 路径")
    args = ap.parse_args()

    summary = run(args)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {p}")
    # 退出码只反映"脚本跑完了"。成功率低是**测量结果**不是脚本失败，
    # 用非零退出会让 CI 把"发现了瓶颈"误报成"压测挂了"。
    return 0


if __name__ == "__main__":
    sys.exit(main())
