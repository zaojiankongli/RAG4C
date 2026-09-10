"""用 GLM-5.3-flash（dsh 视觉端点）评估页面截图美观度。

用法：python scripts/visual/assess_pages.py [--dir output/visual]
输出：每页美观度评分（1-10）+ 问题描述 + 改进建议（JSON 落盘）。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from openai import OpenAI  # noqa: E402

BASE_URL = "https://ai.seeway.co/v1"
API_KEY = "sk-bd74819323d2df9b1ba3ea1af7fb4c352fe33689d1d23f8f88fb32bea1949a06"
MODEL = "glm-5.3-flash"

PROMPT = (
    "你是 UI/UX 评审员。这是一张 RAG 知识库管理系统的页面截图（二次元动漫主题，"
    "粉橙色调）。请评估美观度并输出严格 JSON："
    '{"score": 1-10 整数, "strengths": [优点数组], '
    '"issues": [问题数组，如配色冲突/布局杂乱/间距不当/元素重叠/空态难看], '
    '"suggestions": [具体改进建议数组]}'
    "只输出 JSON，不要其他文字。"
)


def assess(client: OpenAI, image_path: Path, retries: int = 3) -> dict:
    """评估单张截图；dsh 端点偶发 504，失败重试（间隔退避）。"""
    import time

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    last_error = ""
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "你只输出 JSON。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    },
                ],
                max_tokens=800,
                temperature=0.2,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # 剥 markdown 围栏
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {
                    "score": 0,
                    "issues": [f"JSON 解析失败: {raw[:200]}"],
                    "suggestions": [],
                    "strengths": [],
                }
        except Exception as exc:  # noqa: BLE001 - 端点 504/限流，重试
            last_error = str(exc)[:120]
            time.sleep(3 * (attempt + 1))
    return {"score": 0, "issues": [f"评估失败（重试 {retries} 次）: {last_error}"], "suggestions": [], "strengths": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="output/visual")
    parser.add_argument("--out", default="output/visual/assessment.json")
    parser.add_argument("--start", type=int, default=0, help="起始截图索引（分批用）")
    parser.add_argument("--end", type=int, default=999, help="结束截图索引（不含）")
    parser.add_argument("--gap", type=float, default=3.0, help="每张评估间隔秒（防端点 504）")
    parser.add_argument("--names", default="", help="只评估指定页（逗号分隔，如 01-query,08-eval）")
    args = parser.parse_args()

    images = sorted(Path(args.dir).glob("*.png"))[args.start : args.end]
    if args.names:
        wanted = {n.strip() for n in args.names.split(",") if n.strip()}
        images = [img for img in images if img.stem in wanted]
    if not images:
        print("无截图文件")
        return 1

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=120)
    results: dict[str, dict] = {}
    for img in images:
        print(f"评估 {img.name} ...", flush=True)
        results[img.stem] = assess(client, img)
        if args.gap > 0 and img is not images[-1]:
            time.sleep(args.gap)

    out = Path(args.out)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 美观度评估 ===")
    for name, r in sorted(results.items()):
        score = r.get("score", 0)
        issues = r.get("issues", [])
        print(f"  {name}: {score}/10")
        for issue in issues[:3]:
            print(f"    - {issue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
