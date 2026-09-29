"""钉住「源状态存在哪」只有声明侧一个答案（交接审查 §9 第 30 条）。

写方 ``SourceSyncer`` 按 source-id / 派生 cache key 落状态文件；发现端点曾自己拼
``cache_root / spec.name / "_state.json"``——与两种真实落点都不同名，于是
``/api/datasets`` 的 ``doc_count`` 从上线起恒为 0；``database`` 模式下 JSON 更是
根本不写。修法：读状态的唯一入口是 :func:`sources.runner.read_source_state`
（问 :mod:`sources.state_modes` 声明：ledger 优先、JSON 兜底），key 与写方共用
:func:`sources.runner.derived_cache_key`。

两层守卫：
* 行为——三种模式下 reader 真的按声明选存储，与写方落点逐字节对得上；
  ``database`` 模式 ledger 缺位必须拒绝（否则 doc_count 恒 0 的原始缺陷原样复发）；
* 回潮——除声明侧外不许再拼 ``_state.json`` 路径，路径知识不许长出第二份。
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from sources import SourceSpec
from sources.runner import SourceSyncer, derived_cache_key, read_source_state
from sources.state_modes import source_state_mode

REPO = Path(__file__).resolve().parents[1]


class _Ledger:
    """记录型 ledger：能按 (tenant, name) 找到 source、能读状态。"""

    def __init__(self, sources: dict[tuple[str, str], str], state: dict[str, dict]) -> None:
        self._sources = sources
        self._state = state

    def find_source(self, tenant_id: str, name: str):
        source_id = self._sources.get((tenant_id, name))
        if source_id is None:
            return None

        class _Row:
            id = source_id

        return _Row()

    def load_state(self, source_id: str) -> dict:
        return dict(self._state.get(source_id, {}))


def _spec() -> SourceSpec:
    return SourceSpec(
        type="local_dir",
        name="probe",
        params={"path": "."},
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )


def test_derived_cache_key_matches_where_the_writer_lands_the_json_file(
    tmp_path: Path,
) -> None:
    """reader 用的 key 必须就是写方落盘的 key——否则 doc_count 又会读空。"""
    spec = _spec()
    cache = tmp_path / "cache"
    syncer = SourceSyncer(object(), cache, None, state_mode="json")
    syncer._save_state(spec, {"doc-1": {"hash": "h"}})  # noqa: SLF001
    state_path = syncer._state_path(spec)  # noqa: SLF001
    assert state_path.parent.name == derived_cache_key(spec)
    assert state_path.is_file()


def test_reader_follows_the_declaration_in_each_mode(tmp_path: Path) -> None:
    """json 只看文件；dual ledger 有行用行、没行 JSON 兜底；database 只信 ledger。"""
    spec = _spec()
    cache = tmp_path / "cache"
    syncer = SourceSyncer(object(), cache, None, state_mode="json")
    syncer._save_state(spec, {"doc-1": {"hash": "h"}, "doc-2": {"hash": "h"}})  # noqa: SLF001
    json_docs = 2

    assert len(
        read_source_state(spec, cache_dir=cache, ledger=None, state_mode="json")
    ) == json_docs

    # dual：ledger 有 1 行 → 用 ledger；ledger 空 → JSON 兜底仍是 2
    dual_ledger = _Ledger({("tenant-a", "probe"): "src-9"}, {"src-9": {"doc-x": {}}})
    assert len(
        read_source_state(spec, cache_dir=cache, ledger=dual_ledger, state_mode="dual")
    ) == 1
    empty_ledger = _Ledger({}, {})
    assert len(
        read_source_state(spec, cache_dir=cache, ledger=empty_ledger, state_mode="dual")
    ) == json_docs

    # database：声明 ledger 权威——空状态就是空状态，不回退 JSON
    assert source_state_mode("database").ledger_authoritative is True
    db_ledger = _Ledger({("tenant-a", "probe"): "src-9"}, {})
    assert (
        read_source_state(spec, cache_dir=cache, ledger=db_ledger, state_mode="database")
        == {}
    )
    full_ledger = _Ledger(
        {("tenant-a", "probe"): "src-9"},
        {"src-9": {f"doc-{i}": {} for i in range(5)}},
    )
    assert len(
        read_source_state(spec, cache_dir=cache, ledger=full_ledger, state_mode="database")
    ) == 5

    # database 下 ledger 故障必须抛（与写方 ledger_failures_are_fatal 同调）

    class _DeadLedger(_Ledger):
        def find_source(self, tenant_id: str, name: str):
            raise RuntimeError("ledger is down")

    try:
        read_source_state(spec, cache_dir=cache, ledger=_DeadLedger({}, {}), state_mode="database")
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("database 模式下 ledger 故障必须抛，不能假装状态为空")


def test_database_mode_without_a_ledger_is_refused_not_silently_empty(
    tmp_path: Path,
) -> None:
    """汇总评审 P2-1：database + ledger=None 必须按 requires_ledger 拒绝。

    database 模式 JSON 根本不写；读者此时若静默落回 JSON 分支，返回空 dict，
    原始缺陷（doc_count 恒 0）在 MySQL 不可达的 database 部署下原样复发——
    与写方 ``SourceSyncer.__init__`` 对同一组合抛错不一致。
    """
    assert source_state_mode("database").requires_ledger is True
    with pytest.raises(ValueError, match="requires a SourceSyncLedger"):
        read_source_state(_spec(), cache_dir=tmp_path, ledger=None, state_mode="database")


def test_no_site_outside_the_declaration_assembles_the_state_json_path_itself() -> None:
    """路径知识只许住在 sources/runner.py——第二份拼法就是读空的那类缺陷。

    ``scripts/smoke_sources.py`` 曾在白名单里，但它读的 ``cache/<name>/_state.json``
    与写方真实落点（``cache/<derived_key>/_state.json``）从来就不是一个地方——
    那个白名单是把一个读不到文件的自检脚本固化成了"合法第二读者"，已移除；
    它的落点缺陷另行修复（smoke 现在经 runner 的真实路径读）。
    """
    owners = {"runner.py"}
    offenders: list[str] = []
    for folder in ("core", "server", "sources", "scripts", "config"):
        for path in sorted((REPO / folder).glob("*.py")):
            if path.name in owners:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                code = line.split("#", 1)[0]
                if re.search(r"""["_']_state\.json["']""", code):
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == [], f"_state.json 路径又被人自己拼了：{offenders}"


def test_the_discovery_endpoint_reads_through_the_shared_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端点那半边：真实 SourceSpec + 落盘状态 → _manifest_datasets 报出真值不是 0。

    汇总评审 P2-2 前这里只自读自写、没碰被测函数；现在注入假 settings 真调
    ``server.app._manifest_datasets``，断言 doc_count 就是写方落盘的篇数。
    """
    from types import SimpleNamespace

    spec = _spec()
    cache = tmp_path / "cache"
    syncer = SourceSyncer(object(), cache, None, state_mode="json")
    syncer._save_state(spec, {f"doc-{i}": {"hash": "h"} for i in range(3)})  # noqa: SLF001

    manifest = {
        "sources": [
            {
                "name": "probe",
                "type": "local_dir",
                "dataset_id": "dataset-a",
                "tenant_id": "tenant-a",
                "params": {"path": "."},
            }
        ]
    }
    manifest_path = tmp_path / "sources.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    import server.app as app_module

    fake_settings = SimpleNamespace(
        sources=SimpleNamespace(cache_dir=str(cache), manifest_path=str(manifest_path))
    )
    monkeypatch.setattr(app_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("config.settings.get_settings", lambda: fake_settings)

    datasets = app_module._manifest_datasets()

    assert datasets["dataset-a"]["doc_count"] == 3
    assert datasets["dataset-a"]["source_name"] == "probe"
