# Tencent Knowledge Quality and Publishing Reference for Stage 20

**Date:** 2026-08-29  
**Scope:** Enterprise Knowledge Base quality certification, publish gates and waivers  
**Primary references:** Tencent Cloud Agent Development Platform documentation

## 1. Sources reviewed

- Knowledge Base recall testing: https://cloud.tencent.com/document/product/1759/135538
- Application evaluation: https://cloud.tencent.com/document/product/1759/104208
- Application publishing overview: https://cloud.tencent.com/document/product/1759/104209
- Agent application workflow: https://cloud.tencent.com/document/product/1759/122549
- Product update notes: https://cloud.tencent.com/document/product/1759/104191

The pages were live-read on 2026-08-29. The recall-testing page showed a most-recent update of 2026-08-05.

## 2. Tencent patterns that should be retained

### 2.1 Evaluation is a separate workspace

Tencent's recall testing is explicitly separate from live Application configuration. Test-only retrieval settings do not silently mutate the Application. RAG4C should preserve the same boundary:

```text
Retrieval Experiment = evaluation fact
Release = deployment fact
Certification = governed statement connecting those two facts
```

Certification must never mutate the experiment, judgment, Release Manifest or configured Dataset profile.

### 2.2 History and comparison are first-class

Recall testing retains history and can compare multiple retrieval configurations. Application evaluation separates reusable datasets from evaluation tasks and supports comparison/reporting. RAG4C already has immutable per-query Retrieval Experiments and reviewer judgments, so Stage 20 should add a reusable **Quality Baseline** that groups those existing facts rather than creating a second runner.

### 2.3 Evaluation uses multiple signals

Tencent Application evaluation supports reusable evaluation sets/tasks and multiple scoring modes, including model, rule and code scoring. RAG4C Stage 20 should remain grounded in facts it already owns:

- completed/failed experiment status;
- result count and degraded evidence;
- judgment coverage;
- relevance label counts;
- mean score;
- multi-reviewer agreement/conflicts;
- immutable strategy/result/evidence snapshots;
- exact Release Manifest/entry match.

A later stage may add model/code judges. Stage 20 must not fabricate them.

### 2.4 Test first, then publish

Tencent's workflow places dialogue debugging/evaluation before formal publishing and distinguishes pending publication, publishing history and restoration. RAG4C's Release Channel promotion is the matching enterprise boundary. Quality evidence therefore belongs in a gate immediately before Channel promotion, not in Dataset editing.

### 2.5 Enterprise operations are auditable

Tencent exposes publishing history, version restoration, channels and operation logs. Stage 20 should add immutable Certification evidence and append-only lifecycle events; waivers must be approval-backed and expiring.

## 3. RAG4C-specific adaptation

Tencent evaluation is primarily Application-centric. RAG4C must certify a **Knowledge Base Release**, because Release Manifest digest is the stable identity of content, QA, Sources, Projection and Dataset policy.

The recommended authority chain is:

```text
Existing Retrieval Experiments + reviewer Judgments
  -> frozen Quality Baseline revision
  -> Certification evaluated against one Release + one policy revision
  -> Channel Publish Gate
  -> optional expiring approval-backed Waiver
```

## 4. UI implications

Keep the existing Tencent-like resource shell and three-layer Release Center. Add Certification as a governed detail surface, not a separate marketing dashboard:

1. Channel gate summary in the existing Channel summary.
2. Certification column/status in Release history.
3. Certification tab inside Release Detail Drawer.
4. One Certification Drawer/Panel for baseline evidence and policy thresholds.
5. Waiver uses one Dialog and deep-links to the existing Approval Center.

Desktop should use TDesign Descriptions, PrimaryTable, Tabs, Tag, Alert, Progress and Dialog. Mobile should use compact cards and labelled Selects. No card-wall KPI dashboard and no second Drawer.

## 5. Explicit exclusions

- no new Retrieval Experiment runner;
- no changes to protected Retrieval Quality implementation paths;
- no model-provider mutation;
- no automatic production certification, promotion or waiver;
- no certification derived from mutable or unscoped UI state.
