"""列出各端点上真实可用的模型（OpenAI 兼容 /v1/models）。

为什么需要这个：配 11 个 LLM 槽位时，模型 ID 必须**一字不差**。
凭印象写（"qwen3-max"、"deepseek-v4"）大概率拼错，而拼错的表现往往是
一句含糊的 404 或 "model not found"，看不出是名字错了还是没权限。
直接问端点要一份列表，是唯一可靠的做法。

**用 openai SDK 而不是自己发 HTTP**：一来 openai 包本来就是项目依赖
（嵌入 / LLM 都走它），零新增依赖；二来鉴权、重试、base_url 拼接的细节
和生产路径完全一致，不会出现"脚本能列出来但项目连不上"的假象。
（本仓库环境里装的是 httpx2 而非 httpx，直接 import httpx 会失败。）

默认遍历**所有配置到的端点**并去重——项目里嵌入、重排、11 个 LLM 槽位
可以各指各的服务，只查一个很容易看漏。

用法::

    .venv\\Scripts\\python.exe scripts\\list_models.py
    .venv\\Scripts\\python.exe scripts\\list_models.py --grep qwen
    .venv\\Scripts\\python.exe scripts\\list_models.py --endpoint embedding

注意：``/v1/models`` 只回答"有哪些模型"，**不回答价格和限流**。
计费请以服务商定价页为准（硅基流动：https://siliconflow.cn/pricing）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402

_LLM_SLOTS = (
    "rewrite", "router_llm", "hyde", "subqueries", "stepback",
    "generation", "judge", "triplet", "contextual", "classifier",
)


def _collect_endpoints(only: str) -> dict[tuple[str, str], dict[str, str]]:
    """收集 (base_url, api_key) -> {用途名: 当前配置模型}。

    按 (base_url, api_key) 去重：同一个服务只查一次，但把所有指向它的
    用途都列出来，这样一眼能看出"哪些槽位共用了同一个端点"。
    """
    s = get_settings()
    out: dict[tuple[str, str], dict[str, str]] = {}

    def _add(key: str, base_url: str, api_key: str, model: str) -> None:
        out.setdefault((base_url, api_key), {})[key] = model

    if only in ("", "embedding"):
        c = s.embedding
        _add("embedding", c.api_base_url, c.api_key, c.api_model)
    if only in ("", "reranker"):
        c = s.reranker
        _add("reranker", c.api_base_url, c.api_key, c.api_model)
    for name in _LLM_SLOTS:
        if only not in ("", name):
            continue
        slot = getattr(s.llm, name, None)
        if slot is not None:
            _add(f"llm.{name}", slot.base_url, slot.api_key, slot.model)
    return out


def _mask(secret: str) -> str:
    if not secret:
        return "(空)"
    if len(secret) <= 10:
        return secret[:2] + "***"
    return f"{secret[:6]}...{secret[-4:]}"


def _list_one(base_url: str, api_key: str, grep: str) -> tuple[bool, list[str]]:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=30.0)
    page = client.models.list()
    ids = sorted(str(getattr(m, "id", "") or "") for m in page)
    ids = [i for i in ids if i]
    if grep:
        needle = grep.lower()
        ids = [i for i in ids if needle in i.lower()]
    return True, ids


def main() -> int:
    parser = argparse.ArgumentParser(description="列出各端点上可用的模型")
    parser.add_argument(
        "--endpoint", default="",
        help="只查某个用途：embedding / reranker / 某个 LLM 槽位名。缺省查全部",
    )
    parser.add_argument("--grep", default="", help="只显示 ID 含该子串的模型（忽略大小写）")
    args = parser.parse_args()

    endpoints = _collect_endpoints(args.endpoint)
    if not endpoints:
        print(f"[FAIL] 未知用途 {args.endpoint!r}")
        print(f"       可选：embedding / reranker / {' / '.join(_LLM_SLOTS)}")
        return 1

    failed = 0
    for (base_url, api_key), uses in endpoints.items():
        print("=" * 68)
        print(f"端点 {base_url}")
        print(f"密钥 {_mask(api_key)}")
        # 把"谁在用这个端点、各自配的什么模型"摊开，便于核对
        by_model: dict[str, list[str]] = {}
        for use, model in sorted(uses.items()):
            by_model.setdefault(model, []).append(use)
        for model, names in sorted(by_model.items()):
            print(f"  当前配置 {model:<28} <- {', '.join(names)}")
        print("-" * 68)

        try:
            _, ids = _list_one(base_url, api_key, args.grep)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 拉取失败：{type(exc).__name__}: {exc}")
            print("       - 401/403 -> key 无效或无权限")
            print("       - 404     -> base_url 结尾是否漏了 /v1")
            print("       - 连接错误 -> 服务未启动或地址不通")
            print("       （部分本地服务不实现 /v1/models，属正常）")
            failed += 1
            print()
            continue

        if not ids:
            print("（没有匹配的模型）")
        else:
            configured = set(uses.values())
            print(f"可用 {len(ids)} 个：")
            for model_id in ids:
                marker = "  <- 当前在用" if model_id in configured else ""
                print(f"  {model_id}{marker}")
            # 配了但列表里没有的，几乎肯定是拼错或没权限，单独点出来
            missing = sorted(configured - set(ids))
            if missing and not args.grep:
                print()
                for m in missing:
                    print(f"  [注意] 配置里的 {m!r} 不在该端点的模型列表中")
                    print("         -> 检查拼写，或确认账号是否有该模型权限")
        print()

    print("=" * 68)
    print("提示：/v1/models 只说明「有哪些」，不含价格与限流。")
    print("      计费与免费额度以服务商定价页为准。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
