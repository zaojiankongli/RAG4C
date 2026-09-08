"""导出 RAG4C 桥服务的 OpenAPI schema，作为前端类型的单一事实源。

作用：
- 加载 server.app 并输出 openapi.json（FastAPI 自动从所有 response_model / 请求体模型生成）。
- ``--check`` 模式：schema 与已有文件不一致时以非零退出码退出（供 CI 契约门禁使用）。

运行::

    python scripts/export_openapi.py                # 覆写 frontend/src/types/generated/openapi.json
    python scripts/export_openapi.py --check       # 校验一致（CI 用）

设计说明：
- 输出目录与 ``frontend/src/types/generated/`` 对齐，生成的 .ts 由前端工具链
  （openapi-typescript）消费；本脚本只负责产出事实源 JSON。
- 不触碰 .env：桥服务 app 的装配只读配置，不应被本脚本改写。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

_OUTPUT = (
    _PROJECT_ROOT
    / "frontend"
    / "src"
    / "types"
    / "generated"
    / "openapi.json"
)


def build_schema() -> dict:
    """导入 server.app 并返回其 OpenAPI schema（dict）。"""
    from server.app import app  # noqa: F401  -- 导入即注册全部路由

    return app.openapi()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="与已有文件比较；不一致时退出码 1（CI 契约门禁）",
    )
    args = parser.parse_args()

    schema = build_schema()

    if args.check:
        if not _OUTPUT.exists():
            print(f"[contract] 缺少 {_OUTPUT.relative_to(_PROJECT_ROOT)}，请先运行导出")
            return 1
        existing = json.loads(_OUTPUT.read_text(encoding="utf-8"))
        if existing != schema:
            print("[contract] OpenAPI schema 与生成文件不一致（后端契约已漂移）")
            return 1
        print(f"[contract] OpenAPI 一致（{len(schema.get('paths', {}))} paths, "
              f"{len(schema.get('components', {}).get('schemas', {}))} schemas）")
        return 0

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[contract] OpenAPI 已导出: {_OUTPUT.relative_to(_PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
