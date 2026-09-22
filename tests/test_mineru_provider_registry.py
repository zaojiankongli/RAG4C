"""Axis (§D-8): one declaration says which MinerU ``provider`` builds which parser.

There were two independent dispatchers over ``MineruSettings.provider``, and a plain ``str``
field, so each one was wrong on a different axis:

* ``indexing/parsers/plugins.py`` — production — treated "not cli" as http. A typo'd provider
  therefore routed documents to the **external paid HTTP API** instead of the local CLI. An
  egress decision made by fallthrough is the thing to refuse, not to default.
* ``indexing/parsers/base.py::create_parser`` — the smoke path — did refuse unknown values,
  but never forwarded ``mode``, so it always built the ``free`` variant and never exercised
  the ``paid`` one the deployment is configured for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from indexing.parsers.base import DocumentParser, create_parser
from indexing.parsers.mineru_providers import (
    MINERU_PROVIDER_NAMES,
    build_mineru_parser,
    mineru_provider_spec,
)

REPO = Path(__file__).resolve().parents[1]


def _settings(provider: object) -> object:
    """用真的 pydantic 模型，别自己凑字段：少一个就把构造器自己饿死。"""
    from config.settings import MineruSettings

    return MineruSettings(provider=str(provider) if provider is not None else "")


# --------------------------------------------------------------------------- #
# 判据 §3.1：加一个 provider 只改一张表
# --------------------------------------------------------------------------- #


def test_each_declared_provider_builds_the_implementation_it_names() -> None:
    assert MINERU_PROVIDER_NAMES == ("cli", "http")
    from indexing.parsers.mineru_cli import MineruCliParser
    from indexing.parsers.mineru_http import MineruHttpParser

    assert isinstance(build_mineru_parser(_settings("cli")), MineruCliParser)
    assert isinstance(build_mineru_parser(_settings("http")), MineruHttpParser)
    assert mineru_provider_spec("cli").name == "cli"


@pytest.mark.parametrize("provider", ["clii", "CLI", " cli", "mineru-http", "", None, "gpu"])
def test_an_undeclared_provider_is_refused_instead_of_falling_through_to_egress(
    provider: object,
) -> None:
    """生产那条路径过去把"不是 cli"当成 http —— 拼错一个字母就把本地文档送去外部付费 API。

    三条路径都要问：注册表里的生产工厂、共享声明表、smoke 脚本用的工厂。
    只测后两条会放过当初真正出事的那一处 —— `_mineru_factory` 是 fallthrough 的家。
    """
    from indexing.parsers.plugins import _mineru_factory

    settings = _NamespaceSettings(provider)
    with pytest.raises(ValueError):
        _mineru_factory(_EngineCfg("free"), settings)
    with pytest.raises(ValueError):
        build_mineru_parser(settings)
    with pytest.raises(ValueError):
        create_parser(settings)


class _NamespaceSettings:
    """两条路径读 `settings.provider`，注册表那条读 `settings.mineru.provider`。"""

    def __init__(self, provider: object) -> None:
        self.provider = provider
        self.mineru = self


class _EngineCfg:
    def __init__(self, mode: str) -> None:
        self.mode = mode


def test_mode_reaches_the_implementation_on_both_paths() -> None:
    """两条路径都必须把 mode 交下去。

    改之前 smoke 那条永不传 mode，于是它**永远只跑 free**，配置写着 paid 也测不到付费分支；
    mineru_cli 自己会拒非法 mode，所以这里拿非法值当探针：mode 传不到就报不出这个错。
    """
    from indexing.parsers.base import MineruParserError
    from indexing.parsers.mineru_cli import MineruCliParser

    builds = {
        "声明表": lambda mode: build_mineru_parser(_settings("cli"), mode=mode),
        "smoke 工厂": lambda mode: create_parser(_settings("cli"), mode=mode),
    }
    for label, build in builds.items():
        with pytest.raises(MineruParserError, match="模式"):
            build("not-a-real-mode")
        assert isinstance(build("free"), MineruCliParser), label


def test_create_parser_still_returns_a_document_parser() -> None:
    assert isinstance(create_parser(_settings("cli")), DocumentParser)


# --------------------------------------------------------------------------- #
# 回潮栅栏：不许有第三处自己问 provider
# --------------------------------------------------------------------------- #


def test_no_module_outside_the_declaration_compares_provider_itself() -> None:
    """``provider`` 是裸 ``str``（没有字面量校验），所以每个问它的地方都是执行面。

    两处手写时它们对同一个未知值给出了两种相反的答案 —— 这比"少一个分支"更糟。
    """
    owners = {"mineru_providers.py", "settings.py"}
    offenders = []
    for path in sorted((REPO / "indexing").rglob("*.py")) + sorted((REPO / "core").rglob("*.py")):
        if path.name in owners or "test" in path.name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if re.search(r"provider\s*(==|!=|\bin\b|\bnot in\b)", code):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {code.strip()[:70]}")
    assert offenders == [], f"provider 又被人自己问了一遍：{offenders}"
