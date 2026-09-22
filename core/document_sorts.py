"""Document catalog sorts: one declaration per sort instead of four copies.

Listing documents used to consult four separate places that had to agree: a frozenset of
accepted names, a ladder that built the ORDER BY expression, a ladder that picked the
direction (``asc`` twice, then an ``else`` that meant descending), and two hard-coded
``"updated_at_desc"`` comparisons in the keyset-cursor path. The dangerous one was the
direction ladder: a new *ascending* sort added to the expression ladder would fall into
``else`` and page **backwards** — still a valid-looking page of documents, just with
broken cursor/next-page semantics. Nothing complained.

The keyset cursor stays deliberately single-implementer
    The predicate is written with ``<`` (descending) and the value it encodes comes from
    the ``updated_at or created_at`` coalesce. So a second ``keyset_cursor=True`` sort
    would page wrongly rather than fail loudly, and ``_validate`` refuses a second
    declaration outright. Making another sort cursor-capable is a real change to the
    predicate and the extractor, and this table will not let someone pretend otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = [
    "DocumentSortSpec",
    "DIRECTIONS",
    "register_document_sort",
    "unregister_document_sort",
    "resolve_document_sort",
    "document_sort_names",
    "cursor_capable_sorts",
]

DIRECTIONS = frozenset({"asc", "desc"})


@dataclass(frozen=True)
class DocumentSortSpec:
    """One sort: its name, its direction, and how to build its ORDER BY expression."""

    sort: str
    direction: str
    expression: Callable[[Any, set[str]], Any]
    keyset_cursor: bool = False


_BY_SORT: dict[str, DocumentSortSpec] = {}


def _validate(spec: DocumentSortSpec) -> None:
    if not isinstance(spec, DocumentSortSpec):
        raise TypeError(f"expected DocumentSortSpec, got {type(spec).__name__}")
    name = spec.sort
    if not isinstance(name, str) or not name.strip():
        raise ValueError("DocumentSortSpec.sort must be a non-empty string")
    if name != name.strip().lower():
        raise ValueError(f"DocumentSortSpec.sort must already be its own canonical key: {name!r}")
    if spec.direction not in DIRECTIONS:
        raise ValueError(f"{name}: direction must be one of {sorted(DIRECTIONS)}")
    if not callable(spec.expression):
        raise ValueError(f"{name}: expression must be callable")
    if spec.keyset_cursor:
        if spec.direction != "desc":
            raise ValueError(
                f"{name}: the keyset predicate is written for descending order (<), "
                "an ascending cursor sort would page the wrong way"
            )
        existing = cursor_capable_sorts()
        if existing:
            raise ValueError(
                f"{name}: only one sort may declare keyset_cursor; the cursor predicate and "
                f"the encoded value are written for {existing[0]!r} alone. Making another sort "
                "cursor-capable means generalising both, not adding a row here."
            )


def register_document_sort(spec: DocumentSortSpec, *, replace: bool = False) -> None:
    """Declare one catalog sort."""
    _validate(spec)
    if spec.sort in _BY_SORT and not replace:
        raise ValueError(f"document sort already registered: {spec.sort}")
    _BY_SORT[spec.sort] = spec


def unregister_document_sort(sort: str) -> None:
    """Withdraw a declaration; unknown names are ignored (idempotent teardown)."""
    _BY_SORT.pop(str(sort).strip().lower(), None)


def resolve_document_sort(sort: object) -> DocumentSortSpec | None:
    """Look one sort up by exact spelling. ``None`` means "not a sort we know", and every
    caller must reject it rather than pick a default direction."""
    if not isinstance(sort, str):
        return None
    return _BY_SORT.get(sort)


def document_sort_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_SORT))


def cursor_capable_sorts() -> list[str]:
    return sorted(name for name, spec in _BY_SORT.items() if spec.keyset_cursor)
