"""Development-only legacy backend stub for the Task 14 browser screenshot matrix.

This process intentionally omits the Runs API, serves only synthetic metrics, writes no
files or run history, and binds to loopback. It must never be used as a production server.
"""

from __future__ import annotations

import argparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


HOST = "127.0.0.1"
ALLOWED_ORIGINS = ("http://localhost:1420", "http://127.0.0.1:1420")


def _stat(*, count: int, total: float, p50: float, p95: float, p99: float) -> dict[str, float | int]:
    mean = total / count if count else 0.0
    minimum = 0.0 if count == 0 else min(p50, mean)
    maximum = 0.0 if count == 0 else max(p99, mean)
    return {
        "count": count,
        "sum": total,
        "mean": mean,
        "min": minimum,
        "max": maximum,
        "p50": p50,
        "p95": p95,
        "p99": p99,
    }


METRICS_SNAPSHOT = {
    "ts": "2026-08-24 09:02:00",
    "metrics": {
        "query.total": _stat(count=2, total=3120.0, p50=1280.0, p95=1840.0, p99=1840.0),
        "query.completed": _stat(count=2, total=3120.0, p50=1280.0, p95=1840.0, p99=1840.0),
        "query.abstained": _stat(count=1, total=1840.0, p50=1840.0, p95=1840.0, p99=1840.0),
    },
    "recent_queries": [
        {
            "query": "合成问题 A",
            "route": "hybrid",
            "abstained": False,
            "duration_ms": 1280,
            "citations": 3,
            "traces": [],
            "ts": "2026-08-24 09:00:00",
        },
        {
            "query": "合成问题 B",
            "route": "vector",
            "abstained": True,
            "duration_ms": 1840,
            "citations": 0,
            "traces": [],
            "ts": "2026-08-24 09:01:00",
        },
    ],
    "uptime_s": 900.0,
    "queue": {"pending": 0, "max_concurrent": 4, "queue_max": 20},
    "cache": {
        "size": 2,
        "max": 64,
        "hits": 1,
        "misses": 1,
        "hit_rate": 0.5,
        "ttl_s": 600,
    },
    "circuits": {},
    "qa_stub": True,
    "development_only": True,
}

HEALTH_SNAPSHOT = {
    "status": "ok",
    "milvus_uri": "qa-stub://not-connected",
    "embedding_model": "qa-stub",
    "reranker_model": "qa-stub",
    "generation_model": "qa-stub",
    "graph_engine_on": False,
    "qa_stub": True,
    "development_only": True,
}

app = FastAPI(
    title="RAG4C Legacy Ops QA Stub — development only",
    description="Synthetic loopback-only backend for the legacy Runs banner screenshot.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/runs/health")
def runs_health() -> None:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "legacy_backend",
            "message": "development-only QA stub intentionally omits the Runs API",
        },
    )


@app.get("/api/metrics")
def metrics() -> dict[str, object]:
    return METRICS_SNAPSHOT


@app.get("/api/health")
def health() -> dict[str, object]:
    return HEALTH_SNAPSHOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the loopback-only development legacy Ops QA stub."
    )
    parser.add_argument("--port", type=int, default=8000, help="Loopback port (default: 8000).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import uvicorn

    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
