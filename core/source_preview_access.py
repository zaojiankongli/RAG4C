"""Turn a stored ``file_path`` into bytes we are willing to hand to an operator.

``documents.file_path`` is the path ingest was *given* (``server/documents.py`` validates
only that it is a file), and the six remote storage providers are still
``validated_config_only`` with no SDK wired. So "view the original" reads a local absolute
path that an operator chose at upload time — which is a path-traversal surface waiting to be
walked if anything downstream ever accepts a path from a request.

Two rules make that impossible by construction, and both are checked here rather than at the
call site:

1. **A configured root, and no default.** ``locate_preview_source`` refuses everything when
   ``roots`` is empty. The feature is off until an operator names the directories that hold
   knowledge-base originals, and "off" reports as ``source_preview_disabled`` instead of
   quietly succeeding.
2. **Compare resolved paths on both sides.** The candidate and each root are ``resolve()``d
   before the prefix test, so a symlink that lives inside the root but points outside it is
   refused — testing the unresolved string would let it through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.source_previews import SourcePreviewSpec, resolve_source_preview_for_path

__all__ = [
    "PreviewSource",
    "SourcePreviewRefused",
    "locate_preview_source",
    "normalize_preview_roots",
]


class SourcePreviewRefused(RuntimeError):
    """Why this document's bytes are not viewable. ``code`` is the operator-facing reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PreviewSource:
    """A file that passed every gate: resolved path, declared kind, known size."""

    path: Path
    spec: SourcePreviewSpec
    size: int


def normalize_preview_roots(roots: Iterable[Any]) -> tuple[Path, ...]:
    """Drop blanks and resolve what is left, so the comparison later is string-free."""
    out: list[Path] = []
    for raw in roots or ():
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            out.append(Path(text).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return tuple(out)


def locate_preview_source(
    file_path: Any,
    *,
    roots: Iterable[Any],
    max_bytes: int | None = None,
) -> PreviewSource:
    """Resolve ``file_path`` against the configured roots and check it against its table row.

    Raises :class:`SourcePreviewRefused` for every way a file can fail — including the way
    that looks most like success: a file that exists, is the right type, and is simply not
    inside any root the operator declared.
    """
    resolved_roots = normalize_preview_roots(roots)
    if not resolved_roots:
        raise SourcePreviewRefused(
            "source_preview_disabled",
            "未配置原文目录（source_preview_roots），原文查看关闭",
        )
    raw = str(file_path or "").strip()
    if not raw:
        raise SourcePreviewRefused("source_preview_missing", "这份文档没有记录原文路径")
    try:
        candidate = Path(raw).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SourcePreviewRefused(
            "source_preview_unavailable", "原文路径无法解析"
        ) from exc
    if not any(candidate == root or candidate.is_relative_to(root) for root in resolved_roots):
        raise SourcePreviewRefused(
            "source_preview_out_of_scope",
            "原文不在已声明的目录内（符号链接跳出也算）",
        )
    # 后缀取自 resolve 之后的路径：content type 描述的是我们真正交出去的那些字节。按记录下来的
    # 原始路径判类型，会让 root 内一个 link.pdf -> real.html 的符号链接拿到 application/pdf
    # 却交出 HTML —— 界面上它会被当 PDF 塞进 iframe。放在越界检查之后，是为了让"跳出去了"
    # 这个安全信号优先于"这个后缀不认得"。
    spec = resolve_source_preview_for_path(candidate)
    if spec is None:
        raise SourcePreviewRefused(
            "source_preview_unsupported_kind",
            "这个后缀不在可查看来源表里；不给它一个默认 content type",
        )
    if not candidate.is_file():
        raise SourcePreviewRefused(
            "source_preview_missing", "原文文件不存在或已被移除"
        )
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise SourcePreviewRefused(
            "source_preview_unavailable", "读不到原文大小"
        ) from exc
    ceiling = spec.max_bytes if max_bytes is None else min(int(max_bytes), spec.max_bytes)
    if size > ceiling:
        raise SourcePreviewRefused(
            "source_preview_too_large",
            f"原文 {size} 字节，超过这一类可查看来源的 {ceiling} 字节上限（不截断：截断的 PDF"
            " 会渲染成一份看起来正常的残页）",
        )
    return PreviewSource(path=candidate, spec=spec, size=size)
