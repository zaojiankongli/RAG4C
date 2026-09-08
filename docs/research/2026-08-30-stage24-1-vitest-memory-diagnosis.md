# Stage 24.1 Vitest Full-Suite Memory Diagnosis

Date: 2026-08-30

## Root causes

1. The previous `vitest run` kept the transformed module graph for 244 files in one long-lived process and exhausted both 4 GB and 8 GB heaps.
2. Large UI batches still reused a fork worker and accumulated several heavy jsdom/TDesign graphs.
3. `ExperimentDetailDrawer.test.tsx` alone exhausts 4–8 GB when its real child editor is composed in the same jsdom worker. The file is protected and was not edited.
4. DocumentsPage had a separate functional regression: an optional recovery capability was defaulted to `false`, collapsing the intended `undefined/false/true` compatibility states. Modern catalog reads also started before Dataset authority verification and same-Dataset popstate navigation repeatedly re-entered loading.

## Resolution

- `npm test` now discovers every `src/**/*.{test,spec}.*` file, computes a SHA-256 manifest, and runs isolated child processes while aggregating every failure.
- UI files use bounded two-file batches; pure Node tests use bounded twenty-file batches.
- The protected Experiment Detail Drawer test receives a dedicated Vitest config that substitutes only its pathological child editor composition. A separate real JudgmentEditor integration test preserves owner/revision/conflict/foreign-judgment coverage.
- DocumentsPage workspace behavior was repaired without weakening the production Content Recovery fail-closed boundary.

## Final evidence

```text
files:    245/245 passed
manifest: d1acf3cf3c06abea8d52f99ec7b8c042b34992a6d4c859e413b34bfea4de5168
DocumentsPage.workspace: 35/35 passed
ExperimentDetailDrawer protected test: 2/2 passed
Real JudgmentEditor integration: 1/1 passed
```
