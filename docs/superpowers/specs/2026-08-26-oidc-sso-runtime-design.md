# RAG4C OIDC Authorization Code + PKCE Runtime Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai  
**Stage:** 12  
**Target revision:** `0024_oidc_sso_runtime`

## Objective

Connect the Stage 9 OIDC control-plane configuration to a real Authorization Code + PKCE login runtime for existing active tenant members. SAML ACS and automatic JIT account creation remain out of scope.

## Database contract

### `tenant_oidc_login_transactions`

- tenant/provider scope;
- state digest and nonce digest only;
- encrypted PKCE code verifier and key version;
- redirect URI and expiry;
- status `pending | consumed | failed | expired`;
- consumed/failed evidence and sanitized error code;
- revision and timestamps;
- state digest globally unique;
- provider/tenant FK.

### `tenant_oidc_subject_links`

- tenant/provider/account mapping;
- issuer and subject digest;
- normalized email snapshot;
- revision and last-login timestamp;
- unique tenant/provider/subject and tenant/provider/account;
- Account and active-provider tenant FKs.

### `tenant_sso_sessions`

- opaque session ID and session token hash only;
- tenant/account/provider scope;
- status `active | revoked | expired`;
- expiry, last-seen, IP/UA hashes;
- revision and lifecycle evidence;
- tenant-scoped member/provider FKs;
- raw session/actor token never persisted.

## Cryptography

- State, nonce and PKCE verifier use cryptographically secure randomness.
- DB stores state/nonce digests, never raw values.
- PKCE verifier is encrypted with Fernet using a key derived from the configured KnowledgeOps signing secret and explicit domain separation.
- Key version is persisted for future rotation.
- Callback uses constant-time digest comparison.
- Session token is returned once and stored only as a digest.

## OIDC client

Define injectable `OidcRuntimeClient`:

- build authorization URL;
- exchange authorization code with code_verifier;
- validate ID token issuer, audience, signature, expiry, nonce and email claims.

Production adapter uses optional `authlib` and bounded `httpx` networking. Tests use an in-memory client and perform no external requests.

## Runtime flow

### Start

```text
POST /api/enterprise/sso/oidc/start
```

Input: tenant ID/provider ID and redirect URI.

Requirements:

- provider is active OIDC primary;
- trusted domain verified;
- runtime config internally valid;
- redirect URI matches a server allowlist and is HTTPS or direct loopback development URI;
- create DB transaction and return authorization URL plus one-time state/nonce cookie evidence.

### Callback

```text
POST /api/enterprise/sso/oidc/callback
```

- lock state transaction by digest;
- reject expired/consumed/failed state;
- decrypt PKCE verifier;
- exchange code and validate ID token through injected client;
- verify issuer/client audience/nonce and normalized email domain;
- require an existing Account and active TenantMember;
- create/update subject link;
- create SSO session and issue a short-lived signed KnowledgeActor token;
- consume state, write audit, commit once.

No unknown Account or membership is automatically created.

### Session revoke

```text
POST /api/enterprise/sso/sessions/{session_id}/revoke
```

Owner/admin may revoke a tenant session; an actor may revoke its own session. Revision-fenced and audited.

## Security

- Exact provider/tenant predicates on every query.
- OIDC callback never trusts frontend email/subject claims outside validated ID token results.
- Raw code, state, nonce, verifier, ID/access/refresh tokens and raw session tokens never enter audit, logs, replay payloads or database plaintext.
- Refresh tokens are not requested in Stage 12.
- Callback error responses are stable and sanitized.
- Failed callback rolls back session, subject link, transaction consume and audit together.
- Login start requires a signed active KnowledgeActor whose tenant exactly matches the requested tenant; a future public login entry point must be a separate rate-limited contract.
- Discovery, authorization, token and JWKS endpoints are revalidated at runtime as HTTPS public-network destinations, redirects are disabled, and JSON bodies are streamed under a hard byte cap.
- Callback atomically claims a pending state, releases the database transaction and serialization lock before the IdP exchange, then finalizes through a second revision-fenced transaction.
- OIDC-issued KnowledgeActor tokens carry the signed SSO session ID; every authenticated request verifies that session remains active and unexpired, so revoke takes effect immediately.

## Frontend

- Enterprise Identity Center shows `oidc_runtime_ready` only at 0024 with active provider.
- Add TDesign SSO runtime evidence, Start Login button and callback status page.
- Callback page accepts code/state only from URL long enough to submit, then removes them with `history.replaceState` before rendering the result.
- Actor token is stored using the existing knowledge actor token mechanism; no ID/access token is exposed.
- Desktop/dark/375/280 states and explicit SAML `not_connected` remain.

## Production boundary

No real external IdP login or production migration is executed in this stage. Tests use injected OIDC results. Production adapter may perform bounded network calls only when an operator/user invokes login.
