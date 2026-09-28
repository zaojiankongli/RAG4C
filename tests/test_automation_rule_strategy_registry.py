"""Stage25 condition/action Strategy + Registry guards."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any
from typing import get_args

import pytest

from core import automation_rule_strategies as registry
from core import enterprise_automation_workflows as authority
from core import enterprise_automation_workflows_service as service
from models.orm import TenantAutomationActionRequest, TenantAutomationRuleRevision
from server import enterprise_automation_workflows_api as automation_api
from server.enterprise_automation_workflows_api import (
    AutomationActionCode,
    AutomationConditionCode,
    ActionPlanStep,
)


TIME = "2026-08-30T00:00:00.000000Z"
EVENT = {
    "tenant_id": "tenant-a",
    "trigger_code": "task_failed",
    "source_stream_id": "task-stream-a",
    "source_event_id": "task-event-a",
    "source_event_digest": "a" * 64,
    "sequence": 1,
    "status": "failed",
    "severity": "error",
    "action_required": True,
    "attempt_number": 3,
    "max_attempts": 3,
    "source_current": True,
    "occurred_at": TIME,
    "safe_facts": {"task_id": "task-a"},
}


def test_builtin_strategies_are_the_single_parameter_and_behavior_declaration() -> None:
    assert registry.condition_strategy_names(builtin_only=True) == (
        "always",
        "status_is",
        "action_required",
        "severity_at_least",
        "attempt_exhausted",
        "source_is_stale",
    )
    assert registry.action_strategy_names(builtin_only=True) == (
        "notify_operator",
        "request_approval",
        "open_task_attention",
        "pause_rule",
    )
    assert set(registry.condition_strategy_names(builtin_only=True)) == (
        authority.AUTOMATION_CONDITION_CODES
    )
    assert set(registry.action_strategy_names(builtin_only=True)) == authority.AUTOMATION_ACTION_CODES
    assert registry.condition_parameter_names("always") == frozenset()
    assert registry.condition_parameter_names("attempt_exhausted") == frozenset(
        {"minimum_attempts"}
    )
    assert registry.action_parameter_names("notify_operator") == frozenset(
        {"category", "severity", "title"}
    )
    assert registry.action_parameter_names("pause_rule") == frozenset({"reason_code"})


def test_custom_condition_is_honoured_by_the_real_pure_evaluation_path() -> None:
    def evaluate(params: dict[str, Any], event: dict[str, Any]) -> bool:
        return params["probe"] == event["safe_facts"]["probe"]

    strategy = registry.AutomationConditionStrategy(
        "runtime-probe",
        (registry.AutomationParameterSpec("probe", "code", ("ready-now",)),),
        evaluate,
    )
    registry.register_condition_strategy(strategy)
    try:
        event = {**EVENT, "safe_facts": {"probe": "ready_now"}}
        assert authority.evaluate_automation_condition(
            "runtime-probe", {"probe": "ready-now"}, event
        )
        with pytest.raises(authority.AutomationAuthorityInvalid, match="condition"):
            authority.canonical_automation_rule_revision(
                {
                    "tenant_id": "tenant-a",
                    "rule_id": "rule-a",
                    "rule_revision_id": "revision-a",
                    "revision": 1,
                    "trigger_code": "task_failed",
                    "condition_code": "runtime-probe",
                    "condition_params": {"probe": "ready-now"},
                    "action_plan": [
                        {
                            "action_code": "pause_rule",
                            "params": {"reason_code": "probe"},
                        }
                    ],
                    "created_at": TIME,
                    "created_by": "owner-a",
                }
            )
    finally:
        registry.unregister_condition_strategy("runtime-probe")


def test_custom_action_target_is_honoured_by_the_real_service_target_path() -> None:
    seen: list[dict[str, Any]] = []

    def target(_event: dict[str, Any], rule_id: str) -> registry.AutomationTargetReference:
        seen.append(dict(_event))
        return registry.AutomationTargetReference("runtime_target", f"{rule_id}-target")

    registry.register_action_strategy(
        registry.AutomationActionStrategy(
            "runtime-action",
            (registry.AutomationParameterSpec("reason_code", "code"),),
            target,
        )
    )
    try:
        assert service._target_for_event(EVENT, "runtime-action", "rule-a") == (
            "runtime_target",
            "rule-a-target",
            None,
        )
        assert seen[0]["safe_facts"] == {"task_id": "task-a"}
        with pytest.raises(
            service.EnterpriseAutomationWorkflowsInvalid, match="forbidden|unsafe"
        ):
            service._target_for_event(
                {**EVENT, "safe_facts": {"url": "https://unsafe.example"}},
                "runtime-action",
                "rule-a",
            )
        assert len(seen) == 1
        with pytest.raises(authority.AutomationAuthorityInvalid, match="action_code"):
            authority.canonical_automation_action_plan(
                [
                    {
                        "action_code": "runtime-action",
                        "params": {"reason_code": "probe"},
                    }
                ]
            )
    finally:
        registry.unregister_action_strategy("runtime-action")


def test_dispatch_bypass_fails_the_dynamic_condition_path(monkeypatch: pytest.MonkeyPatch) -> None:
    registry.register_condition_strategy(
        registry.AutomationConditionStrategy(
            "reverse_probe",
            (),
            lambda _params, _event: True,
        )
    )
    try:
        monkeypatch.setattr(authority, "resolve_condition_strategy", lambda _code: None)
        with pytest.raises(authority.AutomationAuthorityInvalid, match="condition_code"):
            authority.evaluate_automation_condition("reverse_probe", {}, EVENT)
    finally:
        registry.unregister_condition_strategy("reverse_probe")


def test_registry_core_api_and_orm_contracts_are_independently_aligned() -> None:
    assert registry.condition_strategy_names(builtin_only=True) == (
        "always",
        "status_is",
        "action_required",
        "severity_at_least",
        "attempt_exhausted",
        "source_is_stale",
    )
    assert set(get_args(AutomationConditionCode)) == authority.AUTOMATION_CONDITION_CODES
    assert set(get_args(AutomationActionCode)) == authority.AUTOMATION_ACTION_CODES

    def constraint_values(model: Any, name: str) -> set[str]:
        constraint = next(item for item in model.__table__.constraints if item.name == name)
        return set(re.findall(r"'([^']+)'", str(constraint.sqltext)))

    assert constraint_values(
        TenantAutomationRuleRevision,
        "ck_tenant_automation_rule_revisions_condition",
    ) == authority.AUTOMATION_CONDITION_CODES
    assert constraint_values(
        TenantAutomationActionRequest,
        "ck_tenant_automation_action_requests_action",
    ) == authority.AUTOMATION_ACTION_CODES


def test_runtime_strategy_results_are_checked_before_they_enter_domain_paths() -> None:
    registry.register_condition_strategy(
        registry.AutomationConditionStrategy(
            "bad_result",
            (),
            lambda _params, _event: 1,  # type: ignore[return-value]
        )
    )
    try:
        with pytest.raises(authority.AutomationAuthorityInvalid, match="boolean"):
            authority.evaluate_automation_condition("bad_result", {}, EVENT)
    finally:
        registry.unregister_condition_strategy("bad_result")

    registry.register_action_strategy(
        registry.AutomationActionStrategy(
            "bad_target",
            (),
            lambda _event, _rule_id: object(),  # type: ignore[return-value]
        )
    )
    try:
        with pytest.raises(service.EnterpriseAutomationWorkflowsInvalid, match="reference"):
            service._target_for_event(EVENT, "bad_target", "rule-a")
    finally:
        registry.unregister_action_strategy("bad_target")


def test_registry_rejects_duplicate_invalid_and_async_strategies_at_registration() -> None:
    with pytest.raises(ValueError, match="reserved"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy("always", (), lambda _p, _e: True)
        )
    with pytest.raises(ValueError, match="duplicate condition parameter"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy(
                "duplicate_probe",
                (
                    registry.AutomationParameterSpec("probe", "code"),
                    registry.AutomationParameterSpec("probe", "code"),
                ),
                lambda _p, _e: True,
            )
        )
    with pytest.raises(ValueError, match="lowercase"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy("RuntimeProbe", (), lambda _p, _e: True)
        )
    with pytest.raises(TypeError, match="declared positional context"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy("bad_signature", (), lambda _p: True)
        )
    with pytest.raises(ValueError, match="only code"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy(
                "bad_parameter_kind",
                (
                    registry.AutomationParameterSpec(
                        "probe", "integer", allowed_values=("one",)
                    ),
                ),
                lambda _p, _e: True,
            )
        )
    with pytest.raises(ValueError, match="after normalisation"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy(
                "duplicate_normalised_values",
                (
                    registry.AutomationParameterSpec(
                        "probe", "code", ("ready-now", "ready_now")
                    ),
                ),
                lambda _p, _e: True,
            )
        )

    async def async_evaluate(_params: dict[str, Any], _event: dict[str, Any]) -> bool:
        return True

    with pytest.raises(TypeError, match="synchronous"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy("async_probe", (), async_evaluate)
        )
    assert asyncio.iscoroutinefunction(async_evaluate)

    class AsyncCallable:
        async def __call__(
            self, _params: dict[str, Any], _event: dict[str, Any]
        ) -> bool:
            return True

    with pytest.raises(TypeError, match="synchronous"):
        registry.register_condition_strategy(
            registry.AutomationConditionStrategy("async_callable", (), AsyncCallable())
        )

    async def async_target(
        _event: dict[str, Any], _rule_id: str
    ) -> registry.AutomationTargetReference:
        return registry.AutomationTargetReference("runtime", "target")

    with pytest.raises(TypeError, match="synchronous"):
        registry.register_action_strategy(
            registry.AutomationActionStrategy("async_action", (), async_target)
        )


def test_api_consumes_identifier_declarations_for_action_and_condition_shapes() -> None:
    with pytest.raises(ValueError, match="invalid"):
        ActionPlanStep(
            action_code="open_task_attention",
            params={"task_id": "task id", "reason_code": "attention"},
        )
    with pytest.raises(ValueError, match="invalid"):
        ActionPlanStep(
            action_code="open_task_attention",
            params={"task_id": "a" * 129, "reason_code": "attention"},
        )

    registry.register_condition_strategy(
        registry.AutomationConditionStrategy(
            "runtime-identifier",
            (registry.AutomationParameterSpec("resource", "identifier"),),
            lambda _params, _event: True,
        )
    )
    try:
        with pytest.raises(ValueError, match="invalid"):
            automation_api._validate_condition_params(
                "runtime-identifier", {"resource": "not a resource"}
            )
    finally:
        registry.unregister_condition_strategy("runtime-identifier")

    registry.register_condition_strategy(
        registry.AutomationConditionStrategy(
            "runtime-integer",
            (registry.AutomationParameterSpec("attempts", "integer"),),
            lambda _params, _event: True,
        )
    )
    try:
        with pytest.raises(ValueError, match="invalid"):
            automation_api._validate_condition_params(
                "runtime-integer", {"attempts": -1}
            )
    finally:
        registry.unregister_condition_strategy("runtime-integer")


def test_builtin_registry_tampering_is_detected_and_builtin_unregistration_is_rejected() -> None:
    with pytest.raises(ValueError, match="reserved"):
        registry.unregister_condition_strategy("always")
    with pytest.raises(ValueError, match="reserved"):
        registry.unregister_action_strategy("pause_rule")

    condition_registry = registry._CONDITION_STRATEGY_REGISTRY
    original = condition_registry.get_factory("always")
    condition_registry.unregister("always")
    try:
        with pytest.raises(TypeError, match="was unregistered"):
            registry.resolve_condition_strategy("always")
    finally:
        condition_registry.register("always", original)

    action_registry = registry._ACTION_STRATEGY_REGISTRY
    original_action = action_registry.get_factory("pause_rule")
    action_registry.register("pause_rule", lambda _config: original_action(None), replace=True)
    try:
        with pytest.raises(TypeError, match="contract was replaced"):
            registry.resolve_action_strategy("pause_rule")
    finally:
        action_registry.register("pause_rule", original_action, replace=True)


def test_api_uses_strategy_declarations_instead_of_a_second_code_map() -> None:
    source = Path("server/enterprise_automation_workflows_api.py").read_text(encoding="utf-8")
    assert "allowed_by_action" not in source
    assert "allowed_by_condition" not in source
