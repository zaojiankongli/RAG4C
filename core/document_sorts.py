"""Document catalog sorts: one declaration per sort instead of four copies.

Listing documents used to consult four separate places that had to agree: a frozenset of
accepted names, a ladder that built the ORDER BY expression, a ladder that picked the
direction (``asc`` twice, then an ``else`` that meant descending), and five hard-coded
``"updated_at_desc"`` comparisons in the cursor path (the encoder's was missed in the first
pass and is converted now). The dangerous one was the
direction ladder: a new *ascending* sort added to the expression ladder would fall into
``else`` and page **backwards** — still a valid-looking page of documents, just with
broken cursor/next-page semantics. Nothing complained.

The keyset cursor stays deliberately single-implementer
    The predicate is written with ``<`` (descending) and the encoded value has to be a
    datetime. So a second ``keyset_cursor=True`` sort would page wrongly rather than fail
    loudly, and ``_validate`` refuses a second declaration outright. What a cursor sort must
    *supply* is now part of the declaration rather than part of an error message: it names
    the value it pages on through ``cursor_value``, because declaring "I can cursor" while
    leaving the machinery to guess which column you sort by is exactly the drift this table
    exists to prevent. Making another sort cursor-capable is still a real change to the
    predicate and the encoder — but it can no longer be *claimed* without being carried.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

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
    """One sort: its name, its direction, how to build its ORDER BY expression, and — for
    the one sort allowed to page — how to read that same key back out of a result row."""

    sort: str
    direction: str
    expression: Callable[[Any, set[str]], Any]
    keyset_cursor: bool = False
    cursor_value: Callable[[Mapping[str, Any]], Any] | None = None


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
        if not callable(spec.cursor_value):
            raise ValueError(
                f"{name}: keyset_cursor=True requires cursor_value — the sort expression and "
                "the value written into the cursor must come from the same declaration, or a "
                "later edit can make them disagree"
            )
        if spec.direction != "desc":
            raise ValueError(
                f"{name}: the keyset predicate is written for descending order (<), "
                "an ascending cursor sort would page the wrong way"
            )
        existing = [held for held in cursor_capable_sorts() if held != name]
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
