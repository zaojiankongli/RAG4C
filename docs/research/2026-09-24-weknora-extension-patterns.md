# Local WeKnora extension-pattern review (2026-09-24)

## Source inspected

- Local checkout: `C:\Users\饶策\Desktop\me\WeKnora-0.8.0`
- HEAD at review time: `6408ab5`
- Read-only files reviewed:
  - `internal/application/service/retriever/factory.go`
  - `internal/application/service/retriever/factory_test.go`
  - `internal/infrastructure/docparser/engine_registry.go`
  - `internal/infrastructure/docparser/engine_registry_reader_test.go`
  - `internal/infrastructure/docparser/engine_registry_test.go`

The checkout has unrelated working-tree modifications, but the reviewed files
were not listed as modified. No WeKnora files were edited and no WeKnora tests
were run.

## Patterns worth adapting

### 1. Factory boundary carries authorization and error semantics

`retriever/factory.go` separates store-bound and unbound construction paths,
keeps dependencies explicit (`TenantStoreOwnership`, engine registry, tenant
identity), and verifies tenant ownership before resolving a bound engine. It
uses typed sentinel errors to distinguish forbidden, not-found, retryable
unavailable, missing tenant context, and caller cancellation. Internal logs can
retain store identifiers while the sentinel messages remain safe to expose.

The useful lesson for RAG4C is that a factory/adapter boundary should own
selection **and** its failure contract; callers should not reconstruct
ownership checks or collapse “not found” and “temporarily unavailable” into
one fallback. WeKnora also keeps separate entry points for synchronous context
resolution and async payload resolution while sharing the verified bound path.

### 2. Engine registration is a capability contract, not only a name map

`EngineRegistration` combines a stable name, description, supported file
types, availability check, and per-request `NewReader` construction. `ReaderDeps`
injects optional services explicitly. `ListAllEngines` merges local engines
with remote discovery, and `NewReader` routes remote-only parser engines
through the docreader adapter.

This supports Strategy + Adapter design: the selected implementation declares
what it can do, how it is available, and how it is constructed. The tests cover
local/simple/remote routing, unavailable dependencies, credentials, and
build-tag-dependent engines.

### 3. Preserve intentional fallback boundaries

WeKnora's parser registry deliberately routes unknown names to the remote
docreader so Python-only engines can be discovered without a Go release. That
is a parser product decision, not a universal registry rule. RAG4C must not
copy it for projection targets, authorization-sensitive capability states, or
dead-letter replay: an unknown backend/operation must remain fail-closed.

Likewise, do not copy the local parser registry's mutable `init()`-appended
slice as a second registry kernel. RAG4C's existing
`core.providers.ProviderRegistry` already supplies the common synchronized
registration mechanism and is the required reuse point.

## RAG4C application

- Continue using explicit factories and ports at infrastructure boundaries,
  with error taxonomies that preserve retryability and avoid leaking secrets.
- Keep adapters responsible for translating a target's external representation
  into a stable internal contract.
- Require registration-time validation and real-path tests for custom target
  policies; unknown or incomplete runtime combinations fail closed.
- For the current projection work, worker strategy, revision/lifecycle policy,
  durable-delete policy, and dead-letter requeue policy are separate declared
  contracts that are joined by a fail-closed runtime preflight. The consistency
  report reader remains a distinct open design problem; requeue registration
  does not imply read-side reconciliation support.

This review corroborates the earlier RAG4C design notes at
`docs/2026-09-23-projection-operation-registry.md` and
`docs/2026-09-23-projection-target-producer-registry.md`; it does not copy
WeKnora implementation code or its remote fallback behavior.
