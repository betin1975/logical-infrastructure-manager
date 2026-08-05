# LIM Architecture

## Status and intent

This document defines the target architecture and the boundaries new code must
respect. LIM is currently a foundation: configuration, runtime, logging, SQLite
persistence, authoritative server inventory, discovery observation history,
SSHManager, a read-only Linux collector, and remote host bootstrap exist, while
plugins, polling, and the job engine remain planned.
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

All domain-state persistence crosses repository interfaces. Business logic never
imports SQLite, executes SQL, or receives a raw connection. Only repository
implementations execute domain SQL. `SSHManager`, plugins, and jobs consume
injected repository interfaces and must never access `DatabaseManager` or execute
SQL. Migration, transaction, backup, and database-policy components may execute
their narrowly scoped internal SQLite operations, but cannot persist domain state.

## Modules

The current `app.config` module remains a supported public import. New modules
should evolve toward these boundaries without a compatibility-breaking move:

```text
app/
  config.py              Layered configuration (implemented)
  runtime.py             Runtime path lifecycle (implemented)
  logging_manager.py     Central logging and redaction (implemented)
  bootstrap/             Remote monitor-account provisioning (implemented)
  collectors/linux/      Read-only Linux observation collector (implemented)
  inventory/             Immutable model, repository protocol, service
  discovery/             Observation model, repository protocol, service
  persistence/           SQLite policy, transactions, migrations, backups
  __main__.py            Current foundation composition root (implemented)
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

Application bootstrap is the composition root and the only place that constructs
the dependency graph. It is distinct from `BootstrapService`, which provisions a
remote monitor account. Its responsibilities are:

1. Load and validate configuration.
2. Construct `RuntimeManager`, then initialize and verify runtime paths and
   permissions.
3. Construct and initialize `LoggingManager` with configuration and runtime
   dependencies.
4. Construct and initialize `DatabaseManager`, then run `MigrationManager`.
5. Construct repositories and domain/application services.
6. Construct the single `SSHManager`.
7. Construct and locally initialize `BootstrapService` without network access.
8. Discover and validate future plugins.
9. Start the future job engine and selected entry point.
10. Shut down workers, SSH sessions, and database connections in reverse order.

The current `app.__main__` is a minimal one-shot composition root. A future
long-running entry point should expose a frozen dependency container and graceful
shutdown. Constructors accept injected replacements for deterministic tests.

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
Application services depend on repository interfaces rather than concrete SQLite
repositories, connections, or `DatabaseManager`.

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

The inventory domain is implemented in `app.inventory` without importing SQLite.
`Server` is an immutable aggregate containing stable UUID and hostname identity,
primary and optional management addresses, platform and operating-system facts,
classification, enabled and managed policy, discovery and health state, poll and
bootstrap timestamps, soft deletion, synchronization state, optimistic
`inventory_version`, tags, labels, and operator notes. `Tag` and `Label` are
immutable normalized value objects; all state categories use enums.

`InventoryService` is the only business mutation gateway. It registers and
updates servers, controls enabled and lifecycle states, records discovery,
health, and polling outcomes, maintains failures, tags, and labels, and performs
soft delete and restore. It depends only on `InventoryRepository` and a contextual
logger. Future SSH, plugin, polling, and job code must call this service or other
approved application services rather than repositories directly.

Schema version 2 adds only normalized inventory tables:

- `inventory_servers` owns the server aggregate, state, timestamps, and optimistic
  version. Case-insensitive hostname uniqueness reserves identity across deletion.
- `inventory_server_addresses` stores primary and management addresses. Its
  address primary key enforces global cross-kind uniqueness.
- `inventory_tags` stores case-insensitive tag identities.
- `inventory_server_tags` owns the many-to-many server/tag relationship.
- `inventory_server_labels` stores unique per-server key/value labels.

`SQLiteInventoryRepository` is the sole inventory SQL implementation. Every
method owns one explicit transaction, uses parameters for values, hydrates domain
objects before returning, and translates infrastructure failures into inventory
exceptions. Updates compare the previous `inventory_version`; stale writes fail.
Soft deletion is non-destructive and continues reserving hostname and addresses,
making restoration deterministic.

Inventory indexes are intentional and covered by migration tests:

- SQLite's hostname unique index supports identity lookup and duplicate defense.
- The address primary key supports address lookup and cross-kind uniqueness.
- `idx_inventory_servers_enabled` supports active-operation selection.
- `idx_inventory_servers_managed` supports managed-target selection.
- `idx_inventory_servers_health` supports health-state lookup.
- `idx_inventory_servers_status` supports lifecycle and deleted-state operations.
- `idx_inventory_addresses_server` supports aggregate address hydration.
- `idx_inventory_server_tags_tag` supports reverse tag lookup.
- `idx_inventory_labels_key_value` supports label-based inventory lookup.

Search currently uses bounded, parameterized substring matching across hostname,
display name, environment, location, addresses, tags, and labels. FTS is deferred
until measured inventory size and query requirements justify it.

## Discovery

Discovery records collected facts and historical evidence; it is not an alternate
inventory authority. Every `DiscoveryObservation` references an inventory server
UUID and records its source, collection timing, collector version, host identity,
operating-system and hardware facts, network facts, services, packages,
containers, processes, bounded platform metadata, lifecycle, and synchronization
state. Models are immutable, timestamps are UTC, and state categories are enums.

`DiscoveryService` is the SQL-free observation lifecycle boundary. It records
pending observations, marks them successful or failed, expires completed history,
retrieves latest/history views, and explicitly purges only expired observations
older than a supplied UTC cutoff. It never invokes `InventoryRepository` or
changes authoritative inventory. A future acceptance workflow must call
`InventoryService`; discovery synchronization cannot be changed through the
discovery lifecycle repository.

Schema version 3 adds normalized discovery-owned tables for observations,
interfaces, addresses, disks, services, packages, containers, processes, and
namespaced key/value metadata. Foreign keys cascade observation children and
prevent observations for nonexistent inventory servers. Collected facts are
immutable after insertion; lifecycle changes use optimistic observation versions.

Discovery indexes match implemented queries:

- Server UUID plus descending discovery time supports latest and history reads.
- Source plus discovery time supports collector-specific history.
- Status plus discovery time supports outcome filtering.
- State plus update time supports lifecycle queries and retention cleanup.
- Address value supports fact search; child foreign-key indexes support hydration.
- Metadata namespace/key supports future bounded metadata inspection.

`SQLiteDiscoveryRepository` is the only discovery SQL implementation. It uses
short explicit transactions, parameterized values, bounded pages, normalized
aggregate hydration, rollback-safe child insertion, and optimistic lifecycle
updates. Search covers hostname, FQDN, addresses, services, and packages. Cleanup
is intentionally destructive only for already-expired history and requires an
explicit cutoff; automatic scheduling and retention policy remain future work.

## SSH

`SSHManager` is the only implementation allowed to create SSH connections,
execute remote commands, or transfer files. Jobs and plugins request remote
operations through its interface and never import an SSH library directly.
It returns typed facts and never receives InventoryService, DiscoveryService,
SQLite, repositories, or persistence managers.

The implemented system-OpenSSH boundary owns:

- Explicit argument-array process execution with no local shell.
- Mandatory strict host verification against application-owned trust.
- Authentication adapters without plaintext credential persistence.
- Connect, command, idle, and shutdown timeouts.
- Output size limits, decoding, and structured command results.
- Future-compatible cancellation and process-group cleanup.
- Auditable metadata with secret redaction.

`OpenSSHProcessRunner` is the only application code importing `subprocess`. It
uses a minimal locale-only environment, closed file descriptors, null stdin,
separate process sessions, concurrent output draining, and configured byte/time
limits. Executable paths are explicit and validated; personal SSH configuration,
personal known-hosts, password authentication, keyboard-interactive
authentication, agent identity selection, and trust-on-first-use are disabled.

Structured remote commands are an executable plus an argument tuple. Each item is
validated and independently POSIX-quoted because the OpenSSH wire protocol passes
one command string to a remote shell. No arbitrary shell-text API exists. SCP is
limited to one explicit regular file and safe absolute remote paths; recursive
transfer and permission escalation are absent.

The admin and monitor identity files live beneath a configured credential root,
must be non-symlink regular files with no owner-write, group, or world bits, and
are never copied or modified. Application `known_hosts` is a separate mode `0600`
file directly beneath runtime data. `SSHTrustStore` scans public keys, calculates
SHA256 fingerprints, detects unknown/changed/multiple key types, and uses locked,
fsynced atomic replacement. New and changed trust require explicit methods and a
fresh matching fingerprint; post-write confirmation rolls back a racing change.

Commands return bounded stdout/stderr, return code, UTC timestamps, duration,
timeout and truncation flags, attempt count, correlation ID, and a typed failure.
Only connection refusal and connection timeout are transiently retried. Trust,
authentication, DNS, cancellation, command timeout, output-limit, local-process,
and remote nonzero failures are never automatically retried. Diagnostics inspect
trust without mutation and attempt monitor authentication only for a trusted host.

## Remote bootstrap

`BootstrapService` is an application service for preparing the constrained
monitor identity on one existing inventory target. It is not the application
composition root. It depends on `SSHManager`, `InventoryService`, configuration,
runtime paths, and contextual logging; it imports neither repositories,
persistence, discovery, nor `LinuxCollector`. SSHManager remains the only remote
transport, and InventoryService remains the only inventory mutation boundary.

Initialization is deliberately local: startup validates the configured public
key, standalone artifact, paths, modes, utility paths, schema versions, and
identity availability without making a network connection. An explicit request
then executes a typed 15-step plan: validate inventory and identities, verify
strict trust and admin authentication, establish Linux and `sudo -n`
prerequisites, inspect/create/repair the monitor account, prepare directories,
stage files, atomically install the marked forced key and collector, clean
temporary state, verify the complete result, and finally record success.

The admin public key must already be installed by an operator. Bootstrap never
accepts passwords, enables agent forwarding, modifies host trust, or transfers a
private key. The monitor account has a locked password and is removed from the
configured privileged groups. Its LIM-owned authorized-key line uses `restrict`
plus a forced absolute collector command. A bounded remote Python helper performs
path/type checks and atomic replacement while preserving all unrelated key lines.
Every mutating step is idempotent, and failed partial state is safe to retry.
The default shell is `/bin/sh` because OpenSSH evaluates a forced command through
the account's shell; the `restrict` option, forced command, locked password, and
absence of privileged groups constrain that necessary shell capability.

The deployed artifact is a self-contained, standard-library Python 3.9+ program.
It accepts no arguments, runs a fixed read-only command catalog without a shell,
discards stderr, bounds time/output/document size, distinguishes absent, inactive,
and unknown services, and emits one versioned JSON document. Post-verification
checks account ownership, password lock, forbidden groups, file modes/types,
artifact digest, direct execution, monitor authentication, forced-command
confinement, schema/version, and plausible host identity. Only complete
verification permits `InventoryService.record_bootstrap_success()`.

Bootstrap is a repair workflow, not a distributed transaction: it does not roll
back an account created before a later failure. Temporary staging is cleaned on
both success and failure, and cleanup failures never hide the primary typed
failure. Concurrent bootstrap of the same target is not yet serialized.

## Linux collector

The Linux collector is an application-layer adapter between authoritative target
identity and remote observation. It receives a `Server`, `SSHManager`, contextual
logger, and injected monitor username. It selects the server's management address
when present, otherwise its primary address, and always uses `SSHIdentity.MONITOR`.
It returns a pending immutable `DiscoveryObservation`; the caller owns submission
to `DiscoveryService` and the later successful or failed lifecycle transition.

The collector owns a closed catalog of read-only commands. Each command is a
structured argument tuple and declares a timeout. SSHManager remains responsible
for strict trust, credentials, byte limits, transport retries, and execution.
The collector performs no additional retry, so one collection request cannot
multiply SSHManager's configured retry policy. Shell fallback is modeled as
sequential structured calls, never as `||` or arbitrary shell text.

Parsing is isolated in pure functions. JSON is used for block devices, interfaces,
Docker versions, and containers; `/etc/os-release` is parsed as data without
evaluation; other text is split by semantic delimiters or tokens rather than
fixed column widths. Unknown fields and malformed nested records are ignored when
safe. Malformed required output marks the observation partial; a missing optional
product command does not. Raw command output and stderr are neither persisted nor
logged. Bounded raw metadata contains only an OS identifier, supported-distribution
flag, and degraded-command count.

Ubuntu, Debian, Rocky Linux, and AlmaLinux are the verified distribution set.
Other Linux distributions are best effort. Product detection requires positive
evidence and remains observational: Docker, MySQL/MariaDB, Redis, Prometheus,
Asterisk, and FreePBX detection never changes inventory. Collectors cannot import
repositories, persistence managers, subprocess, or `InventoryService`; automated
architecture tests enforce that boundary.

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
not open SQLite, execute SQL, construct repositories, load global configuration,
read credentials directly, or create its own SSH client. Plugin failures are
isolated and translated into structured results without corrupting inventory or
worker state.

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

Job handlers receive repository interfaces or application services. They never
import SQLite, execute SQL, construct repositories, or access `DatabaseManager`.

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

1. Define inventory relationships beyond servers, including logical resources,
   connections, provider identity, and ownership, before extending schema version
   2.
2. Define discovery-to-inventory provenance, merge/conflict and promotion policy,
   bulk operations, and collection orchestration before automated polling is
   added.
3. Define production backup scheduling, retention, quotas, off-host copies, and
   destructive restore orchestration with operator confirmation and rollback.
4. Define collector configuration, command-set compatibility/versioning, and a
   production integration workflow that records through `DiscoveryService`.
5. Define a versioned plugin manifest and typed capability contract before
   implementing provider plugins.
6. Define the durable job state machine, recovery, idempotency, retries,
   cancellation, concurrency limits, retention, and artifact cleanup.
7. Replace the one-shot composition root with a frozen dependency container and
   graceful shutdown after the first long-running entry point is selected.
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
19. Define and ship a least-privilege admin sudoers policy for bootstrap instead
    of relying on an externally provisioned general `sudo -n` capability.
20. Add per-target bootstrap serialization so two operators cannot race an
    otherwise atomic `authorized_keys` repair, and define operator-visible audit
    retention for bootstrap results.
21. Test the standalone artifact across the supported distribution/Python matrix
    and define its independent schema/version compatibility and upgrade window.
19. Add integration tests using disposable SQLite databases and SSH test servers;
    keep them isolated from the default unit suite.
20. Convert placeholder provider directories into real plugins only after the
    contract exists; remove any placeholder that is no longer on the roadmap.
