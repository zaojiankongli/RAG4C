"""SourceKind axis guards: the registry owns "which kinds exist", not the HTTP layer.

Before this, ``server/knowledge_sources_api.py`` decided the kind by hand in two
places written as ``if kind == "local_dir": ... else: <按 github 校验>``. The
``else`` was a fail-open: ``_update_source`` takes its kind from the *database*
column ``source_type`` (``String(64)``, no CHECK constraint), so a fourth value
would have been validated against GitHub's config model and passed GitHub's
allowlist. A third, dead copy of the value set (``_ALLOWED_KINDS``) sat next to it.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import BaseModel, Field

from server.knowledge_sources_api import (
    SourceKind,
    _connector_contract,
    _default_preflight,
    _github_repo_preflight,
    _local_dir_preflight,
    _validate_connector_model,
)
from sources.base import SourceError
from sources.registry import (
    SourcePlugin,
    attach_source_contract,
    register_source_plugin,
    source_contract,
    source_kind_names,
    unregister_source_plugin,
)


@pytest.fixture
def probe_plugin():
    """A throwaway kind, registered then withdrawn, so guards can exercise the open path."""
    name = "probe_fs"

    class _ProbeConfig(BaseModel):
        model_config = {"extra": "forbid"}
        share: str = Field(min_length=1)

    register_source_plugin(
        SourcePlugin(
            name=name,
            factory=lambda params, settings=None: SimpleNamespace(params=params),
            describe=lambda: "probe",
            required_params=("share",),
        )
    )
    attach_source_contract(name, config_model=_ProbeConfig, preflight=lambda n, s: n)
    yield name, _ProbeConfig
    unregister_source_plugin(name)


def test_the_wire_enum_and_the_registry_are_the_same_set() -> None:
    """Literal 是 OpenAPI 契约（前端拿它做表单选项），注册表是运行时真相：不许漂移。"""
    assert set(get_args(SourceKind)) == set(source_kind_names())


def test_every_registered_kind_carries_its_own_contract() -> None:
    for name in source_kind_names():
        plugin = source_contract(name)
        assert plugin.config_model is not None, name
        assert callable(plugin.preflight), name


def test_unknown_kind_is_refused_instead_of_inheriting_github() -> None:
    """判据的核心一条：第四个 kind 绝不再落进 else 分支。"""
    with pytest.raises(ValueError, match="未知的文档源类型"):
        _validate_connector_model("s3", {"bucket": "x"})  # type: ignore[arg-type]
    with pytest.raises(SourceError, match="未知的文档源类型"):
        source_contract("s3")


def test_a_kind_registered_without_a_contract_is_refused() -> None:
    register_source_plugin(
        SourcePlugin(
            name="bare_kind",
            factory=lambda params, settings=None: SimpleNamespace(params=params),
            describe=lambda: "bare",
        )
    )
    try:
        with pytest.raises(SourceError, match="没有挂载 API 契约"):
            source_contract("bare_kind")
        with pytest.raises(ValueError, match="没有挂载 API 契约"):
            _validate_connector_model("bare_kind", {})  # type: ignore[arg-type]
    finally:
        unregister_source_plugin("bare_kind")


def test_contract_cannot_be_attached_to_an_unregistered_kind() -> None:
    with pytest.raises(SourceError, match="未注册的源"):
        attach_source_contract(
            "never_registered", config_model=SimpleNamespace, preflight=lambda n, s: n
        )
    with pytest.raises(SourceError, match="契约不完整"):
        attach_source_contract("local_dir", config_model=None, preflight=lambda n, s: n)


def test_declaring_a_new_kind_is_two_declarations_and_zero_branch_edits(
    tmp_path: Path, probe_plugin
) -> None:
    """判据的准确版本 —— 加一种源 = 注册工厂 + 挂一行契约，**两处声明、零处分支**。

    原来这条的标题写着"不用改 HTTP 层"，那是假的：挂载语句就住在 HTTP 层这个文件里
    （配置模型与白名单安检本来就在 API 侧）。真正不许动的，是三个派发函数；而
    "注册即生效"要由行为证明，不是由一个恒真的字节比较证明。
    """
    name, model = probe_plugin
    hosts = {
        fn: inspect.getsource(fn)
        for fn in (_connector_contract, _validate_connector_model, _default_preflight)
    }

    assert source_contract(name).config_model is model
    assert _validate_connector_model(name, {"share": "s"}) is not None  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=f"{name} 配置字段无效"):
        _validate_connector_model(name, {"nope": 1})  # type: ignore[arg-type]

    for fn, source in hosts.items():
        assert inspect.getsource(fn) == source, f"{fn.__name__} 为了新 kind 被改过"
        assert name not in source
    assert '"local_dir"' not in hosts[_validate_connector_model]


def test_a_sibling_directory_sharing_the_roots_prefix_is_not_inside_it(
    tmp_path: Path,
) -> None:
    """`_is_within` 的真实语义：`/srv/allowed_evil` 不在 `/srv/allowed` 里面。

    写成 `str(path).startswith(root)` 的退化版本会放过它 —— 这是我这一轮评审里
    唯一一条"改了安检实现却没有测试变红"的变异，所以这条用例是补的那道网。
    """
    root = tmp_path / "allowed"
    root.mkdir()
    sibling = tmp_path / "allowed_evil"
    sibling.mkdir()
    settings = SimpleNamespace(
        local_allowed_roots=[str(root)], local_allowed_extensions=[".md"]
    )

    with pytest.raises(ValueError, match="不在允许目录内"):
        _local_dir_preflight({"path": str(sibling), "extensions": [".md"]}, settings)


def test_the_connector_is_not_built_before_the_allowlist_has_passed(
    tmp_path: Path, monkeypatch
) -> None:
    """顺序本身就是安检：先造连接器再查白名单 = 未授权的路径已经被打开过。"""
    import server.knowledge_sources_api as api

    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    settings = SimpleNamespace(
        sources=SimpleNamespace(
            local_allowed_roots=[str(root)],
            local_allowed_extensions=[".md"],
        )
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        knowledge_auth_settings=settings,
        knowledge_source_connector_preflight=None,
    )))
    built: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        api,
        "create_connector",
        lambda kind, params, _settings: built.append((kind, dict(params))),
    )

    with pytest.raises(ValueError, match="不在允许目录内"):
        api._default_preflight(request, "local_dir", {"path": str(outside)})
    assert built == [], "白名单还没放行，连接器就已经被构造过了"

    api._default_preflight(request, "local_dir", {"path": str(root)})
    assert [kind for kind, _params in built] == ["local_dir"]


def test_http_layer_names_no_kind_at_all() -> None:
    """宿主守卫：kind 的字面量只能出现在契约挂载那一行，不能出现在判断里。"""
    for fn in (_validate_connector_model, _local_dir_preflight, _github_repo_preflight):
        source = inspect.getsource(fn)
        assert '"local_dir"' not in source and '"github_repo"' not in source, fn.__name__
    module = inspect.getsource(inspect.getmodule(_validate_connector_model) or object)
    assert "_ALLOWED_KINDS" not in module, "值集的第二份抄本又长回来了"


# --------------------------------------------------------------------------- #
# 安检语义逐字不变（这一段是安全边界，迁移只搬位置不改判断）
# --------------------------------------------------------------------------- #


def test_local_dir_allowlist_still_gates_path_and_extensions(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    settings = SimpleNamespace(
        local_allowed_roots=[str(root)], local_allowed_extensions=[".md"]
    )
    unconfigured = SimpleNamespace(local_allowed_roots=[], local_allowed_extensions=[".md"])

    ok = _local_dir_preflight({"path": str(root), "extensions": [".md"]}, settings)
    assert ok["path"] == str(root.resolve())
    # 不写 extensions 时用全局默认白名单，而不是放行任意后缀
    assert _local_dir_preflight({"path": str(root)}, settings)["extensions"] == [".md"]

    for config, message in (
        ({"path": str(outside), "extensions": [".md"]}, "不在允许目录内"),
        ({"path": str(root), "extensions": [".exe"]}, "超出允许范围"),
    ):
        with pytest.raises(ValueError, match=message):
            _local_dir_preflight(dict(config), settings)
    with pytest.raises(ValueError, match="allowlist 未配置"):
        _local_dir_preflight({"path": str(root)}, unconfigured)


def test_github_allowlist_still_gates_repository_and_organization() -> None:
    both = SimpleNamespace(
        github_allowed_repositories=["Milvus/Milvus"], github_allowed_organizations=["acme"]
    )
    assert _github_repo_preflight({"repo": "milvus/milvus"}, both)["repo"] == "milvus/milvus"
    assert _github_repo_preflight({"repo": "Acme/Docs"}, both)["repo"] == "acme/docs"

    with pytest.raises(ValueError, match="不在 repository/organization allowlist 内"):
        _github_repo_preflight({"repo": "evil/docs"}, both)
    with pytest.raises(ValueError, match="allowlist 未配置"):
        _github_repo_preflight(
            {"repo": "milvus/milvus"},
            SimpleNamespace(github_allowed_repositories=[], github_allowed_organizations=[]),
        )


def test_config_models_still_reject_stray_fields_and_bad_shapes() -> None:
    with pytest.raises(ValueError, match="local_dir 配置字段无效"):
        _validate_connector_model("local_dir", {"pathtwo": "x"})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="github_repo 配置字段无效"):
        _validate_connector_model("github_repo", {"repo": "no-separator-here"})  # type: ignore[arg-type]


def test_re_registering_a_plugin_does_not_lose_its_attached_contract() -> None:
    """`register_builtin_sources()` 自称幂等，而契约是 API 模块在 import 期挂上去的。

    若同名覆盖把契约一起丢掉，症状是"所有源请求都 422"，而肇事者只是重跑了一次注册。
    """
    from sources.plugins import register_builtin_sources

    before = source_contract("local_dir").config_model
    assert before is not None
    register_builtin_sources()
    after = source_contract("local_dir").config_model
    assert after is before, "重跑内置注册把 API 契约冲掉了"
