# Frontend enterprise capability policy registry

## Outcome

Added a declarative frontend capability policy registry:

- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-admin\capabilityPolicy.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-admin\capabilityPolicy.test.ts`

`App.tsx` no longer repeats the readiness/read-only rules for notifications,
content recovery, task operations, automation workflows, and knowledge serving.
All five now resolve through one pure policy adapter.

## Preserved behavior

- A missing actor token, tenant mismatch, missing identity, or non-`ready`
  backend capability never produces `ready=true`.
- Offline, degraded, and down service states remain read-only.
- Knowledge serving still has its additional `knowledge.manage` permission
  gate; other capabilities do not inherit that gate.
- Route ownership, workspace selection, notification controller lifecycle,
  dirty-draft protection, and `AppRouteContext` props remain unchanged.
- No backend, OpenAPI, or public frontend contract changed.

## Verification

- Capability policy tests: **4 passed**.
- App focused regression: **7 files / 40 tests passed**.
- Focused ESLint: passed.
- Production build: passed, **7,124 modules transformed**.
- The 293-file full suite was started but not used as this slice's completion
  gate; it was stopped after the focused regression had passed. No full-suite
  pass is claimed here. The previous frontend full gate remains documented in
  the preceding handoffs.
- Independent sub-agent review: **PASS**, no P0/P1/P2/P3 findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-enterprise-capability-policy-registry-design.md`.
