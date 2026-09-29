from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.enterprise_release_quality_operations import (
    ReleaseQualityOperationsInvalid,
    canonical_observation,
    canonical_operations_digest,
    evaluate_release_quality_slo,
    resolve_slo_policy_projection,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 29, 12, 0, 0, 500_000, tzinfo=UTC)


def policy(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "slo-policy-global",
        "tenant_id": "tenant-a",
        "name": "Release quality operations",
        "scope_type": "global",
        "scope_value": "*",
        "channel_id": None,
        "active_scope_key": "global:*",
        "status": "active",
        "revision": 3,
        "certification_warning_minutes": 60,
        "certification_critical_minutes": 15,
        "waiver_warning_minutes": 30,
        "max_open_alerts": 20,
        "auto_queue_recertification": True,
        "require_passing_certification": True,
        "allow_active_waiver": True,
        "policy_digest": "a" * 64,
    }
    value.update(overrides)
    return value


def gate(
    *,
    state: str = "passed",
    valid_until: datetime | str | None = NOW + timedelta(minutes=61),
    waiver_expires_at: datetime | str | None = None,
    risk_tier: str = "high",
    is_default_serving: bool = True,
    **overrides: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "state": state,
        "reason": "quality_gate_current",
        "channel": {
            "id": "channel-a",
            "risk_tier": risk_tier,
            "is_default_serving": is_default_serving,
        },
        "certification": None,
        "waiver": None,
    }
    if valid_until is not None:
        value["certification"] = {
            "id": "certification-a",
            "status": "passed",
            "certification_digest": "c" * 64,
            "valid_until": valid_until,
        }
    if waiver_expires_at is not None:
        value["waiver"] = {
            "id": "waiver-a",
            "status": "active",
            "waiver_digest": "d" * 64,
            "expires_at": waiver_expires_at,
        }
    value.update(overrides)
    return value


def observation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "release_id": "release-a",
        "channel_id": "channel-a",
        "scan_run_id": "scan-run-a",
        "slo_policy_id": "slo-policy-global",
        "slo_policy_revision": 3,
        "release_role": "active",
        "gate_state": "passed",
        "gate_reason": "quality_gate_current",
        "certification_id": "certification-a",
        "certification_digest": "c" * 64,
        "certification_valid_until": NOW + timedelta(minutes=61),
        "waiver_id": None,
        "waiver_digest": None,
        "waiver_expires_at": None,
        "minutes_to_certification_expiry": 61,
        "minutes_to_waiver_expiry": None,
        "severity": "healthy",
        "observed_at": NOW,
        "observed_by": "system:quality-scanner",
        "request_id": "request-a",
    }
    value.update(overrides)
    return value


def test_resolve_policy_projection_prefers_channel_then_risk_tier_then_global() -> None:
    resolved = resolve_slo_policy_projection(
        [
            policy(
                id="global",
                scope_type="global",
                scope_value="*",
                active_scope_key="global:*",
            ),
            policy(
                id="risk",
                scope_type="risk_tier",
                scope_value="high",
                active_scope_key="risk_tier:high",
            ),
            policy(
                id="channel",
                scope_type="channel",
                scope_value="channel-a",
                channel_id="channel-a",
                active_scope_key="channel:channel-a",
            ),
        ],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )
    assert resolved["id"] == "channel"
    assert resolved["resolution"] == "channel"

    risk = resolve_slo_policy_projection(
        [
            policy(
                id="global",
                scope_type="global",
                scope_value="*",
                active_scope_key="global:*",
            ),
            policy(
                id="risk",
                scope_type="risk_tier",
                scope_value="high",
                active_scope_key="risk_tier:high",
            ),
            policy(
                id="disabled-channel",
                scope_type="channel",
                scope_value="channel-a",
                channel_id="channel-a",
                active_scope_key="channel:channel-a",
                status="disabled",
            ),
        ],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )
    assert risk["id"] == "risk"
    assert risk["resolution"] == "risk_tier"

    global_policy = resolve_slo_policy_projection(
        [policy(scope_type="global", scope_value="*", active_scope_key="global:*")],
        channel_id="channel-a",
        risk_tier="low",
        tenant_id="tenant-a",
    )
    assert global_policy["id"] == "slo-policy-global"
    assert global_policy["resolution"] == "global"


def test_policy_projection_rejects_duplicate_or_noncanonical_active_scope() -> None:
    with pytest.raises(ReleaseQualityOperationsInvalid, match="duplicate"):
        resolve_slo_policy_projection(
            [
                policy(id="one"),
                policy(id="two"),
            ],
            channel_id="channel-a",
            risk_tier="high",
            tenant_id="tenant-a",
        )

    with pytest.raises(ReleaseQualityOperationsInvalid, match="active_scope_key"):
        resolve_slo_policy_projection(
            [policy(active_scope_key="global:wrong")],
            channel_id="channel-a",
            risk_tier="high",
            tenant_id="tenant-a",
        )


def test_policy_projection_is_strict_about_scope_numbers_booleans_and_digests() -> None:
    malformed = policy(certification_warning_minutes=True)
    with pytest.raises(ReleaseQualityOperationsInvalid, match="exact integer"):
        resolve_slo_policy_projection(
            [malformed],
            channel_id="channel-a",
            risk_tier="high",
            tenant_id="tenant-a",
        )

    malformed = policy(policy_digest="not-a-digest")
    with pytest.raises(ReleaseQualityOperationsInvalid, match="digest"):
        resolve_slo_policy_projection(
            [malformed],
            channel_id="channel-a",
            risk_tier="high",
            tenant_id="tenant-a",
        )

    malformed = policy(auto_queue_recertification=1)
    with pytest.raises(ReleaseQualityOperationsInvalid, match="exact boolean"):
        resolve_slo_policy_projection(
            [malformed],
            channel_id="channel-a",
            risk_tier="high",
            tenant_id="tenant-a",
        )


def test_certification_horizons_use_exact_signed_utc_minutes() -> None:
    resolved_policy = resolve_slo_policy_projection(
        [policy(certification_warning_minutes=10, certification_critical_minutes=5)],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )

    healthy = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(valid_until=NOW + timedelta(minutes=11, seconds=1)),
    )
    assert healthy["state"] == "healthy"
    assert healthy["severity"] == "healthy"
    assert healthy["minutes_to_certification_expiry"] == 11

    warning = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(valid_until=NOW + timedelta(minutes=10)),
    )
    assert warning["state"] == "warning"
    assert warning["reason"] == "certification_expiring"
    assert warning["minutes_to_certification_expiry"] == 10

    critical = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(valid_until=NOW + timedelta(minutes=5)),
    )
    assert critical["state"] == "critical"
    assert critical["reason"] == "certification_expiring"
    assert critical["minutes_to_certification_expiry"] == 5

    expired = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(valid_until=NOW - timedelta(microseconds=1)),
    )
    assert expired["state"] == "critical"
    assert expired["reason"] == "certification_expired"
    assert expired["minutes_to_certification_expiry"] == -1


def test_waiver_horizons_and_policy_allowance_are_explicit() -> None:
    resolved_policy = resolve_slo_policy_projection(
        [policy(waiver_warning_minutes=30)],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )

    active = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(
            state="waived",
            valid_until=None,
            waiver_expires_at=NOW + timedelta(minutes=31),
        ),
    )
    assert active["state"] == "healthy"
    assert active["minutes_to_waiver_expiry"] == 31

    expiring = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(
            state="waived",
            valid_until=None,
            waiver_expires_at=NOW + timedelta(minutes=30),
        ),
    )
    assert expiring["state"] == "critical"
    assert expiring["reason"] == "waiver_expiring"

    expired = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(
            state="waived",
            valid_until=None,
            waiver_expires_at=NOW,
        ),
    )
    assert expired["state"] == "critical"
    assert expired["reason"] == "waiver_expired"

    disallowed_policy = resolve_slo_policy_projection(
        [policy(allow_active_waiver=False)],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )
    disallowed = evaluate_release_quality_slo(
        NOW,
        disallowed_policy,
        gate(
            state="waived",
            valid_until=None,
            waiver_expires_at=NOW + timedelta(minutes=31),
        ),
    )
    assert disallowed["state"] == "warning"
    assert disallowed["reason"] == "active_waiver_not_allowed"


def test_blocked_stale_unavailable_and_not_required_fail_closed() -> None:
    resolved_policy = resolve_slo_policy_projection(
        [policy()],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )

    blocked = evaluate_release_quality_slo(
        NOW, resolved_policy, gate(state="blocked", valid_until=None)
    )
    assert blocked["severity"] == "critical"
    assert blocked["reason"] == "quality_gate_blocked"

    stale = evaluate_release_quality_slo(NOW, resolved_policy, gate(state="stale"))
    assert stale["severity"] == "critical"
    assert stale["reason"] == "certification_stale"

    explicit_expired = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(
            certification={
                "id": "certification-expired",
                "status": "expired",
                "certification_digest": "e" * 64,
                "valid_until": NOW - timedelta(minutes=1),
            }
        ),
    )
    assert explicit_expired["severity"] == "critical"
    assert explicit_expired["reason"] == "certification_expired"
    assert explicit_expired["alert_type"] == "certification_expired"

    unavailable = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(state="unavailable", valid_until=None),
    )
    assert unavailable["severity"] == "unavailable"
    assert unavailable["reason"] == "quality_authority_unavailable"

    not_required = evaluate_release_quality_slo(
        NOW,
        None,
        gate(state="not_required", valid_until=None, risk_tier="low", is_default_serving=False),
    )
    assert not_required["state"] == "not_required"
    assert not_required["severity"] == "healthy"

    unsafe_not_required = evaluate_release_quality_slo(
        NOW,
        None,
        gate(state="not_required", valid_until=None, risk_tier="high", is_default_serving=False),
    )
    assert unsafe_not_required["severity"] == "unavailable"
    assert unsafe_not_required["reason"] == "quality_authority_unavailable"


def test_malformed_evaluator_inputs_return_safe_unavailable_projection() -> None:
    resolved_policy = resolve_slo_policy_projection(
        [policy()],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )

    malformed_timestamp = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(valid_until="not-a-timestamp"),
    )
    assert malformed_timestamp["severity"] == "unavailable"
    assert malformed_timestamp["reason"] == "quality_authority_unavailable"

    malformed_boolean = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(
            certification={
                "id": "certification-a",
                "status": "passed",
                "certification_digest": "c" * 64,
                "valid_until": NOW,
                "stale": 1,
            }
        ),
    )
    assert malformed_boolean["severity"] == "unavailable"

    malformed_digest = evaluate_release_quality_slo(
        NOW,
        resolved_policy,
        gate(
            certification={
                "id": "certification-a",
                "status": "passed",
                "valid_until": NOW,
                "certification_digest": "x",
            }
        ),
    )
    assert malformed_digest["severity"] == "unavailable"


def test_canonical_observation_is_utc_microsecond_precise_and_body_free() -> None:
    raw = observation(
        observed_at="2026-08-29T20:00:00.123456+08:00",
        query="customer password=secret",
        result_body="should never persist",
        judgment_note="reviewer note",
        approval_ticket="opaque-ticket-123",
        api_key="sk_" + "test_not-persisted",
    )
    projected = canonical_observation(raw)
    assert projected["observed_at"] == "2026-08-29T12:00:00.123456Z"
    assert projected["observation_digest"] == canonical_observation(raw)["observation_digest"]
    rendered = repr(projected)
    for forbidden in (
        "customer",
        "password",
        "should never persist",
        "reviewer note",
        "opaque-ticket",
    ):
        assert forbidden not in rendered
    assert "api_key" not in projected


def test_observation_and_operations_digests_are_deterministic_and_exclude_forbidden_fields() -> (
    None
):
    first = observation(
        observed_at=NOW,
        query="one",
        body="two",
        note="three",
        ticket="four",
        extra={"nested": {"value": 1}},
    )
    second = dict(reversed(list(first.items())))
    second.update(
        {
            "query": "a different query",
            "body": "a different body",
            "note": "a different note",
            "ticket": "a different ticket",
        }
    )
    assert canonical_observation(first) == canonical_observation(second)

    digest_one = canonical_operations_digest(
        "scan-summary",
        {"b": 2, "a": 1, "note": "ignored", "ticket": "ignored"},
    )
    digest_two = canonical_operations_digest(
        "scan-summary",
        {"ticket": "changed", "a": 1, "b": 2, "note": "changed"},
    )
    assert digest_one == digest_two

    with pytest.raises(ReleaseQualityOperationsInvalid, match="credential-like"):
        canonical_operations_digest("scan-summary", {"safe_reason": "token=topsecret"})


def test_canonical_operations_digest_normalizes_utc_datetime_and_rejects_bad_namespace() -> None:
    first = canonical_operations_digest(
        "timestamp",
        {"observed_at": NOW, "expires_at": "2026-08-29T20:00:00.500000+08:00"},
    )
    second = canonical_operations_digest(
        "timestamp",
        {"expires_at": "2026-08-29T12:00:00.500000Z", "observed_at": NOW.isoformat()},
    )
    assert first == second

    with pytest.raises(ReleaseQualityOperationsInvalid):
        canonical_operations_digest("", {"value": 1})
    with pytest.raises(ReleaseQualityOperationsInvalid):
        canonical_operations_digest("namespace", {"bad": float("nan")})


def test_observation_flattens_validated_envelopes_and_rejects_forged_minutes() -> None:
    resolved_policy = resolve_slo_policy_projection(
        [policy()],
        channel_id="channel-a",
        risk_tier="high",
        tenant_id="tenant-a",
    )
    quality_gate = gate(valid_until=NOW + timedelta(minutes=61))
    evaluation = evaluate_release_quality_slo(NOW, resolved_policy, quality_gate)
    projected = canonical_observation(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        release_id="release-a",
        channel_id="channel-a",
        scan_run_id="scan-run-a",
        release_role="active",
        observed_at=NOW,
        observed_by="system:quality-scanner",
        request_id="request-a",
        slo_policy=resolved_policy,
        gate=quality_gate,
        evaluation=evaluation,
    )
    assert projected["gate_state"] == "passed"
    assert projected["slo_policy_id"] == "slo-policy-global"
    assert projected["minutes_to_certification_expiry"] == 61

    with pytest.raises(ReleaseQualityOperationsInvalid, match="minute calculation"):
        canonical_observation(
            projected | {"minutes_to_certification_expiry": 60},
        )
