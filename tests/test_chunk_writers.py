"""Extension-point guards for the chunk write path.

These pin the acceptance criteria of
``docs/compose/spec/chunk-lifecycle-writers.md`` S2.5: extensibility here is a measured
property (a new mode or verb costs one registration, not a new branch), not an adjective.
"""

from __future__ import annotations

import types
import typing

import pytest

from core.chunk_catalog import ChunkRevisionConflict
from server import chunk_operations as co

SOURCE_CHUNK_OPERATIONS = co.__file__


def _mutation(op: str, **kwargs: typing.Any) -> co.ChunkMutation:
    payload: typing.Dict[str, typing.Any] = {
        "op": op,
        "doc_id": "doc-1",
        "chunk_id": "chunk-1",
        "expected_revision": 0,
    }
    payload.update(kwargs)
    return co.ChunkMutation(**payload)


def _context() -> co.WriterContext:
    return co.WriterContext(
        doc={},
        pipeline=None,
        catalog_api=None,
        chunk_catalog=None,
        operation_queue=None,
        include_graph=False,
    )


def _fake_catalog_api() -> types.SimpleNamespace:
    """Stands in for ``core.catalog`` so a probe writer never needs a live database."""
    return types.SimpleNamespace(
        get_document=lambda doc_id: {"id": doc_id, "tenant_id": "tenant-1"}
    )


# --------------------------------------------------------------------------- #
# S2.5.1 -- the mode axis must stay branch-free
# --------------------------------------------------------------------------- #
def test_chunk_write_path_carries_no_authority_mode_branches() -> None:
    """The three rollout modes may differ only by declared writer data.

    A reintroduced comparison against ``authority_mode`` means a fourth mode would
    have to be threaded through the shared code by hand again.
    """
    with open(SOURCE_CHUNK_OPERATIONS, encoding="utf-8") as handle:
        source = handle.read()
    for needle in ("authority_mode ==", "authority_mode !=", "authority_mode in "):
        assert needle not in source, f"chunk 写路径重新出现了模式分支: {needle!r}"


def test_every_authority_mode_is_served_by_a_registered_writer() -> None:
    assert co.CHUNK_WRITERS.names() == ("active", "off", "shadow")
    for name in co.CHUNK_WRITERS.names():
        writer = co.CHUNK_WRITERS.create(name, None)
        assert writer.mode == name
        for flag in (
            "strict_missing_head",
            "verify_completeness",
            "mirrors_legacy",
            "async_projection",
            "counts_heads_as_remaining",
        ):
            assert isinstance(getattr(writer, flag), bool), f"{name}.{flag}"
        assert isinstance(writer.initial_status, str)


# --------------------------------------------------------------------------- #
# S2.5.2 -- a new mode arrives by registration, with zero edits to existing code
# --------------------------------------------------------------------------- #
class _ProbeWriter:
    mode = "probe"
    strict_missing_head = False
    verify_completeness = False
    initial_status = "pending"
    mirrors_legacy = False
    async_projection = False
    counts_heads_as_remaining = False

    def __init__(self) -> None:
        self.seen: list[co.ChunkMutation] = []

    def apply(self, m: co.ChunkMutation, ctx: co.WriterContext) -> co.ChunkWriteOutcome:
        self.seen.append(m)
        return co.ChunkWriteOutcome(chunk="probe", authority_changed=False)


def test_new_writer_is_reached_without_touching_existing_writers() -> None:
    probe = _ProbeWriter()
    co.register_chunk_writer(probe)
    try:
        outcome = co.apply_chunk_mutation(
            _mutation("edit", text="body"),
            authority_mode="probe",
            catalog_api=_fake_catalog_api(),
            pipeline=object(),
        )
    finally:
        co.CHUNK_WRITERS.unregister("probe")

    assert outcome.chunk == "probe"
    assert [item.op for item in probe.seen] == ["edit"]
    assert "probe" not in co.CHUNK_WRITERS.names()


def test_unknown_mode_reports_the_registered_choices() -> None:
    with pytest.raises(ValueError, match="unknown chunk writer provider") as exc:
        co.apply_chunk_mutation(
            _mutation("edit", text="body"),
            authority_mode="quorum",
            catalog_api=_fake_catalog_api(),
            pipeline=object(),
        )
    message = str(exc.value)
    for name in co.CHUNK_WRITERS.names():
        assert name in message


def test_restore_and_revert_are_refused_without_chunk_head_authority() -> None:
    """``off`` has no ChunkHead, so it must fail visibly instead of half-applying."""
    mutations = (
        _mutation("restore"),
        _mutation("revert", target_revision=1),
    )
    for m in mutations:
        with pytest.raises(co.ChunkAuthorityIncomplete, match="需要 ChunkHead 权威"):
            co.LegacyWriter().apply(m, _context())


# --------------------------------------------------------------------------- #
# S2.5.3 -- a new verb lands in exactly one table, and the projection op is derived
# --------------------------------------------------------------------------- #
def test_every_verb_has_exactly_one_authority_step() -> None:
    assert set(co.AUTHORITY_STEPS) == set(typing.get_args(co.ChunkOp))
    for verb in co.AUTHORITY_STEPS.values():
        assert callable(verb.decide) and callable(verb.write)


def test_projection_operation_follows_the_resulting_head_not_the_verb() -> None:
    enabled = types.SimpleNamespace(enabled=True)
    tombstoned = types.SimpleNamespace(enabled=False)
    assert co._durable_operation(enabled) == "edit"
    assert co._durable_operation(tombstoned) == "delete"


def test_mutation_arguments_are_validated_before_any_mode_is_consulted() -> None:
    with pytest.raises(ValueError, match="切片正文不能为空"):
        _mutation("edit", text="   ")
    with pytest.raises(ValueError, match="target_revision"):
        _mutation("revert")
    with pytest.raises(ValueError, match="不接受"):
        _mutation("tombstone", text="body")
    with pytest.raises(ValueError, match="未知切片动作"):
        _mutation("merge", text="body")


def test_idempotent_verbs_decide_no_change_so_no_projection_work_is_enqueued() -> None:
    """Re-tombstoning or re-restoring must not mint a second durable operation."""
    tombstoned = types.SimpleNamespace(enabled=False, content_revision=0)
    live = types.SimpleNamespace(enabled=True, content_revision=0)

    assert co.AUTHORITY_STEPS["tombstone"].decide(tombstoned, _mutation("tombstone")) == "noop"
    assert co.AUTHORITY_STEPS["tombstone"].decide(live, _mutation("tombstone")) == "proceed"
    assert co.AUTHORITY_STEPS["restore"].decide(live, _mutation("restore")) == "noop"
    assert co.AUTHORITY_STEPS["restore"].decide(tombstoned, _mutation("restore")) == "proceed"


def test_an_idempotent_verb_still_honours_the_compare_and_swap_fence() -> None:
    """A stale ``expected_revision`` must never read as a harmless repeat."""
    tombstoned = types.SimpleNamespace(enabled=False, content_revision=4)
    live = types.SimpleNamespace(enabled=True, content_revision=4)
    with pytest.raises(ChunkRevisionConflict, match="chunk revision conflict"):
        co.AUTHORITY_STEPS["tombstone"].decide(
            tombstoned, _mutation("tombstone", expected_revision=0)
        )
    with pytest.raises(ChunkRevisionConflict, match="chunk revision conflict"):
        co.AUTHORITY_STEPS["restore"].decide(
            live, _mutation("restore", expected_revision=0)
        )


def test_edit_and_revert_reject_a_tombstoned_head() -> None:
    tombstoned = types.SimpleNamespace(enabled=False, content_revision=0)
    live = types.SimpleNamespace(enabled=True, content_revision=0)
    for op, extra in (("edit", {"text": "body"}), ("revert", {"target_revision": 1})):
        verb = co.AUTHORITY_STEPS[op]
        assert verb.decide(tombstoned, _mutation(op, **extra)) == "reject"
        assert verb.decide(live, _mutation(op, **extra)) == "proceed"
