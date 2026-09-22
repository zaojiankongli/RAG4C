"""跨源可读响应头栅栏：前端 `headers.get(...)` 读到的，必须要么在 CORS 白名单里，要么被显式 expose。

起因是我自己修的一个 blocker（原文查看读 `Content-Disposition` 恒为空串），然后第十一轮评审
指出我**漏了同一类里的另一个**：`Retry-After` 在 `server/run_ops.py:933` 发出、在
`frontend/src/api/transport.ts:115/179` 读，却同样不在 `expose_headers` 里。一次修一个头就是
在赌"没人再读第三个"。这条栅栏把赌局换成断言：以后任何前端新读一个非白名单响应头，第一天就红，
而不是安静地拿到空串再花一轮去猜。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "server/app.py"
FRONTEND = REPO / "frontend/src"

# fetch 规范里"简单响应头"：不带 expose 也永远可读。
# `content-length` 也在其中（第十二轮指出我漏了它）—— 少一条就是将来某个前端读
# Content-Length 时被这条栅栏假红一次。
CORS_SIMPLE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-length",
        "content-language",
        "content-type",
        "expires",
        "last-modified",
        "pragma",
    }
)

_READ_RE = re.compile(r"""headers\.get\(\s*["']([A-Za-z0-9-]+)["']""")


def _exposed_headers() -> frozenset[str]:
    source = APP.read_text(encoding="utf-8")
    match = re.search(r"expose_headers=\[([^\]]*)\]", source)
    assert match, "server/app.py 里找不到 expose_headers —— CORS 面变了，栅栏得跟着搬，不能算通过"
    return frozenset(part.strip().strip("\"'") for part in match.group(1).split(",") if part.strip())


def _read_headers() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(FRONTEND.rglob("*.ts")) + sorted(FRONTEND.rglob("*.tsx")):
        if "node_modules" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO).as_posix()
        for offset, line in enumerate(source.splitlines(), start=1):
            for name in _READ_RE.findall(line):
                found.setdefault(name.lower(), []).append(f"{relative}:{offset}")
    return found


def test_every_header_the_frontend_reads_is_readable_cross_origin() -> None:
    exposed = {name.lower() for name in _exposed_headers()}
    blind = sorted(
        name
        for name in _read_headers()
        if name not in exposed and name not in CORS_SIMPLE_RESPONSE_HEADERS
    )
    assert not blind, (
        f"前端读了但跨源读不到：{blind} —— 浏览器会给出空串而不是报错，"
        "所以症状是界面安静地丢掉信号"
    )


def test_retry_after_is_exposed() -> None:
    """第十一轮评审查出的那一条，单独立一条，免得它被上面的通用断言稀释掉。"""
    assert "retry-after" in {name.lower() for name in _exposed_headers()}
    assert "retry-after" in _read_headers(), "前端已经不读它了 —— 那就把 expose 一并撤掉"


def test_content_disposition_and_etag_stay_exposed() -> None:
    exposed = {name.lower() for name in _exposed_headers()}
    assert {"content-disposition", "etag"} <= exposed


def test_the_fence_itself_overs_the_right_surface() -> None:
    """栅栏的自查：它得真的抓到过这两个已知头，否则"没红"不代表"没瞎"。"""
    reads = _read_headers()
    assert {"content-disposition", "retry-after"} <= set(reads), reads.keys()
