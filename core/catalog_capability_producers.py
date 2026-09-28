"""Shared revision-aware capability inspection strategy for the catalog."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
import inspect as python_inspect
from typing import Any, Literal

from sqlalchemy import inspect, text

CapabilityResult = tuple[str, tuple[str, ...]]
IssueChecker = Callable[..., tuple[str, ...]]
FallbackInspector = Callable[[Any], CapabilityResult]
SchemaConnectionRunner = Callable[
    [Any, Callable[[Any], CapabilityResult | None]], CapabilityResult | None
]
KnownRevisions = Callable[[], Collection[str]]
RevisionOrder = Callable[[str], int]


@dataclass(frozen=True)
class CatalogCapabilityPolicy:
    """Declarative inputs for the shared revision/dialect capability ladder."""

    minimum_revision: str
    required_tables: frozenset[str]
    supported_dialects: frozenset[str] | None
    capability_label: str
    minimum_revision_issue: str
    inspection_error_prefix: str
    issue_checker: IssueChecker
    revision_aware: bool = False
    missing_revision_issue: str | None = None
    unknown_revision_issue: str | None = None
    before_minimum_revision_issue: str | None = None
    missing_tables_at_minimum_issue: str | None = None
    missing_tables_at_minimum_state: Literal["not_available", "unavailable"] = "not_available"
    missing_tables_after_minimum: Literal["inspect", "not_available", "unavailable"] = "inspect"
    missing_tables_after_minimum_issue: str | None = None
    inspection_error_mode: Literal["unavailable", "raise"] = "unavailable"
    fallback_inspector: FallbackInspector | None = None


def validate_catalog_capability_policy(policy: CatalogCapabilityPolicy) -> None:
    """Reject malformed policies before they can become runtime producers."""

    if not isinstance(policy, CatalogCapabilityPolicy):
        raise TypeError("catalog capability policy must be CatalogCapabilityPolicy")
    for name in (
        "minimum_revision",
        "capability_label",
        "minimum_revision_issue",
        "inspection_error_prefix",
    ):
        value = getattr(policy, name)
        if type(value) is not str or not value.strip():
            raise ValueError(f"catalog capability policy {name} must be a non-empty string")
    if (
        not isinstance(policy.required_tables, frozenset)
        or not policy.required_tables
        or any(type(table) is not str or not table.strip() for table in policy.required_tables)
    ):
        raise ValueError("catalog capability policy required_tables must be a non-empty frozenset")
    if policy.supported_dialects is not None and (
        not isinstance(policy.supported_dialects, frozenset)
        or not policy.supported_dialects
        or any(
            type(dialect) is not str or not dialect.strip() for dialect in policy.supported_dialects
        )
    ):
        raise ValueError(
            "catalog capability policy supported_dialects must be None or a non-empty frozenset"
        )
    for name in (
        "missing_revision_issue",
        "unknown_revision_issue",
        "before_minimum_revision_issue",
        "missing_tables_at_minimum_issue",
        "missing_tables_after_minimum_issue",
    ):
        value = getattr(policy, name)
        if value is not None and (type(value) is not str or not value.strip()):
            raise ValueError(f"catalog capability policy {name} must be None or a non-empty string")
    if policy.missing_tables_at_minimum_state not in {"not_available", "unavailable"}:
        raise ValueError(
            "catalog capability policy missing_tables_at_minimum_state must be "
            "'not_available' or 'unavailable'"
        )
    if policy.missing_tables_after_minimum not in {
        "inspect",
        "not_available",
        "unavailable",
    }:
        raise ValueError(
            "catalog capability policy missing_tables_after_minimum must be "
            "'inspect', 'not_available' or 'unavailable'"
        )
    if (
        policy.missing_tables_after_minimum == "unavailable"
        and not policy.missing_tables_after_minimum_issue
    ):
        raise ValueError(
            "catalog capability policy missing_tables_after_minimum_issue is required "
            "when missing_tables_after_minimum='unavailable'"
        )
    if policy.inspection_error_mode not in {"unavailable", "raise"}:
        raise ValueError(
            "catalog capability policy inspection_error_mode must be 'unavailable' or 'raise'"
        )
    fallback = policy.fallback_inspector
    if fallback is not None:
        if not callable(fallback):
            raise TypeError("catalog capability policy fallback_inspector must be callable")
        fallback_methods = (fallback, getattr(fallback, "__call__", None))
        if any(
            python_inspect.iscoroutinefunction(method)
            or python_inspect.isasyncgenfunction(method)
            or python_inspect.isgeneratorfunction(method)
            for method in fallback_methods
            if method is not None
        ):
            raise ValueError("catalog capability policy fallback_inspector must be synchronous")
        try:
            python_inspect.signature(fallback).bind(object())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "catalog capability policy fallback_inspector must accept one positional bind"
            ) from exc
    if type(policy.revision_aware) is not bool:
        raise ValueError("catalog capability policy revision_aware must be a bool")
    if not callable(policy.issue_checker):
        raise TypeError("catalog capability policy issue_checker must be callable")
    checker_call = getattr(policy.issue_checker, "__call__", None)
    if (
        python_inspect.iscoroutinefunction(policy.issue_checker)
        or python_inspect.isasyncgenfunction(policy.issue_checker)
        or python_inspect.iscoroutinefunction(checker_call)
        or python_inspect.isasyncgenfunction(checker_call)
    ):
        raise ValueError("catalog capability policy issue_checker must be synchronous")
    try:
        signature = python_inspect.signature(policy.issue_checker)
        if policy.revision_aware:
            signature.bind(object(), "revision")
        else:
            signature.bind(object())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "catalog capability policy issue_checker has an incompatible signature"
        ) from exc


def build_catalog_capability_producer(
    policy: CatalogCapabilityPolicy,
    *,
    schema_connection: SchemaConnectionRunner,
    known_revisions: KnownRevisions,
    revision_order: RevisionOrder,
) -> Callable[[Any], CapabilityResult]:
    """Build a fail-closed inspector while keeping policy-specific data declarative."""

    validate_catalog_capability_policy(policy)
    if (
        not callable(schema_connection)
        or not callable(known_revisions)
        or not callable(revision_order)
    ):
        raise TypeError("catalog capability runtime dependencies must be callable")
    known = known_revisions()
    minimum_revision_order = revision_order(policy.minimum_revision)
    if policy.minimum_revision not in known or minimum_revision_order < 0:
        raise ValueError(
            "catalog capability policy minimum_revision must have a known catalog revision order"
        )

    def produce(bind: Any) -> CapabilityResult:
        def inspect_connection(connection: Any) -> CapabilityResult | None:
            dialect = str(getattr(getattr(connection, "dialect", None), "name", "")).casefold()
            if policy.supported_dialects is not None and dialect not in policy.supported_dialects:
                return "unavailable", (
                    f"unsupported database dialect for {policy.capability_label}: "
                    f"{dialect or 'unknown'}",
                )

            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            capability_present = bool(policy.required_tables & tables)
            if "alembic_version" not in tables:
                if policy.fallback_inspector is not None:
                    return None
                return "unavailable", (
                    policy.missing_revision_issue or policy.minimum_revision_issue,
                )
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
            if len(revisions) > 1:
                if policy.fallback_inspector is not None:
                    return None
                return "unavailable", ("alembic_version contains multiple revisions",)
            if not revisions:
                if policy.fallback_inspector is not None:
                    return None
                return "unavailable", (
                    policy.missing_revision_issue or policy.minimum_revision_issue,
                )

            revision = revisions[0]
            revision_known = revision in known_revisions()
            if not revision_known:
                if policy.fallback_inspector is not None:
                    return None
                return "unavailable", (
                    policy.unknown_revision_issue or policy.minimum_revision_issue,
                )
            if revision_order(revision) < revision_order(policy.minimum_revision):
                if policy.fallback_inspector is not None:
                    return None
                if not capability_present and revision_known:
                    return "not_available", ()
                return "unavailable", (
                    policy.before_minimum_revision_issue or policy.minimum_revision_issue,
                )

            if not capability_present:
                if revision == policy.minimum_revision and policy.missing_tables_at_minimum_issue:
                    return (
                        policy.missing_tables_at_minimum_state,
                        (policy.missing_tables_at_minimum_issue,),
                    )
                if policy.missing_tables_after_minimum == "not_available":
                    return "not_available", ()
                if policy.missing_tables_after_minimum == "unavailable":
                    assert policy.missing_tables_after_minimum_issue is not None
                    return "unavailable", (policy.missing_tables_after_minimum_issue,)

            issues = (
                policy.issue_checker(connection, revision)
                if policy.revision_aware
                else policy.issue_checker(connection)
            )
            if not isinstance(issues, tuple) or any(type(issue) is not str for issue in issues):
                raise TypeError("catalog capability issue_checker must return tuple[str, ...]")
            return ("ready", ()) if not issues else ("unavailable", issues)

        try:
            result = schema_connection(bind, inspect_connection)
        except Exception as exc:
            if policy.inspection_error_mode == "raise":
                raise
            return "unavailable", (f"{policy.inspection_error_prefix}: {exc.__class__.__name__}",)
        if result is None:
            if policy.fallback_inspector is None:
                raise RuntimeError("catalog capability producer returned no result")
            fallback_result = policy.fallback_inspector(bind)
            if (
                not isinstance(fallback_result, tuple)
                or len(fallback_result) != 2
                or type(fallback_result[0]) is not str
                or not isinstance(fallback_result[1], tuple)
                or any(type(issue) is not str for issue in fallback_result[1])
            ):
                raise TypeError("catalog capability fallback_inspector returned an invalid result")
            return fallback_result
        return result

    return produce


__all__ = [
    "CatalogCapabilityPolicy",
    "build_catalog_capability_producer",
    "validate_catalog_capability_policy",
]
