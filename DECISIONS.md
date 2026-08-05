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

## ADR-0006: Domain persistence crosses repository interfaces only

- **Status:** Accepted
- **Context:** Allowing business logic, SSH, plugins, or jobs to execute SQL would
  couple domain behavior to SQLite, scatter transaction policy, and permit
  persistence invariants to be bypassed.
- **Decision:** Only repository implementations may execute SQL that reads or
  persists domain state. Business logic depends on repository interfaces and
  never receives raw SQLite connections. `SSHManager`, plugins, and jobs never
  import SQLite, execute SQL, construct repositories, or access
  `DatabaseManager`. Bootstrap constructs concrete repositories and injects their
  interfaces. Persistence infrastructure may execute internal schema, transaction,
  backup, and validation SQL only within its documented responsibility.
- **Consequences:** Domain tests use repository doubles without SQLite. Concrete
  repository integration tests use temporary databases. New code outside
  `app.persistence` is automatically checked for forbidden SQLite imports.

## ADR-0007: Inventory is the authoritative infrastructure model

- **Status:** Accepted
- **Context:** SSH, plugins, polling, and jobs will observe overlapping and
  occasionally conflicting infrastructure state.
- **Decision:** The validated inventory model is LIM's authoritative
  representation of managed infrastructure. External observations become
  authoritative only after an `InventoryService` transition commits through an
  inventory repository. Remote systems, caches, plugin output, and job artifacts
  remain observations.
- **Consequences:** Inventory UUIDs and versions are stable coordination keys.
  Future components consume inventory interfaces and must define provenance and
  conflict behavior before applying observations.

## ADR-0008: Inventory domain models are immutable

- **Status:** Accepted
- **Context:** Mutable server objects make validation, optimistic concurrency,
  logging, and rollback ambiguous.
- **Decision:** `Server`, `Tag`, `Label`, and repository results are frozen
  dataclasses. Server changes create a newly validated instance with an incremented
  `inventory_version` and UTC `updated_at` value. State categories use enums.
- **Consequences:** Callers cannot modify accepted inventory in place. Repository
  updates can reject stale versions deterministically, and tests compare complete
  values without hidden mutation.

## ADR-0009: Inventory deletion is soft by default

- **Status:** Accepted
- **Context:** Hard deletion destroys operational identity and makes accidental
  or malicious removal difficult to recover or investigate.
- **Decision:** Server deletion records `deleted_at`, disables the server, and
  changes its status without removing normalized records. Hostname and addresses
  remain reserved. Restore clears deletion state and returns the server disabled.
- **Consequences:** Default queries hide deleted servers, while explicit
  repository inspection can include them. Purge and retention require a future
  approved design; no destructive delete API exists.

## ADR-0010: Inventory changes go through InventoryService

- **Status:** Accepted
- **Context:** Letting SSH, plugins, jobs, or interfaces write repositories would
  duplicate lifecycle rules and bypass validation and meaningful logging.
- **Decision:** `InventoryService` is the only business mutation gateway. It owns
  registration, lifecycle, discovery, health, polling, failure, tag, label, delete,
  and restore transitions. It depends on `InventoryRepository` and a contextual
  logger and contains no SQL. Concrete repositories are constructed only by the
  composition root.
- **Consequences:** Future components receive the service or narrower application
  interfaces. Bulk workflows may require new service methods, but must not bypass
  the mutation boundary.

## ADR-0011: Discovery represents observations, not truth

- **Status:** Accepted
- **Context:** Collectors may report incomplete, stale, conflicting, or failed
  infrastructure facts that must remain auditable without silently changing LIM's
  accepted model.
- **Decision:** Discovery owns immutable observation history and lifecycle state.
  Observations reference inventory servers but are never authoritative inventory.
- **Consequences:** Collector output is validated and retained independently.
  Expired history is purged only through an explicit cutoff-based operation.

## ADR-0012: Inventory remains the sole authoritative infrastructure model

- **Status:** Accepted
- **Context:** Adding durable discovery history could otherwise be interpreted as
  a second infrastructure authority.
- **Decision:** SQLite inventory tables remain the only accepted infrastructure
  model. Discovery tables contain evidence only, regardless of source or status.
- **Consequences:** Reads requiring accepted state use inventory services. A
  successful discovery observation alone never changes operational targeting.

## ADR-0013: Only InventoryService promotes discovery facts

- **Status:** Accepted
- **Context:** Direct discovery-to-inventory SQL or repository writes would bypass
  validation, optimistic concurrency, provenance, and future conflict policy.
- **Decision:** Only an approved `InventoryService` operation may accept discovery
  facts into authoritative inventory. `DiscoveryService` never updates inventory,
  and its repository cannot change synchronization state.
- **Consequences:** Promotion is deliberately not implemented in this foundation.
  Its future design must define field ownership, conflicts, audit records, and
  retries before adding behavior.

## ADR-0014: SSHManager is the sole SSH implementation

- **Status:** Accepted
- **Context:** Multiple SSH clients would scatter trust, credential, timeout,
  output, retry, and audit policy.
- **Decision:** Only `app.ssh.SSHManager` may inspect host keys, modify LIM trust,
  execute SSH commands, or transfer files. It returns typed facts and never
  imports persistence or mutates Inventory or Discovery.
- **Consequences:** Jobs, collectors, plugins, and interfaces must receive this
  boundary. Architecture tests reject subprocess/OpenSSH ownership elsewhere.

## ADR-0015: LIM uses system OpenSSH with structured commands

- **Status:** Accepted
- **Context:** The supported platforms already provide a mature OpenSSH client;
  adding a Python SSH library would expand dependency and security surface.
- **Decision:** LIM invokes explicitly configured `ssh`, `scp`, and `ssh-keyscan`
  executables through argument arrays and never a local shell. Remote commands are
  executable/argument tuples whose values are individually POSIX-quoted for the
  OpenSSH remote-shell protocol. Arbitrary shell text is unsupported.
- **Consequences:** Images install `openssh-client`. Output is drained and bounded,
  processes have a minimal environment and separate session, and only explicitly
  transient connection failures are retried.

## ADR-0016: Host trust is strict and application-owned

- **Status:** Accepted
- **Context:** Personal known-hosts and automatic trust-on-first-use are neither
  deterministic nor safe for unattended infrastructure management.
- **Decision:** Strict host verification is mandatory against LIM's isolated
  runtime `known_hosts`. Automatic trust is forbidden. New and replacement trust
  require an explicit operation, a freshly scanned key, and matching SHA256
  fingerprint confirmation.
- **Consequences:** Unknown and changed keys fail closed. Atomic updates and
  post-write rescans prevent corruption and detect replacement races. Diagnostics
  never alter trust.

## ADR-0017: Private identities and writable trust are separated

- **Status:** Accepted
- **Context:** A writable credential mount would allow accidental or compromised
  code to overwrite authentication material.
- **Decision:** Admin and monitor private keys are separately mounted read-only,
  validated beneath an approved credential root, and referenced only by enum.
  Application trust is a separate writable mode `0600` runtime file.
- **Consequences:** LIM does not generate, copy, rotate, return, or log private
  keys. Compose uses individual read-only mounts and has no broad writable SSH
  mount; host files must already have secure ownership and mode.
