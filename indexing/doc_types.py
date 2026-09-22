#: 文档类型能力规格表：扩展名 -> doc_type -> (要不要 parser / 是不是表格类)。
#:
#: 这一张表回答三个本来就被分开问的问题，并把它们保持分开：
#:   * ``doc_type`` 是什么 —— 入库管线据此记录元数据，并被
#:     :mod:`indexing.chunking_router` 用来选切分模式；
#:   * ``readable_without_parser`` —— 能不能不经过 parser 直接按 UTF-8 读；
#:   * ``table_like`` —— 是否按「逐行 QA 对」切。
#:
#: 「用哪种预切分策略」不在本表里：那是 :mod:`indexing.strategies` 的
#: ``_STRATEGY_REGISTRY``（doc_type -> 策略类），它本来就是一张注册表而不是
#: if 链，再抄一份到这里是制造第二个真相源。
#:
#: **解析器能不能啃某个文件**同样不在本表里，那是 parser 自己的 ``supports()``。
#: 两者过去是混的：``base.SUPPORTED_EXTENSIONS`` 其实是 MinerU 的能力清单，却被
#: ``DocumentParser._validate_file`` 当成全管线闸门，于是配别的 parser 时报错写着
#: "解析器不支持该文件类型"——把"这个 parser 管不了"说成了"系统读不了"。
#:
#: 改造前上面这些判断分散在四五个互不派生的字面量集合里
#: （``parsers/base.SUPPORTED_EXTENSIONS``、``ingest._EXTENSION_DOC_TYPES``、
#: ``ingest._PLAINTEXT_EXTENSIONS``、``chunking_router._TABLE_DOC_TYPES``）。
#: 现在加一种格式 = 在这里加一行 ``DocTypeSpec``（或调
#: :func:`register_doc_type_spec`），那几个消费点都是实时查表，不必再改。

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DOC_TYPE_SPECS",
    "DocTypeSpec",
    "all_specs",
    "doc_type_for_extension",
    "extension_to_doc_type",
    "is_table_like_doc_type",
    "plain_text_extensions",
    "readable_without_parser",
    "register_doc_type_spec",
    "resolve_spec_by_doc_type",
    "resolve_spec_by_extension",
    "table_like_doc_types",
    "unregister_doc_type_spec",
]


@dataclass(frozen=True)
class DocTypeSpec:
    doc_type: str
    label: str
    extensions: tuple[str, ...]
    #: 可以不经 parser、按 UTF-8 直读（入库管线的免解析通道）。
    readable_without_parser: bool = False
    #: 表格/逐行型：切分路由据此选 qa（逐行问答对）模式。
    table_like: bool = False
    #: table_like 的历史别名：``chunking_router`` 过去把 xlsx/xls 也直接列进表格类，
    #: 尽管扩展名映射早已把它们规范成 excel。保留是为了行为逐字不变。
    table_like_aliases: tuple[str, ...] = ()
    #: 入库管线是否按扩展名记录该 doc_type。False = 这一行只为完整性而声明，
    #: 不改动 ``ingest`` 今天的映射（``.ppt`` / 图片在改造前也不在那张表里）。
    ingest_resolves: bool = True


DOC_TYPE_SPECS: tuple[DocTypeSpec, ...] = (
    # ``.mdx`` 归 markdown：它就是 markdown 加 JSX 组件，标题语法完全一致，
    # MarkdownStrategy 的按标题分节照样成立。``.adoc`` / ``.rst`` 则单列类型而
    # 不硬塞进 markdown——它们的标题是 ``== 标题`` / 下划线式，用 ``#`` 规则去切
    # 会一段都切不出来，不如诚实地落到段落切分（``strategies.strategy_for`` 对
    # 未知类型回退 ParagraphStrategy），同时让元数据如实记录格式。
    DocTypeSpec("markdown", "Markdown", (".md", ".markdown", ".mdx"),
                readable_without_parser=True),
    DocTypeSpec("asciidoc", "AsciiDoc", (".adoc", ".asciidoc"),
                readable_without_parser=True),
    DocTypeSpec("restructuredtext", "reStructuredText", (".rst",),
                readable_without_parser=True),
    DocTypeSpec("txt", "纯文本", (".txt",), readable_without_parser=True),
    # ``.text`` 今天只出现在「免 parser 直读」那一侧，不在「扩展名 -> 切分类型」
    # 那一侧：它的 doc_type 走文本启发式回退（见 ``ingest._infer_doc_type``）。
    # 把它一并规范成 txt 会改掉一份 .text 内容的切分方式，而这不在本轮要修的
    # 缺陷里，所以逐字保留今天的不对称。
    DocTypeSpec("text", "纯文本（.text）", (".text",),
                readable_without_parser=True, ingest_resolves=False),
    # csv 本来就一直是免 parser 直读的文本；table_like 让 chunking_router 里那条
    # 早就写好的 qa 路由真的能被走到 —— 改造前没有任何扩展名会产出 doc_type="csv"，
    # 于是 qa 模式对真实 CSV 上传是不可达的死代码。
    DocTypeSpec("csv", "CSV", (".csv",), readable_without_parser=True, table_like=True),
    DocTypeSpec("pdf", "PDF", (".pdf",)),
    DocTypeSpec("word", "Word", (".doc", ".docx")),
    # docling 认领 .html，但过去没有任何一张表知道它存在：resolved_doc_type 落回
    # 文本启发式，一份 HTML 会被记成 doc_type="txt"。登记它是为了让元数据如实
    # 记录格式；策略仍走默认回退（ParagraphStrategy），切分结果不变。
    DocTypeSpec("html", "HTML", (".html", ".htm")),
    # ppt/图片在改造前不在 ingest 的扩展名映射里（doc_type 落到文本启发式回退），
    # 这里声明它们是为了格式清单完整，不改动今天的解析结果。
    DocTypeSpec("powerpoint", "PowerPoint", (".ppt", ".pptx"), ingest_resolves=False),
    DocTypeSpec("image", "图片", (".png", ".jpg", ".jpeg"), ingest_resolves=False),
    DocTypeSpec("excel", "Excel", (".xls", ".xlsx"), table_like=True,
                table_like_aliases=("xlsx", "xls")),
)

_BY_EXTENSION: dict[str, DocTypeSpec] = {}
_BY_DOC_TYPE: dict[str, DocTypeSpec] = {}
_EXTRA: list[DocTypeSpec] = []


def _normalize(ext: str) -> str:
    normalized = str(ext or "").strip().lower()
    if not normalized.startswith("."):
        raise ValueError(f"扩展名要带点: {ext!r}")
    return normalized


def _index(spec: DocTypeSpec) -> None:
    """Validate the whole row first, then mutate — a rejected row leaves nothing behind."""
    canonical = spec.doc_type.strip().lower()
    if not canonical:
        raise ValueError("doc_type 不能为空")
    clash = _BY_DOC_TYPE.get(canonical)
    if clash is not None:
        raise ValueError(f"doc_type {canonical!r} 重复登记")
    normalized = [_normalize(ext) for ext in spec.extensions]
    if not normalized:
        raise ValueError(f"{canonical!r} 至少要有一个扩展名")
    for ext in normalized:
        owner = _BY_EXTENSION.get(ext)
        if owner is not None:
            raise ValueError(f"扩展名 {ext!r} 同时属于 {owner.doc_type} 与 {spec.doc_type}")
    _BY_DOC_TYPE[canonical] = spec
    for ext in normalized:
        _BY_EXTENSION[ext] = spec


for _spec in DOC_TYPE_SPECS:
    _index(_spec)


def register_doc_type_spec(spec: DocTypeSpec) -> None:
    """Declare one more document format. Every consumer reads through this table."""
    _index(spec)
    _EXTRA.append(spec)


def unregister_doc_type_spec(doc_type: str) -> None:
    """Withdraw a spec registered by :func:`register_doc_type_spec`.

    The built-in rows in :data:`DOC_TYPE_SPECS` cannot be withdrawn —— they are the
    contract the ingest tests pin.
    """
    canonical = doc_type.strip().lower()
    spec = _BY_DOC_TYPE.get(canonical)
    if spec is None or spec not in _EXTRA:
        raise KeyError(doc_type)
    _EXTRA.remove(spec)
    _BY_DOC_TYPE.pop(canonical, None)
    for ext in spec.extensions:
        _BY_EXTENSION.pop(_normalize(ext), None)


def all_specs() -> tuple[DocTypeSpec, ...]:
    return (*DOC_TYPE_SPECS, *_EXTRA)


def resolve_spec_by_extension(extension: str) -> DocTypeSpec | None:
    return _BY_EXTENSION.get(str(extension or "").strip().lower())


def resolve_spec_by_doc_type(doc_type: str) -> DocTypeSpec | None:
    return _BY_DOC_TYPE.get(str(doc_type or "").strip().lower())


def doc_type_for_extension(extension: str) -> str | None:
    """入库管线用的「扩展名 -> 切分类型」查询。"""
    spec = resolve_spec_by_extension(extension)
    if spec is None or not spec.ingest_resolves:
        return None
    return spec.doc_type


def readable_without_parser(extension: str) -> bool:
    """该扩展名能否不经 parser 直接按 UTF-8 读。"""
    spec = resolve_spec_by_extension(extension)
    return bool(spec and spec.readable_without_parser)


def is_table_like_doc_type(doc_type: str) -> bool:
    """切分路由用：该 doc_type（含历史别名）是否按表格/逐行处理。"""
    needle = str(doc_type or "").strip().lower()
    return needle in _table_like_names()


def _table_like_names() -> frozenset[str]:
    return frozenset(table_like_doc_types())


def table_like_doc_types() -> tuple[str, ...]:
    """``chunking_router`` 的表格类集合（含历史别名），顺序稳定。"""
    declared: list[str] = []
    for spec in all_specs():
        if spec.table_like:
            declared.append(spec.doc_type)
            declared.extend(spec.table_like_aliases)
    return tuple(dict.fromkeys(x.lower() for x in declared))


def plain_text_extensions() -> frozenset[str]:
    """免 parser 直读的扩展名全集（用于可读的错误提示）。"""
    return frozenset(
        ext for ext, spec in _BY_EXTENSION.items() if spec.readable_without_parser
    )


def extension_to_doc_type() -> dict[str, str]:
    """整张「扩展名 -> 切分类型」映射（调试与守卫测试用的全景视图）。"""
    return {
        ext: spec.doc_type
        for ext, spec in sorted(_BY_EXTENSION.items())
        if spec.ingest_resolves
    }
