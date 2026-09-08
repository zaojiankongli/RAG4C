"""内置文档源注册（import 即注册）。

与 :mod:`indexing.parsers.plugins` 同一形态：模块底部直接调用注册函数，
``import sources`` 就完成注册，调用方不需要显式初始化。
"""
from __future__ import annotations

from typing import Any

from sources.base import DocumentSource
from sources.github_repo import GitHubRepoSource
from sources.local_dir import LocalDirectorySource
from sources.registry import SourcePlugin, register_source_plugin


def _github_factory(params: dict[str, Any], settings: Any) -> DocumentSource:
    timeout = 300.0
    if settings is not None:
        timeout = float(getattr(getattr(settings, "sources", None), "http_timeout", timeout))
    return GitHubRepoSource(
        repo=params["repo"],
        ref=params.get("ref", "main"),
        include=params.get("include"),
        exclude=params.get("exclude"),
        extensions=params.get("extensions"),
        strip_prefix=params.get("strip_prefix", ""),
        timeout=float(params.get("timeout", timeout)),
        max_files=int(params.get("max_files", 0)),
        mode=str(params.get("mode", "auto")),
    )


def _local_factory(params: dict[str, Any], settings: Any) -> DocumentSource:  # noqa: ARG001
    return LocalDirectorySource(
        path=params["path"],
        include=params.get("include"),
        exclude=params.get("exclude"),
        extensions=params.get("extensions"),
        max_files=int(params.get("max_files", 0)),
    )


def register_builtin_sources() -> None:
    """注册内置源（幂等，同名覆盖）。

    ``describe`` 用 lambda 包一层而不是直接传 ``类.describe``：后者是未绑定的
    实例方法，注册表按无参 callable 调用会缺 ``self``。包一层同时也保证了
    「列出有哪些源」不需要先构造实例——而构造实例是要参数的。
    """
    register_source_plugin(
        SourcePlugin(
            name="github_repo",
            factory=_github_factory,
            describe=lambda: GitHubRepoSource.__doc__.strip().splitlines()[0],
            required_params=("repo",),
        )
    )
    register_source_plugin(
        SourcePlugin(
            name="local_dir",
            factory=_local_factory,
            describe=lambda: LocalDirectorySource.__doc__.strip().splitlines()[0],
            required_params=("path",),
        )
    )


register_builtin_sources()

__all__ = ["register_builtin_sources"]
