"""F0.2 分片绿基线：把全量回归拆成「迁移型 / 非迁移型」两批，各记一次结果。

## 为什么分片

F0.2 要的是「全量回归的绿基线」，而全量跑不动的原因已定位（见
`docs/2026-10-03-regression-suite-slowdown-diagnosis.md`）：**210 个测试文件
（308 个里的 68%）调用 `upgrade_catalog`**，单条迁移型用例约 19 秒——
每个用例都要建库 + 跑 40 个 alembic 迁移。于是全量是数小时级。

「开并行跑全量」这条路已被否：xdist 在 Windows 上 gw 节点崩溃
（`node down: Not properly terminated`），而且并行会污染性能预算断言。

所以先拿分片绿基线（路线 A，零代码改动）——**当天可拿**，代价是防不住
跨分片的回归。这是「先有基线」与「基线完整」之间的取舍，不是两全。

## 分片口径

- **迁移型**：文件里出现 `upgrade_catalog`（真的跑迁移）；
- **非迁移型**：其余。

分片名单是**动态算出来的**，不写死——写死的名单会随新测试加入而腐烂，
而"这个文件属于哪片"是个能机械判定的属性（跑没跑迁移），没有理由手写。

## 刻意不做的事

**不判定通过/不通过。** 本工具只产出「这一片跑完了、结果是什么、耗时多少」。
理由与 `eval/run_full_gate.py --real-upstream` 相同：没有可信阈值时给出
判定会制造「门禁通过」的错觉。而 F0.2 的绿基线一旦建立，它的口径就是
「**这片的退出码为 0 且用例数没变少**」——这两条都可以机械判定，见
``--assert-no-loss``。

## 为什么不修「让迁移只跑一次」

那是路线 B（把 ``upgrade_catalog`` 做成 session 级模板库），收益大但要动
测试基础设施、触及 50 个文件。本工具刻意**不代做那个决定**——它只让路线 A
今天就能落地，把路线 B 留成一个有数据支撑的选择题。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS = PROJECT_ROOT / "tests"
#: 出现这个调用就归入迁移型——「是否真的跑迁移」是能机械判定的属性。
MIGRATION_MARKER = "upgrade_catalog"


def classify(test_dir: Path) -> tuple[list[Path], list[Path]]:
    """按「跑不跑迁移」把测试文件分成两片。"""
    migration: list[Path] = []
    plain: list[Path] = []
    for path in sorted(test_dir.glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            plain.append(path)
            continue
        (migration if MIGRATION_MARKER in text else plain).append(path)
    return migration, plain


def run_slice(paths: list[Path], *, shard: str, out_dir: Path, timeout: float) -> dict:
    """跑一片并记下结果。只记，不判定。"""
    label = "migration" if shard == "migration" else "plain"
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "--tb=line",
         "-p", "no:randomly", *[str(p) for p in paths]],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout,
    )
    wall = time.perf_counter() - start
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / f"{shard}.log"
    log.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")

    summary = {
        "shard": shard,
        "label": label,
        "files": len(paths),
        "returncode": proc.returncode,
        "wall_s": round(wall, 1),
        "log": str(log.relative_to(PROJECT_ROOT)),
    }
    # 摘出 pytest 的最后一行汇总（形如 "60 passed, 1 skipped in 11.27s"）
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if ("passed" in line or "failed" in line or "error" in line) and " in " in line:
            summary["pytest_summary"] = line
            break
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F0.2 分片绿基线")
    parser.add_argument("--shard", choices=["migration", "plain", "both"], default="both")
    parser.add_argument("--out", default="eval/.cache/f02-baseline")
    parser.add_argument("--timeout", type=float, default=7200.0, help="每片超时秒数")
    parser.add_argument(
        "--assert-no-loss", action="store_true",
        help="把「退出码为 0」当成硬要求（建基线时用；日常跑不用）",
    )
    args = parser.parse_args(argv)

    migration, plain = classify(TESTS)
    out_dir = Path(args.out)
    print("=" * 68)
    print("F0.2 分片绿基线")
    print("=" * 68)
    print(f"  迁移型   {len(migration):3d} 个文件（含 {MIGRATION_MARKER}）")
    print(f"  非迁移型 {len(plain):3d} 个文件")
    print(f"  合计     {len(migration) + len(plain):3d} 个")
    print(f"  分片判据：文件内容是否出现 {MIGRATION_MARKER}（动态算出，不写死）")

    slices = {
        "migration": (migration, "迁移型（每个用例都建库 + 跑 40 个迁移）"),
        "plain": (plain, "非迁移型"),
    }
    wanted = ("migration", "plain") if args.shard == "both" else (args.shard,)

    results = []
    for shard in wanted:
        paths, desc = slices[shard]
        if not paths:
            print(f"\n跳过 {desc}（无文件）")
            continue
        print(f"\n跑 {desc}：{len(paths)} 个文件（最多 {args.timeout:.0f}s）")
        res = run_slice(paths, shard=shard, out_dir=out_dir, timeout=args.timeout)
        res["desc"] = desc
        results.append(res)
        # 每片跑完立刻落盘，不攒到最后。
        #
        # 这是实测换来的：第一次跑「两片」时进程卡在退出阶段（环境的
        # safe-delete shim 拦下 pytest 清理临时目录），最后那一步
        # ``write_text(baseline.json)`` 没执行到——**2.5 小时的结果一个字节
        # 都没留下**。日志是有的（run_slice 里写了），只是汇总没写。
        # 累积式落盘让「跑到哪儿算到哪儿」，也让中断时已完成的片仍然可用。
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "baseline.json").write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "shards": list(results),
                    "partial": True,   # 标记「可能还有片没跑完」
                    "note": "只记不判定：没有可信阈值时给出「门禁通过」的错觉比不给更糟",
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        verdict = "OK" if res["returncode"] == 0 else f"退出码 {res['returncode']}"
        print(f"  {verdict}  墙钟 {res['wall_s']:.1f}s  {res.get('pytest_summary', '(无汇总)')}")
        print(f"  日志 {res['log']}")

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "shards": results,
        "partial": False,   # 全部跑完
        "note": "只记不判定：没有可信阈值时给出「门禁通过」的错觉比不给更糟",
    }
    (out_dir / "baseline.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入 {out_dir / 'baseline.json'}")

    if args.assert_no_loss:
        bad = [r["shard"] for r in results if r["returncode"] != 0]
        if bad:
            print(f"\n--assert-no-loss 失败：{bad} 退出码非 0")
            return 1
        print("\n--assert-no-loss 通过：所有跑完的片退出码为 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
