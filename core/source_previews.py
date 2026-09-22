"""Which document suffixes may be handed back to an operator, and as what.

The Knowledge Lifeline keeps the original bytes outside the catalog: ``documents.file_path``
is the path ingest was given, and there is no object-store client wired for the six remote
providers (``core/storage_backends.py`` declares them ``validated_config_only``). So the
question a "view the original" feature has to answer is not where the bytes are but **what
we are willing to show, and under which content type** — and that answer used to exist
nowhere at all.

Making it a table rather than a branch means the risky default is impossible to inherit: an
unlisted suffix has no row, and callers must refuse it. A default here would turn "we never
thought about this file type" into "serve it as octet-stream and let the browser decide",
which is how a stored ``.html`` ends up running as a script on the console's origin.

One row per suffix
    The content type belongs to the suffix, not to a family: five image extensions are one
    *kind* but five media types, and labelling a JPEG ``image/png`` is a bug the browser
    cannot forgive once ``nosniff`` is set. So the store is keyed by the exact suffix.

Inline-able means
    Only types the browser renders without executing the document's own content may be
    inline. ``text/html`` and ``image/svg+xml`` are deliberately absent from
    ``INLINE_SAFE_MEDIA_TYPES``, so a declaration cannot opt into them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "SourcePreviewSpec",
    "BUILTIN_SOURCE_PREVIEWS",
    "INLINE_SAFE_MEDIA_TYPES",
    "register_source_preview",
    "unregister_source_preview",
    "resolve_source_preview",
    "resolve_source_preview_for_path",
    "source_preview_suffix_names",
]

#: Media types that render inside the console's own origin without executing the document's
#: content. Anything else is download-only.
INLINE_SAFE_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/csv",
        "application/json",
    }
)

_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,12}$")
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")

#: A truncated PDF renders as a plausible short document rather than an error, so the
#: endpoint refuses an over-limit file instead of cutting it.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class SourcePreviewSpec:
    """One viewable suffix: its family, its real content type, and its size ceiling."""

    suffix: str
    kind: str
    media_type: str
    inline_renderable: bool = False
    max_bytes: int = DEFAULT_MAX_BYTES


def _rows(
    kind: str,
    pairs: tuple[tuple[str, str], ...],
    *,
    inline: bool,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[SourcePreviewSpec, ...]:
    return tuple(
        SourcePreviewSpec(
            suffix=suffix,
            kind=kind,
            media_type=media_type,
            inline_renderable=inline,
            max_bytes=max_bytes,
        )
        for suffix, media_type in pairs
    )


BUILTIN_SOURCE_PREVIEWS: tuple[SourcePreviewSpec, ...] = (
    *_rows("pdf", ((".pdf", "application/pdf"),), inline=True),
    *_rows(
        "image",
        (
            (".png", "image/png"),
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".gif", "image/gif"),
            (".webp", "image/webp"),
        ),
        inline=True,
        max_bytes=12 * 1024 * 1024,
    ),
    *_rows(
        "text",
        (
            (".txt", "text/plain"),
            (".md", "text/plain"),
            (".csv", "text/csv"),
            (".json", "application/json"),
        ),
        inline=True,
        max_bytes=2 * 1024 * 1024,
    ),
    # Office documents stay download-only: rendering them needs a converter we do not have,
    # and the bytes are zip containers no browser should inline.
    *_rows(
        "office",
        (
            (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ),
        inline=False,
    ),
)

_BY_SUFFIX: dict[str, SourcePreviewSpec] = {}


def _validate(spec: SourcePreviewSpec) -> None:
    if not isinstance(spec, SourcePreviewSpec):
        raise TypeError(f"expected SourcePreviewSpec, got {type(spec).__name__}")
    suffix = spec.suffix
    if not isinstance(suffix, str) or not _EXTENSION_RE.match(suffix or ""):
        raise ValueError(
            f"SourcePreviewSpec.suffix must be one lowercase suffix starting with '.'"
            f" (got {suffix!r}) — '.pdf' and 'pdf' must not be two spellings of one key"
        )
    if not isinstance(spec.kind, str) or not spec.kind.strip() or spec.kind != spec.kind.strip().lower():
        raise ValueError(f"{suffix}: kind must be a non-empty canonical (lowercase) label")
    media_type = spec.media_type
    if (
        not isinstance(media_type, str)
        or media_type != media_type.strip().lower()
        or not _MEDIA_TYPE_RE.match(media_type or "")
    ):
        raise ValueError(f"{suffix}: media_type must look like 'type/subtype' in lowercase")
    if spec.inline_renderable and media_type not in INLINE_SAFE_MEDIA_TYPES:
        raise ValueError(
            f"{suffix}: media_type {media_type!r} may not be inlined — it would render inside"
            " the console's own origin; text/html and image/svg+xml are never inline-able"
        )
    if (
        not isinstance(spec.max_bytes, int)
        or isinstance(spec.max_bytes, bool)
        or spec.max_bytes <= 0
    ):
        raise ValueError(f"{suffix}: max_bytes must be a positive integer number of bytes")


def register_source_preview(spec: SourcePreviewSpec, *, replace: bool = False) -> None:
    """Declare one viewable suffix."""
    _validate(spec)
    if spec.suffix in _BY_SUFFIX and not replace:
        raise ValueError(
            f"source preview already registered for {spec.suffix}"
            f" (kind {_BY_SUFFIX[spec.suffix].kind}); pass replace=True to reassign it"
        )
    _BY_SUFFIX[spec.suffix] = spec


def unregister_source_preview(suffix: str) -> None:
    """Withdraw a declaration; unknown suffixes are ignored (idempotent teardown)."""
    _BY_SUFFIX.pop(str(suffix).strip().lower(), None)


def resolve_source_preview(suffix: object) -> SourcePreviewSpec | None:
    """Look one suffix up by exact spelling. ``None`` means *nothing is known about it*."""
    if not isinstance(suffix, str):
        return None
    return _BY_SUFFIX.get(suffix.strip().lower())


def resolve_source_preview_for_path(path: Any) -> SourcePreviewSpec | None:
    """Find the declaration answering for this file's suffix; ``None`` must be refused."""
    try:
        suffix = Path(str(path)).suffix
    except (TypeError, ValueError):
        return None
    return resolve_source_preview(suffix)


def source_preview_suffix_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_SUFFIX))


for _spec in BUILTIN_SOURCE_PREVIEWS:
    register_source_preview(_spec)
