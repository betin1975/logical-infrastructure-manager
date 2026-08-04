# LIM Architecture

## Status and intent

This document defines the target architecture and the boundaries new code must
respect. LIM is currently a foundation: configuration, runtime, logging, and
SQLite persistence infrastructure exist, while inventory repositories, SSH,
plugins, and the job engine remain planned.
The architecture is intentionally explicit before those business capabilities
are implemented.

## System overview

LIM is a local-first modular monolith. A single application process will compose
domain services, an authoritative SQLite inventory, controlled SSH access, a job
engine, and provider plugins. A modular monolith keeps transactions and operation
simple while allowing clean boundaries to be extracted later if scale requires.

Dependency direction is inward:

```text
entry points -> bootstrap -> application services -> domain
                    |                 |
                    v                 v
              infrastructure <--- interfaces
                    |
           SQLite / SSHManager / plugins
```

The domain must not import transport, SQLite, SSH libraries, Docker APIs, or
vendor plugins. Infrastructure implements interfaces owned by its consumers.

## Modules

The current `app.config` module remains a supported public import. New modules
should evolve toward these boundaries without a compatibility-breaking move:

```text
app/
  config.py              Layered configuration (implemented)
  runtime.py             Runtime path lifecycle (implemented)
  logging_manager.py     Central logging and redaction (implemented)
  persistence/           SQLite policy, transactions, migrations, backups
  bootstrap.py           Composition root and lifecycle (planned)
  domain/                Entities, value objects, invariants, domain errors
  application/           Use cases, commands, queries, service interfaces
  infrastructure/
    ssh/                 SSHManager and remote execution data types
    jobs/                Durable job store and worker implementation
    plugins/             Discovery, validation, and plugin adapters
  interfaces/            Future CLI, HTTP, or UI entry points
```

This is a target layout, not permission to create empty abstractions. Add a
boundary when implementing a concrete use case, and preserve existing imports
with re-exports when moving code.

## Configuration

`ConfigManager` loads `config/default.yml`, overlays optional
`config/local.yml`, then applies `LIM_` environment variables. Configuration is
validated before an atomic reload. Consumers receive settings or resolved paths
through dependency injection; they do not reopen configuration files.

Relative paths are resolved during bootstrap against an explicit application
root. No domain or infrastructure component may depend on the process working
directory.

## Bootstrap

Bootstrap is the composition root and will be the only place that constructs the
application dependency graph. Its responsibilities are:

1. Load and validate configuration.
2. Construct `RuntimeManager`, then initialize and verify runtime paths and
   permissions.
3. Construct and initialize `LoggingManager` with configuration and runtime
   dependencies.
4. Construct and initialize `DatabaseManager`, then run `MigrationManager`.
5. Construct repositories and domain/application services.
6. Construct the single `SSHManager`.
7. Discover and validate plugins.
8. Start the job engine and selected entry point.
9. Shut down workers, SSH sessions, and database connections in reverse order.

Bootstrap will expose a dependency container, preferably a frozen dataclass. It
must accept injected replacements for deterministic tests.

## Inventory and database

SQLite is LIM's authoritative inventory. Remote observations become authoritative
only after validation and a committed inventory transaction. Plugin-local caches,
job output, YAML files, and remote state are not inventory authorities.

The default database is `runtime/data/lim.sqlite3`, resolved through
`ConfigManager` and `RuntimeManager`; consumers never hardcode it. The
`app.persistence` package owns SQLite policy with these rules:

- Foreign keys enabled on every connection.
- Ordered, versioned, transactional schema migrations.
- Parameterized SQL only.
- Explicit transaction boundaries in application services.
- Centrally configured busy timeout and journal mode.
- UTC timestamps and stable identifiers.
- SQLite online backup API for consistent backups.
- Repository interfaces testable against temporary databases.

`DatabaseManager` securely creates the file and opens a new connection for each
operation. Connections use autocommit mode at the driver boundary so only
`TransactionManager` can start or finish transactions. Foreign keys, busy
timeout, journal mode, synchronous mode, row factory, connection timeout,
transaction mode, and thread checks come from validated configuration. The
default is WAL with `NORMAL` synchronization, 5-second busy and connection
timeouts, `sqlite3.Row`, deferred transactions, and same-thread connection use.

`TransactionManager` commits successful outer scopes and rolls them back on any
exception. Nested scopes use uniquely named savepoints. SQLite authorization
rejects transaction-control statements from repository code, preventing hidden
commits. Repositories receive the active connection and contain explicit,
parameterized domain SQL; there is deliberately no generic CRUD framework.

Migrations are consecutively versioned Python records. Every migration runs and
records its history in one transaction, while separate migrations commit
independently so earlier successful versions survive a later failure. The initial
migration creates only `lim_schema_migrations`; no business table exists yet.
Inspection uses a read-only connection and never creates metadata.

`BackupManager` copies a live source with SQLite's online backup API into a mode
`0600` temporary file, validates it, then atomically publishes it under
`runtime/backups`. Candidate restore validation opens a contained, non-symlink
file read-only, runs `PRAGMA integrity_check`, validates migration metadata, and
reports the schema version. It never replaces the active database. Scheduling,
retention, off-host copies, and destructive restore orchestration are deferred.

Inventory entities are expected to include logical resources, connections,
provider identity, observed state, desired metadata, and audit timestamps. Their
schema is intentionally deferred until use cases are approved.

## SSH

`SSHManager` will be the only implementation allowed to create SSH connections,
execute remote commands, or transfer files. Jobs and plugins request remote
operations through its interface and never import an SSH library directly.

The manager will own:

- Connection lifecycle and bounded pooling.
- Strict known-host verification and trust policy.
- Authentication adapters without plaintext credential persistence.
- Connect, command, idle, and shutdown timeouts.
- Output size limits, decoding, and structured command results.
- Cancellation and cleanup.
- Auditable metadata with secret redaction.

SSH configuration and trust material live under configured paths. The `ssh/`
working directory is runtime state and is excluded from images and version
control except for its placeholder.

## Plugins

Plugins adapt external systems such as Linux, Docker, Cisco, FreePBX, MySQL, and
Redis. They are not independent applications and may not bypass LIM services.

Each plugin will provide a versioned manifest and implement a typed contract that
declares name, version, API compatibility, capabilities, configuration schema,
and supported discovery or action handlers. The plugin runtime will validate a
plugin before registration and reject duplicate names or incompatible API
versions.

Dependencies are injected. A plugin receives narrowly scoped inventory services,
an `SSHManager` interface when remote access is required, and a logger. It must
not open SQLite, load global configuration, read credentials directly, or create
its own SSH client. Plugin failures are isolated and translated into structured
results without corrupting inventory or worker state.

## Job engine

Potentially slow or remote operations execute as durable jobs rather than inside
request handlers. The job engine will define an explicit state machine such as
`queued`, `running`, `succeeded`, `failed`, and `cancelled`, with legal transitions
enforced centrally.

Jobs require stable IDs, type and schema version, creation/start/finish times,
bounded attempts, idempotency policy, progress metadata, structured result or
redacted error, and cancellation semantics. Job metadata belongs in SQLite;
large temporary artifacts may use configured `runtime/jobs` paths referenced by
the database. A process crash must not silently lose or duplicate accepted work.

The initial implementation should favor an in-process bounded worker pool backed
by SQLite. External brokers are not justified until measured requirements demand
them.

## Runtime and backups

All writable state is beneath configured runtime roots:

```text
runtime/data/       SQLite and durable state
runtime/jobs/       Bounded temporary job artifacts
runtime/logs/       Optional local file logs
runtime/backups/    Managed, restorable backups
ssh/                SSH trust and credential references
```

Runtime contents are excluded from Git and container images. Containers mount
only the writable paths they need. Startup will validate ownership and
permissions without silently weakening them. Retention, cleanup, backup, and
restore policies must be explicit before production use.

`RuntimeManager` owns creation and validation of the configured runtime tree. All
paths originate in `ConfigManager` and are resolved against the application root
injected by startup. Configured data, job, log, and backup directories must remain
inside the configured runtime root. Initialization creates missing directories
and `.gitkeep` placeholders, verifies actual write access, and can be called
repeatedly. Safe helper methods return single-component child paths without
creating inventory, job, log, or backup files.

## Logging

`LoggingManager` exclusively configures the dedicated `lim` logger namespace.
Components receive `ContextLogger` adapters rather than creating handlers or
configuring Python logging themselves. The manager owns handler lifecycle,
idempotent initialization, rotation settings, UTC formatting, and atomic
reconfiguration. Replacement handlers are constructed and validated before the
active handlers change, so failed reconfiguration preserves the last valid
setup.

LIM uses one rotating `runtime/logs/application.log` plus optional console output.
Using one file avoids duplicated routing handlers and keeps correlation across
components chronological. Bootstrap, SSH, and job records use the structured
`component` field; this satisfies component-specific logging without separate
files. Supported context also includes server ID, server name, job ID, operation,
and correlation ID. The rotating handler enforces file mode `0640` on the active
file and each new file created during rollover.

Redaction is applied before each handler and again after final formatting so it
covers ordinary messages, arguments, nested structures, context fields, exception
messages, and tracebacks. Sensitive configuration and environment values are
registered with the redactor. Password, token, secret, API-key, private-key,
authorization, and credential keys are always treated as sensitive. Callers still
must avoid logging raw secrets because no pattern-based control can guarantee
recognition of every credential format.

## Interfaces

No permanent CLI, HTTP API, or UI transport has been selected. Future interfaces
must call application use cases and must not contain SQL, SSH, plugin orchestration,
or domain policy. Authentication and authorization belong at the interface and
application-service boundaries, not inside plugins.

## Folder-structure review

The existing top-level layout is compatible with the target architecture and is
being retained. Recommended evolution:

- Keep `app.config` stable; introduce `domain`, `application`, `infrastructure`,
  and `interfaces` only with their first real use cases.
- Keep provider code in `plugins/` and add manifests when the plugin API exists.
- Use `docs/` for detailed operator material while keeping core governance files
  discoverable at the repository root.
- Reserve `scripts/` for thin wrappers around tested Python APIs.
- Reserve `ansible/` for an adapter driven through application services; it must
  not become a second source of inventory.
- Reserve `docker/` for entrypoints or support assets that cannot remain in the
  root Dockerfile or Compose file.

No directories are moved now, avoiding speculative churn and import breakage.

## Technical Debt

The following work is recommended but intentionally not implemented as business
functionality in this foundation change:

1. Define approved inventory use cases and a normalized SQLite schema.
2. Design the first domain repository interfaces and inventory migrations only
   after inventory use cases and retention requirements are approved.
3. Define production backup scheduling, retention, quotas, off-host copies, and
   destructive restore orchestration with operator confirmation and rollback.
4. Design and implement the sole `SSHManager`, including host-key policy,
   credential abstraction, timeouts, cancellation, output limits, and audit data.
5. Define a versioned plugin manifest and typed capability contract before
   implementing provider plugins.
6. Define the durable job state machine, recovery, idempotency, retries,
   cancellation, concurrency limits, retention, and artifact cleanup.
7. Implement bootstrap and graceful shutdown after the first real application
   entry point is selected.
8. Select and threat-model the operator interface (CLI, HTTP API, or UI),
   including authentication, authorization, CSRF/session policy where relevant,
   rate limits, and audit requirements.
9. Replace the container's foundation validation command with the real service
   command, then add a meaningful health check and resource limits.
10. Evaluate JSON output and external aggregation only after an operational need
    exists; structured context, correlation IDs, central redaction, and local
    rotation are implemented.
11. Introduce typed configuration schemas while preserving layered loading and
    actionable validation errors.
12. Define secret-provider and credential-reference interfaces; never store
    plaintext secrets in SQLite or configuration files.
13. Add CI for Python 3.12 tests, Ruff, type checking, container builds,
    dependency auditing, and secret scanning.
14. Select a static type checker and establish an incremental strictness policy.
15. Establish a coverage baseline and enforce meaningful coverage for critical
    inventory, migration, SSH, and job-state code.
16. Adopt a reproducible dependency lock and automated dependency/security update
    workflow before the first production release.
17. Add package metadata and release automation when LIM has an installable CLI
    or service artifact.
18. Define runtime ownership, disk quotas, backup retention, disaster recovery,
    and upgrade/rollback procedures for supported deployments.
19. Add integration tests using disposable SQLite databases and SSH test servers;
    keep them isolated from the default unit suite.
20. Convert placeholder provider directories into real plugins only after the
    contract exists; remove any placeholder that is no longer on the roadmap.
