"""One declaration for which MinerU ``provider`` value builds which parser.

``MineruSettings.provider`` is a plain ``str`` (``config/settings.py:884``), so the config layer
accepts anything. There were two independent dispatchers over it and they failed differently:

* ``indexing/parsers/plugins.py`` — the **production** path — treated "not cli" as http, so a
  typo such as ``clii`` silently sent document bytes to a paid external API instead of the
  local CLI. That is an egress decision made by a fallthrough.
* ``indexing/parsers/base.py::create_parser`` — the smoke-script path — raised on an unknown
  value (good) but never passed ``mode``, so it always built the free variant and never
  exercised the paid one the deployment is configured for.

Both now come through :func:`build_mineru_parser`, so a provider nobody declared is a reason to
refuse, never a reason to pick whichever implementation happens to be last in the file.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "MINERU_PROVIDER_NAMES",
    "MineruProviderSpec",
    "build_mineru_parser",
    "mineru_provider_spec",
]


def _build_cli(settings: Any, mode: str) -> Any:
    # 惰性 import：与引擎注册表的「disabled 引擎绝不加载重依赖」同一约定。
    from indexing.parsers.mineru_cli import MineruCliParser

    return MineruCliParser(settings, mode=mode)


def _build_http(settings: Any, mode: str) -> Any:
    from indexing.parsers.mineru_http import MineruHttpParser

    return MineruHttpParser(settings, http_client=None, mode=mode)


class MineruProviderSpec:
    """How one ``provider`` value assembles a parser."""

    __slots__ = ("name", "build")

    def __init__(self, name: str, build: Callable[[Any, str], Any]) -> None:
        self.name = name
        self.build = build


MINERU_PROVIDERS: tuple[MineruProviderSpec, ...] = (
    MineruProviderSpec("cli", _build_cli),
    MineruProviderSpec("http", _build_http),
)

MINERU_PROVIDER_NAMES: tuple[str, str] = tuple(spec.name for spec in MINERU_PROVIDERS)

_BY_NAME = {spec.name: spec for spec in MINERU_PROVIDERS}


def mineru_provider_spec(provider: Any) -> MineruProviderSpec:
    text = str(provider or "")
    spec = _BY_NAME.get(text)
    if spec is None:
        raise ValueError(
            f"未知 mineru provider: {text!r}（可选 {' / '.join(MINERU_PROVIDER_NAMES)}）。"
            " 这里不兜默认值：兜一个实现等于让配置写错的那一侧决定文档内容送到哪儿 —— "
            " 落到 http 就是把本地解析的文档发去外部付费 API。"
        )
    return spec


def build_mineru_parser(settings: Any, *, mode: str = "free") -> Any:
    """Assemble the parser ``settings.provider`` names, honouring ``mode``."""
    return mineru_provider_spec(getattr(settings, "provider", None)).build(settings, mode)
