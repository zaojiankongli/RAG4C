"""F1.2 答案层基线的一键运行入口（真实管线 + 真实上游）。

跑法::

    python scripts/run_answer_baseline.py                 # 全量 145 条
    python scripts/run_answer_baseline.py --limit 10      # 先小批验通路
    python scripts/run_answer_baseline.py --workers 4 --out eval/baseline.json

为什么需要这个脚本，而不是直接 ``python -m eval.run_eval``：

1. **上游切换不落在仓库里**。``.env`` 的八个槽位指向 DashScope，而该账号
   目前欠费（实测 ``Arrearage`` / HTTP 400，1 秒即拒），所以本脚本从
   ``.env`` 现场取硅基流动的密钥，用**进程内环境变量**把八个槽位临时切过去。
   不改 ``.env``——那是运维的部署决定，不该被一次评测悄悄改掉。
2. **思考链必须显式关掉**。见 ``eval/.cache/probe_thinking.py`` 的实测：
   默认 69.4s / 3397 completion tokens，关掉后 8.1s / 418。145 条按默认值
   跑要 22 小时，按关掉的跑约 40 分钟。
3. **超时给足**。120s 遇上没关思考链的生成必然超时重试（白等 4 分钟/题）；
   这里同时把超时提到 300s，让偶发慢响应不再触发重试。
4. **节流 + 并发**。硅基流动有 429 限流，全速打会把配额烧在重试上。

这些覆盖只在本进程内生效（``os.environ`` + 子进程），退出后不残留。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: 需要切到硅基流动的槽位。缺一个就有一半调用仍然打在被拒的 DashScope 上，
#: 而失败形态是"弃权 + 降级"，读数上会被误当成质量问题——所以这里要全。
_SLOTS = (
    "REWRITE",
    "ROUTER_LLM",
    "HYDE",
    "SUBQUERIES",
    "STEPBACK",
    "METADATA_FILTER",
    "GENERATION",
    "JUDGE",
)

#: 这些槽位要的是结构化 JSON 或短输出，思考链对它们毫无帮助，只增加延迟与
#: 成本。generation 也关掉：它要的是带引用的直答，思考链挤占的是 max_tokens。
_THINKING_OFF = _SLOTS


def _key_from_env_file(name: str) -> str:
    for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def apply_overrides(model: str, timeout: float) -> list[str]:
    """把上游覆盖写进本进程环境；返回实际覆盖项供打印核对。"""
    api_key = _key_from_env_file("RAG4C_EMBEDDING_API_KEY")
    if not api_key:
        raise SystemExit(
            "[baseline] .env 里没有 RAG4C_EMBEDDING_API_KEY，无法切到硅基流动。"
            " 请确认密钥存在，或改用 --provider 指向自有上游。"
        )
    applied = ["RAG4C_LLM_PROVIDERS_SILICONFLOW_API_KEY=***"]
    os.environ["RAG4C_LLM_PROVIDERS_SILICONFLOW_API_KEY"] = api_key
    for slot in _SLOTS:
        os.environ[f"RAG4C_LLM_{slot}_PROVIDER_REF"] = "siliconflow"
        os.environ[f"RAG4C_LLM_{slot}_MODEL"] = model
        os.environ[f"RAG4C_LLM_{slot}_TIMEOUT"] = str(timeout)
        applied.append(f"RAG4C_LLM_{slot}_* -> siliconflow/{model} timeout={timeout:g}")
    for slot in _THINKING_OFF:
        os.environ[f"RAG4C_LLM_{slot}_ENABLE_THINKING"] = "false"
    applied.append("RAG4C_LLM_*_ENABLE_THINKING=false（8 槽位）")
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F1.2 答案层基线运行器")
    parser.add_argument("--model", default="Qwen/Qwen3.5-9B", help="上游模型名")
    parser.add_argument("--timeout", type=float, default=300.0, help="槽位超时秒数")
    parser.add_argument("--workers", type=int, default=2, help="并发线程数")
    parser.add_argument("--sleep-ms", type=float, default=1500.0, help="用例间隔毫秒（429 限流）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0 = 全量）")
    parser.add_argument(
        "--out", default="eval/baseline.json", help="报告输出路径（EvalReport v2）"
    )
    parser.add_argument(
        "--dataset",
        default="eval/answer_eval/gold_answer_production.py:ANSWER_GOLD",
        help="数据集规格（模块:属性）",
    )
    args = parser.parse_args(argv)

    print("[baseline] 施加上游覆盖：")
    for line in apply_overrides(args.model, args.timeout):
        print(f"  - {line}")

    if args.limit:
        # 限量跑通过 --dataset 之外的临时模块不划算（.cache 下不是合法模块名），
        # 改为运行时截断：把 ANSWER_GOLD 切片写进一个可导入的临时模块。
        tmp = PROJECT_ROOT / "eval" / "_baseline_subset.py"
        from eval.answer_eval import gold_answer_production as gold_mod

        subset = gold_mod.ANSWER_GOLD[: args.limit]
        tmp.write_text(
            "from eval.answer_eval.gold_answer_production import ANSWER_GOLD as _ALL\n"
            f"ANSWER_GOLD = _ALL[:{args.limit}]\n",
            encoding="utf-8",
        )
        dataset_spec = "eval._baseline_subset:ANSWER_GOLD"
        print(f"[baseline] 限量 {len(subset)} 条（临时模块 {tmp.name}）")
    else:
        dataset_spec = args.dataset

    from eval.run_eval import as_v2, main as run_eval_main

    argv_eval = [
        "--pipeline",
        "rag:answer_query",
        "--dataset",
        dataset_spec,
        "--out",
        args.out,
        "--workers",
        str(args.workers),
        "--sleep-ms",
        str(args.sleep_ms),
    ]
    try:
        code = run_eval_main(argv_eval)
    finally:
        if args.limit:
            (PROJECT_ROOT / "eval" / "_baseline_subset.py").unlink(missing_ok=True)

    if code == 0:
        # 升成 v2 再存一次：带 schema_version / 数据集规格 / 管线规格，
        # 之后 --baseline 对比与门禁（F1.5）依赖这些元信息。
        from eval.run_eval import EvalReportV2, load_report, save_report

        report = as_v2(
            load_report(args.out),
            dataset_spec=dataset_spec,
            pipeline_spec="rag:answer_query",
        )
        save_report(EvalReportV2.model_validate(report.model_dump()), args.out)
        print(f"[baseline] 已写入 {args.out}（EvalReport v2）")
    return code


if __name__ == "__main__":
    sys.exit(main())
