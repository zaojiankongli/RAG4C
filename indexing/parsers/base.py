"""MinerU 文档解析抽象层。

定义解析结果载体 :class:`ParsedDocument`、解析器接口 :class:`DocumentParser`、
解析失败异常 :class:`MineruParserError`，以及按配置创建具体解析器的
工厂函数 :func:`create_parser`。

设计要点：
- 完全离线可导入：本模块不发起网络请求、不启动子进程，
  具体解析器（CLI / HTTP）的副作用只发生在各自的 ``parse()`` 内；
- 具体实现位于 :mod:`indexing.parsers.mineru_cli` 与
  :mod:`indexing.parsers.mineru_http`，工厂函数按 ``provider`` 路由，
  并在函数体内惰性导入实现类，避免与子模块产生循环依赖；
- ``MineruParserError`` 继承自 ``RuntimeError``，携带可操作的中文提示，
  供入库管线统一捕获。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import MineruSettings

# MinerU 自己的能力清单（比较时统一转小写），只供 ``MineruCliParser`` /
# ``MineruHttpParser`` 的 ``supports()`` 使用，不是全管线的闸门。
# 注意：MinerU 实际还支持 jp2/webp/gif/bmp 等图片格式，此处按入库管线
# 当前约定的范围收窄，避免误放行未经验证的类型。
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".png",
        ".jpg",
        ".jpeg",
    }
)

# DeepDoc 版面组件（10 种基础类型）
LAYOUT_TYPES: frozenset[str] = frozenset(
    {
        "text",           # 正文段落
        "title",          # 标题（章节/小节）
        "figure",         # 图
        "figure_caption", # 图注
        "table",          # 表格
        "table_caption",  # 表注
        "header",         # 页眉
        "footer",         # 页脚
        "reference",      # 参考文献
        "formula",        # 公式
    }
)


def file_extension(file_path: str) -> str:
    """返回文件的小写扩展名（含点号），无扩展名时返回空字符串。"""
    return Path(file_path).suffix.lower()


class MineruParserError(RuntimeError):
    """MinerU 文档解析失败。

    覆盖 CLI 退出码非零 / 空输出 / 超时、HTTP 错误、业务错误码、
    响应不可解析等所有失败场景，消息为可操作的中文说明。
    """


@dataclass
class LayoutBlock:
    """DeepDoc 版面组件块。

    Attributes:
        type: 组件类型（LAYOUT_TYPES：text / title / figure / table ...）。
        text: 组件文本内容（表格为 Markdown 表；公式为 LaTeX 源码）。
        page: 所在页码（0 起）；未知为 None。
        bbox: 归一化边界框 (x0, y0, x1, y1)；未知为 None。
        meta: 组件附加信息（表格：tsr 结构；标题：层级 level 等）。
    """

    type: str
    text: str = ""
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """单份文档的解析结果。

    Attributes:
        text: 解析出的全文 Markdown 文本。
        pages: 按页切分的文本列表。MinerU 输出不提供可靠的分页标记，
            保持为空列表（调用方可按需自行切分）。
        layout: DeepDoc 版面组件块列表（10 种基础类型），供版面感知
            切分（表格不被腰斩、图注跟随图等）。引擎不支持时为空。
        engine: 解析引擎标记：fast（pdf-inspector）/ vision（MinerU OCR）/
            mixed（按页路由）；直读文本为 text。
        metadata: 解析过程相关的元数据（文件信息、使用的解析模式等）。
    """

    text: str
    pages: list[str] = field(default_factory=list)
    layout: list[LayoutBlock] = field(default_factory=list)
    engine: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentParser(ABC):
    """文档解析器接口。

    实现类负责把本地文件转换为 :class:`ParsedDocument`。约定：
    - 实现类必须可离线构造（不联网、不启动子进程）；
    - 网络 / 子进程调用只允许发生在 :meth:`parse` 内部；
    - 所有失败统一抛出 :class:`MineruParserError`。
    """

    @abstractmethod
    def parse(self, file_path: str) -> ParsedDocument:
        """解析 ``file_path`` 指向的本地文件，返回 :class:`ParsedDocument`。"""

    @abstractmethod
    def supports(self, file_path: str) -> bool:
        """按扩展名判断该文件是否受支持（大小写不敏感）。"""

    # ------------------------------------------------------------------ #
    # 子类共享的解析前置校验
    # ------------------------------------------------------------------ #
    def _validate_file(self, file_path: str) -> None:
        """解析前的通用校验：文件存在，且**本解析器**认领这个扩展名。

        闸门只走 :meth:`supports`，不读模块级清单：那个清单是 MinerU 自己的能力
        范围，曾经被这里当成全管线闸门，于是换成别的 parser 时报错内容仍写着
        MinerU 的那一份支持列表——把"这个 parser 管不了"说成了"系统读不了"。
        （``tests/test_doc_type_registry.py`` 有一条宿主守卫钉住这点。）

        Raises:
            MineruParserError: 文件不存在或本解析器不支持该扩展名。
        """
        if not Path(file_path).is_file():
            raise MineruParserError(f"文件不存在: {file_path!r}")
        if not self.supports(file_path):
            raise MineruParserError(
                f"{type(self).__name__} 不支持该文件类型: {file_path!r}"
                f"（{file_extension(file_path) or '无扩展名'}）。"
                "纯文本 / Markdown 类文件无需 parser，入库管线会直读；"
                "若这确是需要解析的格式，请改用支持它的 parser。"
            )


def create_parser(settings: MineruSettings) -> DocumentParser:
    """按 ``settings.provider`` 创建文档解析器（注册表 / 工厂函数）。

    - ``provider="cli"``  -> :class:`indexing.parsers.mineru_cli.MineruCliParser`
    - ``provider="http"`` -> :class:`indexing.parsers.mineru_http.MineruHttpParser`

    实现类在此函数内惰性导入，避免模块级循环依赖；
    调用方持有返回的解析器即可调用 ``supports()`` / ``parse()``。

    Args:
        settings: MineruSettings（provider 决定返回哪个实现）。

    Returns:
        对应的具体解析器实例。

    Raises:
        ValueError: provider 非法。
    """
    if settings.provider == "cli":
        from indexing.parsers.mineru_cli import MineruCliParser

        return MineruCliParser(settings)
    if settings.provider == "http":
        from indexing.parsers.mineru_http import MineruHttpParser

        return MineruHttpParser(settings)
    raise ValueError(
        f"未知 mineru provider: {settings.provider!r}（可选 cli / http）"
    )


__all__ = [
    "ParsedDocument",
    "DocumentParser",
    "MineruParserError",
    "SUPPORTED_EXTENSIONS",
    "file_extension",
    "create_parser",
]
