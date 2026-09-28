from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.enterprise_notification_receipts import (
    NotificationReceiptUnavailable,
    _safe_route,
)
from core.notification_route_adapters import (
    NotificationRouteContext,
    register_notification_route_adapter,
    unregister_notification_route_adapter,
)

_HOST = Path(__file__).parents[1] / "core" / "enterprise_notification_receipts.py"


def _row(*, kind: str, route_code: str, params: dict[str, str]):
    return SimpleNamespace(
        source_kind=kind,
        target_route_code=route_code,
        target_route_params_json=params,
        source_dataset_id=None,
        source_id="source-1",
    )


def test_registered_adapter_is_selected_by_the_real_safe_route_path() -> None:
    before = _HOST.read_bytes()
    seen: list[tuple[str, str, str]] = []

    def adapter(context: NotificationRouteContext):
        seen.append((context.source_kind, context.route_code, context.tenant_id))
        return (
            {"source_id": "source-1"},
            {
                "code": context.route_code,
                "path": "/enterprise/custom",
                "query": {"source": "source-1"},
                "href": "https://external.example/redirect",
            },
        )

    register_notification_route_adapter("custom_notice", "custom_route", adapter)
    try:
        params, route = _safe_route(
            _row(kind="custom_notice", route_code="custom_route", params={"id": "source-1"}),
            "tenant-1",
        )
        assert seen == [("custom_notice", "custom_route", "tenant-1")]
        assert params == {"source_id": "source-1"}
        assert route["href"] == "/enterprise/custom?source=source-1"
    finally:
        unregister_notification_route_adapter("custom_notice", "custom_route")
    assert _HOST.read_bytes() == before


def test_unregistered_or_mismatched_route_adapter_fails_closed() -> None:
    with pytest.raises(NotificationReceiptUnavailable, match="route is unavailable"):
        _safe_route(
            _row(kind="custom_notice", route_code="custom_route", params={"id": "source-1"}),
            "tenant-1",
        )


def test_registered_route_code_for_another_source_kind_is_still_rejected() -> None:
    with pytest.raises(NotificationReceiptUnavailable, match="route is unavailable"):
        _safe_route(
            _row(
                kind="quality_alert",
                route_code="enterprise_approval",
                params={"request_id": "source-1"},
            ),
            "tenant-1",
        )


def test_route_adapter_cannot_return_external_or_out_of_scope_paths() -> None:
    def adapter(context: NotificationRouteContext):
        return (
            {},
            {
                "code": context.route_code,
                "path": "//evil.example/",
                "query": {},
                "href": "//evil.example/",
            },
        )

    register_notification_route_adapter("custom_notice", "unsafe_route", adapter)
    try:
        with pytest.raises(NotificationReceiptUnavailable, match="route is unavailable"):
            _safe_route(
                _row(kind="custom_notice", route_code="unsafe_route", params={}),
                "tenant-1",
            )
    finally:
        unregister_notification_route_adapter("custom_notice", "unsafe_route")


def test_malformed_persisted_route_key_is_classified_as_unavailable() -> None:
    with pytest.raises(NotificationReceiptUnavailable, match="route is unavailable"):
        _safe_route(
            _row(kind="QUALITY_ALERT", route_code="knowledge_quality_operations", params={}),
            "tenant-1",
        )


def test_route_adapter_registry_rejects_duplicate_and_invalid_signatures() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_notification_route_adapter(
            "quality_alert", "knowledge_quality_operations", lambda context: ({}, {})
        )
    with pytest.raises(TypeError, match="callable"):
        register_notification_route_adapter("custom_notice", "custom_route", None)
    with pytest.raises(TypeError, match="accept one positional context"):
        register_notification_route_adapter("custom_notice", "custom_route", lambda: ({}, {}))

    async def async_adapter(_context: NotificationRouteContext):
        return {}, {}

    def generator_adapter(_context: NotificationRouteContext):
        yield {}, {}

    async def async_generator_adapter(_context: NotificationRouteContext):
        yield {}, {}

    for route_code, adapter in (
        ("async_route", async_adapter),
        ("generator_route", generator_adapter),
        ("async_generator_route", async_generator_adapter),
    ):
        with pytest.raises(TypeError, match="must be synchronous"):
            register_notification_route_adapter("custom_notice", route_code, adapter)


def test_route_adapter_runtime_rejects_an_unexpected_awaitable_result() -> None:
    started: list[bool] = []

    async def deferred_route():
        started.append(True)
        return {}, {}

    def adapter(_context: NotificationRouteContext):
        return deferred_route()

    register_notification_route_adapter("custom_notice", "awaitable_route", adapter)
    try:
        with pytest.raises(NotificationReceiptUnavailable, match="route is unavailable"):
            _safe_route(
                _row(
                    kind="custom_notice",
                    route_code="awaitable_route",
                    params={},
                ),
                "tenant-1",
            )
        assert started == []
    finally:
        unregister_notification_route_adapter("custom_notice", "awaitable_route")


def test_builtin_quality_alert_and_approval_routes_keep_legacy_shapes() -> None:
    params, route = _safe_route(
        SimpleNamespace(
            source_kind="quality_alert",
            target_route_code="knowledge_quality_operations",
            target_route_params_json={
                "tenant_id": "tenant-1",
                "dataset_id": "dataset-1",
                "release_id": "release-1",
                "channel_id": "channel-1",
                "alert_id": "source-1",
            },
            source_dataset_id="dataset-1",
            source_id="source-1",
        ),
        "tenant-1",
    )
    assert params == {"dataset_id": "dataset-1", "section": "releases", "alert_id": "source-1"}
    assert (
        route["href"]
        == "/enterprise/knowledge-base?dataset=dataset-1&section=releases&alert=source-1"
    )

    params, route = _safe_route(
        SimpleNamespace(
            source_kind="approval_pending_for_me",
            target_route_code="enterprise_approval",
            target_route_params_json={"tenant_id": "tenant-1", "approval_request_id": "source-2"},
            source_dataset_id=None,
            source_id="source-2",
        ),
        "tenant-1",
    )
    assert params == {"request_id": "source-2"}
    assert route["href"] == "/enterprise/approvals?request=source-2"


def test_adapter_internal_unknown_provider_error_is_not_treated_as_missing_key() -> None:
    from core.providers import UnknownProviderError

    def adapter(_context: NotificationRouteContext):
        raise UnknownProviderError("adapter implementation failed")

    register_notification_route_adapter("custom_notice", "internal_error", adapter)
    try:
        with pytest.raises(UnknownProviderError, match="adapter implementation failed"):
            _safe_route(
                _row(kind="custom_notice", route_code="internal_error", params={}),
                "tenant-1",
            )
    finally:
        unregister_notification_route_adapter("custom_notice", "internal_error")
