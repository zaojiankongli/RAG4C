"""解析引擎插件注册表（插拔式扩展：docling / mineru / 任意新引擎）。

设计目标（「1 配置」）：所有 vision 解析引擎同一形态接入——

- 注册：register_parser_plugin(ParserPlugin(name, factory, describe))，
  引擎实现模块零耦合，新增引擎只需注册 + 在 ParsersSettings 加同构字段；
- 选择：settings.parsers.engine = auto 时按 priority 升序选第一个 enabled；
  显式指定引擎名时直接使用（未启用 / 未注册则报可操作错误）；
- 付费模式：每引擎的 mode 字段（free / paid / local / api）由引擎工厂
  自行解释——免费模式开箱可用，付费模式缺凭据时报可操作错误而非静默降级；
- 惰性：disabled 引擎的依赖（torch / docling 等重包）绝不 import。

用法::

    engine = create_vision_engine(get_settings())  # DocumentRouter 内部调用
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable


class ParserPluginError(RuntimeError):
    """解析引擎插件装配失败（未注册 / 未启用 / 模式凭据缺失）。"""


@dataclass
class ParserPlugin:
    """一个可插拔解析引擎插件。

    Attributes:
        name: 引擎名（配置段字段名，如 mineru / docling）。
        factory: 工厂 (engine_cfg, engine_specific_cfg) -> DocumentParser。
        describe: 无参 -> 能力说明（配置中心展示 / 错误提示）。
        modes: 引擎内可选模式（统一配置 mode 字段的合法值，如
            free/paid、local/api；前端引擎面板据此渲染模式开关，
            新引擎声明即可零前端改动）。
    """

    name: str
    factory: Callable[[Any, Any], Any]
    describe: Callable[[], str]
    modes: tuple[str, ...] = ("free", "paid")


_lock = threading.Lock()
_plugins: dict[str, ParserPlugin] = {}


def register_parser_plugin(plugin: ParserPlugin) -> None:
    """注册解析引擎插件（同名覆盖，便于测试）。"""
    with _lock:
        _plugins[plugin.name] = plugin


def unregister_parser_plugin(name: str) -> None:
    """卸载插件（测试 / 动态切换用）。"""
    with _lock:
        _plugins.pop(name, None)


def list_parser_plugins() -> list[dict[str, Any]]:
    """已注册插件清单（含能力说明，供配置中心 / 健康探活展示）。"""
    with _lock:
        return [
            {"name": p.name, "describe": p.describe(), "modes": list(p.modes)}
            for p in sorted(_plugins.values(), key=lambda x: x.name)
        ]


def create_vision_engine(settings: Any) -> Any:
    """按统一配置装配 vision 引擎（DocumentRouter 的插件化入口）。

    选择逻辑：
    - settings.parsers.engine 显式指定 -> 用该引擎（未启用 / 未注册报错）；
    - auto -> 遍历已注册插件，按各自配置 priority 升序选第一个
      enabled 的引擎（新增引擎只需注册 + 加同构配置段，零改动）；
    - 全部 disabled -> 返回 None（入库管线按无 parser 语义处理）。

    Args:
        settings: Settings 实例（parsers 段含同构引擎配置）。

    Returns:
        DocumentParser 实例；全部禁用时 None。

    Raises:
        ParserPluginError: 显式指定引擎未注册 / 未启用。
    """
    parsers = settings.parsers
    engine_name = getattr(parsers, "engine", "auto")

    with _lock:
        registered = dict(_plugins)

    def _engine_cfg(name: str) -> Any:
        """按引擎名取同构配置：parsers 段同名字段，或 parsers.plugins 字典。"""
        cfg = getattr(parsers, name, None)
        if cfg is None:
            extra = getattr(parsers, "plugins", None)
            if isinstance(extra, dict):
                cfg = extra.get(name)
        return cfg

    def _assemble(plugin: ParserPlugin, cfg: Any) -> Any:
        """调用插件工厂，装配失败归一为 ParserPluginError（保留原始提示）。"""
        try:
            return plugin.factory(cfg, settings)
        except ParserPluginError:
            raise
        except Exception as exc:
            raise ParserPluginError(
                f"解析引擎 {plugin.name!r} 装配失败: {exc}"
            ) from exc

    if engine_name != "auto":
        cfg = _engine_cfg(engine_name)
        if cfg is None or not getattr(cfg, "enabled", False):
            raise ParserPluginError(
                f"解析引擎 {engine_name!r} 未启用（RAG4C_PARSERS_{engine_name.upper()}_ENABLED）"
            )
        plugin = registered.get(engine_name)
        if plugin is None:
            raise ParserPluginError(f"解析引擎 {engine_name!r} 未注册（插件缺失）")
        return _assemble(plugin, cfg)

    # auto：遍历已注册插件（插拔式：新增引擎只需注册 + 加同构配置段，
    # 本函数零改动），按各自配置 priority 升序选第一个 enabled 的引擎
    candidates: list[tuple[int, str, ParserPlugin, Any]] = []
    for name, plugin in registered.items():
        cfg = _engine_cfg(name)
        if cfg is None or not getattr(cfg, "enabled", False):
            continue
        candidates.append((int(getattr(cfg, "priority", 100)), name, plugin, cfg))
    candidates.sort(key=lambda item: (item[0], item[1]))
    for _priority, _name, plugin, cfg in candidates:
        return _assemble(plugin, cfg)
    # 全部禁用 / 插件未安装：返回 None（入库管线按无 parser 语义处理，
    # 调用方给出可操作提示）
    return None


__all__ = [
    "ParserPlugin",
    "ParserPluginError",
    "register_parser_plugin",
    "unregister_parser_plugin",
    "list_parser_plugins",
    "create_vision_engine",
]
