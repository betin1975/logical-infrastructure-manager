# Summary

This document records the completed self-review of all changes currently made on
this branch, including the Configuration Manager, Runtime Manager, Logging
Foundation, Persistence Foundation, Inventory and Discovery domains, SSHManager,
Linux Collector Foundation, Bootstrap Service Foundation, and on-demand Polling
Service. The review covers architecture, security,
maintainability, tests, Docker and Git practices, remaining technical debt, and
release readiness.

This branch establishes the Logical Infrastructure Manager (LIM) project
foundation. It adds permanent engineering guidance, a target architecture,
layered configuration, Python quality tooling, tests, container definitions,
security and lifecycle documentation, and focused Git exclusions. It implements
the authoritative server inventory and explicit single-server polling while
deliberately excluding scheduled polling, plugins, jobs, and user-interface
features.

Runtime lifecycle management and centralized logging are now implemented. Logging
uses configuration-driven console and rotating-file handlers, structured context,
UTC timestamps, atomic reconfiguration, and centralized secret redaction.
SQLite persistence now provides operation-scoped connections, explicit nested
transactions, ordered migrations, injected repository contracts, consistent
backups, and non-destructive restore validation. Schema version 2 introduces only
the normalized server inventory tables approved by Issue #5.
Domain persistence is now explicitly repository-only: business logic,
`SSHManager`, plugins, and jobs may not import SQLite, execute SQL, construct
repositories, or access low-level persistence managers.
The SQL-free `InventoryService` is the sole mutation gateway for immutable server,
tag, label, lifecycle, health, discovery, poll, and synchronization state.

Issue #8 adds a stateless Linux collector that uses SSHManager's monitor identity
and returns typed, non-authoritative observations. Its fixed read-only commands,
bounded parser inputs, partial-failure behavior, positive product detection, and
architecture tests preserve the established Inventory, Discovery, SSH, and
persistence boundaries. It adds no scheduler, direct persistence, inventory
mutation, plugin behavior, or container dependency.

Issue #9 adds an idempotent remote Bootstrap Service with a typed 15-step plan.
It requires pre-established admin-key access, strict LIM-owned trust, Linux, and
`sudo -n`; installs only a restricted monitor public key and a standalone bounded
collector; verifies the resulting account, files, authentication, forced command,
schema, and host identity; and records success only through InventoryService.
Ordinary application startup performs local validation and no network access.

Issue #10A adds a dependency-injected `PollingService` that coordinates exactly
one eligible server through InventoryService, LinuxCollector, and DiscoveryService.
It returns an immutable `PollResult`, preserves partial observations as successful
partial discovery, finalizes Discovery before updating Inventory, and adds no
scheduler, job, API, direct SSH, SQL, repository, or subprocess behavior.

The architectural direction is appropriate for an early-stage infrastructure
manager: a modular monolith, inward dependency flow, SQLite as the authoritative
inventory, a single `SSHManager`, injected dependencies, durable jobs, and
plugins constrained to provider adaptation.

The two original blocking findings have been resolved. Runtime contents are now
recursively ignored except for the four root placeholders, and malformed YAML
environment values are redacted from both exception messages and formatted
tracebacks. Several non-blocking Docker, configuration typing,
version-enforcement, and documentation concerns remain.

# Files Changed

## Created

- `.dockerignore` restricts the Docker build context to runtime application files.
- `.python-version` declares Python 3.12 for compatible version managers.
- `AI_DEVELOPER.md` defines permanent engineering, testing, security, Git, Docker,
  SQLite, SSH, plugin, and job rules.
- `ARCHITECTURE.md` defines the modular-monolith target, ownership boundaries,
  runtime model, and technical-debt register.
- `DECISIONS.md` records accepted persistence authority, lifecycle, migration,
  backup, and restore decisions as ADRs.
- `app/__init__.py` exports the public configuration API.
- `app/config.py` implements layered YAML configuration, environment overrides,
  environment-variable expansion, dotted access, validation, snapshots, and
  atomic reload.
- `app/runtime.py` creates, validates, and exposes the configuration-driven
  runtime tree.
- `app/logging_manager.py` implements centralized handlers, structured adapters,
  rotation, UTC formatting, exception-safe redaction, and atomic reconfiguration.
- `app/__main__.py` initializes configuration, runtime, logging, SQLite, and
  internal migrations in order.
- `app/persistence/` owns database policy, operation-scoped connections,
  transactions, migrations, repository contracts, backup, restore validation,
  and persistence-specific errors.
- `app/inventory/` owns immutable domain models, pure validation, inventory
  exceptions, the repository protocol, and SQL-free `InventoryService`.
- `app/persistence/inventory_schema.py` defines schema version 2 and its documented
  normalized indexes.
- `app/persistence/inventory_repository.py` is the sole concrete inventory SQL
  implementation.
- `app/discovery/` owns immutable observation models, pure validation, discovery
  exceptions, the repository protocol, and SQL-free `DiscoveryService`.
- `app/persistence/discovery_schema.py` defines normalized schema version 3 and
  documented history, filtering, and cleanup indexes.
- `app/persistence/discovery_repository.py` is the sole concrete discovery SQL
  implementation.
- `app/ssh/` owns typed SSH inputs/results, validation, bounded OpenSSH process
  execution, atomic host trust, diagnostics, transfer, and the sole SSHManager.
- `app/collectors/` owns the stateless collector boundary; its Linux package
  defines the fixed command catalog, safe parsers, validation, internal typed
  facts, exceptions, and observation mapping.
- `app/bootstrap/` owns typed bootstrap requests, plans, results, failures,
  validation, atomic remote helper scripts, orchestration, and the standalone
  versioned Python 3.9+ health artifact.
- `app/polling/` owns the narrow on-demand polling coordinator, immutable typed
  results, safe failure classifications, and consumer-side dependency protocols.
- `config/default.yml` provides versioned, non-secret defaults.
- `config/local.yml.example` demonstrates local machine-specific overrides.
- `plugins/README.md` records the restrictions on future provider plugins.
- `pyproject.toml` configures pytest, branch coverage, and Ruff.
- `requirements-dev.txt` separates testing and lint dependencies from runtime
  dependencies.
- `tests/test_config.py` tests configuration loading, merging, typing, expansion,
  validation, immutability, reload behavior, and secret redaction.
- `tests/test_gitignore.py` verifies recursive runtime exclusions and the exact
  `.gitkeep` exceptions through `git check-ignore`.
- `tests/test_runtime.py` verifies runtime creation, permissions, containment,
  helpers, idempotency, and startup integration.
- `tests/test_logging_manager.py` verifies handler lifecycle, rotation, context,
  reconfiguration, redaction, permissions, and disabled outputs.
- `tests/test_persistence.py` verifies SQLite policies, file security,
  transactions, savepoints, migrations, repository injection, backups, restore
  validation, startup, WAL behavior, readers, and writer contention.
- `tests/test_architecture.py` enforces the repository boundary by rejecting
  SQLite imports and low-level persistence-manager imports outside the persistence
  package and composition roots, and prevents collectors from importing
  persistence, repositories, subprocess, or inventory mutation services.
- `tests/test_inventory_domain.py`, `tests/test_inventory_repository.py`, and
  `tests/test_inventory_service.py` verify model invariants, validation, schema,
  indexes, filters, search, pagination, rollback, soft deletion, optimistic
  conflicts, service transitions, dependency injection, and safe logging.
- `tests/test_discovery_domain.py`, `tests/test_discovery_repository.py`, and
  `tests/test_discovery_service.py` verify collected-fact validation, lifecycle,
  schema, indexes, history, search, pagination, rollback, retention, optimistic
  conflicts, dependency injection, and safe logging.
- `tests/test_ssh.py` verifies initialization, private-key security, trust
  inspection and mutation, fingerprint races, bounded commands, classification,
  retries, transfers, diagnostics, cancellation, and safe logging without a
  network or operator credential.
- `tests/test_linux_collector.py` verifies Ubuntu, Debian, Rocky Linux, AlmaLinux,
  hostname fallback, command policy, timeouts, retries, partial failures,
  malformed output, missing Docker/systemd, product detection, discovery mapping,
  and safe logging through an SSHManager double.
- `tests/test_bootstrap_service.py` verifies prerequisites, ordered/fatal plan
  behavior, account creation and repair, idempotency, deployment, cleanup,
  verification, InventoryService integration, and safe failures/logging through
  protocol-compatible doubles.
- `tests/test_bootstrap_scripts.py` executes the remote helper against temporary
  paths to verify atomic key preservation/replacement, idempotency, and symlink,
  unsafe-path, invalid-key, and unknown-action rejection.
- `tests/test_remote_health_artifact.py` verifies Python 3.9 grammar, supported
  distributions, bounded execution/document output, required versus optional
  failure behavior, service-state distinctions, and credential exclusion.
- `tests/test_polling_service.py` verifies single-server success, partial results,
  collector exceptions, eligibility rejection, Discovery submission and
  finalization failures, Inventory update failures, call ordering, immutable
  results, and safe logging without SSH, SQL, or network access.

## Modified

- `.gitignore` was reduced from generic template content to project-focused
  Python, build, secret, runtime, database, SSH, editor, and container rules.
- `CHANGELOG.md` now follows Keep a Changelog with an `Unreleased` section.
- `Dockerfile` now builds a minimal Python 3.12 image and runs as a non-root user.
- `INSTALL.md` now documents development and container setup.
- `README.md` now documents project status, principles, configuration, testing,
  containers, layout, and supporting documents.
- `ROADMAP.md` now orders future development by architectural dependency.
- `SECURITY.md` now defines private reporting and sensitive-data rules.
- `UPGRADE.md` now records pre-release limitations and future migration needs.
- `docker-compose.yml` now defines a hardened one-shot configuration check.
- `requirements.txt` now declares PyYAML as the sole runtime dependency.
- `app/config.py`, `app/runtime.py`, `app/__init__.py`, and `app/__main__.py` now
  validate persistence configuration, expose initialization state and public
  APIs, and initialize/migrate SQLite after logging.
- `config/default.yml` and `config/local.yml.example` define safe SQLite defaults.
- `README.md`, `ARCHITECTURE.md`, `INSTALL.md`, `DECISIONS.md`, and `CHANGELOG.md`
  document the Linux collector boundary, supported distributions, command and
  parser strategy, product detection, limitations, and ADR-0018.
- `app/__main__.py`, `app/inventory/service.py`, and `app/ssh/manager.py` now
  compose local bootstrap validation, provide a narrow bootstrap-success
  transition, and expose validated identity availability without leaking paths.
- `config/default.yml` and `config/local.yml.example` define non-secret bootstrap
  policy and local public-key configuration.
- `docker-compose.yml` separately mounts the monitor public key read-only.
- `README.md`, `ARCHITECTURE.md`, `AI_DEVELOPER.md`, `DECISIONS.md`, `SECURITY.md`,
  `CHANGELOG.md`, `INSTALL.md`, and `UPGRADE.md` document bootstrap prerequisites,
  boundaries, security, idempotency, partial failure, artifact compatibility, and
  deployment layout.
- `tests/test_architecture.py` additionally prevents polling from importing SSH,
  subprocess, persistence managers, SQL, or concrete repositories.

# Architecture Review

## Strengths

- A modular monolith is the right initial deployment model. It keeps operational
  complexity low while allowing strong internal ownership boundaries.
- SQLite is explicitly identified as the authoritative inventory. Remote state,
  plugin output, files, and caches are correctly treated as observations.
- `SSHManager` is reserved as the only SSH implementation. Preventing jobs and
  plugins from creating SSH clients will centralize host verification, credentials,
  timeouts, cancellation, pooling, output limits, and audit behavior.
- Application bootstrap is correctly defined as the composition root and future
  lifecycle owner, distinct from the remote `BootstrapService`. Dependency
  injection is preferred over global clients and service locators.
- Plugins are treated as provider adapters rather than independent applications
  or alternate inventory stores.
- Durable jobs, repositories, migrations, and interfaces are documented without
  prematurely implementing speculative abstractions.
- Existing `app.config` imports are preserved, and future package reorganization
  is explicitly incremental.
- Runtime and development dependencies are separated cleanly.
- `LoggingManager` owns the dedicated `lim` namespace, preventing components from
  constructing handlers or textual context prefixes.
- Replacement logging handlers are built and validated before an atomic swap, so
  failed reconfiguration preserves the active setup.
- A single structured application log keeps cross-component chronology intact and
  avoids duplicated routing handlers.
- SQLite connections are short-lived and never stored globally. Configuration is
  applied uniformly to every connection, including foreign keys and busy timeout.
- `TransactionManager` is the sole transaction owner; nested work uses savepoints,
  and SQLite authorization rejects hidden repository commits or rollbacks.
- Python migrations remain transparent and dependency-free. Each version commits
  independently, while its schema change and history record are atomic.
- Backup uses SQLite's online API, validates before publication, and never infers
  permission to replace the active authoritative database.
- Business logic depends only on repository interfaces. `SSHManager`, plugins,
  and jobs are forbidden from executing SQL, while concrete repository
  construction remains a composition-root responsibility.
- Inventory models are frozen values, state uses enums, and all transitions
  construct a complete validated replacement rather than mutating accepted state.
- Addresses, tags, and labels are normalized relationally. Cross-kind address
  uniqueness prevents a management address from silently colliding with another
  server's primary address.
- Optimistic `inventory_version` checks prevent stale service or repository
  updates from overwriting newer accepted inventory.

## Inventory domain self-review

- **Domain model:** `Server` includes every approved identity, platform,
  operating-system, classification, policy, lifecycle, poll, timestamp,
  synchronization, version, tag, label, and note field. Frozen dataclasses and
  enums prevent partial mutation and magic state strings.
- **Normalization:** Servers, addresses, tags, server/tag relationships, and
  labels are separate tables. No inventory JSON blob exists. Foreign keys cascade
  only normalized children and are enforced on every connection.
- **Indexes:** Unique hostname and address indexes enforce identity. Partial
  enabled, managed, and health indexes serve target filters; status, address,
  reverse-tag, and label indexes serve documented lifecycle and lookup paths.
- **Repository API:** The domain owns `InventoryRepository`; only
  `SQLiteInventoryRepository` receives persistence dependencies. Values are
  parameterized, pages are bounded, reads hydrate immutable aggregates, and every
  mutation is transactional.
- **Service boundary:** Registration and all lifecycle, discovery, health, poll,
  failure, tag, label, delete, and restore mutations pass through
  `InventoryService`. Generic updates are limited to descriptive/configuration
  fields and cannot bypass dedicated state transitions.
- **Soft delete:** Deleted rows remain disabled and hidden by default while UUID,
  hostname, and addresses stay reserved. Restore is explicit and returns the
  server disabled. There is no hard-delete path.
- **Security:** Inventory SQL never leaves the persistence package. Search input
  and all values are parameterized. Logs contain UUID, hostname, and operation
  only—not addresses, descriptions, labels, notes, or SQL parameters.
- **Scalability:** Pages are capped at 1,000 and tag/label hydration is batched per
  page. SQLite remains single-writer; substring search is intentionally simple
  until measured scale justifies FTS.

The review found and fixed missing database enum checks, permissive tag/label
coercion, incomplete poll timestamp invariants, overly broad generic service
updates, version churn on idempotent operations, double-clock poll transitions,
and insufficient `created_at` defense in the concrete repository. No blocking
inventory defect remains.

## Persistence foundation self-review

- **SQL injection:** No caller value is interpolated into SQL. Migration metadata
  and lookups use parameters. Dynamic pragma values come from closed allowlists,
  savepoint names use manager-generated integers, and internal identifiers are
  fixed constants. Trusted migration functions are reviewed application code.
- **Transactions:** Driver autocommit is enabled only so `TransactionManager`
  explicitly controls `BEGIN`, commit, rollback, and savepoints. Tests prove full
  rollback, nested rollback isolation, and rejection of repository commits.
- **Migrations:** Versions are positive, unique, contiguous, sorted, and validated
  before mutation. Each migration and history insert share one transaction;
  earlier successful versions survive a later failure. Inspection is read-only.
- **Backup and paths:** Database and backup names are single safe components.
  Managed directories, database files, candidates, and destinations reject
  symlinks and traversal. SQLite URI no-follow mode supplements pre-open checks.
  Backup publication is temporary-file based, mode `0600`, validated, and atomic.
- **Concurrency:** Every thread obtains its own connection. Tests cover concurrent
  readers, deterministic writer contention, configured busy timeout, and WAL
  snapshot reads. LIM still has SQLite's single-writer constraint.
- **Logging and secrets:** Persistence does not log SQL, parameters, paths, or
  database contents. Startup logs only schema version, and failures pass through
  the centralized redacting logger with generic persistence messages.

No blocking persistence defect remained after the review. The review did identify
and fix dot-prefixed database-name handling, SQLite no-follow URI use, strict
migration-history validation, and fail-closed database reinitialization state.

## Concerns

- Python `>=3.12,<3.13` is stated as policy but is not mechanically enforced.
  `.python-version` and Ruff's target are advisory; there is no package
  `requires-python` metadata or CI compatibility gate.
- The README and engineering guide list `ansible/`, `docker/`, `docs/`, and
  `scripts/` as repository directories, but empty directories are not represented
  in Git and will not exist in a fresh clone. They should be described as target
  layout or retained with meaningful documentation when needed.
- `ConfigManager` accepts unknown sections and keys. Misspelled environment
  variables therefore create unused configuration instead of failing fast.
- `get()` accepts a tuple of expected types while `require()` accepts only one
  type, creating a small public-API inconsistency.
- The root-mapping validation branch is redundant after `_load_yaml()` has already
  restricted input to dictionaries. The optional missing-file branch is also not
  reached through normal reload flow.
- Setup and validation commands are duplicated across README, INSTALL, and
  AI_DEVELOPER and have already drifted regarding whether coverage is mandatory.
- The changelog's `HEAD...HEAD` comparison link can never display changes.
- Licensing, contribution guidance, ownership, and cross-platform Git attributes
  are not yet defined.

# Security Review

## Resolved blocking findings

### Resolved: runtime contents are recursively ignored

The runtime rule now uses `runtime/**`, with explicit exceptions only for the four
runtime directories and their root `.gitkeep` placeholders. Direct files, nested
files, unexpected runtime paths, and nested `.gitkeep` files are ignored.

Automated tests verify representative paths including:

```text
runtime/data/inventory.json
runtime/jobs/output.txt
runtime/logs/lim.txt
runtime/backups/archive.tar.gz
```

The tests also prove that only `runtime/data/.gitkeep`,
`runtime/jobs/.gitkeep`, `runtime/logs/.gitkeep`, and
`runtime/backups/.gitkeep` remain eligible for tracking.

### Resolved: parser exceptions redact environment secrets

Malformed YAML environment values now produce a `ConfigError` containing only the
environment variable name. The underlying PyYAML exception is suppressed so its
source excerpt cannot appear through normal chained traceback formatting.

A regression test verifies that a synthetic secret is absent from both the direct
exception string and the complete formatted traceback. It also verifies that no
explicit cause is retained and exception context is suppressed.

## Additional concerns

- The one-shot foundation container now consumes `runtime/` for logging, but it
  still mounts `ssh/` read-write before any SSH capability exists. The unused SSH
  mount unnecessarily exposes trust or credential material.
- A future runtime should separate read-only credentials, writable known-host
  state, database storage, job artifacts, and backup output rather than mounting
  one broad SSH or runtime tree.
- Docker uses a mutable `python:3.12-slim` base and dependencies use ranges without
  a lock or hashes. Identical commits can produce different images.
- `config/local.yml` is documented as a normal configuration layer but is neither
  copied nor mounted in the container path. Container users may incorrectly
  believe their local configuration is active.
- Docker's read-only root filesystem is applied only through Compose. A direct
  `docker run` does not enforce it.
- The security-policy fallback is not actionable if private GitHub advisories are
  unavailable because no concrete private contact is provided.
- No CI job currently builds or scans the container, audits dependencies, or scans
  committed content for secrets.

## Positive controls

- YAML is loaded with `safe_load`, avoiding arbitrary object construction.
- Configuration snapshots are deep copies, reducing accidental mutation.
- Failed reloads retain the last valid state.
- `.dockerignore` uses an allowlist-style build context.
- The container uses a dedicated non-root user.
- Compose drops capabilities, enables `no-new-privileges`, and makes the root
  filesystem read-only.
- Local configuration, common key formats, SQLite files, `.env` files, and SSH
  contents have explicit Git exclusions, including recursive runtime exclusions.
- Messages, arguments, nested structures, context, exceptions, configuration
  secrets, environment secrets, authorization headers, and private-key blocks are
  redacted before output.
- Final formatted output is redacted again, covering exception traceback text.
- Active and rotated logs are restricted to mode `0640`.
- Log files are opened with no-follow semantics where the platform supports them,
  and existing symlinks are rejected elsewhere to prevent writes outside the
  configured runtime tree.
- Component child handlers are removed before use, preventing redaction bypass and
  duplicate output within the owned logger namespace.
- SQLite database, journal, temporary backup, and published backup files use mode
  `0600`; runtime Git exclusions cover all of them recursively.
- Database and backup writes reject symlinks and traversal, use SQLite no-follow
  URI mode, and remain directly contained in RuntimeManager-owned directories.
- Backup and restore errors do not include database contents or integrity output,
  and migration failures report only version and exception type.

## Discovery domain self-review

- **Architecture:** Discovery and inventory remain separate domains. Discovery
  references inventory identity but neither its service nor repository imports or
  mutates inventory. Promotion remains deliberately unimplemented and reserved
  for `InventoryService`.
- **Normalization:** Observations own normalized interfaces, addresses, disks,
  services, packages, containers, processes, and namespaced metadata. Children
  cascade only with their observation; inventory rows are never cascaded.
- **Repository design:** The domain owns the protocol and only
  `SQLiteDiscoveryRepository` executes discovery SQL. Writes are transactional,
  values parameterized, pages bounded, lifecycle changes optimistic, and
  collected facts immutable after insertion.
- **Lifecycle and retention:** Legal transitions are pending to successful or
  failed, then expired. Failed history retains its bounded reason after expiry.
  Cleanup requires a UTC cutoff and deletes only expired observations.
- **Security:** Models exclude credentials, command lines, and complete command
  output. Credential-shaped metadata keys are rejected, metadata is bounded,
  service logs include only component, server UUID,
  operation, and counts, and repository exceptions expose no SQL parameters or
  collected data.
- **Scalability:** Latest/history, source, status, state/retention, address, child,
  and metadata queries have documented indexes. Pagination is capped at 1,000;
  metadata and collection duration are bounded where applicable. Child hydration
  currently issues one query per child type per observation and should be measured
  before high-volume use.

Self-review found and fixed backward lifecycle timestamps, loss of failed reasons
on expiry, duplicate normalized facts, invalid nested model acceptance,
credential-shaped metadata acceptance, incomplete database lifecycle checks,
mutable-fact replacement through repository updates, and unauthorized
synchronization-state changes. No blocking discovery finding remains.

## SSHManager security self-review

- **Command injection:** Local execution uses an argument array with
  `shell=False`. Hosts, usernames, ports, commands, and transfer paths are
  validated. Remote executable/argument tuples are independently POSIX-quoted;
  arbitrary shell text is not exposed.
- **Trust:** Strict application-owned trust is always enabled. Unknown and changed
  keys remain untrusted. New/replacement trust requires a fresh presented key and
  matching fingerprint, uses atomic replacement, and rescans after writing;
  changed-during-confirmation trust is rolled back.
- **Credentials and paths:** Admin and monitor identities are enum-selected,
  contained by the credential root, regular, non-symlinked, and mode `0400`.
  Known hosts is separately writable under runtime data, rejects symlinks and
  writable parents, and is mode `0600`.
- **Processes and resources:** OpenSSH processes receive null stdin, a minimal
  environment, closed descriptors, a new session, time/cancellation enforcement,
  process-group termination, and concurrent bounded stdout/stderr draining.
- **Retries:** Only connection refusal and connection timeout are retried within a
  configured bound. Authentication, DNS, trust, command timeout, cancellation,
  output limits, local failures, and remote nonzero exits are not retried.
- **Secrets and logging:** Results never contain private-key contents. Identity and
  trust-store paths are removed from stderr. Logs contain structured target,
  identity, trust, timing, exit, and classification metadata but no command,
  output, transferred contents, credentials, or fingerprints.
- **Architecture:** SSHManager imports no SQLite, persistence, inventory, or
  discovery service. Architecture tests confine subprocess ownership to
  `app.ssh.command` and direct SSH-tool invocation to `app.ssh`.

Self-review fixed unsupported SSH logging context, descendant-process leakage on
timeout, an incorrect remote-command separator, missing hard resource ceilings,
host-key replacement race rollback, malformed scan decoding, unsafe trust-parent
permissions, post-initialization trust-path substitution, cancellation-hook
failure cleanup, unused configured default port, and container credential mount
ownership. No blocking SSH or application-security finding remains.

## Linux collector self-review

- **Architecture:** `LinuxCollector` depends on immutable `Server`, SSHManager,
  discovery value types, and a contextual logger only. It returns a pending
  observation for submission through `DiscoveryService`. It imports no
  persistence, repository, subprocess, plugin, scheduler, or inventory mutation
  service; an AST test enforces these restrictions.
- **Command safety:** All probes come from one closed read-only command catalog.
  Every request has an explicit timeout and uses `SSHIdentity.MONITOR`.
  Hostname fallback is two structured requests instead of shell syntax. Output
  bounds, trust, authentication, and transient retries remain owned by SSHManager;
  the collector never multiplies retry attempts.
- **Parser robustness:** JSON, os-release key/value data, delimiter parsing, and
  token parsing are isolated in pure functions. Unknown keys and malformed nested
  records are ignored where safe. Invalid top-level JSON, unexpected core text,
  truncated output, timeouts, missing commands, and SSHManager command errors are
  classified without exposing stdout or stderr and do not abort unrelated probes.
- **Discovery mapping:** Host identity, OS/distribution/version, Linux kernel,
  architecture, CPU, memory, disks, interfaces, addresses, listening and running
  services, containers, product evidence, timestamp, duration, collector version,
  and bounded metadata map into immutable discovery values. Complete collection is
  returned `PENDING/UNKNOWN`; core degradation is `PENDING/PARTIAL`, preserving
  the existing DiscoveryService lifecycle contract.
- **Product detection:** Docker, MySQL/MariaDB, Redis, Prometheus, Asterisk, and
  FreePBX require positive command evidence. Absence is not treated as inventory
  truth or as a core collection failure. No Docker daemon is needed for tests.
- **Logging and privacy:** Logs bind only server UUID, inventory hostname, and
  operation, then record fixed command identifier, duration, exit code, attempt
  count, collector version, and safe failure class. They never contain remote
  output, stderr, credentials, SSH paths, keys, or fingerprints.
- **Performance:** Collection is intentionally sequential so fixed probes cannot
  create uncontrolled remote concurrency. Parser input is capped at 1 MiB even
  when a test double violates SSHManager's output contract. Large fact collections
  remain bounded by SSHManager bytes and discovery-domain metadata constraints.

Self-review identified and fixed incorrect complete-status construction for a
pending observation, unsafe shell-style hostname fallback, empty/unexpected core
output not marking partial, one SSHManager command exception aborting remaining
probes, unbounded parser input from test doubles, and accidental exposure risk
from inspecting stderr. No blocking collector finding remains.

## Bootstrap Service self-review

The final review traced every prerequisite, mutation, cleanup, and verification
path as a senior Linux, SSH, and application-security review. No blocking finding
remains.

- **Architecture:** `BootstrapService` receives SSHManager and InventoryService;
  it imports no subprocess, SQLite, repository, discovery, or LinuxCollector code.
  The standalone remote artifact is the sole deliberate subprocess exception and
  cannot import LIM packages. AST tests enforce both boundaries.
- **User and group handling:** an absent account is created with the configured
  home, shell, comment, system-user policy, and locked password. An existing
  account with the LIM ownership comment is repaired; a conflicting account fails
  closed. Final verification rechecks passwd identity, home, shell, password lock,
  and every configured forbidden group. The `/bin/sh` default is necessary for
  OpenSSH forced-command evaluation and is constrained by the forced key.
- **Sudo assumptions:** admin authentication and each configured utility are
  checked before mutation, followed by `sudo -n true`. No password or token input
  exists. The absence of a shipped least-privilege sudoers template is documented
  as operational technical debt, not silently treated as solved.
- **Authorized keys:** a bounded helper validates the public key and LIM marker,
  preserves unrelated lines, removes all stale/duplicate LIM-owned lines, writes
  through a same-directory temporary file, fsyncs, sets owner/mode, and atomically
  replaces the destination. Tests execute first, repeat, stale-key, preservation,
  symlink, unsafe-path, and invalid-key cases.
- **Forced command:** the generated entry uses configured OpenSSH `restrict` and
  exactly one quoted absolute collector path. Verification authenticates with the
  monitor identity and proves an arbitrary requested marker command is not
  honored while valid collector JSON is returned.
- **Ownership and modes:** the service creates and verifies the account home,
  `.ssh`, `authorized_keys`, collector directory, and collector as expected
  regular files/directories with configured owner and restrictive modes. Unsafe
  symlinks and unexpected types fail closed before replacement.
- **Artifact:** the deployed program is standalone, standard-library-only,
  Python 3.9 grammar-compatible, argument-free, read-only, shell-free, bounded by
  command time/output/document size, and versioned by collector and JSON schema.
  Its service model distinguishes installed-active, installed-inactive,
  not-installed, and unknown/permission failure. Stderr is discarded.
- **Idempotency and partial failure:** every mutation compares or safely repairs
  current state. Repeat execution neither duplicates the account/key nor rewrites
  unchanged artifacts and avoids an unnecessary inventory version. Bootstrap is
  explicitly a repair plan, not a false distributed transaction; partial state
  is safe to retry, temporary files are cleaned, and a cleanup error cannot hide
  the primary failure.
- **Private keys and logging:** neither private identity can enter a request,
  transfer, command, result, or message. Public-key bodies, authorized-key data,
  remote stdout/stderr, collector JSON, artifact contents, credentials, and raw
  exception text are absent from logs and typed failures. Regression tests use
  sentinel secrets and inspect both result text and captured contextual logs.
- **Verification and inventory:** strict trust is checked before and after remote
  changes and is never mutated. Collector digest, direct sudo-as-monitor output,
  monitor authentication, forced-command confinement, schema/version, and host
  identity all must pass before the narrow
  `InventoryService.record_bootstrap_success()` operation is called. A failure
  never updates inventory.
- **Startup and Docker:** startup validates local paths, keys, artifact, SSH
  identities, and settings only. It performs no remote operation. Compose keeps
  the admin private key, monitor private key, and monitor public key as separate
  read-only mounts while runtime trust/data remains the only writable state.

Non-blocking limitations are per-target concurrent repair races, the externally
managed breadth of admin sudo rights, lack of disposable multi-distribution SSH
integration tests on this Docker-less host, and the need for an explicit artifact
schema compatibility window. These are recorded below.

## On-demand Polling Service self-review

- **Architecture:** `PollingService` receives narrow protocols implemented by
  InventoryService, DiscoveryService, and LinuxCollector. It imports no SSHManager,
  subprocess, SQLite, persistence manager, or repository implementation. An AST
  architecture test enforces these boundaries.
- **Scope:** one explicit call polls exactly one server. There is no scheduler,
  retry loop, durable job, dashboard, API, concurrency owner, or startup wiring.
- **Eligibility:** collection is rejected before side effects when the inventory
  server is disabled, unmanaged, deleted, unavailable, or lacks a bootstrap
  timestamp.
- **Lifecycle ordering:** a collected observation is recorded pending, then
  finalized through DiscoveryService before any Inventory poll transition. A
  complete or partial observation is finalized successful; this preserves
  `DiscoveryStatus.PARTIAL` rather than incorrectly overwriting it as failed.
- **Failure consistency:** collector exceptions record one failed Inventory poll
  because no observation exists. Submission failures leave Inventory unchanged.
  If successful finalization fails, PollingService attempts the supported failed
  finalization and records Inventory failure only after that succeeds. If neither
  final state is known, Inventory remains unchanged. An Inventory success-update
  failure can never produce a successful `PollResult`.
- **Idempotency:** each request performs at most one Inventory success or failure
  transition and never applies a compensating second version change. Distinct
  operator-requested polls remain distinct timestamped events.
- **Results and logging:** `PollResult` is a frozen dataclass with typed status and
  failure enums, observation lifecycle/status, duration, and Inventory-update
  state. The earlier `PollingResult` name remains a compatibility alias. Logging
  uses injected structured context and fixed messages; dependency exception text,
  raw SSH output, and credentials are never included.

No blocking polling correctness, persistence-boundary, SSH-boundary, secret-
logging, or maintainability finding remains in the scoped review.

# Technical Debt

## Configuration

- Add a typed, closed configuration schema with actionable unknown-key errors.
- Define the exact environment scalar types. PyYAML currently converts values
  such as ISO dates into `datetime.date` and accepts surprising YAML boolean forms.
- Make scalar type checks exact where needed. Python considers `bool` a subclass
  of `int`, so `expected_type=int` currently accepts `true`.
- Reject duplicate environment names that normalize to the same lowercase key.
- Document that full configuration snapshots may contain secrets and must never
  be logged directly; LoggingManager currently registers those values for
  redaction.
- Resolve configured paths at bootstrap and inject `pathlib.Path` instances into
  consumers.

## Inventory and persistence

- Define logical-resource relationships, connections, provider identity,
  provenance, merge/conflict policy, and ownership before extending schema 2.
- Define bulk service/repository operations and conflict-retry policy before
  high-volume discovery or polling.
- Establish inventory history, retention, and an explicitly authorized purge
  process; soft deletion currently retains identities indefinitely.
- Measure substring-search behavior and adopt SQLite FTS only when justified.
- Define backup scheduling, retention, quotas, off-host copies, and alerting.
- Design destructive restore orchestration with shutdown coordination, operator
  confirmation, rollback, and end-to-end recovery tests.
- Define migration compatibility, application-version requirements, downgrade
  policy, and optional migration checksums before the first stable release.
- Add multi-process contention and crash-recovery integration tests on every
  supported deployment filesystem.

## Discovery

- Define promotion field ownership, provenance, merge/conflict policy, and audit
  records before adding any `InventoryService` acceptance operation.
- Define a production retention duration, scheduling owner, quotas, and legal or
  operational preservation requirements before automating cleanup.
- Batch child hydration across observation pages if representative workloads show
  the current per-observation child queries are material.
- Define source-specific metadata schema versioning and broader allowlists before
  adding collectors or plugin-owned observation fields.
- Add bulk recording and cleanup limits before high-volume polling exists.

## Linux collector

- Define a typed collector configuration and bootstrap integration for the monitor
  username without coupling the collector to ConfigManager.
- Version the command/output compatibility contract before changing probes in a
  way that could alter persisted observation meaning.
- Add disposable OpenSSH integration tests for each supported distribution when
  CI provides containers; unit tests intentionally remain network-free.
- Define collection concurrency, cancellation, scheduling, durable result
  submission, and overall collection deadlines in the future job/polling design.
- Consider command batching only after measurement; it must not weaken structured
  command safety, per-command timeouts, partial-failure attribution, or logging.
- Establish fact-count limits for interfaces, disks, services, and containers in
  addition to existing SSH byte and discovery metadata limits before polling
  untrusted high-cardinality hosts.

## On-demand polling

- Define per-server concurrency exclusion and request idempotency before polling
  can be invoked concurrently by a future interface or worker.
- Add cancellation and an overall poll deadline when durable jobs own remote
  execution; do not add retries independently of SSHManager policy.
- Decide how a future operator interface reports the rare state where Discovery
  finalized successfully but the subsequent Inventory success update failed.
- Add composition coverage with real InventoryService and DiscoveryService over a
  temporary database when polling is wired into an entry point; current tests use
  deterministic service-shaped doubles.
- Keep scheduling, backoff, retention, and fleet concurrency in the future job
  engine rather than expanding the on-demand coordinator.

## Bootstrap Service

- Define and ship a least-privilege sudoers policy covering only the configured
  bootstrap utilities and argument shapes; current unattended sudo capability is
  an explicitly external prerequisite.
- Add a per-target lock or durable orchestration owner before concurrent dashboard
  requests are possible; atomic replacement prevents corruption but two valid
  simultaneous key repairs can still produce last-writer-wins behavior.
- Define remote collector schema/version compatibility, staged rollout, downgrade,
  and retention policy before more than one artifact version is supported.
- Add disposable OpenSSH integration coverage across Ubuntu, Debian, Rocky Linux,
  and AlmaLinux with their supported OpenSSH/systemd/Python versions when Docker
  CI is available.
- Define durable, redacted bootstrap audit records and operator authorization
  before exposing bootstrap through a UI or job engine.
- Evaluate whether an existing LIM-owned account whose numeric UID/GID changes
  outside LIM needs an approved conflict policy beyond final ownership repair.
- If bootstrap gains additional platforms or actions, split the currently
  cohesive but large service module into injected account, deployment, and
  verification collaborators; avoid doing so before those boundaries have a
  second use case.

## SSH, plugins, and jobs

- Add Windows-specific process termination only if Windows becomes a supported
  runtime; the current implementation intentionally targets POSIX OpenSSH.
- Define identity rotation, external secret-provider integration, host-key audit
  persistence, and multi-process trust locking before production deployment.
- Add end-to-end tests against disposable OpenSSH containers when CI provides
  Docker, without using production keys or infrastructure.
- Define a versioned plugin manifest and typed contract before implementing any
  provider plugin.
- Define durable job states, legal transitions, recovery, idempotency, retries,
  cancellation, concurrency limits, retention, and artifact cleanup.

## Operations and delivery

- Replace the one-shot composition root with a dependency container and graceful
  shutdown after selecting the first long-running entry point.
- Select and threat-model the operator interface and its authentication and
  authorization model.
- Evaluate JSON output or external aggregation only when operational requirements
  justify them; local structured context, correlation, redaction, and rotation are
  implemented.
- Add CI for Python 3.12, tests, coverage, Ruff, type checking, container builds,
  dependency auditing, and secret scanning.
- Select a static type checker and incremental strictness policy.
- Add reproducible dependency locking and automated security updates.
- Pin container base variants or digests for release builds.
- Define runtime ownership, quotas, backup retention, disaster recovery, upgrades,
  and rollback.
- Add package metadata and release automation when LIM has an installable entry
  point.
- Add a license, contribution guide, Git attributes, and explicit review ownership.

# Tests

The implemented suite was run against CPython 3.12.13 with these results:

```text
365 tests passed
90.47% branch-aware coverage
Ruff passed
Python compilation passed
Configuration, runtime, logging, persistence, inventory, discovery, SSH, Linux collector, Bootstrap Service, standalone remote artifact, startup, container-layout, and concurrency tests passed
Compose YAML structural parsing passed
git diff --check passed
```

The later scoped PollingService validation was intentionally limited by the
Issue #10A instruction and completed separately:

```text
14 polling-service tests passed
Scoped Ruff check passed
```

The 365-test coverage figure above predates the polling package; a new full-suite
coverage measurement was intentionally not run under the scoped command limit.

Docker was not installed on the validation host, so an actual image build and
container execution were not verified.

Coverage is configured with a 90% threshold, but the threshold is applied only
when pytest is invoked with `--cov`. The permanent instructions and quick-start
also show plain `python -m pytest`, which does not enforce coverage.

## Missing tests

- Additional `git check-ignore` invariants for database, SSH, and local-secret
  paths outside the runtime regression cases.
- Exact `bool` versus `int` type behavior.
- YAML timestamp, date, and nonstandard boolean behavior.
- Duplicate normalized environment keys.
- Unknown configuration-key rejection.
- The real `os.environ` loading path.
- The committed `config/default.yml` through the normal pytest suite.
- Docker image build, non-root execution, and read-only filesystem behavior.
- Compose behavior with local configuration and bind-mount permissions.
- Documentation links and command snippets.
- Python-version enforcement.
- Concurrent multi-manager reconfiguration stress tests.
- Property-based or fuzz tests for redaction false negatives and pathological
  message sizes.
- Multi-process SQLite contention and process-crash recovery.
- Backup failure injection for disk-full, permission loss, interrupted atomic
  rename, and cleanup failure conditions.
- Restore validation against adversarial SQLite files beyond synthetic corruption
  and malformed migration metadata.
- Container persistence behavior on Linux bind mounts and named volumes.
- Multi-process optimistic inventory update races and conflict retry behavior.
- Inventory search/load performance at representative maximum server, tag, and
  label cardinalities.
- An explicit internationalized-hostname policy; current validation accepts only
  normalized ASCII hostnames.
- Property-based state-transition and repository round-trip testing.
- Parser fuzz/property tests for large, deeply nested, mixed-type, and
  distribution-specific command responses.
- End-to-end Linux collection through disposable trusted OpenSSH targets for each
  supported distribution; the current suite deliberately mocks SSHManager.
- High-cardinality interface, disk, service, and container observation limits and
  performance tests once product limits are approved.
- End-to-end bootstrap through disposable OpenSSH targets across each supported
  distribution, OpenSSH version, and Python baseline; Docker was unavailable.
- Concurrent bootstrap attempts against the same target and interruption at each
  remote mutation boundary; the unit suite covers representative partial retry.
- Polling composition with real InventoryService and DiscoveryService over
  temporary SQLite persistence, plus concurrent same-server requests. These are
  deferred until an entry point or job owner exists.

# Remaining TODOs

## Resolved blocking TODOs

1. Recursive runtime ignore rules and regression coverage are complete.
2. Environment parser values are redacted from messages and tracebacks.

## Strongly recommended for this foundation branch

1. Decide and document how Docker consumes local configuration.
2. Make the Python compatibility policy enforceable or phrase it as a tested
   baseline rather than a hard compatibility range.
3. Make Docker builds reproducible enough for the intended development stage.
4. Fix boolean-versus-integer validation and define supported YAML scalar types.
5. Make the canonical test command enforce coverage consistently.
6. Correct the repository-layout documentation for directories absent from Git.
7. Replace or remove the ineffective changelog comparison link.

## Acceptable as explicitly tracked follow-up

1. Add typed configuration schemas and duplicate-key detection.
2. Add CI, container builds, type checking, dependency auditing, and secret
    scanning.
3. Add licensing and contributor-governance files.
4. Implement inventory relationships, plugins, jobs, and interfaces only after
   their designs are approved.
5. Place the implemented on-demand polling coordinator behind a future durable
   job/interface boundary without adding scheduling or retries to PollingService.
6. Define least-privilege admin sudoers, per-target bootstrap serialization, and
   artifact compatibility before dashboard-driven or concurrent bootstrap.

# Overall Assessment

The branch provides a thoughtful and unusually well-documented architectural
foundation. It correctly avoids premature business implementation, keeps runtime
dependencies minimal, establishes useful module ownership, and creates a solid
unit-testing baseline. The SQLite, SSHManager, plugin, job, and bootstrap rules
are appropriate long-term guardrails.

The Runtime Manager, Logging Foundation, and Persistence Foundation follow those
boundaries. Logging is
configuration-driven, uses only the standard library, prevents duplicate or
bypass handlers, preserves valid configuration on failure, redacts structured and
exception data, and applies restrictive file permissions and symlink-safe opens.
No blocking issue was found in the final logging self-review after the `0640`
rollover and no-follow hardening.

Persistence is likewise configuration-driven and standard-library only. It keeps
connections and transactions operation-scoped, enforces SQLite policy centrally,
fails closed on invalid migration state, uses consistent atomic backups, and
limits restore support to read-only validation. The only domain tables are the
approved normalized inventory schema; no generic CRUD framework was introduced.
No blocking database or security issue remains
after the persistence self-review and hardening changes described above.

The Inventory Domain follows the repository-only boundary: immutable models and
`InventoryService` contain business rules, the repository protocol belongs to the
domain, and SQLite implementation details remain in `app.persistence`. The schema
is normalized, indexed for implemented queries, foreign-key protected, soft-delete
safe, and optimistic-concurrency aware. No SSH, plugin, job, API, UI, or
authorization behavior exists inside the Inventory domain; polling reaches it
only through InventoryService.

The Discovery Domain preserves the same layering while keeping observations
explicitly non-authoritative. Its immutable model, normalized schema, service
lifecycle, bounded metadata, indexed history, optimistic updates, and expired-only
cleanup are consistent with the approved design. Self-review regressions cover
immutable collected facts and reserved synchronization authority. Collection
remains observation-only and contains no polling orchestration, plugin, job,
interface, authentication, or authorization behavior.

The SSHManager foundation is the sole remote-access implementation and remains
fully outside persistence and domain mutation. It uses strict isolated trust,
read-only identities, bounded environment-independent OpenSSH processes,
structured command arguments, typed safe results, explicit fingerprint-confirmed
trust, conservative retries, and non-mutating diagnostics. Security review found
no remaining command-injection, silent-trust, writable-key, symlink, unbounded
output, unsafe-retry, or secret-logging blocker. No polling, job, plugin, API,
dashboard, password authentication, or RBAC feature was introduced.

The Linux Collector Foundation respects both domains and the SSH boundary. It
uses the least-privileged monitor identity and fixed structured commands, maps
only bounded validated facts, continues after isolated failures, and returns an
unpersisted observation suitable for `DiscoveryService`. Its parsers prefer JSON
and key/value formats, ignore unknown fields, never inspect output for logging,
and distinguish optional product absence from degraded core collection. The
implementation adds no SQL, inventory mutation, subprocess, scheduler, plugin,
Docker dependency, or network-dependent test. No blocking parser, security,
performance, maintainability, or extensibility finding remains after self-review.

The on-demand PollingService is a small application coordinator over the three
approved boundaries. It validates inventory eligibility, collects once, persists
and finalizes the observation, then records exactly one corresponding Inventory
poll outcome. Partial collection remains a successful partial observation; an
unknown Discovery final state cannot update Inventory; and no failure path can
return a successful immutable result. It owns no scheduling, job durability,
transport, SQL, repository, API, or UI behavior. No blocking polling finding
remains after the scoped tests and architecture review.

The Bootstrap Service preserves the same boundaries while providing the narrowly
approved remote repair workflow. Pre-existing admin access and strict trust are
mandatory, monitor access is locked to the deployed read-only artifact, private
keys never move, all remote writes are bounded and atomic, and exhaustive
post-verification gates the only InventoryService mutation. The standalone
artifact is compatible with Python 3.9 grammar and has no LIM or third-party
runtime dependency. No blocking user/group, sudo, authorization-file,
forced-command, permissions, symlink, idempotency, partial-failure, key-handling,
logging, or architecture finding remains after self-review.

The repository-only persistence rule is documented in engineering policy,
architecture, ADR-0006, repository contracts, and README guidance. An automated
AST boundary test prevents direct SQLite or low-level persistence-manager imports
from entering future business, SSH, plugin, or job modules and separately prevents
collectors from gaining repository, subprocess, or inventory-mutation ownership.

The original blocking findings are resolved. Runtime inventory, job, log, and
backup artifacts are recursively excluded from Git, and malformed environment
values are redacted from configuration errors and tracebacks. Automated regression
tests cover both fixes.

No blocking security finding from this review remains. The documented
Docker/configuration and policy-enforcement concerns remain non-blocking review
items and should be handled in deliberately scoped follow-up work.

## Minimal CLI self-review

- **Architecture:** `app.cli` is a thin argparse adapter over the composed
  InventoryService, SSHManager, BootstrapService, and PollingService. It imports
  no SQLite, subprocess, concrete repository, SSH process runner, scheduler, job,
  plugin, REST, or dashboard implementation. `app.composition` is the neutral
  composition root shared by startup and CLI and returns a frozen
  `ApplicationServices` dependency container; it is neither global nor mutable.
- **Commands implemented:** `server add`, `server list`, `server show`, `trust
  inspect`, `trust add`, `bootstrap`, and `poll`. Server references resolve by
  hostname or UUID through new read-only InventoryService methods that delegate
  to the existing repository interface. `server add --user` stores the approved
  administrative username as inventory metadata; the CLI accepts no password.
- **Output security:** commands emit bounded status, inventory identifiers, and
  public host-key fingerprints only. They never print private keys, public-key
  bodies, credentials, authorized-key contents, collector JSON, or raw SSH
  stdout/stderr. Operational exceptions map to fixed safe messages and nonzero
  exit codes.
- **Tests run:** targeted CLI, composition, InventoryService, architecture,
  startup, runtime, and persistence tests completed with `100 passed`. Scoped
  Ruff checks for every modified Python file passed.
- **Remaining limitations:** the CLI intentionally has no interactive prompts,
  password authentication, trust replacement command, pagination flags, machine-
  readable output, update/delete commands, scheduler, durable jobs, API,
  dashboard, plugins, shell completion, or operator authorization layer. Those
  require separately approved designs.
- **Blocking issues:** none found. The CLI preserves service ownership and the
  reusable composition performs local initialization only; network operations
  occur solely when an operator explicitly invokes trust, bootstrap, or poll.
