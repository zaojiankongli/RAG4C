#!/usr/bin/env python
"""模拟上游：OpenAI 兼容的 /chat/completions、/embeddings、/rerank。

存在的理由是**并发压测不能用真实上游**：

- 真实上游要花钱。一次问答串起 3~4 次 LLM，压 200 个请求就是七八百次调用。
- 真实上游的延迟抖动比被测对象大得多。云端 LLM 单次 30~90 秒、方差极大，
  这种噪声会把「服务端排队/连接池改没改好」的信号整个淹掉。
- 压测要能复现。改一版代码再压一次，两次的差必须来自代码，不能来自
  「今天云端比较忙」。

所以这里给一个**延迟可控、结果确定**的假上游：延迟固定（可加抖动），
返回结构与真实端点一致。压出来的数字衡量的是 RAG4C 自己的并发能力
——排队、线程池、连接复用、缓存击穿——而这正是要优化的东西。

不模拟 Milvus：milvus-lite 是本地文件，本来就快且免费，留着真的更有代表性。

用法::

    python scripts/bench/mock_upstream.py --port 8899 --latency-ms 80
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 维度必须与被测配置的 RAG4C_MILVUS_DIM 一致，否则写入/检索会直接报维度不符。
DIM = 1024

_args: argparse.Namespace
_stats_lock = threading.Lock()
_stats: dict[str, int] = {}


def _bump(name: str) -> None:
    with _stats_lock:
        _stats[name] = _stats.get(name, 0) + 1


def _fake_vector(text: str) -> list[float]:
    """由文本内容确定性地生成单位向量。

    确定性很关键：同一段文本每次必须得到同一个向量，否则「同一个问题」在
    两轮压测里会召回不同的 chunk，耗时差异就没法归因了。用哈希扩展成字节流
    再解释为 float，比 random.seed 省事且无全局状态。
    """
    buf = b""
    seed = text.encode("utf-8")
    counter = 0
    while len(buf) < DIM * 4:
        buf += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    vals = struct.unpack(f"{DIM}f", buf[: DIM * 4])
    # 归一化：余弦相似度在非单位向量上会被模长带偏，检索排序就不稳定了
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / norm for v in vals]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # 支持 keep-alive，否则测的是握手开销

    def log_message(self, *a: object) -> None:  # 压测时日志会拖慢自己
        pass

    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw)
        except Exception:  # noqa: BLE001
            req = {}
        path = self.path.split("?")[0]

        time.sleep(_args.latency_ms / 1000.0)

        if path.endswith("/embeddings"):
            _bump("embeddings")
            inp = req.get("input")
            texts = inp if isinstance(inp, list) else [inp or ""]
            _bump_by("embed_texts", len(texts))
            self._send({
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": _fake_vector(str(t))}
                    for i, t in enumerate(texts)
                ],
                "model": req.get("model", "mock-embed"),
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            })
            return

        if path.endswith("/rerank"):
            _bump("rerank")
            docs = req.get("documents") or []
            # 按原顺序线性递减打分：既有区分度（探活脚本会断言相关性可分），
            # 又完全确定，不会让两轮压测召回不同的 chunk。
            n = max(1, len(docs))
            self._send({
                "results": [
                    {"index": i, "relevance_score": round(1.0 - i / n, 6)}
                    for i in range(len(docs))
                ]
            })
            return

        if path.endswith("/chat/completions"):
            _bump("chat")
            # judge / router_llm / metadata_filter 槽位要求 JSON 对象输出。
            # 返回一个"全部支持"的判定，让链路走到底而不是在弃权门提前返回
            # ——压测要压的是完整链路的开销，中途弃权会把耗时测低。
            want_json = (req.get("response_format") or {}).get("type") == "json_object"
            if want_json:
                content = json.dumps({
                    "verdict": "supported", "supported": True, "score": 0.95,
                    "route": "vector", "confidence": 0.9, "filters": {},
                    "results": [{"index": 0, "supported": True, "score": 0.95}],
                })
            else:
                content = "这是压测用的模拟回答，用于衡量服务端并发能力[1]。"
            if req.get("stream"):
                self._send_stream(content)
                return
            self._send({
                "id": "mock", "object": "chat.completion", "created": 0,
                "model": req.get("model", "mock-llm"),
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
            return

        self.send_error(404)

    def _send_stream(self, content: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(payload: str) -> None:
            data = f"data: {payload}\n\n".encode("utf-8")
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")

        for ch in content:
            chunk(json.dumps({
                "id": "mock", "object": "chat.completion.chunk", "created": 0,
                "model": "mock-llm",
                "choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}],
            }))
        chunk("[DONE]")
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/__stats"):
            with _stats_lock:
                self._send(dict(_stats))
            return
        self.send_error(404)


def _bump_by(name: str, n: int) -> None:
    with _stats_lock:
        _stats[name] = _stats.get(name, 0) + n


def main() -> int:
    global _args
    ap = argparse.ArgumentParser(description="压测用的模拟上游（LLM / 嵌入 / 重排）")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument(
        "--latency-ms", type=float, default=80.0,
        help="每次调用的固定延迟；模拟真实网络往返，别设 0（0 会让服务端看起来毫无排队压力）",
    )
    _args = ap.parse_args()

    srv = ThreadingHTTPServer(("127.0.0.1", _args.port), Handler)
    srv.daemon_threads = True
    print(f"模拟上游已启动 http://127.0.0.1:{_args.port}  延迟 {_args.latency_ms}ms  维度 {DIM}")
    print(f"  调用计数： curl http://127.0.0.1:{_args.port}/__stats")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
