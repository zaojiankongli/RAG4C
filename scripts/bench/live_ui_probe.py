"""实机 UI 探测：用 Playwright 驱动前端，测**界面真实感受得到的**性能。

和 `load_query.py`（服务端吞吐）、`component_ablation.py`（逐组件开关）分工不同：
这一支只看浏览器里发生的事——首屏、提问到首字、流式结束、以及监控页显示的
阶段耗时是否等于服务端 /api/metrics 的真值。读数不一致就是要报出来，别默认 UI 只是装饰。

用法::

    python scripts/bench/live_ui_probe.py --app-url http://localhost:1420 \\
        --api-base http://127.0.0.1:8011 --questions eval/.cache/bench_questions.json

退出码：0 = 全部页都读到数；1 = 有页面读数不一致或缺元素（读数不可信）；2 = 起不来。
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

QA_HASHES = ("#/query", "#/retrieval-lab", "#/qa", "#/ask")
MONITOR_HASH = "#/monitor"

# 选择器取自前端源码，而不是靠文案猜：
#   QueryPage.tsx      -> textarea.query-input + aria-label="问题输入框"/"发送"
#   StatCard.tsx       -> .stat-card > .stat-label / .stat-value
#   WorkspaceNavigation -> .workspace-navigation__item[aria-label]
# 文案会改，"发送/停止"两态按钮是应用自己的忙闲指示，用它判"这条答完了"比
# 按 DOM 字符数猜结束更真。
INPUT_SELECTOR = "textarea.query-input, textarea[aria-label='问题输入框']"
SEND_SELECTOR = "button.send-btn[aria-label='发送']"
BUSY_SELECTOR = "button.send-btn:has-text('停止')"
NAV_ITEM_SELECTOR = ".workspace-navigation__item"

# 对账的键直接取自前端自己的声明表 `frontend/src/monitor/monitorProjection.ts`
# 的 PROTECTION_DEFS（label -> metrics key），加上 MonitorPage.tsx:695 那张
# "实际计算累计 P95"卡。此前我按 LATENCY_CARDS 里的 label 去找，那些 label 喂的是
# 图（ECharts canvas，DOM 里读不到），不是这批卡片，于是整批读成 "missing"。
UI_VS_API_PAIRS = (
    ("系统选择弃权", "query.abstained", "count"),
    ("QA 权威命中", "query.qa_retrieval.hit", "count"),
    ("QA 无命中", "query.qa_retrieval.no_match", "count"),
    ("QA 目录读取失败", "query.qa_retrieval.catalog_error", "count"),
    ("重排降级", "retrieval.rerank.degraded", "count"),
    ("图编排回退", "graph.fallback", "count"),
    ("跳过无效二轮检索", "query.round2.skipped", "count"),
    ("实际计算累计 P95", "query.total", "p95"),
)


def _api_snapshot(api_base: str) -> dict[str, Any]:
    with urllib.request.urlopen(api_base + "/api/metrics", timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _goto_query(page, app_url: str, route: str) -> None:
    page.goto(app_url + route, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(INPUT_SELECTOR, timeout=20000, state="visible")


def _wait_enabled(page, selector: str, timeout_s: float) -> bool:
    """等元素**可交互**，不是等它出现。

    实测踩过：`wait_for_selector(state="visible")` 拿到 textarea 就 click，而
    TDesign 的 textarea 首帧是 disabled 的，Playwright 按 500ms 重试到超时后抛
    "Element is not attached to the DOM"——报错指向 DOM 挂载，真实原因是没等 enabled。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if page.is_enabled(selector):
                return True
        except Exception:  # noqa: BLE001 - 重渲染瞬间选择器可能落空
            pass
        time.sleep(0.2)
    return False


def _dom_summary(page) -> dict[str, Any]:
    """失败时把界面真实状态写进报告，而不是只留一句"点不动"。"""
    try:
        return json.loads(page.evaluate(
            """() => JSON.stringify({
              url: location.href,
              boxes: Array.from(document.querySelectorAll('textarea')).map((el) => ({
                cls: el.className, aria: el.getAttribute('aria-label'),
                disabled: el.disabled,
                visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
              })),
              send: Array.from(document.querySelectorAll('button.send-btn')).map((el) => ({
                aria: el.getAttribute('aria-label'), disabled: el.disabled,
              })),
            })"""
        ))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _wait_idle(page, timeout_ms: int = 180_000) -> bool:
    """等应用自己宣布"不在生成"：发送按钮回到 ``aria-label='发送'`` 那一态。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            busy = page.query_selector(BUSY_SELECTOR)
            if busy is None or not busy.is_visible():
                return True
        except Exception:  # noqa: BLE001 - 重渲染瞬间元素可能脱离 DOM
            pass
        time.sleep(0.3)
    return False


def probe_question(page, app_url: str, route: str, question: str,
                   *, idle_timeout_s: int = 300) -> dict[str, Any]:
    """在问答页问一次：量「按下提交 → 首个流响应包 → 界面答完」，并核对引用数。

    实测三件事决定了这里怎么写：
    1. 提问期间 vite dev 会整页热重载，JS 执行上下文会被销毁 —— DOM 轮询必须容错，
       首字时间以**网络事件**为准；
    2. 元素"可见"不等于"可点"，输入框与发送按钮要分别等 enabled；
    3. 任一步等不到就把 DOM 摘要写进行读数并返回 error —— 让探针带着证据退出，
       而不是抛栈把整轮报告一起丢掉。
    """
    _goto_query(page, app_url, route)
    # 问答页在上一条还在流式生成时把输入框 disabled 掉（QueryPage 的 disabled={loading}）。
    # 同 hash 再 goto 不会重挂载组件，所以 loading 会带着跨过去：不等上一条结束就发
    # 下一条，读到的就是"输入框始终不可用"，而不是任何前端缺陷。
    waited_previous_ms: float | None = None
    if not _wait_enabled(page, INPUT_SELECTOR, 5):
        busy_started = time.perf_counter()
        if not _wait_idle(page, timeout_ms=idle_timeout_s * 1000):
            return {"question": question, "error": "上一条问题始终没结束，无法开始下一条",
                    "dom": _dom_summary(page)}
        waited_previous_ms = round((time.perf_counter() - busy_started) * 1000, 1)
        if not _wait_enabled(page, INPUT_SELECTOR, 15):
            return {"question": question, "error": "上一条已结束但输入框仍不可用",
                    "dom": _dom_summary(page)}
    try:
        page.fill(INPUT_SELECTOR, question)
    except Exception as exc:  # noqa: BLE001
        return {"question": question, "error": f"填入问题失败：{type(exc).__name__}",
                "dom": _dom_summary(page)}

    send_ready = _wait_enabled(page, SEND_SELECTOR, 8)
    stream_first_byte_ms: float | None = None
    started_holder: list[float] = []

    def on_response(response) -> None:
        nonlocal stream_first_byte_ms
        if "/api/query" in response.url and stream_first_byte_ms is None and started_holder:
            stream_first_byte_ms = (time.perf_counter() - started_holder[0]) * 1000.0

    page.on("response", on_response)
    try:
        if send_ready:
            page.click(SEND_SELECTOR)
            button_selector = SEND_SELECTOR
        else:
            # 回车是应用支持的另一条提交路径（Enter 发送 / Shift+Enter 换行）
            page.press(INPUT_SELECTOR, "Enter")
            button_selector = "keyboard:Enter"
        started_holder.append(time.perf_counter())
        idle = _wait_idle(page, timeout_ms=idle_timeout_s * 1000)
        started = started_holder[0]
        done_ms = (time.perf_counter() - started) * 1000 if idle else None
    except Exception as exc:  # noqa: BLE001
        return {"question": question, "error": f"提交失败：{type(exc).__name__}",
                "dom": _dom_summary(page)}
    finally:
        page.remove_listener("response", on_response)

    try:
        citations = page.evaluate(
            """() => document.querySelectorAll('[data-role="citation"], .citation-item, .citation-card').length"""
        )
        answer_len = page.evaluate(
            """() => { const n = document.querySelector('.chat-inner, main');
                       return n ? (n.innerText || '').trim().length : 0; }"""
        )
    except Exception:  # noqa: BLE001
        citations, answer_len = None, None
    return {
        "question": question,
        "button_selector": button_selector,
        "waited_previous_ms": waited_previous_ms,
        "stream_first_byte_ms": None if stream_first_byte_ms is None else round(stream_first_byte_ms, 1),
        "ui_done_ms": None if done_ms is None else round(done_ms, 1),
        "idle_at_end": idle,
        "idle_timeout_s": None if idle else idle_timeout_s,
        "citations_in_dom": citations,
        "answer_text_len": answer_len,
    }


def monitor_readings(page) -> dict[str, Any]:
    """读监控页上显示的卡片读数，供与服务端 /api/metrics 对账。

    判"读到了"的标准有两个，缺一不可：
    1. 卡片本身存在（`/api/metrics` 要排队时这一页先渲染文字骨架，
       按文字长度提前收工会得到"0 张卡片"这种假读数）；
    2. **读数稳定**——`.stat-card` 刚 attach 时计数器还全是 0，
       这时取数会把 8 项本来对得上的卡片全读成 mismatch（实测踩过）。
       连续两次取到完全一样的卡片才算落定。
    """
    deadline = time.time() + 90
    previous: list[dict[str, Any]] | None = None
    last_text_len = 0
    while time.time() < deadline:
        try:
            page.wait_for_selector(".stat-card", timeout=8000, state="attached")
            cards = json.loads(page.evaluate(
        """() => JSON.stringify(
          Array.from(document.querySelectorAll('.stat-card')).map((el) => ({
            label: (el.querySelector('.stat-label')?.innerText || '').replace(/\\s+/g,' ').trim(),
            value: (el.querySelector('.stat-value')?.innerText || '').replace(/\\s+/g,' ').trim(),
          })).filter((c) => c.label && c.value)
        )"""
            ))
            last_text_len = page.evaluate(
                """() => { const m = document.querySelector('.page-shell') || document.body;
                           return (m.innerText || '').length; }"""
            )
        except Exception:  # noqa: BLE001 - 懒加载 chunk 会换上下文，重试
            page.wait_for_timeout(2000)
            continue
        if cards:
            if previous is not None and previous == cards:
                return {"cards": cards, "text_len": last_text_len}
            previous = cards
        page.wait_for_timeout(2000)
    return {"cards": previous or [], "text_len": last_text_len,
            "error": "等 90 秒后监控页读数仍未稳定，对账不可信"}


def reconcile_with_api(cards: list[dict[str, Any]], metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """界面卡片数字 vs **同一时刻**的服务端指标。差得离谱就是要报的缺陷。

    服务端计数器每问一次就在动，DOM 读数之后过几秒再取 `/api/metrics` 会把
    "读数不同源"当成"界面读错了"——本轮我就是这么把 8 张对得上的卡片全读成 missing 的。
    """
    by_label = {card["label"]: card["value"] for card in cards}
    out = []
    for label, metric_key, field in UI_VS_API_PAIRS:
        shown = by_label.get(label)
        stat = (metrics or {}).get(metric_key)
        stat = stat if isinstance(stat, dict) else {}
        api_value = stat.get(field)
        if api_value is None:
            api_value = (stat.get("count") if field != "count" else None)
            api_value = 0.0 if api_value is None else api_value
        if shown is None:
            out.append({"label": label, "metric": f"{metric_key}.{field}",
                        "ui": None, "api": round(float(api_value), 1), "status": "card-missing"})
            continue
        digits = "".join(ch for ch in shown if ch.isdigit() or ch == ".")
        # 界面按 zh-CN 分组（"142,757.6 ms"），千位分隔符不是小数点
        ui_value = float(digits.replace(",", "")) if digits else None
        out.append({
            "label": label, "metric": f"{metric_key}.{field}", "ui": ui_value,
            "api": round(float(api_value), 1),
            "status": "ok" if ui_value is not None and abs(ui_value - float(api_value)) <= 0.5
            else "mismatch",
        })
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", default="http://localhost:1420")
    parser.add_argument("--api-base", default="http://127.0.0.1:8011")
    parser.add_argument("--questions", required=True)
    parser.add_argument("--out", default="output/bench/ui_probe.json")
    parser.add_argument("--shots", default="output/bench/shots")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--idle-timeout-s", type=int, default=300,
                        help="单条问题在界面上等结束的上限秒数；实测问答整答约 155~230 秒")
    parser.add_argument("--monitor-only", action="store_true",
                        help='只跑监控页对账（验"界面↔服务端"这段逻辑时不必再等一条几分钟的问答）')
    args = parser.parse_args(argv)

    items = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    questions = [item["query"] for item in items][:5]
    if not questions:
        print("[error] 问题集为空", file=sys.stderr)
        return 2

    # 前端不在跑时，页面会半渲染：延迟读数看着像"很慢"，其实是应用在报错。
    # 先探一次可达性，把结论钉成"起不来"，别让它冒充性能数据。
    try:
        code = urllib.request.urlopen(args.app_url, timeout=10).status  # noqa: S310
    except Exception as exc:  # noqa: BLE001
        print(f"[error] 前端 {args.app_url} 不可达（{type(exc).__name__}），"
              "先起 vite dev 再测", file=sys.stderr)
        return 2
    if code >= 400:
        print(f"[error] 前端 {args.app_url} 返回 HTTP {code}", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[error] 当前解释器没有 playwright，用 .venv 或带 playwright 的环境跑", file=sys.stderr)
        return 2

    Path(args.shots).mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"app_url": args.app_url, "api_base": args.api_base,
                              "console_errors": [], "qa": [], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    failures: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(locale="zh-CN")
        context.add_init_script(
            f"""
            try {{
              localStorage.setItem('rag4c.base_url', '{args.api_base}');
              // 首次进入会弹「新手引导」模态层，它会拦截所有点击（实测探针被它
              // 卡住 55 次重试）。测量按"回头用户"口径预先标记已看过。
              localStorage.setItem('rag4c.onboarding.v1', '1');
              localStorage.setItem('rag4c.onboarding_done', '1');
            }} catch (e) {{}}
            """
        )
        page = context.new_page()
        page.on("console", lambda msg: report["console_errors"].append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: report["console_errors"].append(str(exc)[:200]))

        nav_start = time.perf_counter()
        page.goto(args.app_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        report["shell_load_ms"] = round((time.perf_counter() - nav_start) * 1000, 1)
        report["nav_timing"] = page.evaluate(
            """() => { const n = performance.getEntriesByType('navigation')[0] || {};
                       return {domContentLoaded: Math.round(n.domContentLoadedEventEnd || 0),
                               load: Math.round(n.loadEventEnd || 0)}; }"""
        )
        report["nav_labels"] = page.evaluate(
            f"""() => Array.from(document.querySelectorAll('{NAV_ITEM_SELECTOR}'))
                     .map((el) => (el.getAttribute('aria-label') || el.innerText || '')
                           .replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0, 40)"""
        )
        # 导航是分组可折叠的：只数展开那一组会得到"3 项"这种读数，它不是产品缺陷，
        # 是探针口径。同时记下 DOM 里的总项数，两者一比就知道是折叠还是真少。
        report["nav_item_count_in_dom"] = page.evaluate(
            f"""() => document.querySelectorAll('{NAV_ITEM_SELECTOR}').length"""
        )
        page.screenshot(path=str(Path(args.shots) / "01_shell.png"), full_page=True)

        landed = ""
        for candidate in () if args.monitor_only else QA_HASHES:
            page.goto(args.app_url + candidate, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector(INPUT_SELECTOR, timeout=8000, state="visible")
            except Exception:  # noqa: BLE001 - 这一条路由没有问答输入框，换下一条
                continue
            landed = candidate
            break
        if not landed and not args.monitor_only:
            failures.append(f"问答页没找到输入框，试过 {list(QA_HASHES)}")
        else:
            report["qa_route"] = landed
            for question in questions:
                outcome = probe_question(page, args.app_url, landed, question,
                                         idle_timeout_s=args.idle_timeout_s)
                outcome["route"] = landed
                report["qa"].append(outcome)
                if not outcome.get("idle_at_end"):
                    failures.append(f"这条问题在界面上没有结束信号：{question[:24]}")
                elif outcome.get("citations_in_dom", 0) == 0:
                    failures.append(f"这条问题在界面上没有引用：{question[:24]}")
            page.screenshot(path=str(Path(args.shots) / "02_qa.png"), full_page=True)

        page.goto(args.app_url + MONITOR_HASH, wait_until="domcontentloaded", timeout=30000)
        report["monitor"] = monitor_readings(page)
        # 紧挨着 DOM 读数取快照：晚一取，计数器就已经被这几秒里的别的请求推进过了。
        report["api_at_monitor_read"] = (_api_snapshot(args.api_base).get("metrics") or {})
        page.screenshot(path=str(Path(args.shots) / "03_monitor.png"), full_page=True)
        browser.close()

    server = _api_snapshot(args.api_base)
    report["api_metrics_keys"] = sorted(server.keys())
    stats = server.get("metrics") or {}
    report["api_qa_counters"] = {
        key: value.get("count")
        for key, value in stats.items()
        if key.startswith("query.qa_retrieval") and isinstance(value, dict)
    }
    report["api_stage_p50_ms"] = {k: round(v, 1) for k, v in _stage_percentiles(stats).items()}
    report["ui_vs_api"] = reconcile_with_api(
        report["monitor"]["cards"], report.get("api_at_monitor_read") or stats
    )

    firsts = [row["stream_first_byte_ms"] for row in report["qa"] if row.get("stream_first_byte_ms")]
    totals = [row["ui_done_ms"] for row in report["qa"] if row.get("ui_done_ms")]
    report["summary"] = {
        "questions": len(report["qa"]),
        "stream_first_byte_p50_ms": round(_percentile(firsts, 0.5), 1) if firsts else None,
        "stream_first_byte_p95_ms": round(_percentile(firsts, 0.95), 1) if firsts else None,
        "ui_done_p50_ms": round(_percentile(totals, 0.5), 1) if totals else None,
        "console_error_count": len(report["console_errors"]),
    }
    if not report["monitor"]["cards"] and report["monitor"]["text_len"] < 400:
        failures.append("监控页没读到任何卡片，界面读数无法与服务端对账")
    # 控制台报错单独判红：本轮 363 条 React "two children with the same key" 就是在
    # 读数全部正常的前提下刷出来的——只核对数字会把真缺陷放过去。
    if report["console_errors"]:
        distinct = collections.Counter(e.split("\n")[0][:160] for e in report["console_errors"])
        top = "; ".join(f"{n}x {k}" for k, n in distinct.most_common(3))
        failures.append(f"浏览器控制台有 {len(report['console_errors'])} 条错误（去重后 {len(distinct)} 类）：{top}")
    for item in report["ui_vs_api"]:
        if item["status"] != "ok":
            failures.append(
                f"界面卡片「{item['label']}」读数({item['ui']}) 与服务端 "
                f"{item['metric']}({item['api']}) 不一致：{item['status']}"
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"外壳加载 {report['shell_load_ms']}ms，导航条 {len(report['nav_labels'])} 项: "
          f"{report['nav_labels'][:8]}")
    print(f"流首包 p50={report['summary']['stream_first_byte_p50_ms']} "
          f"p95={report['summary']['stream_first_byte_p95_ms']}"
          f" | 界面答完 p50={report['summary']['ui_done_p50_ms']}")
    print(f"监控页卡片 {len(report['monitor']['cards'])} 张，服务端阶段 p50: "
          + ", ".join(f"{k}={v}" for k, v in list(report['api_stage_p50_ms'].items())[:6]))
    print("界面/服务端对账: " + ", ".join(
        f"{i['metric']}={i['status']}(UI {i['ui']} vs API {i['api']})" for i in report["ui_vs_api"]))
    print(f"QA 目录计数器: {report['api_qa_counters']}")
    if report["console_errors"]:
        print(f"控制台错误 {len(report['console_errors'])} 条，示例: {report['console_errors'][:2]}")
    print(f"报告: {out_path}")
    for item in failures:
        print(f"[读数不可信] {item}", file=sys.stderr)
    return 1 if failures else 0


def _stage_percentiles(metrics: dict[str, Any]) -> dict[str, float]:
    """把 /api/metrics 的 ``metrics`` 段摊平成 ``指标名 -> p50``。

    快照顶层是 ``{ts, metrics, degraded, cache, ...}``，只有 ``metrics`` 段里
    每个键才挂着 ``{count, p50, p95, ...}``；把它当嵌套两层扫会得到
    ``metrics.query.total`` 这种假前缀，对账时找不到 ``query.total``。
    """
    return {
        name: float(stat["p50"])
        for name, stat in (metrics or {}).items()
        if isinstance(stat, dict) and isinstance(stat.get("p50"), (int, float))
    }


if __name__ == "__main__":
    raise SystemExit(main())
