"""One declaration per source-sync state mode: which stores it writes, which it trusts.

``SourceSyncRunner`` used to answer those questions with three hand-written membership tests
(``in {"dual", "database"}`` twice and ``in {"json", "dual"}`` once) plus a string equality on
``"database"`` for authority, and the same three-value vocabulary was written a second time in
``config/settings.py`` as a ``Literal``. Adding a fourth mode meant remembering all four, and
the failure mode of forgetting one is a source silently reading or writing the wrong store —
not an error.

Each mode is now one row here. The membership tests became named properties, and
``source_state_mode`` is total: a mode nobody declared is a reason to refuse, never a reason to
fall back to whichever store the code happened to check first.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SOURCE_STATE_MODE_NAMES",
    "SourceStateModeSpec",
    "source_state_mode",
    "source_state_mode_names",
]


@dataclass(frozen=True)
class SourceStateModeSpec:
    """What one ``state_mode`` value promises about the two state stores."""

    name: str
    #: Whether synced state is written to the per-source JSON file.
    writes_json: bool
    #: Whether the sync ledger is consulted at all (load / ``ensure_source``).
    uses_ledger: bool
    #: When the ledger answers, is it the whole answer? ``False`` means the ledger wins
    #: only if it actually has rows, and JSON still back-fills — that is ``dual``.
    ledger_authoritative: bool
    #: Whether a raising ledger call is fatal. In ``dual`` it is logged and the JSON path
    #: still carries the state; swallowing it in ``database`` would silently pretend a
    #: write happened when nothing was recorded.
    ledger_failures_are_fatal: bool
    #: Whether constructing a runner in this mode without a ledger is a mistake. Only
    #: ``database`` says so today; ``dual`` without a ledger deliberately degrades to JSON,
    #: which is what pre-0018 deployments already do.
    requires_ledger: bool


SOURCE_STATE_MODES: tuple[SourceStateModeSpec, ...] = (
    SourceStateModeSpec(
        name="json",
        writes_json=True,
        uses_ledger=False,
        ledger_authoritative=False,
        ledger_failures_are_fatal=False,
        requires_ledger=False,
    ),
    SourceStateModeSpec(
        name="dual",
        writes_json=True,
        uses_ledger=True,
        ledger_authoritative=False,
        ledger_failures_are_fatal=False,
        requires_ledger=False,
    ),
    SourceStateModeSpec(
        name="database",
        writes_json=False,
        uses_ledger=True,
        ledger_authoritative=True,
        ledger_failures_are_fatal=True,
        requires_ledger=True,
    ),
)

#: A tuple literal so ``config/settings.py`` can spell the vocabulary as
#: ``Literal[tuple(SOURCE_STATE_MODE_NAMES)]`` and the two sides cannot drift silently.
SOURCE_STATE_MODE_NAMES: tuple[str, str, str] = tuple(  # type: ignore[assignment]
    spec.name for spec in SOURCE_STATE_MODES
)

_BY_NAME = {spec.name: spec for spec in SOURCE_STATE_MODES}


def source_state_mode_names() -> tuple[str, ...]:
    return SOURCE_STATE_MODE_NAMES


def source_state_mode(name: object) -> SourceStateModeSpec:
    """Look up one declared mode; refuse anything nobody declared."""
    text = str(name or "")
    spec = _BY_NAME.get(text)
    if spec is None:
        raise ValueError(
            f"未知的 state_mode {text!r}；已声明的是 {' / '.join(SOURCE_STATE_MODE_NAMES)}。"
            " 加一种模式要在 SOURCE_STATE_MODES 登记，不要在这里兜默认值 —— 兜默认值等于"
            "让一个没审过的模式按 json 的写法走，状态会静默写到错的存储上。"
        )
    return spec
