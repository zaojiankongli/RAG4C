"""One declaration for what the "gate passed" state is called in the catalog vs on the wire.

The stored spelling is ``passing`` — pinned by ``ck_quality_observations_gate_state``
(``models/orm.py:3674``, ``catalog_migrations/versions/0031_enterprise_release_quality_operations.py:483``)
— while the console and every response body say ``passed``. Nothing else in the vocabulary is
aliased, which is why only this one pair is declared.

Before this module, six places knew the alias by hand: three wrote the inverse ternary
(``"passing" if x == "passed" else x``) and three kept both spellings in a set or a ``Literal``,
including a frontend that branches on both. Each of those was a separate decision about whether
``passing`` is legal at that boundary, and none of them was checked against the ``CHECK``
constraint that actually decides what a row can contain.

Every function here is total: a state nobody declared is a reason to refuse, never a reason to
pass a value through unchanged.
"""

from __future__ import annotations

__all__ = [
    "GATE_STATE_PASSED_PUBLIC",
    "GATE_STATE_PASSED_STORAGE",
    "GATE_STATES_BASE",
    "gate_states_public",
    "gate_states_storage",
    "to_public_gate_state",
    "to_storage_gate_state",
]

#: The four states whose spelling is the same in both directions.
GATE_STATES_BASE = frozenset(
    {"waived", "not_required", "blocked", "unavailable"}
)

GATE_STATE_PASSED_STORAGE = "passing"
GATE_STATE_PASSED_PUBLIC = "passed"


def gate_states_storage() -> frozenset[str]:
    """The values a ``quality_observations.gate_state`` row may hold."""
    return GATE_STATES_BASE | {GATE_STATE_PASSED_STORAGE}


def gate_states_public() -> frozenset[str]:
    """The values this state takes once it leaves the catalog."""
    return GATE_STATES_BASE | {GATE_STATE_PASSED_PUBLIC}


def _translate(value: object, *, source: str, target: str, mapping: dict[str, str]) -> str:
    text = str(value or "")
    if text not in mapping:
        raise ValueError(f"{source} gate_state {text!r} 不在声明里，不能当成 {target} 用")
    return mapping[text]


def to_storage_gate_state(value: object) -> str:
    """Map a caller-supplied state (either spelling) to the one the catalog stores."""
    return _translate(
        value,
        source="对外",
        target="存储",
        mapping={
            **{state: state for state in GATE_STATES_BASE},
            GATE_STATE_PASSED_PUBLIC: GATE_STATE_PASSED_STORAGE,
            GATE_STATE_PASSED_STORAGE: GATE_STATE_PASSED_STORAGE,
        },
    )


def to_public_gate_state(value: object) -> str:
    """Map a stored state to the spelling the console is promised."""
    return _translate(
        value,
        source="存储",
        target="对外",
        mapping={
            **{state: state for state in GATE_STATES_BASE},
            GATE_STATE_PASSED_STORAGE: GATE_STATE_PASSED_PUBLIC,
        },
    )
