"""Pure, Tenant-safe Enterprise Automation & Workflow authority primitives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
import re
from typing import Any
from urllib.parse import urlencode

from core.automation_rule_strategies import (
    AUTOMATION_ACTION_CONTRACT_CODES,
    AUTOMATION_CONDITION_CONTRACT_CODES,
    AUTOMATION_SEVERITY_ORDER,
    AUTOMATION_STATUS_CODES,
    AutomationTargetReference,
    resolve_action_strategy,
    resolve_condition_strategy,
)

UTC = timezone.utc
AUTOMATION_SCHEMA_VERSION = 1
AUTOMATION_TRIGGER_CODES = frozenset(
    {
        "task_failed",
        "task_source_stale",
        "source_sync_failed",
        "release_quality_alert_opened",
        "release_recertification_blocked",
        "approval_request_terminal",
    }
)
AUTOMATION_EVENT_TYPES = frozenset(
    {
        "rule_created",
        "revision_created",
        "revision_activated",
        "rule_paused",
        "trigger_observed",
        "condition_not_matched",
        "run_started",
        "action_requested",
        "action_rejected",
        "run_completed",
        "run_failed",
    }
)
AUTOMATION_CONDITION_CODES = frozenset(AUTOMATION_CONDITION_CONTRACT_CODES)
AUTOMATION_ACTION_CODES = frozenset(AUTOMATION_ACTION_CONTRACT_CODES)
_SEVERITIES = AUTOMATION_SEVERITY_ORDER
_STATUSES = AUTOMATION_STATUS_CODES
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_URL = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://|(?:^|\s)www\.)\S+")
_SECRET = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|bearer|access[_ -]?token|"
    r"refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)"
    r"\s*[:=]\s*\S+"
)
_SQL_LIKE = re.compile(
    r"(?is)(?:"
    r"\bselect\s+(?:distinct\s+)?[\w*\"`'([]"
    r"|\binsert\s+(?:into\s+)?[\w\"`'(]"
    r"|\bupdate\s+[\w\"`.]+\s+set\b"
    r"|\bdelete\s+from\b"
    r"|\bdrop\s+(?:table|database|schema|index|view|trigger|procedure|function)\b"
    r"|\balter\s+(?:table|database|schema|index|view|trigger|procedure|function)\b"
    r"|\bcreate\s+(?:table|database|schema|index|view|trigger|procedure|function)\b"
    r"|\bgrant\s+\w+\s+on\b"
    r"|\brevoke\s+\w+\s+on\b"
    # 写成 \bex(?:ec|ecute) 而不是 \bexec(?:ute)?：两者匹配的字符串完全一致
    # （exec / execute），但不含会被扫描器当成 exec( 调用签名的字面子串。
    r"|\bex(?:ec|ecute)\s+\S+"
    r"|\bunion\s+(?:all\s+)?select\b"
    r"|\b(?:select|insert|update|delete|drop|alter|create|grant|revoke|"
    r"ex(?:ec|ecute)|union)\s*$"
    r")"
)
_BEARER = re.compile(r"(?i)\bbearer\b")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])"
)
_MAX_SAFE_DEPTH = 12
_MAX_SAFE_ITEMS = 64
_MAX_SAFE_UTF8_BYTES = 16_384
_FORBIDDEN_PARTS = frozenset(
    {
        "sql",
        "query",
        "url",
        "uri",
        "href",
        "webhook",
        "code",
        "script",
        "expression",
        "eval",
        "exec",
        "prompt",
        "body",
        "content",
        "raw",
        "payload",
        "token",
        "credential",
        "ticket",
        "password",
        "secret",
        "authorization",
        "bearer",
        "cookie",
        "header",
        "answer",
        "apikey",
        "accesskey",
    }
)


class AutomationAuthorityError(ValueError):
    code = "enterprise_automation_authority_invalid"
    status = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AutomationAuthorityInvalid(AutomationAuthorityError):
    pass


EnterpriseAutomationError = AutomationAuthorityError
EnterpriseAutomationInvalid = AutomationAuthorityInvalid


def _invalid(message: str) -> AutomationAuthorityInvalid:
    return AutomationAuthorityInvalid(message)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{field} must be an object")
    return dict(value)


def _exact_keys(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    raw = _mapping(value, field)
    extras = set(raw) - allowed
    if extras:
        raise _invalid(f"{field} contains unsupported fields: {', '.join(sorted(extras))}")
    return raw


def _normalized_key(key: str) -> str:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    return re.sub(r"[^a-z0-9]+", "_", separated.casefold()).strip("_")


def _forbidden_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if normalized in {
        "trigger_code",
        "condition_code",
        "action_code",
        "event_type",
        "error_code",
        "reason_code",
        "status_code",
    }:
        return False
    compact = normalized.replace("_", "")
    if "code" in compact or compact.startswith("raw"):
        return True
    return any(part in compact for part in _FORBIDDEN_PARTS - {"code", "raw"})


def _safe_text(value: Any, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or _CONTROL.search(result):
        raise _invalid(f"{field} is invalid")
    if _URL.search(result) or _SECRET.search(result) or _SQL_LIKE.search(result):
        raise _invalid(f"{field} is unsafe")
    if _BEARER.search(result) or _JWT.search(result):
        raise _invalid(f"{field} is unsafe")
    return result


def _identifier(value: Any, field: str, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum)
    if len(result) > maximum or _IDENTIFIER.fullmatch(result) is None or ".." in result:
        raise _invalid(f"{field} is invalid")
    return result


def _code(value: Any, field: str, allowed: frozenset[str] | None = None) -> str:
    result = _safe_text(value, field, 128).casefold().replace("-", "_").replace(" ", "_")
    if _CODE.fullmatch(result) is None or (allowed is not None and result not in allowed):
        raise _invalid(f"{field} is not allowed")
    return result


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _invalid(f"{field} must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise _invalid(f"{field} must be an exact integer")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise _invalid(f"{field} must be a boolean")
    return value


def _timestamp(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise _invalid(f"{field} must be an ISO timestamp") from exc
    else:
        raise _invalid(f"{field} must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_value(
    value: Any, field: str, depth: int = 0, item_count: list[int] | None = None
) -> Any:
    if depth > _MAX_SAFE_DEPTH:
        raise _invalid(f"{field} is too deeply nested")
    budget = item_count if item_count is not None else [0]
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, datetime):
        return _timestamp(value, field)
    if isinstance(value, str):
        return _safe_text(value, field, 2048)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_SAFE_ITEMS:
            raise _invalid(f"{field} contains too many items")
        budget[0] += len(value)
        if budget[0] > _MAX_SAFE_ITEMS:
            raise _invalid(f"{field} contains too many items")
        return [
            _safe_value(item, f"{field}[{index}]", depth + 1, budget)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        if len(value) > _MAX_SAFE_ITEMS:
            raise _invalid(f"{field} contains too many fields")
        budget[0] += len(value)
        if budget[0] > _MAX_SAFE_ITEMS:
            raise _invalid(f"{field} contains too many fields")
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key or _forbidden_key(key):
                raise _invalid(f"{field} contains a forbidden field")
            result[key] = _safe_value(nested, f"{field}.{key}", depth + 1, budget)
        return {key: result[key] for key in sorted(result)}
    raise _invalid(f"{field} contains an unsupported value")


def _safe_json_value(value: Any, field: str) -> Any:
    canonical = _safe_value(value, field)
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid(f"{field} contains an unsupported value") from exc
    if len(encoded) > _MAX_SAFE_UTF8_BYTES:
        raise _invalid(f"{field} exceeds the UTF-8 byte limit")
    return canonical


def _canonical_digest(namespace: str, value: Mapping[str, Any]) -> str:
    canonical = _safe_json_value(value, namespace)
    encoded = json.dumps(
        {"namespace": namespace, "schema_version": AUTOMATION_SCHEMA_VERSION, "value": canonical},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def canonical_automation_event_digest(value: Mapping[str, Any]) -> str:
    return _canonical_digest("automation-event", value)


def canonical_automation_action_request_digest(value: Mapping[str, Any]) -> str:
    return _canonical_digest("automation-action-request", value)


def canonical_automation_run_digest(value: Mapping[str, Any]) -> str:
    return _canonical_digest("automation-run", value)


def _normalize_strategy_parameter(
    parameter: Any,
    value: Any,
    field: str,
) -> Any:
    if parameter.kind == "boolean":
        return _boolean(value, field)
    if parameter.kind == "integer":
        return _integer(
            value,
            field,
            0 if parameter.minimum is None else parameter.minimum,
            parameter.maximum,
        )
    if parameter.kind == "identifier":
        return _identifier(value, field)
    if parameter.kind == "text":
        return _safe_text(
            value,
            field,
            512 if parameter.maximum_length is None else parameter.maximum_length,
        )
    allowed = (
        frozenset(
            item.casefold().replace("-", "_").replace(" ", "_")
            for item in parameter.allowed_values
        )
        if parameter.allowed_values
        else None
    )
    return _code(value, field, allowed)


def _reject_deferred_result(value: Any, message: str) -> None:
    if not inspect.isawaitable(value):
        return
    close = getattr(value, "close", None)
    if callable(close):
        close()
    raise _invalid(message)


def _condition_params(condition_code: str, value: Any) -> dict[str, Any]:
    strategy = resolve_condition_strategy(condition_code)
    if strategy is None:
        raise _invalid("condition_code is not allowed")
    parameters = strategy.parameters
    raw = _exact_keys(value, "condition_params", {parameter.name for parameter in parameters})
    return {
        parameter.name: _normalize_strategy_parameter(
            parameter,
            raw.get(parameter.name),
            f"condition_params.{parameter.name}",
        )
        for parameter in parameters
    }


def _action_params(action_code: str, value: Any) -> dict[str, Any]:
    strategy = resolve_action_strategy(action_code)
    if strategy is None:
        raise _invalid("action_code is not allowed")
    raw = _mapping(value, "action.params")
    if any(not isinstance(key, str) or _forbidden_key(key) for key in raw):
        raise _invalid("action.params contains a forbidden field")
    parameters = strategy.parameters
    allowed = {parameter.name for parameter in parameters}
    extras = set(raw) - allowed
    if extras:
        raise _invalid("action.params contains unsupported fields")
    return {
        parameter.name: _normalize_strategy_parameter(
            parameter,
            raw.get(parameter.name),
            f"action.params.{parameter.name}",
        )
        for parameter in parameters
    }


def canonical_automation_action_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _invalid("action_plan must contain 1 to 4 actions")
    if not 1 <= len(value) <= 4:
        raise _invalid("action_plan must contain 1 to 4 actions")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        raw = _exact_keys(item, f"action_plan[{index}]", {"action_code", "params"})
        action_code = _code(raw.get("action_code"), "action_code", AUTOMATION_ACTION_CODES)
        result.append(
            {
                "step_index": index,
                "action_code": action_code,
                "params": _action_params(action_code, raw.get("params", {})),
            }
        )
    return result


def canonical_automation_rule_revision(
    value: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = dict(value or {})
    raw.update(fields)
    raw = _exact_keys(
        raw,
        "automation_rule_revision",
        {
            "tenant_id",
            "rule_id",
            "rule_revision_id",
            "revision",
            "trigger_code",
            "condition_code",
            "condition_params",
            "action_plan",
            "created_at",
            "created_by",
            "definition_digest",
        },
    )
    trigger_code = _code(raw.get("trigger_code"), "trigger_code", AUTOMATION_TRIGGER_CODES)
    condition_code = _code(raw.get("condition_code"), "condition_code", AUTOMATION_CONDITION_CODES)
    result: dict[str, Any] = {
        "tenant_id": _identifier(raw.get("tenant_id"), "tenant_id", 64),
        "rule_id": _identifier(raw.get("rule_id"), "rule_id"),
        "rule_revision_id": _identifier(raw.get("rule_revision_id"), "rule_revision_id"),
        "revision": _integer(raw.get("revision"), "revision", 1),
        "trigger_code": trigger_code,
        "condition_code": condition_code,
        "condition_params": _condition_params(condition_code, raw.get("condition_params", {})),
        "action_plan": canonical_automation_action_plan(raw.get("action_plan")),
        "created_at": _timestamp(raw.get("created_at"), "created_at"),
        "created_by": _identifier(raw.get("created_by"), "created_by", 64),
    }
    computed = _canonical_digest("automation-rule-definition", result)
    supplied = raw.get("definition_digest")
    if supplied is not None and _digest(supplied, "definition_digest") != computed:
        raise _invalid("definition_digest does not match canonical rule definition")
    result["definition_digest"] = computed
    return result


def canonical_automation_trigger_event(
    value: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = dict(value or {})
    raw.update(fields)
    raw = _exact_keys(
        raw,
        "automation_trigger_event",
        {
            "tenant_id",
            "trigger_code",
            "source_stream_id",
            "source_event_id",
            "source_event_digest",
            "sequence",
            "status",
            "severity",
            "action_required",
            "attempt_number",
            "max_attempts",
            "source_current",
            "occurred_at",
            "safe_facts",
        },
    )
    severity = _code(raw.get("severity"), "severity")
    if severity not in _SEVERITIES:
        raise _invalid("severity is not allowed")
    status = _code(raw.get("status"), "status")
    if status not in _STATUSES:
        raise _invalid("status is not allowed")
    attempt = _integer(raw.get("attempt_number"), "attempt_number", 0, 1000)
    maximum = _integer(raw.get("max_attempts"), "max_attempts", 1, 1000)
    if attempt > maximum:
        raise _invalid("attempt_number exceeds max_attempts")
    return {
        "tenant_id": _identifier(raw.get("tenant_id"), "tenant_id", 64),
        "trigger_code": _code(raw.get("trigger_code"), "trigger_code", AUTOMATION_TRIGGER_CODES),
        "source_stream_id": _identifier(raw.get("source_stream_id"), "source_stream_id"),
        "source_event_id": _identifier(raw.get("source_event_id"), "source_event_id"),
        "source_event_digest": _digest(raw.get("source_event_digest"), "source_event_digest"),
        "sequence": _integer(raw.get("sequence"), "sequence", 1),
        "status": status,
        "severity": severity,
        "action_required": _boolean(raw.get("action_required"), "action_required"),
        "attempt_number": attempt,
        "max_attempts": maximum,
        "source_current": _boolean(raw.get("source_current"), "source_current"),
        "occurred_at": _timestamp(raw.get("occurred_at"), "occurred_at"),
        "safe_facts": _safe_json_value(raw.get("safe_facts", {}), "safe_facts"),
    }


def evaluate_automation_condition(
    condition_code: str, condition_params: Mapping[str, Any], event: Mapping[str, Any]
) -> bool:
    code = _code(condition_code, "condition_code")
    strategy = resolve_condition_strategy(code)
    if strategy is None:
        raise _invalid("condition_code is not allowed")
    params = _condition_params(code, condition_params)
    canonical_event = canonical_automation_trigger_event(event)
    try:
        result = strategy.evaluator(params, canonical_event)
    except AutomationAuthorityError:
        raise
    except Exception as exc:
        raise _invalid("condition strategy is unavailable") from exc
    _reject_deferred_result(result, "condition strategy must be synchronous")
    if type(result) is not bool:
        raise _invalid("condition strategy must return a boolean")
    return result


def resolve_automation_action_target(
    event: Mapping[str, Any], action_code: str, rule_id: str
) -> tuple[str, str, str | None]:
    """Resolve an action target through its registered side-effect-free adapter."""

    code = _code(action_code, "action_code")
    strategy = resolve_action_strategy(code)
    if strategy is None:
        raise _invalid("action_code is not allowed")
    canonical_event = canonical_automation_trigger_event(event)
    try:
        reference = strategy.target_resolver(canonical_event, rule_id)
    except AutomationAuthorityError:
        raise
    except Exception as exc:
        raise _invalid("action target strategy is unavailable") from exc
    _reject_deferred_result(reference, "action target strategy must be synchronous")
    if not isinstance(reference, AutomationTargetReference):
        raise _invalid("action target strategy returned an invalid reference")
    target_kind = _code(reference.target_kind, "target_kind")
    target_id = _identifier(reference.target_id, "target_id")
    task_id = (
        None
        if reference.task_id is None
        else _identifier(reference.task_id, "task_id")
    )
    return target_kind, target_id, task_id


def preview_automation_rule(
    rule_revision: Mapping[str, Any], trigger_event: Mapping[str, Any]
) -> dict[str, Any]:
    revision = canonical_automation_rule_revision(rule_revision)
    event = canonical_automation_trigger_event(trigger_event)
    if revision["tenant_id"] != event["tenant_id"]:
        raise _invalid("automation preview crossed Tenant scope")
    if revision["trigger_code"] != event["trigger_code"]:
        raise _invalid("automation preview trigger does not match rule")
    matched = evaluate_automation_condition(
        revision["condition_code"], revision["condition_params"], event
    )
    return {
        "tenant_id": revision["tenant_id"],
        "rule_id": revision["rule_id"],
        "rule_revision_id": revision["rule_revision_id"],
        "trigger_event_id": event["source_event_id"],
        "trigger_event_digest": event["source_event_digest"],
        "matched": matched,
        "action_requests": revision["action_plan"] if matched else [],
        "side_effects_performed": False,
        "dispatch_attempted": False,
    }


def canonical_automation_event(
    value: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = dict(value or {})
    raw.update(fields)
    raw = _exact_keys(
        raw,
        "automation_event",
        {
            "tenant_id",
            "rule_id",
            "run_id",
            "stream_key",
            "sequence",
            "event_type",
            "previous_event_digest",
            "event_digest",
            "actor_id",
            "request_id",
            "safe_snapshot",
            "occurred_at",
        },
    )
    sequence = _integer(raw.get("sequence"), "sequence", 1)
    event_type = _code(raw.get("event_type"), "event_type", AUTOMATION_EVENT_TYPES)
    previous = raw.get("previous_event_digest")
    if sequence == 1:
        if event_type not in {"rule_created", "run_started"}:
            raise _invalid("first event type is invalid")
        if previous is not None:
            raise _invalid("first event must not have previous digest")
    elif previous is None:
        raise _invalid("non-first event requires previous digest")
    elif previous is not None:
        previous = _digest(previous, "previous_event_digest")
    result: dict[str, Any] = {
        "tenant_id": _identifier(raw.get("tenant_id"), "tenant_id", 64),
        "rule_id": _identifier(raw.get("rule_id"), "rule_id"),
        "run_id": None if raw.get("run_id") is None else _identifier(raw.get("run_id"), "run_id"),
        "stream_key": _identifier(raw.get("stream_key"), "stream_key"),
        "sequence": sequence,
        "event_type": event_type,
        "previous_event_digest": previous,
        "actor_id": _identifier(raw.get("actor_id"), "actor_id", 64),
        "request_id": _identifier(raw.get("request_id"), "request_id", 64),
        "safe_snapshot": _safe_json_value(raw.get("safe_snapshot", {}), "safe_snapshot"),
        "occurred_at": _timestamp(raw.get("occurred_at"), "occurred_at"),
    }
    computed = canonical_automation_event_digest(result)
    supplied = raw.get("event_digest")
    if supplied is not None and _digest(supplied, "event_digest") != computed:
        raise _invalid("event_digest does not match canonical automation event")
    result["event_digest"] = computed
    return result


def project_automation_source_route(event: Mapping[str, Any]) -> dict[str, Any]:
    projected = canonical_automation_trigger_event(event)
    trigger = projected["trigger_code"]
    facts = projected["safe_facts"] if isinstance(projected["safe_facts"], Mapping) else {}
    if trigger in {"task_failed", "task_source_stale"}:
        path, code, query = "/enterprise/tasks", "enterprise_tasks", {}
        if isinstance(facts.get("task_id"), str):
            query["task"] = facts["task_id"]
    elif trigger == "source_sync_failed":
        path, code, query = "/sources", "knowledge_sources", {}
        if isinstance(facts.get("source_id"), str):
            query["source"] = facts["source_id"]
    elif trigger in {"release_quality_alert_opened", "release_recertification_blocked"}:
        path, code, query = "/enterprise/knowledge-base", "release_quality", {"section": "quality"}
    else:
        path, code, query = "/enterprise/approvals", "enterprise_approvals", {}
        if isinstance(facts.get("approval_request_id"), str):
            query["request"] = facts["approval_request_id"]
    encoded = urlencode(sorted(query.items()))
    return {
        "code": code,
        "path": path,
        "query": query,
        "href": f"{path}?{encoded}" if encoded else path,
    }


__all__ = [
    "AUTOMATION_ACTION_CODES",
    "AUTOMATION_CONDITION_CODES",
    "AUTOMATION_EVENT_TYPES",
    "AUTOMATION_TRIGGER_CODES",
    "AutomationAuthorityError",
    "AutomationAuthorityInvalid",
    "EnterpriseAutomationError",
    "EnterpriseAutomationInvalid",
    "canonical_automation_action_plan",
    "canonical_automation_action_request_digest",
    "canonical_automation_event",
    "canonical_automation_event_digest",
    "canonical_automation_rule_revision",
    "canonical_automation_run_digest",
    "canonical_automation_trigger_event",
    "evaluate_automation_condition",
    "preview_automation_rule",
    "project_automation_source_route",
    "resolve_automation_action_target",
]
