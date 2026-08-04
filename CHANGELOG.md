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

[Unreleased]: https://github.com/betin1975/logical-infrastructure-manager/compare/HEAD...HEAD
