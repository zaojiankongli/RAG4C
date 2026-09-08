# Stage 13 Enterprise Approval Center Implementation Plan

> **For agentic workers:** Use TDD for every production change. Keep the protected retrieval-quality and retrieval-experiment paths untouched.

**Goal:** Build a TDesign React enterprise approval center under `frontend/src/enterprise-approval/**`, expose it at nested `/enterprise/approvals` direct/hash routes, and prove strict data projection, safe mutation UX, accessibility, responsive layout, and honest execution boundaries.

**Architecture:** The page shell remains responsible for authenticated enterprise context; the new approval center owns only approval reads, mutations, projections, and UI state. API functions send tenant/actor authority and bounded idempotency contracts, model projectors drop malformed records and redact sensitive snapshot keys, and the component renders server evidence without filling missing counts. The Stage 13 execution boundary is always surfaced as `execution_adapter_not_connected` unless the API explicitly reports a connected consumer.

**Tech Stack:** React 18, TypeScript, TDesign React 1.18, TDesign Icons, Vitest, Testing Library, Playwright CLI.

**Spec:** `docs/superpowers/specs/2026-08-27-enterprise-approval-center-design.md`; visual calibration: `docs/superpowers/specs/2026-08-27-tencent-approval-visual-reference.md`.

## Global Constraints

- Do not modify `frontend/src/retrieval-quality/**`, `core/retrieval_experiment_runner.py`, `server/retrieval_experiments_api.py`, or related retrieval experiment tests.
- Use TDesign React/TDesign Icons for controls; no new UI dependency and no copied Tencent markup/assets.
- Never fabricate approval rows, counts, catalog revisions, decisions, or connected execution adapters.
- Reject comments are required, trimmed, and at most 500 characters; request snapshots are projected with sensitive keys redacted.
- All mutations carry a stable `Idempotency-Key` and the exact expected revision supplied by the server.
- Direct and hash navigation must preserve the existing page keep-alive behavior.

### Task 3A: Route and typed data contracts

**Files:**
- Create: `frontend/src/enterprise-approval/approvalRoute.ts`
- Create: `frontend/src/enterprise-approval/approvalRoute.test.ts`
- Create: `frontend/src/enterprise-approval/enterpriseApprovalModel.ts`
- Create: `frontend/src/enterprise-approval/enterpriseApprovalModel.test.ts`
- Create: `frontend/src/enterprise-approval/api/enterpriseApprovalApi.ts`
- Create: `frontend/src/enterprise-approval/api/enterpriseApprovalApi.test.ts`
- Modify: `frontend/src/run/appRoute.test.ts` only for nested approval parser coverage when required.

**Contract:** `enterpriseApprovalRouteFromLocation(location)` returns `true` for direct `/enterprise/approvals` and hash `#/enterprise/approvals` with optional query strings. Model projectors return typed pages, redact `token|secret|password|credential|authorization|invite_link|code|state` snapshot keys, preserve nullable evidence as `null`, and default only the known Stage 13 adapter boundary to `execution_adapter_not_connected`. API functions use `/api/enterprise/approvals/...`, `X-RAG4C-Tenant`, `Authorization`, bounded `limit <= 200`, URL-encoded IDs, and exact snake_case mutation bodies.

### Task 3B: Hook state and mutation error projection

**Files:**
- Create: `frontend/src/enterprise-approval/hooks/useEnterpriseApproval.ts`
- Create: `frontend/src/enterprise-approval/hooks/useEnterpriseApproval.test.tsx`

**Contract:** Initial policy/request/detail reads are cancellable and reloadable. Mutations are single-flight, keep the same idempotency key for retry, project 401/403/409/412/503/migration errors into safe Chinese copy, and expose no backend `detail.message` directly. Successful mutation reloads the relevant list/detail.

### Task 3C: TDesign approval center and responsive surfaces

**Files:**
- Create: `frontend/src/enterprise-approval/components/EnterpriseApprovalCenter.tsx`
- Create: `frontend/src/enterprise-approval/components/EnterpriseApprovalCenter.test.tsx`
- Create: `frontend/src/enterprise-approval/enterprise-approval.css`
- Create: `frontend/src/enterprise-approval/enterprise-approval.css.test.ts`
- Create: `frontend/src/enterprise-approval/index.ts`

**Contract:** Render an evidence strip, `审批申请`/`审批规则` tabs, dense desktop tables, priority mobile cards at 375/280px, detail drawer tabs `申请详情`/`审批流程`, process timeline, approve confirmation, required reject dialog, requester-only cancel confirmation, and policy create/edit dialog. Use semantic regions, tabs, labels, focus-visible styles, no horizontal overflow on mobile, and one primary action per narrow row. Empty/failed/unavailable states remain explicit.

### Task 3D: Enterprise page integration and verification

**Files:**
- Modify: `frontend/src/pages/EnterpriseAdminPage.tsx`
- Modify: `frontend/src/pages/EnterpriseAdminPage.test.tsx`
- Modify: `frontend/src/run/appRoute.ts` only if route helper needs shell-level support.

**Contract:** On direct/hash `/enterprise/approvals`, keep enterprise authentication/context loading and render the approval center as the nested work surface; existing `/enterprise`, identity, compliance, invitation, and OIDC callback behavior must remain unchanged.

**Verification:**

```powershell
cd frontend
npm test -- --run src/enterprise-approval src/pages/EnterpriseAdminPage.test.tsx src/run/appRoute.test.ts
npx prettier --check src/enterprise-approval src/pages/EnterpriseAdminPage.tsx src/run/appRoute.ts
npm run lint
npm run build
```

Use a mock Playwright response harness to capture `output/playwright/enterprise-approval-stage13/desktop-light.png`, `desktop-dark.png`, `mobile-375.png`, and `mobile-280.png`; assert zero console errors, zero unknown requests, visible keyboard focus, and no horizontal overflow.
