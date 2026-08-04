# Summary

This document records the completed self-review of all changes currently made on
this branch, including the Configuration Manager, Runtime Manager, Logging
Foundation, Persistence Foundation, and Inventory Domain Foundation. The review
covers architecture, security, maintainability, tests, Docker and Git practices,
remaining technical debt, and release readiness.

This branch establishes the Logical Infrastructure Manager (LIM) project
foundation. It adds permanent engineering guidance, a target architecture,
layered configuration, Python quality tooling, tests, container definitions,
security and lifecycle documentation, and focused Git exclusions. It implements
the authoritative server inventory while deliberately excluding SSH, polling,
plugins, jobs, scheduling, and user-interface features.

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

The architectural direction is appropriate for an early-stage infrastructure
manager: a modular monolith, inward dependency flow, SQLite as the authoritative
inventory, a single future `SSHManager`, injected dependencies, durable jobs, and
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
  package and composition roots.
- `tests/test_inventory_domain.py`, `tests/test_inventory_repository.py`, and
  `tests/test_inventory_service.py` verify model invariants, validation, schema,
  indexes, filters, search, pagination, rollback, soft deletion, optimistic
  conflicts, service transitions, dependency injection, and safe logging.
- `tests/test_discovery_domain.py`, `tests/test_discovery_repository.py`, and
  `tests/test_discovery_service.py` verify collected-fact validation, lifecycle,
  schema, indexes, history, search, pagination, rollback, retention, optimistic
  conflicts, dependency injection, and safe logging.

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

# Architecture Review

## Strengths

- A modular monolith is the right initial deployment model. It keeps operational
  complexity low while allowing strong internal ownership boundaries.
- SQLite is explicitly identified as the authoritative inventory. Remote state,
  plugin output, files, and caches are correctly treated as observations.
- `SSHManager` is reserved as the only SSH implementation. Preventing jobs and
  plugins from creating SSH clients will centralize host verification, credentials,
  timeouts, cancellation, pooling, output limits, and audit behavior.
- Bootstrap is correctly defined as the composition root and future lifecycle
  owner. Dependency injection is preferred over global clients and service
  locators.
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
- Define source-specific collector schemas and metadata allowlists before SSH or
  plugin collection is implemented.
- Add bulk recording and cleanup limits before high-volume polling exists.

## SSH, plugins, and jobs

- Define and implement the sole `SSHManager`, including strict host verification,
  credential references, timeouts, cancellation, output bounds, and audit data.
- Define a versioned plugin manifest and typed contract before implementing any
  provider plugin.
- Define durable job states, legal transitions, recovery, idempotency, retries,
  cancellation, concurrency limits, retention, and artifact cleanup.

## Operations and delivery

- Implement bootstrap and graceful shutdown after selecting the first real entry
  point.
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
237 tests passed
92.06% branch-aware coverage
Ruff passed
Python compilation passed
Configuration, runtime, logging, persistence, inventory, discovery, startup, and concurrency tests passed
Compose YAML structural parsing passed
git diff --check passed
```

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

# Remaining TODOs

## Resolved blocking TODOs

1. Recursive runtime ignore rules and regression coverage are complete.
2. Environment parser values are redacted from messages and tracebacks.

## Strongly recommended for this foundation branch

1. Decide and document how Docker consumes local configuration.
2. Remove the unused SSH mount from the one-shot foundation container until
   `SSHManager` exists.
3. Make the Python compatibility policy enforceable or phrase it as a tested
   baseline rather than a hard compatibility range.
4. Make Docker builds reproducible enough for the intended development stage.
5. Fix boolean-versus-integer validation and define supported YAML scalar types.
6. Make the canonical test command enforce coverage consistently.
7. Correct the repository-layout documentation for directories absent from Git.
8. Replace or remove the ineffective changelog comparison link.

## Acceptable as explicitly tracked follow-up

1. Add typed configuration schemas and duplicate-key detection.
2. Add CI, container builds, type checking, dependency auditing, and secret
    scanning.
3. Add licensing and contributor-governance files.
4. Implement inventory relationships, SSHManager, plugins, jobs, bootstrap, and
   interfaces only after their designs are approved.

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
safe, and optimistic-concurrency aware. No SSH, polling, plugin, job, API, UI, or
authorization behavior was added.

The Discovery Domain preserves the same layering while keeping observations
explicitly non-authoritative. Its immutable model, normalized schema, service
lifecycle, bounded metadata, indexed history, optimistic updates, and expired-only
cleanup are consistent with the approved design. Self-review regressions cover
immutable collected facts and reserved synchronization authority. No collector,
SSH, polling, plugin, job, interface, authentication, or authorization behavior
was added.

The repository-only persistence rule is documented in engineering policy,
architecture, ADR-0006, repository contracts, and README guidance. An automated
AST boundary test prevents direct SQLite or low-level persistence-manager imports
from entering future business, SSH, plugin, or job modules.

The original blocking findings are resolved. Runtime inventory, job, log, and
backup artifacts are recursively excluded from Git, and malformed environment
values are redacted from configuration errors and tracebacks. Automated regression
tests cover both fixes.

No blocking security finding from this review remains. The documented
Docker/configuration and policy-enforcement concerns remain non-blocking review
items and should be handled in deliberately scoped follow-up work.
