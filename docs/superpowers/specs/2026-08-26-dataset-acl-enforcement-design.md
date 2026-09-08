# RAG4C Dataset ACL Enforcement Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai

## Objective

Make the enterprise access graph authoritative for dataset-scoped authorization without breaking catalogs that have not yet reached the access-graph schema.

## Compatibility ruling

- Catalogs without the access-graph tables keep the existing tenant-role policy.
- Catalogs with the tables but no active grants for one dataset also keep tenant-role fallback.
- The first active grant on a dataset opts that dataset into ACL enforcement.
- Once opted in, actors without a matching grant are denied. Query failures do not fall back.

This permits an incremental rollout while preventing an ACL-enabled dataset from silently bypassing its grant policy.

## Bypass ruling

- Tenant owner and admin retain full access.
- The persisted dataset owner retains manager access.
- Bypass identity is read from the database, never from browser claims.

## Grant subjects

- Direct account grants.
- Active user-group membership grants.
- Active organization-unit membership grants.
- Removed memberships and revoked grants do not contribute.
- Cross-tenant rows are ignored and treated as integrity damage if they can affect a decision.

## Dataset roles

- viewer: knowledge.read
- editor: knowledge.read, knowledge.write, knowledge.delete
- manager: knowledge.read, knowledge.write, knowledge.delete, knowledge.manage, knowledge.audit

Multiple matching grants contribute the union of their permissions.

## Summary contract

The dataset access summary reports the evaluated enforcement mode, matched grants, effective permissions, support flags, and warnings. The frontend presents this as server-owned enforcement evidence and never infers effective access from a visible grant list.

## Production boundary

The real MySQL catalog remains unmigrated. No real ACL is enabled until the approved owner bootstrap, backup verification, restore rehearsal, and migration window are complete.
