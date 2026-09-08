"""文档文本清洗关（解析输出 -> 清洗 -> 切分 之间的纯文本变换）。

设计要点：
- 清洗按固定顺序执行：去 URL -> 去 Markdown 痕迹 -> 去控制字符 ->
  去样板行 -> 折叠空行；每个步骤均可独立开关，全部默认开启；
- 每个清洗步骤都是确定性、幂等的变换（``clean(clean(x)) == clean(x)``），
  相同输入必定得到相同输出，可放心对同一文本重复清洗；
- 只做纯内存文本变换，不涉及任何 I/O 与外部服务，可完全离线运行与测试；
- 保留 Markdown 表格（``|`` 行）、三反引号代码围栏与标题结构，
  交由后续切分器（:class:`~indexing.chunker.StructureAwareChunker`）处理；
- 正则在 Python 3 下默认以 Unicode 模式匹配，字符类只移除明确目标，
  不会误伤中文内容。
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ #
# 正则常量：各清洗步骤的匹配模式
# ------------------------------------------------------------------ #

# 去 URL / Markdown 链接
# - 链接语法 [label](url) 仅保留 label；(?<!!) 排除图片语法 ![..](..)，
#   避免图片被提前拆散（图片由 Markdown 痕迹步骤整体去除）。
_LINK_RE = re.compile(r"(?<!!)\[([^\[\]]*)\]\([^)]*\)")
# - 裸 URL：http/https/www 开头，字符类排除空白、括号与中文（含全角），
#   防止把紧邻 URL 的中文一并吃掉。
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+"
)
_URL_TRAILING_RE = re.compile(r"[.,;:!?。，；：！？、'\"”’}>]+$")

# 去 Markdown 痕迹
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HR_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)
_FENCE_ARTIFACT_RE = re.compile(r"^[ \t]*`{4,}[ \t]*$", re.MULTILINE)
# - HTML 标签以字母 / ! / ? 开头，避免把中文比较式 ``a < b > c`` 误删。
_HTML_TAG_RE = re.compile(r"<(?:/?[a-zA-Z][^>]*|![^>]*|\?[^>]*)>")

# 去控制字符：ASCII 控制字符（保留 \t \n \r）、U+FFFD 替换符、零宽字符
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd\u200b\u200c\u200d\ufeff]")

# 去样板行：纯页码 / 带页码标签 / 纯日期 / 页脚版权
_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
_PAGE_LABEL_RE = re.compile(r"^\s*(?:第\s*\d+\s*页|Page\s+\d+)\s*$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*$")
_DATE_CN_RE = re.compile(r"^\s*\d{4}年\d{1,2}月\d{1,2}日\s*$")
_FOOTER_RE = re.compile(r"版权所有|©|Copyright|Confidential|机密")
# - 以这些前缀开头的行属于结构性内容（标题 / 表格 / 围栏），一律不判样板
_BOILERPLATE_KEEP_PREFIXES = ("#", "|", "`")
_BOILERPLATE_MAX_CHARS = 60

# 折叠空行：3+ 连续空行 -> 1 个空行；去掉首尾纯空白行
_BLANK_COLLAPSE_RE = re.compile(r"\n{3,}")
_LEADING_BLANK_RE = re.compile(r"^(?:[ \t]*\n)+")
_TRAILING_BLANK_RE = re.compile(r"(?:[ \t]*\n)+[ \t]*$")


class CleanerError(RuntimeError):
    """文档清洗失败（非法输入等；供调用方统一捕获）。"""


class Cleaner:
    """文档文本清洗器（清洗关）。

    Args:
        remove_urls: 剥离裸 URL（http/https/www）与 Markdown 链接
            ``[label](url)``（仅保留 label）。
        remove_markdown_artifacts: 去除图片语法 ``![...](...)``、
            水平线行（``---`` 等）、过长的反引号围栏痕迹与 HTML 标签。
        remove_control_chars: 移除 ASCII 控制字符、U+FFFD 替换符
            与零宽字符（U+200B/U+200C/U+200D/U+FEFF）。
        remove_boilerplate: 丢弃纯页码 / 日期 / 版权类的短行
            （< 60 字符），保留标题等实质性短行。
        collapse_blank_lines: 连续 3 个以上空行折叠为 1 个空行，
            并去掉文本首尾的纯空白行。
    """

    def __init__(
        self,
        remove_urls: bool = True,
        remove_markdown_artifacts: bool = True,
        remove_control_chars: bool = True,
        remove_boilerplate: bool = True,
        collapse_blank_lines: bool = True,
    ) -> None:
        self.remove_urls = remove_urls
        self.remove_markdown_artifacts = remove_markdown_artifacts
        self.remove_control_chars = remove_control_chars
        self.remove_boilerplate = remove_boilerplate
        self.collapse_blank_lines = collapse_blank_lines

    # ------------------------------------------------------------------ #
    # 公开入口
    # ------------------------------------------------------------------ #
    def clean(self, text: str) -> str:
        """清洗整段文本，按固定顺序应用已开启的步骤。

        步骤顺序：去 URL -> 去 Markdown 痕迹 -> 去控制字符 ->
        去样板行 -> 折叠空行。每步都是幂等变换，重复清洗结果不变。

        Args:
            text: 文档原始文本（支持 Markdown 与纯文本中文）。

        Returns:
            清洗后的文本。

        Raises:
            CleanerError: text 不是 str。
        """
        if not isinstance(text, str):
            raise CleanerError("text 必须是 str")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.remove_urls:
            text = self._remove_urls(text)
        if self.remove_markdown_artifacts:
            text = self._remove_markdown_artifacts(text)
        if self.remove_control_chars:
            text = self._remove_control_chars(text)
        if self.remove_boilerplate:
            text = self._remove_boilerplate(text)
        if self.collapse_blank_lines:
            text = self._collapse_blank_lines(text)
        return text

    # ------------------------------------------------------------------ #
    # 清洗步骤（内部实现）
    # ------------------------------------------------------------------ #
    def _remove_urls(self, text: str) -> str:
        """去掉裸 URL 与 Markdown 链接，仅保留链接 label。

        链接提取循环执行直到不再变化：链接的 label 本身可能仍是
        ``[label](url)`` 形式（如 ``[![..](..)](..)`` 嵌套），
        循环可保证本步骤幂等。
        """
        while True:
            stripped = _LINK_RE.sub(r"\1", text)
            if stripped == text:
                break
            text = stripped

        def _keep_tail(match: re.Match[str]) -> str:
            """删除 URL 本体，仅保留属于句子的尾部标点。"""
            tail = _URL_TRAILING_RE.search(match.group(0))
            return tail.group(0) if tail else ""

        return _URL_RE.sub(_keep_tail, text)

    def _remove_markdown_artifacts(self, text: str) -> str:
        """去掉图片语法、水平线行、过长反引号痕迹与 HTML 标签。"""
        text = _IMAGE_RE.sub("", text)
        text = _HR_RE.sub("", text)
        text = _FENCE_ARTIFACT_RE.sub("", text)
        return _HTML_TAG_RE.sub("", text)

    def _remove_control_chars(self, text: str) -> str:
        """移除控制字符 / 替换符 / 零宽字符，保留 \\t \\n \\r。"""
        return _CONTROL_RE.sub("", text)

    def _remove_boilerplate(self, text: str) -> str:
        """逐行丢弃纯页码 / 日期 / 版权样板行（仅限短行）。

        标题（``#`` 开头）、表格行（``|`` 开头）与围栏行（`` ` `` 开头）
        无论长短一律保留，避免误删实质性内容。
        """
        kept: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith(_BOILERPLATE_KEEP_PREFIXES)
                or len(stripped) >= _BOILERPLATE_MAX_CHARS
            ):
                kept.append(line)
                continue
            if (
                _PAGE_NUMBER_RE.match(stripped)
                or _PAGE_LABEL_RE.match(stripped)
                or _DATE_RE.match(stripped)
                or _DATE_CN_RE.match(stripped)
                or _FOOTER_RE.search(stripped)
            ):
                continue
            kept.append(line)
        return "\n".join(kept)

    def _collapse_blank_lines(self, text: str) -> str:
        """连续空行折叠为一个空行，并去掉首尾纯空白行。"""
        text = _BLANK_COLLAPSE_RE.sub("\n\n", text)
        text = _LEADING_BLANK_RE.sub("", text)
        return _TRAILING_BLANK_RE.sub("", text)


def create_cleaner(**overrides: bool) -> Cleaner:
    """按覆盖参数构造 :class:`Cleaner`（缺省全部开启）。

    Args:
        **overrides: 与 :class:`Cleaner` 构造参数同名的开关，
            传入 ``False`` 关闭对应清洗步骤。

    Returns:
        配置好的清洗器实例。

    Raises:
        TypeError: 传入了 Cleaner 不认识的参数名。
    """
    return Cleaner(**overrides)


__all__ = ["CleanerError", "Cleaner", "create_cleaner"]
