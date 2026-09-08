# Stage25 Enterprise Automation preflight research record

- **Date:** 2026-08-30
- **Revision:** `0035_enterprise_automation_workflows`
- **Down revision:** `0034_enterprise_task_operations`
- **Scope:** read-only catalog preflight and operator runbook only
- **Write set:** `scripts/enterprise_catalog_upgrade.py`, `tests/test_enterprise_catalog_upgrade.py`, `docs/operations/enterprise-catalog-upgrade.md`, this research record

## Decision

Stage25 reuses the Stage24 catalog-upgrade preflight pattern. The upgrade helper exposes a manifest-backed report for exactly six Tenant-scoped Automation tables:

1. `tenant_automation_rules`
2. `tenant_automation_rule_revisions`
3. `tenant_automation_source_cursors`
4. `tenant_automation_runs`
5. `tenant_automation_action_requests`
6. `tenant_automation_events`

The report performs only schema metadata inspection and `SELECT COUNT(*)`. It never observes a source event, advances a cursor, creates a Run or Action Request, dispatches an Action, or pauses a Rule.

## Evidence contract

The report includes:

- six-table `table_counts` and `counts`;
- `schema_capability_state` and `schema_capability_issues` from the Stage25 catalog manifest inspector;
- `read_only=true`;
- `mutations_performed=false`;
- `automatic_actions=[]`;
- `source_observation_performed=false`;
- `cursor_advanced=false`;
- `action_dispatches=[]`.

Missing 0035 tables or columns are reported as `schema_status=partial` and block the upgrade. Capability failures, revision mismatches, immutable guard failures, broken event-chain checks, and other manifest issues also fail closed. The preflight does not repair or backfill data.

## Database boundary

- **SQLite:** online only. Offline SQL generation is unsupported and must fail closed.
- **MySQL/Postgres:** offline DDL is review evidence only. DBA review must verify six tables, Tenant-leading composite constraints, bounded checks, immutable guards, event-chain validation, indexes, and downgrade protection before live execution.
- **Downgrade:** the six Stage25 authority tables use a nonempty downgrade blocker. Existing rule, revision, cursor, Run, Action Request, or Event facts must not be deleted to make downgrade pass.

## Explicit prohibitions

This preflight/runbook does not authorize or automate Run, Action dispatch, Rule pause, source observation, cursor advancement, notification delivery, approval execution, task retry/cancel, source synchronization, external webhook calls, restore, or rollback.
