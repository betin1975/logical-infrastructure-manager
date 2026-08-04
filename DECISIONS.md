# Architecture Decision Records

This file records accepted LIM architecture decisions. A decision is changed by
adding a new ADR that explicitly supersedes it; accepted history is not rewritten.

## ADR-0001: SQLite is the authoritative persistent store

- **Status:** Accepted
- **Context:** LIM needs a local-first, auditable store for future inventory,
  jobs, alerts, plugins, users, and audit data without operating an external
  database service.
- **Decision:** SQLite is LIM's authoritative persistent store. Files, remote
  observations, caches, plugin responses, and job artifacts are not alternate
  authorities. The database is created only under the configured runtime data
  directory with foreign-key enforcement and centrally defined pragmas.
- **Consequences:** Domain persistence must use repositories and explicit
  transactions. SQLite concurrency limits must be measured before considering a
  different database, and any future replacement requires a new ADR and migration
  plan.

## ADR-0002: Connections and transactions are operation-scoped

- **Status:** Accepted
- **Context:** A global mutable SQLite connection obscures transaction ownership,
  creates unsafe cross-thread behavior, and makes tests order-dependent.
- **Decision:** `DatabaseManager` creates short-lived, operation-scoped
  connections. `TransactionManager` owns `BEGIN`, commit, rollback, and nested
  savepoints. Repositories receive the active connection through dependency
  injection and may not create managers, commit, roll back, or run migrations.
- **Consequences:** Call sites must make transaction boundaries explicit. A
  repository instance is scoped to its injected connection. Each concurrent
  thread opens its own connection; connections are not pooled or shared.

## ADR-0003: Schema changes use ordered Python migrations

- **Status:** Accepted
- **Context:** LIM needs deterministic schema evolution but does not yet need an
  ORM or a large migration framework.
- **Decision:** Schema changes are immutable, consecutively versioned Python
  `Migration` records with an `upgrade(connection)` function. Every migration is
  applied in its own transaction and recorded in `lim_schema_migrations`.
  Domain repositories do not manage or invoke migrations.
- **Consequences:** Failed migrations roll back without undoing earlier successful
  versions. Duplicate, missing, malformed, renamed, or unknown migration metadata
  fails closed. Downgrade migrations are intentionally unsupported until a
  concrete release policy requires them.

## ADR-0004: Backups use SQLite's online backup API

- **Status:** Accepted
- **Context:** Copying a live database file, especially in WAL mode, can produce
  an inconsistent backup.
- **Decision:** `BackupManager` uses SQLite's online backup API to create a
  restrictive temporary database in the configured backup directory, validates
  it, and atomically publishes it. Backups contain only the SQLite database.
- **Consequences:** The source remains usable during backup, and partial files are
  cleaned after failure. Backup retention, scheduling, quotas, and off-host copies
  remain operational follow-up work.

## ADR-0005: Restore is validation-only

- **Status:** Accepted
- **Context:** Replacing an active authoritative database is destructive and needs
  coordinated shutdown, backup, rollback, ownership, and operator confirmation.
- **Decision:** The persistence foundation only validates candidates read-only
  with SQLite integrity checks and migration metadata inspection. It never
  replaces the active database.
- **Consequences:** Production restore orchestration is deliberately deferred. A
  future implementation requires a separate ADR, operator workflow, failure
  recovery design, and end-to-end restore tests.
