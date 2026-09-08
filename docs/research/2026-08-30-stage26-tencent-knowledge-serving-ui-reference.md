# Stage26 Tencent Knowledge Serving UI Reference

Date: 2026-08-30  
Stage: 26  
Target: Enterprise Knowledge Serving & Reliability Center

## Evidence

Public Tencent Cloud knowledge-base documentation was inspected read-only with Playwright. No login, form submission or business mutation was performed.

Pages:

- Knowledge Base overview: `https://cloud.tencent.com/document/product/1759/123551`
- Knowledge Base settings overview: `https://cloud.tencent.com/document/product/1759/123554`
- Knowledge Base recall testing: `https://cloud.tencent.com/document/product/1759/135538`

Fresh evidence:

- `output/playwright/stage26-research/01-kb-overview-desktop.png`
- `output/playwright/stage26-research/01-kb-overview-mobile-375.png`
- `output/playwright/stage26-research/02-kb-settings-desktop.png`
- `output/playwright/stage26-research/02-kb-settings-mobile-375.png`
- `output/playwright/stage26-research/03-recall-test-desktop.png`
- `output/playwright/stage26-research/03-recall-test-mobile-375.png`
- `output/playwright/stage26-research/public-tencent-contact-sheet.png`
- `output/playwright/stage26-research/stage25-evidence-contact-sheet.png`

Existing Stage25 Tencent/WeData/ADP evidence is also reused from:

- `docs/research/2026-08-30-stage25-tencent-automation-workflow-ui-reference.md`
- `output/playwright/tencent-stage25-automation-research/`

## Product hierarchy learned from Tencent

Tencent treats a Knowledge Base as a product resource with distinct operating surfaces rather than one large settings form:

```text
Knowledge Base
├─ Overview
├─ Knowledge management
├─ Documents
├─ QA
├─ Database knowledge
├─ Settings
├─ Agentic RAG
└─ Recall testing
```

The applicable RAG4C lesson is to keep authority domains separate while giving operators one reliable current-state surface. Source synchronization, parsing, chunking, index projection, serving release and experiments are not one lifecycle owner.

## Serving Authority is not Retrieval Experiment

Tencent Recall Testing exposes temporary comparison configuration, multiple knowledge scopes, retrieval strategies, recall count and reranking without directly changing the serving Knowledge Base.

Stage26 preserves the same separation:

```text
Serving Reliability Authority
    !=
Retrieval Experiment / Judgment Authority
```

The new page may hand off to Retrieval Debug or Quality, but it must not duplicate or modify the protected Retrieval Quality runner.

## UI signature

```text
SOURCE -> PARSE -> CHUNK -> INDEX -> SERVE
```

This is the primary memory structure of Stage26. It explains how a Knowledge Base becomes serveable and makes the first blocked stage visible without opening five separate pages.

Each stage displays:

- verified state;
- observed time;
- expected and observed revision/generation;
- lag or error count;
- safe internal evidence count;
- bounded handoff.

## Desktop information architecture

```text
Knowledge Base Resource Shell / Serving
Governance Evidence Strip
SOURCE -> PARSE -> CHUNK -> INDEX -> SERVE rail
Filter bar
Dense TDesign PrimaryTable
Single evidence Drawer
```

The Evidence Strip is not a marketing metric wall. It shows authority facts only:

- Serving state;
- current policy revision;
- current Snapshot;
- source freshness;
- projection lag;
- failed documents;
- pending index operations;
- current Release and certification.

The dense table follows Tencent/WeData operations patterns:

```text
Object | Overall state | Current stage | Revision |
Lag | Errors | Updated | Action
```

State is always text plus icon/tag, never color alone.

## Filters

Desktop hierarchy:

1. overall/stage state;
2. Source / Workspace / Dataset scope;
3. observed time / revision / owner;
4. keyword or internal ID.

Mobile does not compress the desktop filter row. It uses one Filter action opening a Drawer and renders an applied-filter summary above the cards.

## Detail Drawer

One top-level Drawer only:

```text
Overview
Evidence
Pipeline
Serving Policy
History
```

The first screen answers:

- What is the current serving state?
- Which Tenant / Workspace / Dataset owns it?
- What Policy and Snapshot are current?
- Which stage is stale, missing or blocked?
- Which safe existing page can the operator open next?

The Timeline explains causality and responsibility. The Policy surface explains revision differences without editing immutable history.

## Responsive rules

### 1440px

- full Evidence Strip;
- horizontal five-stage rail;
- PrimaryTable;
- right-side Drawer;
- one/two-row bounded filter bar.

### 375px

- compact Knowledge Base context;
- vertical stage rail;
- four most important evidence facts;
- Filter button and applied summary;
- priority cards;
- full-width primary action.

### 280px

- no horizontal data table;
- one-column cards;
- show name, state, current stage, lag and one action;
- full-screen Drawer;
- no multiple dangerous actions above the fold.

## Visual system

Core controls use TDesign React and TDesign Icons. The surface follows the existing RAG4C enterprise language:

- restrained blue authority accents;
- neutral dense cards and tables;
- monospace IDs/digests;
- subtle stage connectors;
- compact Chinese primary labels with English control-plane eyebrows;
- explicit read-only, partial, unavailable and evidence boundaries.

Uiverse/Morphicons may supplement empty-state illustration only. They must not replace TDesign controls, status semantics or keyboard behavior.

## Safe handoff

Allowed destinations:

- Sources;
- Documents;
- Task Center;
- Release Center;
- Quality certification/operations;
- Retrieval Debug / Quality experiment.

The route projector accepts only known internal route codes and validated IDs. No caller-supplied href or URL is rendered.

## Final recommendation

Stage26 should be a Resource Shell Serving section, not another top-level Dashboard. Its enterprise value is the verified relationship between source facts and current serving readiness, expressed through:

```text
Governance Evidence Strip
+ SOURCE -> PARSE -> CHUNK -> INDEX -> SERVE
+ Dense Operations Table
+ Single Evidence Drawer
+ Mobile Priority Cards
```
