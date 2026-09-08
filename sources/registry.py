"""文档源插件注册表。

形态照搬 :mod:`indexing.parsers.registry`（模块级 dict + 线程锁 + dataclass
插件描述），但**选择语义不同，这一点值得说清楚**：

- 解析引擎是「从若干候选里挑一个用」，所以那边有 priority 和 auto；
- 文档源是「同时接入多个，各自带各自的参数」——SpringBoot 文档和 Milvus
  文档是并存关系而不是备选关系。所以这里没有 priority、没有 auto，只有
  「按名字创建，参数由调用方给」。

沿用同一套注册形态的好处仍在：新增一种源（比如 Context7、S3、Confluence）
只需要写一个类 + 一行注册，本模块与调用方零改动。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from sources.base import DocumentSource, SourceError


@dataclass
class SourcePlugin:
    """一个可插拔的文档源插件。

    Attributes:
        name: 源类型名（manifest 里的 ``type`` 字段值，如 ``github_repo``）。
        factory: 工厂 ``(params: dict, settings) -> DocumentSource``。
            ``params`` 是 manifest 中该源的 ``params`` 子对象，原样透传，
            由各源自行解释——注册表不理解也不校验具体参数，这样新增源不需要
            改注册表。
        describe: 无参 -> 能力说明。
        required_params: 必填参数名。工厂调用前统一校验，好处是缺参数时
            报的是「源 X 缺少参数 repo」而不是各源自己抛出的 KeyError。
    """

    name: str
    factory: Callable[[dict[str, Any], Any], DocumentSource]
    describe: Callable[[], str]
    required_params: tuple[str, ...] = ()


_lock = threading.Lock()
_plugins: dict[str, SourcePlugin] = {}


def register_source_plugin(plugin: SourcePlugin) -> None:
    """注册文档源插件（同名覆盖，便于测试替换）。"""
    with _lock:
        _plugins[plugin.name] = plugin


def unregister_source_plugin(name: str) -> None:
    """卸载插件（测试用）。"""
    with _lock:
        _plugins.pop(name, None)


def list_source_plugins() -> list[dict[str, Any]]:
    """已注册源清单（供 CLI ``--list`` 与配置中心展示）。"""
    with _lock:
        return [
            {
                "name": p.name,
                "describe": p.describe(),
                "required_params": list(p.required_params),
            }
            for p in sorted(_plugins.values(), key=lambda x: x.name)
        ]


def create_source(
    source_type: str, params: dict[str, Any], settings: Any = None
) -> DocumentSource:
    """按类型名创建文档源实例。

    Args:
        source_type: manifest 里的 ``type``。
        params: 该源的参数字典（原样透传给工厂）。
        settings: Settings 实例，供源读取全局配置（超时等）。

    Returns:
        :class:`sources.base.DocumentSource`

    Raises:
        SourceError: 类型未注册、必填参数缺失，或工厂装配失败。
    """
    with _lock:
        plugin = _plugins.get(source_type)
    if plugin is None:
        with _lock:
            known = ", ".join(sorted(_plugins)) or "（无）"
        raise SourceError(f"未知的文档源类型 {source_type!r}；已注册: {known}")

    missing = [k for k in plugin.required_params if not params.get(k)]
    if missing:
        raise SourceError(
            f"文档源 {source_type!r} 缺少必填参数: {', '.join(missing)}"
        )

    try:
        return plugin.factory(params, settings)
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - 装配失败归一，保留原始提示
        raise SourceError(f"文档源 {source_type!r} 装配失败: {exc}") from exc


__all__ = [
    "SourcePlugin",
    "register_source_plugin",
    "unregister_source_plugin",
    "list_source_plugins",
    "create_source",
]
