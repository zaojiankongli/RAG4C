"""Axis #10 guard, part 2: the vocabulary may not be re-copied, and the two properties that
choose a store must actually choose it.

``sources/runner.py`` asked "does this mode use the ledger / write JSON" five times by hand and
``config/settings.py`` spelled the vocabulary a second time. The declaration replaced those.
Two things had to be fenced, and the first version of this file only fenced one of them:

* a *shape* fence — the tenth-round review showed a fence that patterned only ``in {…}`` and
  ``== "…"`` is evaded by ``in ("json",)``, by ``!=``, by ``not in {…}`` and by a module-level
  ``frozenset``, all of which re-create the same forget-a-site failure;
* a *behaviour* fence — ``writes_json`` and ``ledger_failures_are_fatal`` decide which store
  wins, so a mis-wired property silently flips where source state lives while every
  vocabulary test stays green.

So the shape fence keys on the word ``"dual"`` (which belongs to this vocabulary only) plus a
comparison scan, and the behaviour fence runs the real ``SourceSyncer`` in each mode.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sources.runner import SourceSyncer
from sources.state_modes import source_state_mode

REPO = Path(__file__).resolve().parents[1]

#: The only two files allowed to spell the vocabulary out.
VOCAB_OWNERS = {"state_modes.py", "settings.py"}

#: Any ``state_mode`` compared against something — the shape a new hand-written branch takes.
COMPARISON = re.compile(
    r"state_mode[^\n]*(==|!=|\bin\b|\bnot in\b|\bif\b.*:)", re.M
)


def _python_files() -> list[Path]:
    return sorted(
        path
        for folder in ("core", "server", "sources", "scripts", "config")
        for path in (REPO / folder).glob("*.py")
    )


# --------------------------------------------------------------------------- #
# 形状栅栏
# --------------------------------------------------------------------------- #


def test_the_vocabulary_word_dual_is_spelled_only_by_its_owner() -> None:
    """``"dual"`` 除了这张表与设置侧的 Literal，别处不该再出现。

    按形状匹配会被绕（元组、``!=``、``not in``、模块级 frozenset 都能躲过），
    而**任何**新增的手抄分支都必须重抄一遍这三个词 —— 所以盯词比盯形状强。
    """
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _python_files()
        if path.name not in VOCAB_OWNERS
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if '"dual"' in line and not line.strip().startswith("#")
    ]
    assert offenders == [], f"state_mode 词表又被抄了一遍：{offenders}"


@pytest.mark.parametrize("target", ["sources/runner.py", "scripts/ingest_source.py"])
def test_a_consumer_asks_the_declaration_instead_of_comparing_the_mode(target: str) -> None:
    """两处消费点（runner 与操作员用的 CLI）都只许问声明，不许自己比字符串。"""
    text = (REPO / target).read_text(encoding="utf-8")
    offenders = [
        line.strip()[:78]
        for line in text.splitlines()
        if COMPARISON.search(line) and not line.strip().startswith("#")
    ]
    assert offenders == [], f"{target} 又自己比了一遍 state_mode 而不是问声明：{offenders}"


# --------------------------------------------------------------------------- #
# 行为栅栏：这两个属性真的要决定存储
# --------------------------------------------------------------------------- #


class _RaisingLedger:
    def __init__(self) -> None:
        self.state: dict[str, dict[str, object]] = {}

    def load_state(self, source_id: str) -> dict[str, object]:
        return dict(self.state.get(source_id, {}))

    def ensure_source(self, spec: object) -> None:  # pragma: no cover - 只用到属性
        raise AssertionError("unused")

    def save_state(self, source_id: str, docs: dict[str, dict[str, object]]) -> None:
        raise RuntimeError("ledger is down")


def _syncer(tmp_path: Path, mode: str, ledger: object | None) -> tuple[SourceSyncer, object, Path]:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    syncer = SourceSyncer(object(), cache_dir, None, ledger=ledger, state_mode=mode)
    syncer._active_source_id = "src-1"  # noqa: SLF001 - 让 ledger 分支真的被走到
    spec = _spec(tmp_path)
    # 路径问 runner 自己拿（它按 source-id / 派生 cache key 算），别在这儿猜一个文件名。
    return syncer, spec, syncer._state_path(spec)  # noqa: SLF001


@pytest.mark.parametrize(
    ("mode", "expect_json"),
    [("json", True), ("dual", True), ("database", False)],
)
def test_writes_json_actually_decides_whether_the_json_file_is_written(
    tmp_path: Path, mode: str, expect_json: bool
) -> None:
    """`database` 写 JSON 就是状态落到第二个存储；`json`/`dual` 不写就是操作员丢历史。

    属性表里翻一个布尔必须在这里变红 —— 第十轮评审指出这两条当时只有字面值钉着，
    把 `_save_state` 从 `writes_json` 改接成 `uses_ledger` 也能全绿。
    """
    syncer, spec, json_path = _syncer(tmp_path, mode, _RaisingLedger())
    syncer._save_state(spec, {"doc-1": {"hash": "h"}})  # noqa: SLF001
    assert json_path.exists() is expect_json


def test_a_ledger_error_is_fatal_only_where_the_ledger_is_the_authority(
    tmp_path: Path,
) -> None:
    """`dual` 里 ledger 挂掉要保住 JSON 这条路；`database` 里必须抛，不能假装写成了。"""
    for mode, should_raise in (("dual", False), ("database", True), ("json", False)):
        syncer, spec, _ = _syncer(tmp_path / mode, mode, _RaisingLedger())
        syncer._save_state(spec, {"doc-1": {"hash": "h"}})  # noqa: SLF001
        if should_raise:
            with pytest.raises(Exception):
                syncer._ledger_call(  # noqa: SLF001
                    "save_state", lambda: syncer.ledger.save_state("src-1", {})
                )
        else:
            assert syncer._ledger_call(  # noqa: SLF001
                "save_state", lambda: syncer.ledger.save_state("src-1", {})
            ) is None


def test_the_json_state_the_runner_writes_is_the_shape_the_reader_expects(
    tmp_path: Path,
) -> None:
    """顺带钉住上面那条不是靠"文件存在"糊过去的：内容真能读回来。"""
    syncer, spec, json_path = _syncer(tmp_path, "json", None)
    syncer._save_state(spec, {"doc-1": {"hash": "h"}})  # noqa: SLF001
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["docs"] == {"doc-1": {"hash": "h"}}
    assert source_state_mode("json").writes_json is True


def _spec(tmp_path: Path) -> object:
    from sources import SourceSpec

    return SourceSpec(
        type="local_dir",
        name="probe",
        params={"path": str(tmp_path)},
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )
