# Changelog

All notable changes to Logical Infrastructure Manager will be documented in this
file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project intends to follow [Semantic Versioning](https://semver.org/)
after its first release.

## [Unreleased]

### Added

- Permanent engineering instructions in `AI_DEVELOPER.md`.
- System architecture, module boundaries, and technical-debt register in
  `ARCHITECTURE.md`.
- Layered YAML configuration with local and environment overrides, safe variable
  expansion, typed access, validation, immutable snapshots, and atomic reloads.
- Idempotent runtime initialization with configuration-driven paths, permission
  validation, managed placeholders, safe child-path helpers, and startup wiring.
- Centralized console and rotating-file logging with structured component context,
  UTC timestamps, idempotent atomic reconfiguration, and nested secret redaction.
- SQLite persistence foundation with operation-scoped connections, enforced
  pragmas, explicit transactions, nested savepoints, and injected repository
  contracts.
- Ordered transactional Python migrations and initial migration-history metadata.
- Atomic SQLite online backups and read-only restore validation with integrity and
  schema-version checks.
- Architecture decision records for persistence authority, connection lifecycle,
  migrations, backup, and deferred destructive restore.
- Enforced the repository-only domain persistence boundary for business logic,
  `SSHManager`, plugins, and jobs.
- Immutable authoritative server inventory with typed platform, operating-system,
  lifecycle, discovery, health, and synchronization states.
- SQL-free `InventoryService` and repository interface covering registration,
  updates, lifecycle, polling outcomes, failures, tags, and labels.
- Schema version 2 with normalized servers, globally unique addresses, tags,
  server/tag relationships, labels, documented indexes, soft deletion, and
  optimistic inventory versions.
- SQLite inventory repository with search, pagination, filters, duplicate defense,
  transaction rollback, and infrastructure-error translation.
- Immutable discovery observation models and a SQL-free lifecycle service for
  recording, completion, failure, expiry, history, and explicit retention cleanup.
- Schema version 3 with normalized discovery observations, interfaces, addresses,
  disks, services, packages, containers, processes, bounded metadata, foreign
  keys, and documented query indexes.
- SQLite discovery repository with latest/history lookup, filtering, search,
  pagination, optimistic lifecycle transitions, and atomic cleanup.
- Sole system-OpenSSH `SSHManager` with structured commands, strict isolated host
  trust, read-only identity validation, bounded output, safe failure
  classification, transient-only retries, diagnostics, and single-file transfer.
- Atomic fingerprint-confirmed host trust operations with unknown, trusted,
  changed, and unreachable states; automatic trust-on-first-use is forbidden.
- Container OpenSSH client and separate read-only admin/monitor key mounts without
  a broad writable SSH mount.
- Read-only Linux collector with a fixed bounded SSH command catalog, resilient
  JSON/key-value/text parsers, partial-result handling, typed discovery mapping,
  and positive Docker, MySQL/MariaDB, Redis, Prometheus, Asterisk, and FreePBX
  detection.
- Collector architecture tests and mocked Ubuntu, Debian, Rocky Linux, AlmaLinux,
  missing-command, timeout, malformed-output, product-detection, and safe-logging
  unit coverage without network or Docker dependencies.
- Idempotent remote `BootstrapService` with an explicit 15-step plan, strict
  trust/admin/sudo prerequisites, monitor-account create-and-repair behavior,
  atomic forced-key installation, temporary cleanup, and post-verification.
- Standalone standard-library Python 3.9+ remote health artifact with fixed
  read-only commands, bounded execution/output/JSON, typed service state, and no
  dependency on LIM application packages.
- Bootstrap configuration, typed results and failures, public-key/artifact local
  initialization, Docker public-key mount, InventoryService success recording,
  and comprehensive mocked and executable regression tests.
- Minimal dependency-injected argparse CLI for server add/list/show, explicit SSH
  trust inspection/addition, bootstrap, and single-server polling.
- Frozen reusable application composition shared by startup and the CLI, plus
  narrow read-only InventoryService list, hostname, UUID, and reference queries.
- Default and example local configuration files.
- Unit tests and reproducible development-tool requirements.
- Python, pytest, coverage, and Ruff project policy.
- Non-root Docker image and hardened Compose foundation.
- Focused Docker build exclusions.
- Installation, security, roadmap, and upgrade documentation.

### Changed

- Replaced the placeholder README with project status, setup, configuration,
  testing, container, and documentation guidance.
- Reduced `.gitignore` to project-relevant rules while retaining Python, secret,
  runtime, database, SSH, editor, and container exclusions.

### Security

- Excluded local secrets, SSH material, SQLite state, and runtime data from Git
  and container build contexts.
- Configured the container to run as a non-root user with dropped capabilities,
  `no-new-privileges`, and a read-only root filesystem under Compose.
- Restricted SQLite database, journal, and backup files to mode `0600`; rejected
  unsafe names, traversal, symlinks, and paths outside runtime-owned directories.
- Required pre-established admin public-key access, strict host trust, `sudo -n`,
  locked monitor passwords, removal from configured privileged groups, OpenSSH
  `restrict` forced commands, atomic remote files, and post-bootstrap verification;
  no private key, password, raw remote output, or collector document is logged.

[Unreleased]: https://github.com/betin1975/logical-infrastructure-manager/compare/HEAD...HEAD
