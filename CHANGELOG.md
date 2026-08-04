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

[Unreleased]: https://github.com/betin1975/logical-infrastructure-manager/compare/HEAD...HEAD
