# Stage 19 Enterprise Knowledge Base Release Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build immutable Knowledge Base Release Manifests, extensible Release Channels, approval-aware promotion/rollback, Application release binding and a persistent TDesign Knowledge Base resource shell.

**Architecture:** Revision `0029_enterprise_knowledge_base_releases` adds append-only release evidence and channel bindings on top of Stage18 Registry, existing Document Version/Source/Projection authorities and Dataset generation fences. The frontend preserves the Registry as a quick-view authority and wraps existing Dataset-scoped workspaces in one resource shell, adding a Release Center without duplicating existing Documents/Taxonomy/Sources/Governance implementations.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, FastAPI, Pydantic, SQLite/MySQL schema tests, React 18, TypeScript, TDesign React, Vitest, Playwright/agent-browser.

**Spec:** `docs/superpowers/specs/2026-08-28-enterprise-knowledge-base-release-control-plane-design.md`

## Global Constraints

- Target revision is exactly `0029_enterprise_knowledge_base_releases` with down revision `0028_enterprise_knowledge_base_registry`.
- `TenantWorkspace.environment` and Release Channel are separate authorities; never infer one from the other.
- Manifest content/entries are immutable; lifecycle evidence is append-only; channel bindings are revision-fenced mutable projections.
- Default channels are development/testing/production, but custom Tenant channels are supported.
- Manifest entries contain no document/QA bodies, raw cursor credentials, secrets or approval tickets.
- Production/default-serving promotion and rollback are approval-required by default.
- TDesign React/TDesign Icons are primary. Uiverse/Morphicons are fallback-only.
- Protect `frontend/src/retrieval-quality/**`, `core/retrieval_experiment_runner.py`, `server/retrieval_experiments_api.py`, `tests/test_retrieval_experiment_runner.py` and `tests/test_retrieval_experiments_api.py`.
- Do not reset, checkout, clean or format the cumulative worktree.
- No real production migration, manifest capture, promotion, rollback, source sync, ingestion, restore or external publication.

---

### Task 1: 0029 Release Database Authority

**Files:**
- Create: `catalog_migrations/versions/0029_enterprise_knowledge_base_releases.py`
- Modify: `models/orm.py`
- Modify: `core/catalog_schema.py`
- Modify: `server/enterprise_readiness_api.py`
- Test: `tests/test_enterprise_knowledge_base_release_migration.py`
- Test: `tests/test_catalog_schema.py`
- Test: `tests/test_enterprise_readiness_api.py`

**Interfaces:**
- Produces ORM models `TenantReleaseChannel`, `DatasetReleaseManifest`, `DatasetReleaseEntry`, `DatasetReleaseEvent`, `DatasetChannelRelease`.
- Extends `Dataset` with `serving_release_id` and `release_revision`.
- Extends `AppDatasetReference` with `release_mode`, `release_channel_id`, `pinned_release_id`.
- Produces Catalog capability constant `0029_enterprise_knowledge_base_releases`.

- [ ] **Step 1: Write RED table/constraint tests**

```python
def test_0029_release_tables_have_tenant_leading_authority() -> None:
    tables = inspect(upgraded_engine)
    assert exact_columns(tables["tenant_release_channels"]) == EXPECTED_CHANNEL_COLUMNS
    assert exact_columns(tables["dataset_release_manifests"]) == EXPECTED_MANIFEST_COLUMNS
    assert exact_columns(tables["dataset_release_entries"]) == EXPECTED_ENTRY_COLUMNS
    assert exact_columns(tables["dataset_release_events"]) == EXPECTED_EVENT_COLUMNS
    assert exact_columns(tables["dataset_channel_releases"]) == EXPECTED_BINDING_COLUMNS
```

Require exact CHECK SQL for status/risk/release mode, positive revisions, SHA-256 lowercase hex, active-slot XOR and default-serving uniqueness.

- [ ] **Step 2: Run RED migration tests**

Run:

```powershell
uv run pytest -q tests/test_enterprise_knowledge_base_release_migration.py
```

Expected: fail because revision/tables do not exist.

- [ ] **Step 3: Implement migration**

Create all five tables, composite Tenant/Dataset/Channel/Release FKs, indexes and dataset/reference columns. Seed three default channels per existing Tenant deterministically using IDs derived from Tenant + channel code. Add approval actions:

```text
knowledge_base_release_publish
knowledge_base_release_rollback
```

- [ ] **Step 4: Add immutability/downgrade contracts**

Published Manifest content, entries and events reject update/delete. Downgrade refuses while any Manifest/event/binding/App pin exists.

- [ ] **Step 5: Add ORM and Catalog/Readiness evidence**

`inspect_release_capability(connection)` must check tables, columns, constraints, indexes, default channels, active-binding uniqueness and compatibility columns. Partial 0029 is `unavailable`, never `limited`.

- [ ] **Step 6: Verify Task 1**

```powershell
uv run pytest -q tests/test_enterprise_knowledge_base_release_migration.py tests/test_catalog_schema.py tests/test_enterprise_readiness_api.py
ruff check catalog_migrations/versions/0029_enterprise_knowledge_base_releases.py models/orm.py core/catalog_schema.py server/enterprise_readiness_api.py tests/test_enterprise_knowledge_base_release_migration.py
ruff format --check catalog_migrations/versions/0029_enterprise_knowledge_base_releases.py models/orm.py core/catalog_schema.py server/enterprise_readiness_api.py tests/test_enterprise_knowledge_base_release_migration.py
uv run alembic heads
```

Expected head: `0029_enterprise_knowledge_base_releases`.

---

### Task 2: Release Snapshot, Digest and Read Core

**Files:**
- Create: `core/enterprise_knowledge_base_releases.py`
- Test: `tests/test_enterprise_knowledge_base_release_core.py`
- Test: `tests/test_enterprise_knowledge_base_release_snapshot.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ReleaseSnapshot:
    manifest: Mapping[str, Any]
    entries: tuple[Mapping[str, Any], ...]
    blockers: tuple[Mapping[str, Any], ...]
    readiness_state: str
    readiness_fingerprint: str
    manifest_digest: str


def capture_release_candidate(
    engine: Engine,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    expected_profile_revision: int,
    expected_ownership_revision: int,
    expected_workspace_revision: int,
    expected_mutation_generation: int,
    expected_serving_generation: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult: ...
```

Read interfaces:

```python
list_release_channels(..., cursor: str | None, limit: int) -> ServiceResult
list_releases(..., dataset_id: str, cursor: str | None, limit: int) -> ServiceResult
get_release(..., dataset_id: str, release_id: str) -> ServiceResult
get_release_readiness(..., dataset_id: str, release_id: str) -> ServiceResult
get_release_impact(..., dataset_id: str, release_id: str) -> ServiceResult
list_channel_bindings(..., dataset_id: str) -> ServiceResult
```

- [ ] **Step 1: Write RED canonical snapshot tests**

Tests must prove deterministic ordering and digest for Dataset profile, every effective Document Version, every effective approved QA row, Sources and Projection revisions. Reordering SQL rows must not change digest.

- [ ] **Step 2: Write RED blocker tests**

Cover missing current Document Version, index drift, active delete, active Source run, Source generation drift, projection pending/dead-letter, QA drift, missing ownership and partial 0029.

- [ ] **Step 3: Implement canonical serialization**

```python
def canonical_release_digest(manifest: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> str:
    payload = {"schema_version": 1, "manifest": manifest, "entries": list(entries)}
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()
```

Canonical JSON sorts keys, rejects non-finite numbers and redacts/forbids secret-bearing keys/URLs.

- [ ] **Step 4: Implement consistent snapshot capture**

Use deterministic lock order from the spec and recheck all expected revisions immediately before insert. Insert Manifest, entries and `candidate_created` event atomically. Replay returns the original sanitized payload.

- [ ] **Step 5: Implement keyset reads and impact**

Release impact reports channel bindings, App follow-channel/pins, current Registry references and blocker drill-down without scanning across Tenants.

- [ ] **Step 6: Verify Task 2**

```powershell
uv run pytest -q tests/test_enterprise_knowledge_base_release_core.py tests/test_enterprise_knowledge_base_release_snapshot.py
ruff check core/enterprise_knowledge_base_releases.py tests/test_enterprise_knowledge_base_release_core.py tests/test_enterprise_knowledge_base_release_snapshot.py
```

---

### Task 3: Channel, Promotion, Rollback and Application Binding Mutations

**Files:**
- Modify: `core/enterprise_knowledge_base_releases.py`
- Modify: `core/enterprise_knowledge_base_registry.py`
- Modify: `server/enterprise_approval_consumers.py`
- Modify: `models/orm.py`
- Test: `tests/test_enterprise_knowledge_base_release_mutations.py`
- Test: `tests/test_approval_gated_knowledge_base_release.py`
- Test: `tests/test_enterprise_knowledge_base_registry_mutations.py`

**Interfaces:**

```python
create_release_channel(..., code: str, name: str, risk_tier: str, promotion_order: int, is_default_serving: bool, ...) -> ServiceResult
update_release_channel(..., channel_id: str, expected_revision: int, ...) -> ServiceResult
promote_release(..., dataset_id: str, release_id: str, channel_id: str, expected_channel_revision: int, expected_profile_revision: int, expected_ownership_revision: int, expected_serving_generation: int, ...) -> ServiceResult
rollback_channel_release(..., dataset_id: str, channel_id: str, target_release_id: str, expected_channel_revision: int, expected_serving_generation: int, ...) -> ServiceResult
update_app_release_binding(..., app_id: str, dataset_id: str, expected_reference_revision: int, release_mode: str, release_channel_id: str | None, pinned_release_id: str | None, ...) -> ServiceResult
```

- [ ] **Step 1: Write RED channel mutation tests**

Cover normalized-code uniqueness, custom channels, default-serving uniqueness, archive blocker while bound, revision conflict, Tenant isolation, audit and idempotency replay.

- [ ] **Step 2: Write RED promotion/rollback tests**

Cover low-risk direct promotion, high-risk approval-required promotion, default-serving compatibility projection, previous Release preservation, rollback, retired/pinned blockers and stale revision refusal.

- [ ] **Step 3: Write RED Application binding tests**

Exact XOR:

```text
follow_channel -> channel required, pin null
pinned -> pin required, channel null
```

Pinned Release must match Tenant/Dataset and be non-retired.

- [ ] **Step 4: Implement atomic mutations**

Promotion transaction locks Dataset, ownership/Workspace, Manifest, Channel and current binding in deterministic order. Default-serving changes update `datasets.serving_release_id`, increment `release_revision` and `serving_generation`, append events and audit.

- [ ] **Step 5: Add approval snapshots/consumer**

Approval execution fact includes manifest digest, Release ID/number, Channel ID/revision, profile/ownership/Workspace revisions and serving generation. Forged Mapping, replay, wrong actor, stale revision and wrong resource scope fail closed.

- [ ] **Step 6: Verify Task 3**

```powershell
uv run pytest -q tests/test_enterprise_knowledge_base_release_mutations.py tests/test_approval_gated_knowledge_base_release.py tests/test_enterprise_knowledge_base_registry_mutations.py
```

---

### Task 4: Strict HTTP API, Readiness and Upgrade Preflight

**Files:**
- Create: `server/enterprise_knowledge_base_releases_api.py`
- Modify: `server/app.py`
- Modify: `server/enterprise_readiness_api.py`
- Modify: `scripts/enterprise_catalog_upgrade.py`
- Modify: `docs/operations/enterprise-catalog-upgrade.md`
- Test: `tests/test_enterprise_knowledge_base_releases_api.py`
- Test: `tests/test_enterprise_catalog_upgrade.py`
- Test: `tests/test_enterprise_readiness_api.py`

**Interfaces:**
- Mount every route defined in design section 9.
- Strict Pydantic models use `extra="forbid"`, exact ints/bools, bounded IDs/reasons and XOR validation.
- HTTP error mapping: 401/403/404/409/422/503 with sanitized stable codes.

- [ ] **Step 1: Write RED router contract tests**

Use real 0029 SQLite integration plus stub service tests. Assert auth, Tenant headers, keyset query names, Idempotency-Key and no raw ticket/secret.

- [ ] **Step 2: Implement router and mount lazily**

Read routes use the read engine provider; mutations use the writable provider only after authentication/permission validation.

- [ ] **Step 3: Extend Registry API**

Registry detail/dependencies expose sanitized active-channel/current-serving Release summary and Application release behavior without loading full entries.

- [ ] **Step 4: Extend preflight/runbook**

Read-only preflight checks default channels, malformed current-version/projection facts, invalid App modes and uniqueness drift. Never creates channels or captures Releases.

- [ ] **Step 5: Verify Task 4**

```powershell
uv run pytest -q tests/test_enterprise_knowledge_base_releases_api.py tests/test_enterprise_catalog_upgrade.py tests/test_enterprise_readiness_api.py
python -m py_compile server/enterprise_knowledge_base_releases_api.py scripts/enterprise_catalog_upgrade.py
```

---

### Task 5: Persistent Knowledge Base Resource Shell and Route Contract

**Files:**
- Create: `frontend/src/enterprise-knowledge-base-shell/KnowledgeBaseResourceShell.tsx`
- Create: `frontend/src/enterprise-knowledge-base-shell/knowledgeBaseResourceRoute.ts`
- Create: `frontend/src/enterprise-knowledge-base-shell/knowledge-base-resource-shell.css`
- Create: `frontend/src/enterprise-knowledge-base-shell/KnowledgeBaseResourceShell.test.tsx`
- Create: `frontend/src/enterprise-knowledge-base-shell/knowledgeBaseResourceRoute.test.ts`
- Create: `frontend/src/pages/EnterpriseKnowledgeBaseWorkspacePage.tsx`
- Modify: `frontend/src/run/appRoute.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/DocumentsPage.tsx`
- Modify: `frontend/src/pages/KnowledgeTaxonomyPage.tsx`
- Modify: `frontend/src/pages/KnowledgeSourcesPage.tsx`
- Modify: `frontend/src/pages/KnowledgeGovernancePage.tsx`

**Interfaces:**

```ts
export type KnowledgeBaseResourceSection =
  | "overview"
  | "documents"
  | "taxonomy"
  | "sources"
  | "governance"
  | "releases";

export interface KnowledgeBaseResourceShellProps {
  active: boolean;
  section: KnowledgeBaseResourceSection;
  children: React.ReactNode;
}
```

Existing pages gain `embedded?: boolean` and suppress their `PageTopbar` when embedded.

- [ ] **Step 1: Write RED route tests**

Direct/hash canonical URLs, hidden PageKey, main-sider selection, resource active section, browser back/forward and Dataset preservation.

- [ ] **Step 2: Write RED composition/a11y tests**

Assert one page heading, one global `WorkspaceScopeBar`, no duplicated `PageTopbar`, desktop TDesign Tabs, mobile labelled Select, dirty-state leave cancellation and focus return.

- [ ] **Step 3: Implement route helpers and shell**

Shell reads verified KnowledgeWorkspaceContext and Registry detail authority. It is active-gated so hidden kept-alive pages do not duplicate network requests.

- [ ] **Step 4: Embed existing pages**

Do not move business logic. Add content-only rendering paths and reuse existing page tests.

- [ ] **Step 5: Adjust Registry Drawer**

Add only “Open Knowledge Base workspace”; do not add Release tabs/mutations. Close Drawer and focus the workspace heading after navigation.

- [ ] **Step 6: Verify Task 5**

```powershell
npm test -- --run src/enterprise-knowledge-base-shell src/pages/DocumentsPage.test.tsx src/pages/KnowledgeTaxonomyPage.test.tsx src/pages/KnowledgeSourcesPage.test.tsx src/pages/KnowledgeGovernancePage.test.tsx src/run/appRoute.test.ts src/App.stage18.test.tsx
npx eslint src/enterprise-knowledge-base-shell src/App.tsx src/run/appRoute.ts
npx tsc --noEmit
```

---

### Task 6: TDesign Release Center

**Files:**
- Create: `frontend/src/enterprise-knowledge-base-release/model/releaseModel.ts`
- Create: `frontend/src/enterprise-knowledge-base-release/model/releaseValidation.ts`
- Create: `frontend/src/enterprise-knowledge-base-release/api/releaseApi.ts`
- Create: `frontend/src/enterprise-knowledge-base-release/hooks/useKnowledgeBaseReleases.ts`
- Create: `frontend/src/enterprise-knowledge-base-release/components/KnowledgeBaseReleaseCenter.tsx`
- Create: `frontend/src/enterprise-knowledge-base-release/components/ReleaseDetailDrawer.tsx`
- Create: `frontend/src/enterprise-knowledge-base-release/components/ReleaseMutationDialog.tsx`
- Create: `frontend/src/enterprise-knowledge-base-release/knowledge-base-release.css`
- Test: corresponding `*.test.ts` / `*.test.tsx`

**Interfaces:**
- API projectors preserve null vs zero and reject malformed authority.
- Hook exposes separate resources for channels, history, selected detail, readiness, impact and mutation; impact/audit are lazy.
- Mutations reuse one idempotency key after transient failure.

- [ ] **Step 1: Write RED model/API tests**

Cover exact wire names, channel custom labels, Manifest digest/revision, opaque cursor, direct/approval outcomes and redaction.

- [ ] **Step 2: Write RED hook tests**

Cover active gating, stale response cancellation, pagination merge, lazy impact/audit and mutation replay.

- [ ] **Step 3: Write RED desktop/mobile component tests**

Desktop table vs mobile cards, labelled resource/channel Selects, one primary action, detail Drawer tabs, no nested Drawer, promotion/rollback Dialog, ESC/focus return and read-only actor state.

- [ ] **Step 4: Implement three-layer Release Center**

Main page renders Channel summary + Release history only. Manifest/Readiness/Impact/Audit live in the detail Drawer. Steps appear only in the mutation flow.

- [ ] **Step 5: Integrate configured/effective comparison**

Read configured profile/policies from existing Governance authority and effective Release facts from Release API. Never infer missing effective values.

- [ ] **Step 6: Verify Task 6**

```powershell
npm test -- --run src/enterprise-knowledge-base-release src/enterprise-knowledge-base-shell
npx eslint src/enterprise-knowledge-base-release src/enterprise-knowledge-base-shell
npx prettier --check src/enterprise-knowledge-base-release src/enterprise-knowledge-base-shell
npm run build
```

---

### Task 7: Controlled Playwright and Operations Evidence

**Files:**
- Create: `output/playwright/enterprise-knowledge-base-release-stage19/**`
- Create: `docs/research/2026-08-28-enterprise-knowledge-base-release-stage19-visual-acceptance.md`
- Modify: `docs/operations/enterprise-catalog-upgrade.md`

**Interfaces:**
- Result schema `stage19-browser-result.v1`.
- Manifest schema `stage19-artifact-manifest.v1`.
- Harness is loopback-only and blocks external network.

- [ ] **Step 1: Build RED pure gate tests**

Require exact direct/hash × light/dark × 1440/375/280 matrix, resource shell, Release table/cards, custom channel, candidate capture, direct promotion, approval promotion, rollback, detail Drawer focus and zero leaks/errors/overflow.

- [ ] **Step 2: Implement controlled fixture**

No real migration, capture, promotion, rollback, approval or external request. All mutation outcomes are sanitized in-memory fixtures.

- [ ] **Step 3: Capture fresh artifacts**

Distinct run-scoped screenshots for Registry open-workspace, shell sections, desktop Release table, mobile Release cards/selects, Manifest Drawer, readiness blockers, direct/approval/rollback dialogs.

- [ ] **Step 4: Verify operations boundary**

Runbook documents read-only preflight and explicitly prohibits automated production migration/backfill/publish/rollback.

---

### Task 8: Final Regression and Independent Review

**Files:**
- No broad formatting or protected-file changes.

- [ ] **Step 1: Run Stage7–19 backend regression**

Start from the exact Stage7–18 664-test baseline and add all new Stage19 tests plus affected Registry/Approval/Readiness/Catalog/upgrade tests.

- [ ] **Step 2: Run enterprise frontend regression**

Run enterprise-admin, approval, workspace, Knowledge Base, shell, Release and knowledge workspace suites plus production build.

- [ ] **Step 3: Static/migration checks**

```powershell
ruff check <Stage19 Python files>
ruff format --check <Stage19 Python files>
python -m py_compile <Stage19 Python files>
npx eslint <Stage19 frontend files>
npx prettier --check <Stage19 frontend files>
npx tsc --noEmit
uv run alembic heads
uv lock --check
git diff --check
```

- [ ] **Step 4: Two independent Luna-max reviews**

Database/Security reviewer and TDesign/UI reviewer report only P0/P1/P2. Resolve all findings and rerun affected evidence.

- [ ] **Step 5: Cleanup**

Stop only the Stage19 Vite listener after verifying its command line. Keep the long-term enterprise goal active.

## Production Prohibition

No real production migration, default-channel backfill, Release capture, promotion, rollback, Application pin, approval execution, source sync, ingestion, restore, destruction or external publication is performed automatically.
