# RAG4C Enterprise Access Graph Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai  
**Status:** Stage 4 approved continuation of the active enterprise KnowledgeOps goal

## Objective

Extend the Tencent/TDesign-inspired enterprise administration surface with an authoritative, tenant-isolated access graph. The graph must represent organization units, user groups, invitations, and dataset-scoped grants without inventing live data or weakening the existing fixed-role authorization boundary.

## Product ruling

The first access-graph wave is read-only at the API and UI layers. Empty authenticated results are valid enterprise states. Mutations remain unavailable until a later wave can provide audit-complete, revision-fenced workflows for organization changes, group membership, invitation issuance, and ACL changes.

This ruling favors a truthful management plane over placeholder controls that appear writable but cannot be made safe yet.

## Database contract

Migration `0017_enterprise_access_graph` introduces:

- `tenant_organization_units` for tenant-scoped hierarchy nodes.
- `tenant_groups` for governed group identity.
- `tenant_group_members` for account membership in governed groups.
- `dataset_access_grants` for account, group, or organization-unit dataset roles.
- `tenant_invitations` with token hashes only; raw invitation tokens are never persisted.

Every mutable aggregate carries lifecycle state and optimistic revision where applicable. Every listing path has tenant-leading indexes. Cross-tenant relations are rejected through composite uniqueness, foreign keys, service validation, or all three.

## Authorization contract

- Organization units, groups, group members, and invitations require `knowledge.manage`.
- Dataset grants require `knowledge.manage` with the path dataset resolver.
- All business reads use the request-scoped read-only catalog engine.
- Missing 0017 schema fails closed with HTTP 503 and `enterprise_access_graph_migration_required`.
- The frontend never requests a capability whose server-owned state is unavailable.

## API contract

- `GET /api/enterprise/organization-units`
- `GET /api/enterprise/groups`
- `GET /api/enterprise/groups/{group_id}/members`
- `GET /api/enterprise/invitations`
- `GET /api/knowledge-bases/{dataset_id}/access-grants`

Collections use descending opaque/simple keyset pagination through `before_id`, bounded `limit`, server filtering, and an explicit `next_before_id`.

Invitation responses exclude `token_hash` unconditionally.

## Frontend contract

The enterprise page adds one full-width `企业访问图谱` surface using TDesign Tabs:

- 组织架构
- 用户组
- 知识库 ACL
- 成员邀请

The surface uses dense desktop tables and compact mobile cards or horizontal scrolling. It includes real loading, empty, 401, 403, 503, migration-required, and load-more states. It does not create synthetic organization nodes, groups, grants, invitations, or counts.

## Production boundary

The real MySQL catalog remains at `0007_chunk_rev` as of 2026-08-26 and is not upgraded by this work. Migration 0016 is already blocked by three active ownerless tenants, so 0017 is also unavailable in production until the approved owner/member bootstrap, backup verification, restore rehearsal, and migration change window are completed.
