# RAG4C Enterprise KnowledgeOps Console Design

**Date:** 2026-08-26

## Objective

Turn the existing RAG4C console into a credible enterprise knowledge platform inspired by Tencent's restrained cloud-console information architecture while preserving RAG4C's differentiator: the Knowledge Lifeline from source authority through retrieval evidence.

## Product surfaces

1. **Knowledge workspace** — overview, documents, taxonomy, sources, QA.
2. **Knowledge operations** — retrieval validation, evaluation, run monitoring, consistency diagnosis.
3. **Enterprise administration** — workspace identity, members and permissions, audit, system readiness.

The first implementation wave upgrades the shared workspace shell and the most visible knowledge pages. Empty enterprise-administration surfaces are allowed when the backend is unavailable, but they must state the capability status honestly and never present fabricated live data.

## Visual direction

- Use TDesign React and TDesign Icons as the primary component and icon system.
- Use a bright enterprise blue only for primary actions, active navigation, links, and focused state.
- Use white data surfaces on a cool gray canvas, 4–8px control radii, fine borders, and very light shadows.
- Avoid glassmorphism, neon decoration, excessive gradients, and card walls.
- Uiverse-style motion is limited to upload, processing, AI generation, and success feedback.
- Morphicons are allowed only when TDesign lacks a clear domain icon.
- Desktop pages favor dense tables and split workspaces; mobile pages favor summary cards and drawers.

## Shared enterprise shell

The app shell exposes organization, active knowledge base, environment, connection health, global search, notifications, audit access, and actor role. Navigation groups become Knowledge Workspace, Knowledge Operations, and Enterprise Administration.

The shell must:

- preserve the existing custom route model and page keep-alive behavior;
- keep mobile navigation accessible;
- clearly distinguish live authoritative data, degraded data, and demo data;
- avoid touching in-progress retrieval-quality files except through existing public page boundaries.

## Knowledge overview

The overview becomes an operational command center rather than a marketing hero. It contains:

- authority and schema-readiness status;
- document, chunk, source, QA, consistency, and failure metrics;
- a Source → Parse → Chunk → Index → Retrieve → Cite → Answer lifecycle rail;
- prioritized attention items;
- recent documents, source runs, and governance events;
- quick actions for import, source creation, retrieval validation, and evaluation.

## Knowledge taxonomy

The taxonomy page uses a directory-and-governance layout:

- hierarchical directory navigation;
- governed tag collection with counts;
- document result surface tied to the active directory/tag;
- clear empty states and deep-link-ready filter vocabulary.

## Enterprise readiness API

Add a read-only endpoint that reports the database schema state and enterprise capability availability without mutating the real database. It must distinguish expected migration head, current migration revision, readiness, missing capability groups, and whether mutations are safe. Errors must fail closed and return a degraded status rather than pretending readiness.

The real MySQL database is not upgraded in this wave. Migration execution remains a separate destructive/operational action requiring backup and explicit approval.

## Testing and verification

- New behavior follows test-first development.
- Frontend component and page tests assert semantics, accessibility, and honest degraded/demo state.
- Backend tests cover ready, behind, unavailable, and malformed schema inspection states.
- Run focused tests during each task, then full frontend tests, TypeScript/Vite build, and focused Python tests.
- Playwright captures desktop/mobile and light/dark screenshots for the shell, overview, and taxonomy pages.

## Protected work

Do not overwrite, revert, format, or otherwise interfere with current uncommitted work in:

- `frontend/src/retrieval-quality/**`
- `core/retrieval_experiment_runner.py`
- `server/retrieval_experiments_api.py`
- related retrieval experiment tests
- existing edits in `server/app.py` must be preserved during final integration.
