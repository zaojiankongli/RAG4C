"""文档源插件注册表。

形态照搬 :mod:`indexing.parsers.registry`（模块级 dict + 线程锁 + dataclass
插件描述），但**选择语义不同，这一点值得说清楚**：

- 解析引擎是「从若干候选里挑一个用」，所以那边有 priority 和 auto；
- 文档源是「同时接入多个，各自带各自的参数」——SpringBoot 文档和 Milvus
  文档是并存关系而不是备选关系。所以这里没有 priority、没有 auto，只有
  「按名字创建，参数由调用方给」。

沿用同一套注册形态的好处仍在：新增一种源（比如 Context7、S3、Confluence）
只需要写一个类 + 一行注册 + 一行 API 契约（配置模型与它自己的白名单安检），
本模块与各调用点零改动。
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
        config_model / preflight: HTTP 面的契约（配置形状 + 该源自己的白名单
            安检）。它们天然住在 API 层（要读 ``credential_ref`` 引用规则与
            ``local_allowed_roots`` 之类的设置），所以由各源在 API 模块 import
            时通过 :func:`attach_source_contract` 挂上来，注册表只负责保管与
            派发。**两者任一为空时 :func:`source_contract` 直接抛错**，不猜默认
            形状——过去 API 层写的是 ``if kind == "local_dir": ... else: 按
            github 校验``，于是一个未知/新增/DB 里残留的 kind 会被默默按
            GitHub 的形状与白名单放过（fail-open）。
    """

    name: str
    factory: Callable[[dict[str, Any], Any], DocumentSource]
    describe: Callable[[], str]
    required_params: tuple[str, ...] = ()
    config_model: Any = None
    preflight: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None


_lock = threading.Lock()
_plugins: dict[str, SourcePlugin] = {}


def register_source_plugin(plugin: SourcePlugin) -> None:
    """注册文档源插件（同名覆盖，便于测试替换）。

    覆盖时**保留已挂载的 API 契约**：契约是别的模块（``server/knowledge_sources_api``）
    在 import 期挂上去的，同名重建插件对象时若把它一起丢掉，那个源就会在
    :func:`source_contract` 处 fail closed —— 表现是"所有源请求都 422"，而肇事者
    只是重跑了一次 ``register_builtin_sources()``（它的 docstring 还写着幂等）。
    """
    with _lock:
        previous = _plugins.get(plugin.name)
        if previous is not None:
            if plugin.config_model is None:
                plugin.config_model = previous.config_model
            if plugin.preflight is None:
                plugin.preflight = previous.preflight
        _plugins[plugin.name] = plugin


def unregister_source_plugin(name: str) -> None:
    """卸载插件（测试用）。"""
    with _lock:
        _plugins.pop(name, None)


def source_kind_names() -> tuple[str, ...]:
    """已注册源类型名（HTTP 面用它派生"有哪些 kind"，不再手抄第二份集合）。"""
    with _lock:
        return tuple(sorted(_plugins))


def attach_source_contract(
    name: str,
    *,
    config_model: Any,
    preflight: Callable[[dict[str, Any], Any], dict[str, Any]],
) -> None:
    """给已注册的源挂上 API 侧契约（配置模型 + 白名单安检）。

    Raises:
        SourceError: 该名字没有注册过，或参数不完整。宁可在 import 期就炸，
            也不要留一个"看起来注册了但 preflight 会静默跳过"的源。
    """
    if config_model is None or preflight is None:
        raise SourceError(f"源 {name!r} 的契约不完整：config_model 与 preflight 都必须给")
    with _lock:
        plugin = _plugins.get(name)
        if plugin is None:
            known = ", ".join(sorted(_plugins)) or "（无）"
            raise SourceError(f"契约要挂到未注册的源 {name!r}；已注册: {known}")
        plugin.config_model = config_model
        plugin.preflight = preflight


def source_contract(name: str) -> SourcePlugin:
    """取出一个源的完整契约；缺任何一块都拒绝，绝不回退到别的源的形状。

    Raises:
        SourceError: 未注册，或注册了但契约没挂。
    """
    with _lock:
        plugin = _plugins.get(name)
    if plugin is None:
        with _lock:
            known = ", ".join(sorted(_plugins)) or "（无）"
        raise SourceError(f"未知的文档源类型 {name!r}；已注册: {known}")
    if plugin.config_model is None or plugin.preflight is None:
        raise SourceError(
            f"文档源 {name!r} 没有挂载 API 契约（config_model / preflight），"
            "拒绝按其它源的形状校验或放行"
        )
    return plugin


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
    "attach_source_contract",
    "source_contract",
    "source_kind_names",
    "list_source_plugins",
    "create_source",
]
