"""Receipt handoff is per source kind. An unknown kind must not query an approval."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from core.enterprise_notification_receipts import _handoff
from core.notification_receipt_kinds import (
    NotificationReceiptKindSpec,
    register_notification_receipt_kind,
    unregister_notification_receipt_kind,
)

_HOST = Path(__file__).parents[1] / "core" / "enterprise_notification_receipts.py"


class _Session:
    def scalar(self, _statement: object) -> object:
        raise AssertionError("handoff queried a source")


def test_unknown_source_kind_handoff_does_not_query_an_approval_request() -> None:
    result = _handoff(
        None,
        _Session(),
        SimpleNamespace(
            source_kind="billing_notice",
            tenant_id="tenant-1",
            source_id="source-1",
            source_dataset_id=None,
        ),
        SimpleNamespace(),
        route={"code": "billing_operations"},
        now=datetime(2026, 9, 23, 12, 0, 0),
    )

    assert result == {
        "state": "unavailable",
        "reason_code": "notification_source_unavailable",
        "route": {"code": "billing_operations"},
        "business_mutation_allowed": False,
    }


def test_registered_kind_handles_handoff_without_editing_the_host() -> None:
    before = _HOST.read_bytes()
    seen: list[str] = []

    def handoff(engine, session, row, member, *, route, now):
        del engine, session, member, now
        seen.append(row.source_kind)
        return {
            "state": "current",
            "reason_code": None,
            "route": dict(route),
            "business_mutation_allowed": False,
        }

    register_notification_receipt_kind(
        NotificationReceiptKindSpec(kind="billing_notice", handoff=handoff)
    )
    try:
        result = _handoff(
            None,
            _Session(),
            SimpleNamespace(
                source_kind="billing_notice",
                tenant_id="tenant-1",
                source_id="source-1",
                source_dataset_id=None,
            ),
            SimpleNamespace(),
            route={"code": "billing_operations"},
            now=datetime(2026, 9, 23, 12, 0, 0),
        )
        assert seen == ["billing_notice"]
        assert result["state"] == "current"
        assert result["route"] == {"code": "billing_operations"}
    finally:
        unregister_notification_receipt_kind("billing_notice")
    assert _HOST.read_bytes() == before
    refused = _handoff(
        None,
        _Session(),
        SimpleNamespace(
            source_kind="billing_notice",
            tenant_id="tenant-1",
            source_id="source-1",
            source_dataset_id=None,
        ),
        SimpleNamespace(),
        route={"code": "billing_operations"},
        now=datetime(2026, 9, 23, 12, 0, 0),
    )
    assert refused["state"] == "unavailable"
    assert refused["reason_code"] == "notification_source_unavailable"
