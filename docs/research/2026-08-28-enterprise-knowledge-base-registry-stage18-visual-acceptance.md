# Stage 18 Enterprise Knowledge Base Registry Visual Acceptance

**Date:** 2026-08-28  
**Timezone:** Asia/Shanghai  
**Target revision:** 0028_enterprise_knowledge_base_registry  
**Evidence directory:** output/playwright/enterprise-knowledge-base-registry-stage18/

## Objective

Prove the authenticated Enterprise Knowledge Base Center with server-owned Knowledge Base, owning Workspace, shared-association, Application-reference and archive-readiness evidence. This worker is a gate, not a frontend fixer: if the application head is incomplete, it must return blocked and must not reuse retained green evidence.

## Required 12-case matrix

The twelve identities are run twice as separate phases:

    direct / hash
    light / dark
    1440x900 / 375x812 / 280x720

Each identity must contain both:

1. **registry baseline** — a clean /enterprise/knowledge-bases visit with no Dataset deep-link;
2. **deep-link detail** — a new navigation to ?dataset=dataset-prod&tab=dependencies with the Detail Drawer visible.

The result keeps registry_baseline_matrix and deep_link_detail_matrix separate and also records phase metadata under matrix_contract. A deep-linked page left visible behind a Drawer cannot satisfy the registry baseline.

## Required registry evidence

- desktop: dense Knowledge Base table and inline filter controls;
- 375px and 280px: resource cards are visible, the table is hidden, and a real Filter Drawer is opened and captured;
- Filter Drawer capture is taken after opening the control, not by treating the toolbar region as a drawer;
- registry facts include owning Workspace, Application references, revisions and archive blocker evidence;
- direct and hash route compatibility is checked independently.

## Required Detail Drawer evidence

The worker opens the Drawer from a visible registry row/card, then verifies:

- focus enters the Drawer;
- Escape closes it;
- focus returns to the originating View action;
- a separate deep-link navigation opens the Drawer and selects Dependencies;
- the Dependency Rail and active Application-reference archive blocker are visible.

## Required transfer evidence

The controlled fixture verifies both mutation outcomes:

- direct-light-1440: direct-applied transfer;
- hash-dark-1440: approval-required transfer.

Each outcome requires a visible transfer dialog, a submitted request, an Idempotency-Key presence boolean, and the expected sanitized outcome. Raw keys, approval tickets and fixture secrets are never serialized.

## Artifact freshness contract

The manifest requires 34 named screenshots:

- 12 *-registry.png baseline captures;
- 12 *-detail.png deep-link captures;
- 8 mobile *-filter-drawer.png captures;
- one direct transfer capture;
- one approval transfer capture.

Before every run the worker removes prior PNG/result/manifest artifacts in this Stage18 directory. Every captured file receives a run-scoped capture_id, captured_this_run and fresh flag. Duplicate filenames or capture IDs are blocking; retained files cannot make the gate green.

## Fail-closed status

- passed / exit 0: all matrix, surface, mutation, focus, network, overflow and artifact gates pass;
- blocked / exit 2: the app or evidence is not ready, including missing mobile Filter Drawer or unavailable transfer targets;
- failed / exit 1: the browser harness itself fails.

The Stage18 artifact-contract tests accept an honest blocked run while still requiring the v2 result/manifest shape and all required gate fields: archive_blocker_visible, mobile cards/Filter Drawer, direct transfer, approval transfer and Drawer focus.

## Production boundary

The controlled fixture does not execute real migration, ownership transfer, Application-reference mutation, archive/delete, approval execution, source sync, document ingestion, restore, external publication or Retrieval Quality work.
