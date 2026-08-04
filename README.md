# Logical Infrastructure Manager

Logical Infrastructure Manager (LIM) is a local-first platform for maintaining
an authoritative infrastructure inventory and safely coordinating inspection and
automation through controlled SSH access, durable jobs, and provider plugins.

> **Project status:** foundation development. Configuration, runtime, centralized
> logging, SQLite persistence, and the authoritative server inventory domain are
> implemented and tested. SSH, plugins, polling, jobs, and user-facing interfaces
> remain planned; they are not production features yet.

## Design principles

- SQLite is the authoritative inventory.
- Business logic reaches persistence only through injected repository interfaces;
  SSH, plugins, and jobs never execute SQL.
- `SSHManager` will be the only SSH implementation.
- Plugins adapt providers but do not own inventory, credentials, or orchestration.
- Bootstrap constructs dependencies; components prefer dependency injection.
- Runtime paths come from configuration and runtime state never enters Git.
- Every behavior change includes deterministic tests.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the system design and technical debt,
and [AI_DEVELOPER.md](AI_DEVELOPER.md) for permanent contribution rules.

## Requirements

- CPython 3.12
- `pip` and `venv`
- Docker with Compose v2, optionally

## Development setup

```shell
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest
ruff check .
```

See [INSTALL.md](INSTALL.md) for setup and verification details.

## Configuration

LIM loads configuration in increasing precedence:

1. `config/default.yml` — versioned, non-secret defaults.
2. `config/local.yml` — optional machine-specific settings, ignored by Git.
3. `LIM_` environment variables — deployment and process overrides.

Copy `config/local.yml.example` to `config/local.yml` when local overrides are
needed. Use double underscores for nested environment keys:

```shell
export LIM_APP__ENVIRONMENT=development
export LIM_LOGGING__LEVEL=DEBUG
```

Environment override values are parsed as safe YAML scalars, preserving types
such as booleans, numbers, lists, and null. Configuration values may reference
ordinary environment variables using `${NAME}` or `${NAME:-fallback}`.

```python
from app import ConfigManager

config = ConfigManager()
log_level = config.require("logging.level", str)
```

Callers receive copies of configuration data, and a failed reload preserves the
last valid configuration.

## Runtime management

`RuntimeManager` resolves every managed path from `ConfigManager` against the
application root supplied by startup. It creates and validates the configured
runtime root, SQLite data, job, log, and backup directories. Initialization is
idempotent and rechecks writability on every call.

Run the implemented startup foundation with:

```shell
python -m app
```

Managed file helpers reject absolute paths, traversal, and nested path components:

```python
from pathlib import Path

from app import ConfigManager, RuntimeManager


def initialize_runtime(application_root: Path) -> RuntimeManager:
    manager = RuntimeManager(
        ConfigManager(),
        application_root=application_root,
    )
    manager.initialize()
    return manager
```

The returned manager exposes `data_path(name)`, `job_path(job_id)`,
`log_path(filename)`, and `backup_path(name)`. These helpers return paths only;
the future inventory, job, logging, and backup components remain responsible for
their own files and formats.

## Logging

`LoggingManager` is the only supported logging configuration entry point. It uses
settings from `ConfigManager` and writes through paths supplied by
`RuntimeManager`. Initialization is idempotent, does not duplicate handlers, and
keeps the previous valid setup active if reconfiguration fails.

Components obtain contextual loggers from the manager:

```python
logger = logging_manager.get_logger(
    "bootstrap",
    operation="startup",
    correlation_id="request-123",
)
logger.info("foundation initialized")

try:
    perform_operation()
except RuntimeError:
    logger.exception("operation failed")
```

Supported context fields are `component`, `server_id`, `server_name`, `job_id`,
`operation`, and `correlation_id`. Callers should bind these values instead of
manually adding prefixes to messages.

LIM deliberately uses one structured rotating file,
`runtime/logs/application.log`. Bootstrap, future SSH, and future job events are
distinguished by their `component` field rather than separate files. Defaults are
console and file logging enabled, `INFO`, 10 MiB per file, five backups, and UTC
timestamps. Log files and rotated backups are restricted to mode `0640`. All
settings are configurable under the `logging` section.

Central redaction covers password, secret, token, API-key, private-key,
authorization, and credential fields; nested mappings and sequences; configured
sensitive values; sensitive environment values; ordinary messages; structured
context; and formatted exception tracebacks. Redaction is defense in depth, not
permission to intentionally log credentials or private keys.

## Persistence

SQLite is LIM's authoritative persistent store. The default database is
`runtime/data/lim.sqlite3`; its location and every SQLite policy come from
configuration and `RuntimeManager`. `DatabaseManager` creates mode `0600` files
and returns short-lived connections with foreign keys enabled, a 5-second busy
timeout, WAL journaling, `NORMAL` synchronization, `sqlite3.Row` access, deferred
explicit transactions, and same-thread enforcement.

Concrete repository implementations use `TransactionManager`, which commits
successful scopes, rolls back failures, and maps nested scopes to savepoints.
Business and interface code never receives its connection. Repositories do not
commit, roll back, or run migrations themselves.
Only repository implementations may execute domain SQL. Business logic,
`SSHManager`, plugins, and jobs depend on repository interfaces and never receive
raw SQLite connections.

Startup applies ordered internal Python migrations and logs only the resulting
schema version. Migration 1 creates `lim_schema_migrations`; migration 2 creates
only normalized inventory tables. No job, user, plugin, alert, SSH, or audit table
exists.

`BackupManager` uses SQLite's online backup API and atomically publishes mode
`0600` files under `runtime/backups`. Restore validation is deliberately
non-destructive: it opens a contained candidate read-only, runs SQLite integrity
validation, verifies migration metadata, and reports its schema version. Backup
retention and active-database replacement are not implemented.

## Inventory domain

`app.inventory` defines the authoritative, SQLite-independent infrastructure
model. `Server`, `Tag`, and `Label` are immutable. Server state uses enums for
platform, operating-system family, server type, administrative status, discovery,
health, and synchronization instead of magic strings.

All inventory changes go through an injected `InventoryService`:

```python
server = inventory_service.register_server(
    "edge-01.example.test",
    "192.0.2.10",
    tags=("linux", "edge"),
    labels={"owner": "platform"},
)
inventory_service.mark_healthy(server.uuid)
```

The service contains business transitions but no SQL. It depends on the
`InventoryRepository` interface; `SQLiteInventoryRepository` is the only concrete
inventory persistence implementation. SSHManager, plugins, polling, jobs, and
interfaces must use application services and never execute SQL.

Schema version 2 normalizes servers, globally unique primary/management
addresses, tags, server/tag relationships, and labels. Hostnames and addresses
remain reserved after soft deletion so restoration cannot collide with a reused
identity. Mutations increment `inventory_version` and stale writes fail instead
of silently overwriting newer inventory. Search and pagination are bounded; no
JSON inventory blobs or destructive delete path are provided.

## Tests and quality checks

Run the complete local validation suite before submitting changes:

```shell
python -m pytest --cov=app
ruff check .
python -m compileall -q app tests
```

The unit suite uses temporary files and does not require network access,
credentials, Docker, SSH servers, or production infrastructure.

## Container foundation

Build and run the current one-shot foundation initialization image:

```shell
docker compose build
docker compose run --rm lim
```

The image runs as a non-root user, excludes secrets and runtime state from its
build context, and uses read-only container filesystems with explicit runtime
mounts. There is deliberately no port or health check until LIM has an approved
long-running interface.

## Repository layout

```text
app/                 Application code and future composition root
config/              Versioned defaults and local configuration example
plugins/             Future provider adapters
tests/               Automated tests
runtime/data/        SQLite inventory and durable runtime data
runtime/jobs/        Future job artifacts
runtime/logs/        Optional local logs
runtime/backups/     Managed backups
ssh/                 Local SSH trust and credential material
ansible/             Reserved Ansible integration boundary
docker/              Reserved container support assets
docs/                Supplemental documentation
scripts/             Reserved maintenance and release scripts
```

Runtime directory placeholders are committed; their contents are ignored.

## Documentation

- [Architecture and technical debt](ARCHITECTURE.md)
- [Architecture decisions](DECISIONS.md)
- [Engineering operating instructions](AI_DEVELOPER.md)
- [Installation](INSTALL.md)
- [Security policy](SECURITY.md)
- [Roadmap](ROADMAP.md)
- [Upgrade guidance](UPGRADE.md)
- [Changelog](CHANGELOG.md)

## Security

Never commit credentials, private keys, local configuration, SQLite databases,
or infrastructure data. Follow [SECURITY.md](SECURITY.md) to report a vulnerability
privately.
