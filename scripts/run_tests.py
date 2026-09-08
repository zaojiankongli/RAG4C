"""RAG4C 统一测试运行器。

作用：先执行 tests/ 下的 pytest 用例，再自动发现 scripts/ 目录下全部
smoke_*.py 冒烟脚本串行逐个执行，汇总每项的 PASS/FAIL/SKIP 结果与耗时，
输出报告并以退出码指示整体结果。

为什么要报 SKIP：冒烟脚本在依赖缺失时（Redis 未装 / 未启动、langgraph 未装）
会打印 [SKIP] 并以 0 退出。只报「通过 N/N」会把这些跳过掩盖成绿色——而被跳过
的恰恰是 L2 缓存、跨进程锁、Streams 准入队列这些分布式并发断言。因此汇总行
必须同时给出 passed / failed / skipped 三个数，且提供 --strict 让 CI 把
跳过判为失败。

运行：
    python scripts/run_tests.py                # 全量执行（pytest + smoke）
    python scripts/run_tests.py --filter 检索    # 只跑名字包含「检索」的脚本
    python scripts/run_tests.py --list         # 只列出发现的脚本，不执行
    python scripts/run_tests.py --strict       # 有跳过即判失败（CI 用）
    python scripts/run_tests.py --no-pytest    # 只跑冒烟脚本，跳过 pytest

退出码：0 = 全部通过；1 = 任一脚本失败（--strict 下含跳过）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_redis import ENV_VAR, TEST_PREFIX_ROOT, sweep  # noqa: E402

# 每个脚本的独立超时（秒）
TIMEOUT = 600
# 失败时回显子进程输出的最大行数。给得比"够看清最后一条断言"更宽：脚本
# 分组打印，10 行经常只够看到收尾统计，看不到真正 [FAIL] 的那一行，于是
# 每次失败都要手动重跑一遍才知道错在哪。
TAIL_LINES = 40

#: 整轮测试统一的 Redis 键前缀。放在运行器这一层是为了让**将来新增的**冒烟
#: 脚本自动获得隔离——靠每个脚本记得自己调 isolate_redis_keyspace()，迟早会
#: 有一个忘了，而忘掉的代价是往共用实例的生产命名空间里写测试数据。
RUN_KEY_PREFIX = f"{TEST_PREFIX_ROOT}run:"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _force_utf8_stdio() -> None:
    """Windows 下统一 stdout/stderr 为 UTF-8，避免 GBK 解码报错。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


#: 冒烟脚本里显式跳过的标记，以及收尾统计里的「N skipped」。两种写法都要认：
#: 前者是逐条断言级的跳过，后者是脚本自己汇总的。取两者较大值作为下界估计
#: ——宁可少报也不能漏报为 0，漏报会让「绿色」重新变得不可信。
_SKIP_MARKER = "[SKIP]"
_SKIP_SUMMARY_RE = re.compile(r"(\d+)\s*skipped")


def _count_skips(output: str) -> int:
    """从脚本输出里估算跳过的断言数（取标记计数与汇总数字的较大值）。"""
    marker_count = output.count(_SKIP_MARKER)
    summary_max = max(
        (int(m.group(1)) for m in _SKIP_SUMMARY_RE.finditer(output)),
        default=0,
    )
    return max(marker_count, summary_max)


def discover_scripts() -> list[Path]:
    """发现 scripts/ 下全部 smoke_*.py 脚本，按名字排序。"""
    return sorted(SCRIPTS_DIR.glob("smoke_*.py"))


def run_pytest() -> tuple[bool, float, str, int]:
    """执行 tests/ 下的 pytest 用例。

    历史上这个运行器完全不跑 pytest，导致 tests/test_engineering_policies.py
    的用例从未在门禁中执行过。这里把它接回来。

    返回 (是否通过, 耗时秒, 失败详情, 跳过数)。tests/ 不存在时视为通过。
    """
    tests_dir = PROJECT_ROOT / "tests"
    if not tests_dir.is_dir():
        return True, 0.0, "", 0
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                ENV_VAR: RUN_KEY_PREFIX,
            },
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, time.monotonic() - start, f"超时（>{TIMEOUT}s）", 0
    except FileNotFoundError:
        return False, time.monotonic() - start, "pytest 未安装", 0
    elapsed = time.monotonic() - start
    output = (result.stdout or "") + (result.stderr or "")
    skips = _count_skips(output)
    if result.returncode == 0:
        return True, elapsed, "", skips
    tail = output.rstrip().splitlines()[-TAIL_LINES:]
    detail = "\n".join(line for line in tail if line.strip()) or "(无输出)"
    return False, elapsed, detail, skips


def run_one(script: Path) -> tuple[bool, float, str, int]:
    """串行执行单个冒烟脚本。

    返回 (是否通过, 耗时秒, 失败详情, 跳过数)。失败详情仅取 stdout/stderr
    末尾 TAIL_LINES 行，避免刷屏。
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                ENV_VAR: RUN_KEY_PREFIX,
            },
            timeout=TIMEOUT,
        )
        elapsed = time.monotonic() - start
        skips = _count_skips((result.stdout or "") + (result.stderr or ""))
        if result.returncode == 0:
            return True, elapsed, "", skips
        tail = (result.stdout or "").rstrip().splitlines()[-TAIL_LINES:]
        tail += (result.stderr or "").rstrip().splitlines()[-TAIL_LINES:]
        detail = "\n".join(line for line in tail if line.strip())
        if not detail:
            detail = "(无输出)"
        return False, elapsed, detail, skips
    except subprocess.TimeoutExpired:
        return False, time.monotonic() - start, f"超时（>{TIMEOUT}s）", 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG4C 统一测试运行器")
    parser.add_argument("--filter", default="", help="只运行文件名包含该子串的脚本")
    parser.add_argument("--list", action="store_true", help="只列出发现的脚本，不执行")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="有跳过即判失败（CI 用）。缺依赖导致的静默跳过不再算绿。",
    )
    parser.add_argument(
        "--no-pytest", action="store_true", help="跳过 pytest，只跑冒烟脚本"
    )
    args = parser.parse_args()

    _force_utf8_stdio()

    scripts = [s for s in discover_scripts() if args.filter in s.name]

    if args.list:
        for s in scripts:
            print(s.name)
        return 0

    if not scripts and args.no_pytest:
        print(f"未发现匹配的 smoke 脚本（filter={args.filter!r}）")
        return 0

    total_start = time.monotonic()
    passed = 0
    failed = 0
    skipped = 0

    # ---- pytest：历史上这一段完全缺失，tests/ 从未在门禁中跑过 ----
    if not args.no_pytest and not args.filter:
        print("[RUN] pytest tests/")
        ok, elapsed, detail, skips = run_pytest()
        skipped += skips
        if ok:
            passed += 1
            suffix = f"，跳过 {skips}" if skips else ""
            print(f"[PASS] pytest tests/ ({elapsed:.2f}s{suffix})")
        else:
            failed += 1
            print(f"[FAIL] pytest tests/ ({elapsed:.2f}s)")
            print(f"  -> {detail}")
        print("----------")

    for script in scripts:
        print(f"[RUN] {script.name}")
        ok, elapsed, detail, skips = run_one(script)
        skipped += skips
        if ok:
            passed += 1
            suffix = f"，跳过 {skips}" if skips else ""
            print(f"[PASS] {script.name} ({elapsed:.2f}s{suffix})")
        else:
            failed += 1
            print(f"[FAIL] {script.name} ({elapsed:.2f}s)")
            print(f"  -> {detail}")
        print("----------")

    total_elapsed = time.monotonic() - total_start
    total = passed + failed
    print(
        f"通过 {passed}/{total}，失败 {failed}/{total}，"
        f"跳过断言 {skipped}，总耗时 {total_elapsed:.1f}s"
    )
    if skipped:
        # 跳过不能只在明细里一闪而过：整轮下来最后只看汇总行的人，必须在这里
        # 就知道「绿」是打了折的绿，以及打折的是哪一类断言。
        print(
            f"注意：本轮有 {skipped} 条断言因依赖缺失被跳过"
            "（Redis / langgraph 等），这些路径本轮未获得任何覆盖。"
        )
        if args.strict:
            print("--strict 已开启：存在跳过，判为失败。")

    # 子进程都是被 kill / 超时也可能跳过 atexit 的，所以清理由运行器兜底做一次。
    # 只扫本轮自己的前缀；键本身都带 TTL，扫不掉也会自灭，因此清理失败不判负。
    removed = sweep(RUN_KEY_PREFIX)
    if removed > 0:
        print(f"已清理本轮测试遗留的 Redis 键 {removed} 条（前缀 {RUN_KEY_PREFIX}）")
    elif removed < 0:
        # 不能沉默：这不是"干净"，是"没看成"。运行器所在进程没配 Redis 时，
        # 子进程若从别处拿到了连接，残留就会留在共用实例上没人知道。
        print(f"警告：无法连接 Redis，未能核对测试键残留（前缀 {RUN_KEY_PREFIX}）")

    if failed:
        return 1
    if args.strict and skipped:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
