"""性能门禁的 argparse 与 mock 生命周期守卫 —— master-plan F3.5。

这次改了两处，都属于「不报错但门禁失效」型：

1. **``--help`` 直接抛 ValueError**。help 串里的 ``"CPU 负载 > 50% 时"`` 被
   argparse 用 ``%`` 格式化渲染，"% 时"里的"时"不是合法转换符 → 整个脚本连
   参数说明都打不出来。这类故障很隐蔽：门禁脚本平时只用命令行跑、不看
   help，于是没人发现它已经坏了。
2. **真实上游模式下 mock 进程为 None**。清理阶段 ``for p in (app, mock)``
   若不判 None 就会 ``AttributeError``；等待阶段若还去等 mock 的
   ``/__stats`` 就会 20 秒后误报"服务启动失败"——**而服务其实活得很好**。

守卫四条：help 串里的字面量百分号必须转义；真实模式不启 mock 也不等它；
真实模式默认只报不判（mock 阈值套到云端会把结构性延迟报成性能退化）；
给定的阈值文件真的被用上。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench" / "run_full_gate.py"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1：argparse help 可用
# ---------------------------------------------------------------------------


def test_help_does_not_crash() -> None:
    """--help 必须能跑完。它之前抛 ValueError（help 串里的字面量 %）。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"退出码 {proc.returncode}: {proc.stderr[:400]}"
    assert "--real-upstream" in proc.stdout


def test_literal_percent_in_help_is_escaped() -> None:
    """help 串里的字面量百分号必须写成 %%。

    argparse 用 %-格式化渲染 help，不转义就会把后面的中文字当转换符。
    """
    src = _source()
    assert 'CPU 负载 > 50%% 时' in src, "字面量 % 未转义"
    # 且不能有裸的 50% 混进去
    assert "CPU 负载 > 50% 时" not in src


# ---------------------------------------------------------------------------
# 2 / 3：真实上游模式
# ---------------------------------------------------------------------------


def test_real_upstream_mode_exists_and_skips_mock() -> None:
    src = _source()
    assert '"--real-upstream"' in src
    # mock 只在非真实模式下启动
    real_block = src.split("# 真实上游模式（master-plan F3.5）", 1)[1]
    assert "if args.real_upstream:" in real_block
    assert "mock = subprocess.Popen" in real_block, "mock 仍需在 mock 模式下启动"


def test_real_mode_uses_project_env_not_bench_env() -> None:
    """真实模式必须读 .env（真实密钥），不能用 config/.env.bench（mock 指向）。"""
    src = _source()
    assert 'RAG4C_ENV_FILE": ".env"' in src, "真实模式应切回 .env"
    assert 'RAG4C_ENV_FILE": "config/.env.bench"' in src, "mock 模式仍用 bench env"


def test_cleanup_skips_none_mock() -> None:
    """真实模式下 mock 是 None，清理循环必须判空。"""
    src = _source()
    cleanup = src.split("finally:", 1)[1][:600]
    assert "if p is None:" in cleanup, "清理循环必须跳过 None 进程"


def test_real_mode_does_not_wait_for_mock() -> None:
    """真实模式不该等 mock 的 /__stats，否则 20 秒后误报"服务启动失败"。"""
    src = _source()
    wait_block = src.split("# 1. 起 mock 上游", 1)[1].split("if not ready", 1)[0]
    assert "if not args.real_upstream:" in wait_block, "等 mock 前必须判真实模式"


def test_real_mode_reports_without_judging_by_default() -> None:
    """真实模式默认只报不判。

    DEFAULT_THRESHOLDS 是按 mock 上游定的（distinct P95 <= 800ms）。真实
    上游下 rerank 单次就是 500ms+ 往返，端到端秒级是结构性的——拿它判定会
    把"云端就这么慢"报成"性能退化"，然后有人去追查一个追不出的回归。
    """
    src = _source()
    assert "真实上游，未给阈值故不判定" in src
    assert 'args.real_upstream and args.thresholds == ""' in src


# ---------------------------------------------------------------------------
# 4：阈值覆盖
# ---------------------------------------------------------------------------


def test_thresholds_flag_is_actually_consumed() -> None:
    """给了 --thresholds 就必须真的用上，不能解析完就丢。"""
    src = _source()
    assert '"--thresholds"' in src
    assert "thresholds = json.loads" in src
    # 判定循环读的是局部变量 thresholds，而不是模块级 THRESHOLDS
    assert "for pattern, th in thresholds.items():" in src
    assert "for pattern, th in THRESHOLDS.items():" not in src.split("def main", 1)[1]


def test_missing_threshold_file_fails_loudly() -> None:
    src = _source()
    assert "阈值文件不存在" in src, "缺失的阈值文件必须明确报错而不是静默用默认"
