"""桥服务并发压测脚本。

用法::

    python scripts/bench.py --path /api/health --n 200 --c 16           # 连接复用（推荐）
    python scripts/bench.py --path /api/metrics --n 100 --c 8 --new     # 每次新建连接

说明：本机回环的「新 TCP 连接」可能被安全软件拖慢（数百 ms~2s），
连接复用才是服务端真实性能（毫秒级）。默认用 httpx 连接池测量。
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def percentile(sorted_lat: list[float], q: float) -> float:
    if not sorted_lat:
        return 0.0
    idx = min(len(sorted_lat) - 1, int(q * len(sorted_lat)))
    return sorted_lat[idx]


def _hit(client: httpx.Client, url: str, timeout: float) -> tuple[bool, float]:
    """单次请求（复用客户端连接池）。"""
    t0 = time.perf_counter()
    try:
        client.get(url, timeout=timeout)
        return True, (time.perf_counter() - t0) * 1000.0
    except Exception:
        return False, (time.perf_counter() - t0) * 1000.0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG4C 桥服务并发压测")
    parser.add_argument("--url", default="http://localhost:8000", help="桥服务地址")
    parser.add_argument("--path", default="/api/health", help="压测路径")
    parser.add_argument("--n", type=int, default=200, help="总请求数")
    parser.add_argument("--c", type=int, default=16, help="并发数")
    parser.add_argument("--timeout", type=float, default=30.0, help="单请求超时(秒)")
    parser.add_argument("--new", action="store_true", help="每次新建连接（对比连接复用）")
    args = parser.parse_args()

    url = f"{args.url.rstrip('/')}{args.path}"
    mode = "每次新建连接" if args.new else "连接复用(推荐)"
    print(f"[bench] {url}  n={args.n}  c={args.c}  [{mode}]")

    t0 = time.perf_counter()
    results: list[tuple[bool, float]] = []

    if args.new:
        # 每次请求新建客户端（新 TCP 连接，会暴露本机连接开销）
        with ThreadPoolExecutor(max_workers=args.c) as pool:
            futures = [
                pool.submit(
                    lambda: _hit(httpx.Client(timeout=args.timeout), url, args.timeout)
                )
                for _ in range(args.n)
            ]
            for fut in as_completed(futures):
                results.append(fut.result())
    else:
        # 每线程一个客户端（连接池复用）；每线程固定分配 n/c 份
        per = max(1, args.n // args.c)

        def _worker(_: int) -> None:
            with httpx.Client(timeout=args.timeout) as client:
                for _ in range(per):
                    results.append(_hit(client, url, args.timeout))

        with ThreadPoolExecutor(max_workers=args.c) as pool:
            list(pool.map(_worker, range(args.c)))
        # 修正剩余（n % c 份由主线程补齐）
        rest = args.n - len(results)
        with httpx.Client(timeout=args.timeout) as client:
            for _ in range(max(0, rest)):
                results.append(_hit(client, url, args.timeout))

    elapsed = time.perf_counter() - t0
    ok = sum(1 for s, _ in results if s)
    fail = args.n - ok
    lat = sorted(latency for _, latency in results)

    print(f"成功={ok}  失败={fail}  总耗时={elapsed:.2f}s  QPS={args.n / elapsed:.1f}")
    print(f"延迟ms  p50={percentile(lat, 0.50):.1f}  p95={percentile(lat, 0.95):.1f}  "
          f"p99={percentile(lat, 0.99):.1f}  max={max(lat):.1f}")
    if fail:
        print(f"[WARN] {fail} 个请求失败（可能触发限流/超时）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
