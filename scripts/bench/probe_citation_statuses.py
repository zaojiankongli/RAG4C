"""Ask the live API once and dump the per-citation status histogram.

Why: the monitor card says 100% citation failure. `verify.citations.failed` counts every
status that is not ``ok`` — including ``exists_only``, which the schema defines as
"evidence exists but only partially/weakly supports". This tells the two apart with real data.

Usage::

    python scripts/bench/probe_citation_statuses.py --api-base http://127.0.0.1:8012
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# 探针只打自己拉起的本机服务：仅接受 http + 数字环回地址（不解析 DNS，即无
# rebinding 面），且禁止重定向。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect not allowed", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _assert_loopback_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in _LOOPBACK_HOSTS:
        raise ValueError("probe_citation_statuses only talks to the loopback api it measures")



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8012")
    parser.add_argument("--questions", default="eval/.cache/bench_questions.json")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--out", default="output/bench/citation_statuses.json")
    args = parser.parse_args()

    items = json.loads((REPO_ROOT / args.questions).read_text(encoding="utf-8"))[: args.limit]
    report = {"api_base": args.api_base, "rows": []}
    for item in items:
        payload = {
            "query": item["query"],
            "dataset_id": item.get("dataset_id"),
            "retry": True,
        }
        request = urllib.request.Request(
            args.api_base + "/api/query",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        _assert_loopback_url(args.api_base)
        try:
            with _OPENER.open(request, timeout=420) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            report["rows"].append({"query": item["query"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        result = body.get("result") or body
        citations = result.get("citations") or []
        statuses = collections.Counter(str(c.get("status")) for c in citations)
        verdict = result.get("verdict") or {}
        # 按 id 前缀分桶：QA 权威证据的 id 是 `qa::…`，它不在 Milvus 投影里，
        # 曾被子进程按"投影查不到"判成 stale。分开看才知道修没修对。
        by_kind = collections.Counter(
            f"{c.get('status')}/{str(c.get('chunk_id') or '').split('::')[0] or 'chunk'}"
            for c in citations
        )
        report["rows"].append({
            "query": item["query"],
            "abstained": result.get("abstained"),
            "citation_count": len(citations),
            "status_histogram": dict(statuses),
            "status_by_id_kind": dict(by_kind),
            "supported": verdict.get("supported"),
            "entailment_evaluated": verdict.get("entailment_evaluated"),
            "non_ok_samples": [
                {k: c.get(k) for k in ("status", "chunk_id", "reason", "claim")}
                for c in citations if c.get("status") != "ok"
            ][:6],
            "traces_with_l3": [t for t in (result.get("traces") or []) if "L3" in t or "抽样" in t][:4],
        })
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in report["rows"]:
        print(row.get("query", "?"), "->", json.dumps(
            {k: row.get(k) for k in ("status_histogram", "supported", "citation_count")},
            ensure_ascii=False))
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
