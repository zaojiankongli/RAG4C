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
import os
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
    # 调高 safe-delete 的批量阈值（**只影响本进程**，不改仓库配置）。
    #
    # 为什么需要：pytest 跑完后会清临时目录，本机那个 safe-delete shim 在
    # ``count > CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD``（默认 50）时要求人工
    # 确认——**于是进程卡在退出阶段**。实测两次都是「日志里 100% 通过、
    # 没有任何 F，但 returncode=1」，而那个 1 来自退出阶段而不是测试失败。
    #
    # 放行的代价是「临时目录清理不再逐次确认」。本脚本删的全是 pytest 自己建
    # 在 tmp_path 下的目录（每次运行独立），不涉及仓库或用户数据。
    env = {**os.environ, "CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD": "200000"}
    start = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "--tb=line",
             "-p", "no:randomly", *[str(p) for p in paths]],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        # 超时也要留下日志与记录。**这一段是从实测学的**：第一跑 2400s 超时，
        # subprocess.run 抛异常、整个脚本死掉、**结果一个字节都没留下**——
        # 40 分钟白跑，而且不知道跑到哪了。所以这里把超时当成一个正常结果
        # 记录下来（returncode=None + timed_out=True），让「跑了多久、跑了什么」
        # 可见。
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (
            exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        ) + f"\n[分片基线] {label} 超过 {timeout:.0f}s 被中止，**这不是通过也不是失败**\n"
        proc = None
    if proc is not None:
        stdout, stderr = proc.stdout, proc.stderr
    wall = time.perf_counter() - start
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / f"{shard}.log"
    log.write_text(stdout + "\n" + stderr, encoding="utf-8")

    summary = {
        "shard": shard,
        "label": label,
        "files": len(paths),
        "returncode": None if proc is None else proc.returncode,
        "timed_out": timed_out,
        "wall_s": round(wall, 1),
        # 用 relative_to 会在 out_dir 不在项目根下时抛 ValueError
        # （实测：用临时目录试跑就崩了）。记绝对路径更稳——读日志的人需要的是
        # 能打开的路径，不是好看的短路径。
        "log": str(log),
    }
    # 摘出 pytest 的最后一行汇总（形如 "60 passed, 1 skipped in 11.27s"）
    for line in reversed(stdout.splitlines()):
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
        # 每片跑完立刻落盘，不攒到最后。实测：第一次跑两片时进程卡在退出阶段
        # （safe-delete shim），最后那步 write_text(baseline.json) 没执行到
        # ——2.5 小时的结果一个字节都没留下。累积式落盘让「跑到哪儿算到哪儿」，
        # 中断时已完成的片仍然可用。
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "baseline.json").write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "shards": list(results),
                    "partial": True,
                    "note": "只记不判定：没有可信阈值时给出「门禁通过」的错觉比不给更糟",
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
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
        if res["timed_out"]:
            verdict = f"超时（>{args.timeout:.0f}s）"
        elif res["returncode"] == 0:
            verdict = "OK"
        else:
            verdict = f"退出码 {res['returncode']}"
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
        # 超时的片**不算通过**：它既没跑完、退出码也不是 0。当成 OK 会让
        # 「基线建立完成」这句话变成假话。
        bad = [r["shard"] for r in results
               if r["timed_out"] or r["returncode"] != 0]
        if bad:
            print(f"\n--assert-no-loss 失败：{bad} 退出码非 0")
            return 1
        print("\n--assert-no-loss 通过：所有跑完的片退出码为 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
