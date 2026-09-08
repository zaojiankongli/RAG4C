# Tencent Knowledge Base Stage 19 Visual Reference

**Observed:** 2026-08-28 (Asia/Shanghai)  
**Method:** fresh `agent-browser` captures of Tencent Cloud public documentation  
**Purpose:** information-architecture and interaction calibration only; no Tencent trademark, proprietary asset, markup or source code is copied.

## Fresh evidence

Evidence directory:

```text
output/playwright/tencent-knowledge-base-stage19-2026-08-28/
```

Captured pages:

- Knowledge Base overview: `https://cloud.tencent.com/document/product/1759/123551`
- Knowledge Base settings: `https://cloud.tencent.com/document/product/1759/123553`
- Knowledge Base settings overview: `https://cloud.tencent.com/document/product/1759/123554`
- Enterprise, Workspace and permission overview: `https://cloud.tencent.com/document/product/1759/122569`
- Enterprise management: `https://cloud.tencent.com/document/product/1759/122570`

Each page has a fresh full-page PNG, accessibility snapshot and body-text extraction.

## Product patterns established by the reference

### 1. A Knowledge Base is a persistent work context

Tencent does not treat Knowledge Base management as one isolated metrics page. The information architecture keeps the user inside one governed Knowledge Base while moving among:

- Documents;
- Q&A;
- databases;
- settings;
- Schema;
- GraphRAG / Agentic RAG;
- recall testing.

For RAG4C this supports a persistent Knowledge Base resource shell above existing Documents, Taxonomy, Sources, Governance and future Release workspaces.

### 2. Settings are separated by operational domain

The settings information architecture explicitly separates:

- knowledge-processing models;
- tag configuration;
- retrieval configuration;
- Schema;
- GraphRAG;
- recall testing.

The page uses a quiet enterprise hierarchy: breadcrumb, one page title, compact navigation, explanatory evidence and focused forms. It does not use a marketing hero or a wall of statistic cards.

### 3. Configured state is constrained by content state

The reference states that the vector model cannot be changed while the Knowledge Base contains uploaded documents or linked databases. This is a useful enterprise rule because an embedding-model change affects vector compatibility and cannot be treated as an ordinary cosmetic setting.

RAG4C should generalize this principle:

- content-affecting settings require explicit readiness evidence;
- unsafe settings are blocked rather than silently accepted;
- release/promotion is distinct from editing configured values;
- unknown readiness is not treated as ready.

### 4. Retrieval is an explicit serving contract

Tencent exposes semantic/hybrid retrieval, recall count, score threshold, reranking and database retrieval as user-visible policy. RAG4C already stores retrieval policy and immutable experiment evidence, but today the active serving state is not tied to a reproducible Knowledge Base release.

### 5. Document governance is operational, not decorative

The reference includes category changes, tags, expiry, source citations, custom source links, download permission and batch operations. RAG4C already has strong Documents, Taxonomy and Governance workspaces, so Stage 19 should link and contextualize them rather than rebuild them.

### 6. Enterprise and Workspace permissions remain layered

Tencent separates enterprise-level roles, Workspace-level roles, functional permissions and data permissions. RAG4C already follows this principle through Tenant membership, Dataset ACL, Workspace authorization and Knowledge Base ownership. Release permissions must remain a separate governed action rather than being inferred from ownership.

## RAG4C-specific conclusion

A UI-only settings clone would not satisfy the requested enterprise database goal. The next coherent vertical slice is:

```text
persistent Knowledge Base workspace shell
+ immutable Knowledge Base Release Manifest
+ environment promotion / rollback authority
+ Application release impact evidence
```

This preserves the Tencent-inspired enterprise hierarchy while using RAG4C's differentiators: immutable Document Versions, Source generations, Projection revisions, Dataset serving generation, audit, approval and idempotency.
