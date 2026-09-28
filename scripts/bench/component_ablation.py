"""可插拔组件的实机消融测量（不是并发压测，是"每个组件值不值"）。

`scripts/bench/load_query.py` 量的是吞吐（QPS/P95），上游用 mock 把方差压掉；
本脚本反过来：**打真实上游、串行、逐组件开关**，读的是每个可插拔组件自己的
延迟成本与产出（引用数、弃权率）。判据是同一批问题在"开 / 关"两种配置下的差值。

为什么每个变体单独起一个服务进程：配置热更新那条路（`/api/config/update`）会把值
**写进 .env 文件**，测量不该改仓库配置；进程环境变量优先级更高（`config/settings.py`
的读取顺序：进程 env > env 文件），所以按变体注入 env 再重启，测完即弃。

用法::

    python scripts/bench/component_ablation.py --env-file config/.env.live-bench \\
        --questions eval/.cache/bench_questions.json --out output/bench/ablation.json

退出码：0 = 跑完；2 = 某个变体起不来或全部请求失败（读数不可信，不当成结果）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_STAGE_MS = re.compile(r"^([a-z_][a-z0-9_]*):(\d+(?:\.\d+)?)ms$")
_BASELINE = "baseline"

# 每个变体 = 一组进程环境变量覆盖（RAG4C_<SECTION>_<KEY>），不改任何文件。
VARIANTS: dict[str, dict[str, str]] = {
    _BASELINE: {},
    "no_hybrid": {"RAG4C_PIPELINE_HYBRID_SEARCH_ON": "false"},
    "no_rerank": {"RAG4C_PIPELINE_RERANK_ON": "false"},
    "no_complexity_gate": {"RAG4C_PIPELINE_COMPLEXITY_GATE_ON": "false"},
    "no_pdf_router": {"RAG4C_PARSERS_ROUTER_ON": "false"},
    "no_llm_rerank": {"RAG4C_GRAPH_USE_LLM_RERANK": "false"},
    "no_hyde": {"RAG4C_PIPELINE_HYDE_ON": "false"},
    "no_subqueries": {"RAG4C_PIPELINE_SUBQUERIES_ON": "false"},
    "no_stepback": {"RAG4C_PIPELINE_STEPBACK_ON": "false"},
    "no_sentence_window": {"RAG4C_PIPELINE_SENTENCE_WINDOW_ON": "false"},
    "no_graph_retrieval": {"RAG4C_PIPELINE_GRAPH_RETRIEVAL_ON": "false"},
    "no_acl_filter": {"RAG4C_PIPELINE_ACL_FILTER_ON": "false"},
    "no_query_cache": {"RAG4C_REDIS_CACHE_ON": "false"},
    "no_endpoint_probe": {"RAG4C_RETRY_ENDPOINT_PROBE_ON": "false"},
    "diversity_group_mmr": {"RAG4C_PIPELINE_SOURCE_DIVERSITY": "group_mmr"},
    # QA 权威（approved+retrieval_enabled 的 FAQ 并入证据）此前从未被消融过：
    # 隔离库里一条 FAQ 都没有，关掉它当然没有 Δ。本轮补了 seed_live_faqs.py 之后才有对照意义。
    "no_qa_retrieval": {"RAG4C_PIPELINE_QA_RETRIEVAL_ON": "false"},
}


def _resolvable(env_name: str, valid: set[str]) -> bool:
    """`RAG4C_PIPELINE_HYBRID_SEARCH_ON` → `pipeline.hybrid_search_on`。

    字段名本身含下划线，不能只按第一个下划线切：改成"前缀=段名，其余部分必须
    能在同一个段里唯一匹配到一个字段路径"。
    """
    body = env_name[len("RAG4C_") :].lower()
    section, _, rest = body.partition("_")
    candidates = {path for path in valid if path.startswith(section + ".")
                  and path.split(".", 1)[1].replace("_", "") == rest.replace("_", "")}
    return len(candidates) == 1


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], timeout: float = 180.0) -> tuple[Any, float]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body, (time.perf_counter() - started) * 1000.0


def _parse_stages(traces: list[Any]) -> dict[str, float]:
    """把 `gate:3711.96ms` 这类 trace 行还原成 {阶段: 毫秒}。

    重复的阶段名取最大值：一次请求里同一阶段可能被记两遍（trace 目前会整表
    追加两次），求和会把耗时虚报一倍。
    """
    stages: dict[str, float] = {}
    for item in traces:
        match = _STAGE_MS.match(str(item).strip())
        if not match:
            continue
        stage, value = match.group(1), float(match.group(2))
        stages[stage] = max(stages.get(stage, 0.0), value)
    return stages


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0}
    ordered = sorted(values)
    return {
        "p50": round(ordered[min(len(ordered) - 1, int(0.50 * len(ordered)))], 1),
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1),
    }


def _spawn(env_file: str, overrides: dict[str, str], port: int, log_path: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["RAG4C_ENV_FILE"] = env_file
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(overrides)
    log = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        stdout=log, stderr=log, env=env, cwd=os.getcwd(),
    )


def _wait_ready(base: str, process: subprocess.Popen, budget_s: float = 120.0) -> None:
    deadline = time.time() + budget_s
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服务进程提前退出（退出码 {process.returncode}）")
        try:
            _get(f"{base}/api/health", timeout=6.0)
            return
        except Exception as exc:  # noqa: BLE001 - 就绪探测：任何失败都继续等
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
    raise RuntimeError(f"服务在 {budget_s}s 内没就绪，最后一次探测失败：{last_error}")


def _measure_variant(
    name: str, overrides: dict[str, str], *, env_file: str, questions: list[dict[str, str]],
    repeat: int, warmup: int, log_dir: str,
) -> dict[str, Any]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    process = _spawn(env_file, overrides, port, os.path.join(log_dir, f"{name}.log"))
    try:
        _wait_ready(base, process)
        config = _get(f"{base}/api/config")
        settings_seen = _flatten_config_sections(config)
        # 预热整轮：LLM 槽位（gate/route/生成/判定）与嵌入都带 Redis 缓存，
        # 只热一两条会让后面的变体走热路径、基线走冷路径，Δ 就是假的。
        # warmup=0 才是纯冷路径读数。
        for _ in range(warmup):
            for item in questions:
                payload = {"query": item["query"]}
                if item.get("dataset_id"):
                    payload["dataset_id"] = item["dataset_id"]
                try:
                    _post_json(f"{base}/api/query", payload, timeout=240.0)
                except Exception:  # noqa: BLE001 - 预热失败不阻断测量，冷读数照跑
                    pass
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for cycle in range(repeat):
            for item in questions:
                payload = {"query": item["query"]}
                if item.get("dataset_id"):
                    payload["dataset_id"] = item["dataset_id"]
                try:
                    body, wall_ms = _post_json(f"{base}/api/query", payload)
                except (urllib.error.URLError, TimeoutError) as exc:
                    errors.append(f"{item['case_id']}:{type(exc).__name__}")
                    continue
                result = body.get("result") or {}
                traces = [str(t) for t in (result.get("traces") or [])]
                rows.append({
                    "case_id": item["case_id"],
                    "cycle": cycle,
                    "wall_ms": round(wall_ms, 1),
                    "server_ms": body.get("duration_ms"),
                    "using_mock": body.get("using_mock"),
                    "route": result.get("route"),
                    "abstained": bool(result.get("abstained")),
                    # 弃权/降级的**原因文本**是读数能不能解释得清的关键：只看
                    # abstained=True 会把"检索没把握"和"上游不可用"混成一件事。
                    "signals": [t for t in traces if ("弃权" in t or "降级" in t or "fence" in t)][:4],
                    "citations": len(result.get("citations") or []),
                    "llm_calls": (result.get("usage") or {}).get("calls"),
                    "stages_ms": _parse_stages(traces),
                })
        metrics = _get(f"{base}/api/metrics", timeout=8.0)
        # 服务端自己的计数器（含降级率）与逐阶段百分位，是"界面读数对不对"的对照组
        api_metrics = {key: value for key, value in metrics.items() if isinstance(value, dict)}
    finally:
        # Windows 上 send_signal(SIGINT) 会 ValueError("Unsupported signal: 2")，
        # terminate() 在两个平台都能正常收尾（uvicorn 收到 CTRL_BREAK/TERM 会退出）。
        try:
            process.terminate()
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    answered = [row for row in rows if not row["abstained"]]
    stage_totals: dict[str, list[float]] = {}
    for row in rows:
        for stage, value in row["stages_ms"].items():
            stage_totals.setdefault(stage, []).append(value)
    return {
        "overrides": overrides,
        "settings_seen": {key: settings_seen.get(key) for key in sorted(overrides)} if overrides else {},
        "requests": len(rows),
        "errors": errors,
        "e2e_ms": _percentiles([row["wall_ms"] for row in rows]),
        "server_ms": _percentiles([float(row["server_ms"]) for row in rows if row["server_ms"]]),
        "abstain_rate": round(1 - len(answered) / len(rows), 4) if rows else None,
        "citations_mean": round(sum(row["citations"] for row in rows) / len(rows), 2) if rows else None,
        "mock_used": sum(1 for row in rows if row["using_mock"]),
        "stages_ms": {stage: _percentiles(values) for stage, values in sorted(stage_totals.items())},
        "routes": sorted({str(row["route"]) for row in rows}),
        "api_metrics": api_metrics,
        "rows": rows,
    }


def _flatten_config_sections(config: dict[str, Any]) -> dict[str, Any]:
    """把 /api/config 的 sections 摊成 `{环境变量名: 服务端实际看到的值}`。

    这一份读数是"覆盖到底生效没有"的证据：写错的键会被 pydantic-settings 静默
    忽略，那个变体就退化成 baseline，Δ 是假的。
    """
    by_env: dict[str, Any] = {}
    sections = config.get("sections")
    if isinstance(sections, dict):
        for fields in sections.values():
            for field in (fields or {}).get("fields") or []:
                env_name = field.get("env")
                if env_name:
                    by_env[str(env_name)] = field.get("value")
    return by_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="config/.env.live-bench")
    parser.add_argument("--questions", required=True, help="JSON 数组：[{case_id, query, dataset_id?}]")
    parser.add_argument("--variants", default=_BASELINE + ",no_hybrid,no_rerank",
                        help=f"逗号分隔；可选 {', '.join(VARIANTS)}")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--out", default="output/bench/ablation.json")
    args = parser.parse_args(argv)

    questions = json.loads(open(args.questions, encoding="utf-8").read())
    if not questions:
        print("[error] 问题集为空", file=sys.stderr)
        return 2
    chosen = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = [name for name in chosen if name not in VARIANTS]
    if unknown:
        print(f"[error] 未知变体 {unknown}；可选 {sorted(VARIANTS)}", file=sys.stderr)
        return 2
    # 覆盖键必须是真的配置路径：写错的键会被 pydantic-settings 静默忽略，
    # 那个变体就退化成 baseline，Δ 读数是假的。
    from config.settings import Settings, _iter_field_paths

    valid = {".".join(path) for section, model in Settings.model_fields.items()
             for path in _iter_field_paths(model.annotation, (section,))}
    bad_keys = sorted({
        env_name for overrides in (VARIANTS[name] for name in chosen)
        for env_name in overrides
        if env_name.startswith("RAG4C_")
        and ".".join(env_name[len("RAG4C_") :].lower().split("_", 1)) not in valid
        and not _resolvable(env_name, valid)
    })
    if bad_keys:
        print(f"[error] 这些覆盖键不在配置模型里，测出来会是假的差值：{bad_keys}", file=sys.stderr)
        return 2
    log_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(log_dir, exist_ok=True)

    report: dict[str, Any] = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "env_file": args.env_file, "questions": questions, "variants": {}}
    for name in chosen:
        print(f"[{name}] 起服务并测量 …", flush=True)
        try:
            report["variants"][name] = _measure_variant(
                name, VARIANTS[name], env_file=args.env_file, questions=questions,
                repeat=args.repeat, warmup=args.warmup, log_dir=log_dir,
            )
        except RuntimeError as exc:
            print(f"[error] 变体 {name} 起不来：{exc}", file=sys.stderr)
            report["variants"][name] = {"failed": str(exc)}
            return 2

    base_payload = report["variants"][_BASELINE] if _BASELINE in report["variants"] else None
    lines = [f"问题 {len(questions)} 条 × 重复 {args.repeat}，env 文件 {args.env_file}"]
    for name, payload in report["variants"].items():
        delta = ""
        if base_payload and name != _BASELINE:
            delta = (f"  Δp50={payload['e2e_ms']['p50'] - base_payload['e2e_ms']['p50']:+.0f}ms"
                     f" Δ弃权={payload['abstain_rate'] - base_payload['abstain_rate']:+.2f}"
                     f" Δ引用={payload['citations_mean'] - base_payload['citations_mean']:+.2f}")
        lines.append(
            f"{name:22s} p50={payload['e2e_ms']['p50']:7.1f} p95={payload['e2e_ms']['p95']:7.1f} "
            f"弃权率={payload['abstain_rate']:.2f} 平均引用={payload['citations_mean']:.2f}"
            f" mock={payload['mock_used']}{delta}"
        )
        top = sorted(payload["stages_ms"].items(), key=lambda kv: -kv[1]["p50"])[:3]
        lines.append("    最贵阶段: " + ", ".join(f"{stage} p50={value['p50']:.0f}ms" for stage, value in top))
    print("\n".join(lines))

    path = os.path.abspath(args.out)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(f"\n报告: {path}")
    failed = [name for name, payload in report["variants"].items() if payload.get("errors")]
    if failed:
        print(f"[warn] 有变体存在请求错误：{failed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
