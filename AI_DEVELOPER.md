# LIM Engineering Operating Instructions

This document is the permanent engineering policy for human and AI contributors
to Logical Infrastructure Manager (LIM). Read it before changing the repository.
When a pull request conflicts with this document, either update the design and
this document deliberately or change the pull request.

## Project purpose

LIM is a local-first infrastructure inventory and automation platform. It will
discover, model, inspect, and operate logical infrastructure through a stable
inventory, controlled SSH access, asynchronous jobs, and provider plugins. The
project must remain auditable, deterministic, and safe to operate on production
infrastructure.

The current repository is a foundation. Do not infer product behavior that has
not been approved and documented.

## Architectural rules

- Keep domain policy independent from transport, persistence, and vendor APIs.
- SQLite is the authoritative inventory. Caches, plugin responses, files, and
  remote systems are observations; they never replace the inventory database as
  LIM's source of truth.
- `SSHManager` is the only SSH implementation. All SSH sessions, commands, file
  transfers, host-key checks, timeouts, and authentication must pass through it.
  Plugins and jobs must never instantiate SSH clients or invoke `ssh` directly.
- Business logic must never communicate directly with SQLite. All domain-state
  persistence passes through repository interfaces, and only repository classes
  may read or persist domain state.
- `SSHManager`, plugins, jobs, UI, API, and application services must never import
  SQLite, execute SQL, construct repositories, or access `DatabaseManager`
  directly. Bootstrap injects repository interfaces into their consumers.
- Keep plugins behind a versioned plugin contract. Plugins may translate vendor
  behavior, but must not own orchestration, credentials, or authoritative state.
- Bootstrap constructs the dependency graph. Prefer dependency injection over
  module globals, hidden singletons, service locators, or ambient process state.
- Never hardcode paths. Obtain paths from configuration and pass resolved
  `pathlib.Path` values into consumers.
- Never duplicate code. Extract shared behavior at the narrowest stable layer;
  do not create premature generic frameworks.
- Use dataclasses for immutable value objects, commands, results, configuration
  records, and dependency containers where their semantics fit.
- Preserve public imports and persisted data formats unless a documented
  compatibility and migration plan accompanies the change.

See `ARCHITECTURE.md` for module ownership and dependency direction.

## Repository layout

```text
app/                 Python application and composition root
config/              Versioned defaults; local.yml is private and ignored
plugins/             Provider adapters implementing the plugin contract
tests/               Tests mirroring application boundaries
runtime/data/        SQLite database and durable application data
runtime/jobs/        Job artifacts and transient job workspaces
runtime/logs/        Local log output when file logging is enabled
runtime/backups/     Managed SQLite and configuration backups
ssh/                 SSH trust material and local credentials; never committed
ansible/             Future managed Ansible integration boundary
docker/              Future container support assets
docs/                Supplemental operator and developer documentation
scripts/             Small, reviewed maintenance and release scripts
```

Do not reorganize existing modules solely for aesthetics. Introduce the target
package boundaries incrementally and leave compatibility imports when moving
public objects.

## Python and dependencies

- The baseline runtime is CPython 3.12.
- Production code must remain compatible with Python `>=3.12,<3.13` until the
  baseline is changed deliberately across documentation, CI, and containers.
- Use modern typing and `pathlib`. Avoid compatibility shims for older Python.
- Runtime dependencies belong in `requirements.txt`; development-only tools
  belong in `requirements-dev.txt`.
- Add a dependency only when the standard library or an existing dependency is
  insufficient. Record the reason in the change description.
- Pin safe version ranges and evaluate lock files before production releases.

## Coding standards

- Follow PEP 8 and format consistently with a maximum line length of 88.
- Ruff is the repository linter. New code must pass `ruff check .`.
- Public modules, classes, functions, and non-obvious algorithms require useful
  docstrings. Comments explain why, not what the code already states.
- Use type annotations for public interfaces and meaningful internal boundaries.
- Keep functions focused and side effects explicit.
- Catch narrow exceptions. Wrap infrastructure errors in domain-specific errors
  while retaining the original exception as the cause.
- Avoid mutable default arguments, wildcard imports, import-time I/O, and global
  clients or database connections.
- Prefer composition to inheritance. Introduce protocols at consumer boundaries
  when multiple implementations or test doubles are justified.

## Naming conventions

- Modules, functions, variables, and database columns: `snake_case`.
- Classes, dataclasses, exceptions, and protocols: `PascalCase`.
- Constants: `UPPER_SNAKE_CASE`.
- Private implementation details: a leading underscore.
- Boolean names should read as predicates (`is_active`, `has_failed`).
- Repository classes end in `Repository`; service coordinators end in `Service`;
  manager names are reserved for resource-lifecycle owners such as `SSHManager`.
- Tests use `test_<behavior>_<condition>` and describe observable behavior.

## Logging standards

- Obtain component loggers from the injected `LoggingManager`. Do not call
  `logging.basicConfig`, construct handlers, or configure logger levels elsewhere.
- Use `ContextLogger.bind()` for server, job, operation, and correlation context;
  do not construct textual prefixes manually.
- Never use `print` for application diagnostics.
- Prefer structured context: operation, job ID, plugin, host/inventory ID, and
  duration. Do not use secrets or raw credentials as context.
- `DEBUG` contains diagnostic detail; `INFO` lifecycle events; `WARNING`
  recoverable degradation; `ERROR` failed operations; `CRITICAL` process-wide
  loss of service.
- Log exceptions once at the boundary that handles or terminates them. Preserve
  tracebacks with the contextual logger's `exception()` method.
- Never log passwords, private keys, tokens, unredacted connection strings, or
  complete command output that may contain secrets.
- Central redaction is defense in depth and does not make deliberate secret
  logging acceptable. Add redaction regression tests when introducing a new
  credential field or representation.

## Configuration and paths

- Load configuration through `ConfigManager`; do not parse YAML or environment
  variables elsewhere.
- Precedence is versioned defaults, optional local configuration, then `LIM_`
  environment overrides.
- Use `LIM_SECTION__KEY` for nested environment keys.
- Keep deployable defaults non-secret. Secrets come from the environment or a
  future secret provider and must not be committed.
- Resolve relative paths once during bootstrap and inject them. Library code must
  not assume the current working directory or repository location.

## SQLite and inventory

- SQLite is the authoritative inventory and must enforce foreign keys.
- Open SQLite only through `DatabaseManager`; connections are short-lived and
  operation-scoped, never global or shared across unrelated work.
- `TransactionManager` exclusively owns transaction control. Repositories receive
  the active connection through injection and never commit or roll back.
- Only repository implementations execute domain SQL. Business logic depends on
  repository interfaces and remains independent of SQLite and schema details.
- Database policy, migrations, backup, and restore-validation infrastructure may
  execute only the internal SQLite operations required by those responsibilities;
  they must never become an alternate path for persisting domain state.
- Schema changes require ordered, transactional migrations and rollback or
  recovery guidance. Run them through `MigrationManager`; repositories never
  migrate. Never edit deployed database files manually.
- Use explicit transactions for multi-record changes and short transactions for
  concurrency. Define busy timeouts and WAL behavior centrally.
- Keep SQL visible and parameterized. Do not introduce generic CRUD abstractions,
  log SQL parameters, or include database contents in exceptions.
- Store UTC timestamps in an unambiguous format.
- Create backups through `BackupManager` using SQLite's backup API. Restore
  validation is read-only; destructive replacement requires a future approved
  design and must never be inferred from validation success.
- Never put database files in Git, images, logs, or bug reports.

## Inventory domain

- `Server`, `Tag`, `Label`, and inventory state values are immutable domain
  objects. Add behavior through validated transitions, never by mutating fields.
- `InventoryService` is the only business mutation gateway. SSH, plugins, jobs,
  and interfaces call it; they do not write through repositories directly.
- Consumers depend on `InventoryRepository`. Only the concrete repository under
  `app.persistence` receives `DatabaseManager` and `TransactionManager` or
  executes inventory SQL.
- Preserve soft-deleted servers, hostnames, and addresses until an approved
  retention process exists. Never add a hard-delete shortcut.
- Use `inventory_version` for optimistic concurrency. A stale update fails rather
  than overwriting a newer accepted state.
- Keep addresses, tags, and labels normalized. Do not replace relational
  inventory state with JSON blobs for convenience.
- Store inventory timestamps as timezone-aware UTC values and use enums instead
  of magic state strings.

## Discovery domain

- Discovery stores immutable observations and history; it never represents
  accepted inventory truth.
- `DiscoveryService` is the observation lifecycle gateway. Collectors provide
  validated facts through it and never write discovery repositories directly.
- Discovery may reference inventory server UUIDs but must never update inventory.
  Only `InventoryService` may accept observed facts into authoritative state.
- Only the concrete discovery repository under `app.persistence` executes
  discovery SQL. SSHManager, plugins, polling, and jobs receive services.
- Retention cleanup is explicit and may purge only expired observations older
  than a validated UTC cutoff. Never silently discard pending or completed
  observation history.
- Bound raw metadata and never collect or persist credentials, command lines, or
  complete command output as discovery facts.

## SSH and remote execution

- `SSHManager` is the sole gateway for SSH behavior.
- Strict host-key verification is the default. Trust-on-first-use, if ever
  supported, must be explicit, audited, and configurable.
- Enforce connect, command, and idle timeouts. Bound captured output.
- Prefer key or agent authentication. Never persist plaintext passwords.
- Treat remote command arguments as untrusted and avoid shell interpolation.
- Record auditable metadata without recording secrets or sensitive output.

## Plugins and jobs

- Plugins implement a versioned interface and declare capabilities and metadata.
- Plugins receive dependencies; they do not read global configuration, open the
  database, or create SSH connections themselves.
- Validate plugin input and normalize output before it reaches the domain layer.
- Jobs have explicit states, durable metadata, bounded retries, idempotency
  expectations, cancellation behavior, and structured result/error records.
- Job handlers coordinate services; they do not duplicate plugin, SSH, or
  repository behavior.

## Testing requirements

- Always write tests for every behavior change and regression fix.
- Mirror application boundaries under `tests/`; keep tests deterministic and
  independent of execution order.
- Unit tests must not require network access, production credentials, wall-clock
  timing, or the developer's filesystem.
- Use temporary directories and databases. Inject environment and infrastructure
  doubles rather than patching broad global state.
- Integration tests must be clearly marked and use disposable resources.
- Before submitting, run:

  ```shell
  python -m pytest
  ruff check .
  python -m compileall -q app tests
  ```

- Do not lower assertions or skip failing tests to make a build pass. Explain any
  platform test that cannot run and provide the exact command for reproduction.

## Security rules

- Apply least privilege to processes, containers, database access, and remote
  operations.
- Validate all external input at a trust boundary.
- Never commit or log secrets, credentials, SSH keys, runtime databases, or
  customer infrastructure data.
- Use safe parsers and parameterized SQL. Avoid shell execution; when unavoidable,
  pass argument arrays and reject untrusted interpolation.
- Verify SSH host keys and TLS certificates by default.
- Bound time, memory, concurrency, retries, and output for remote operations.
- Redact sensitive values from exceptions, logs, job results, and tests.
- Report vulnerabilities according to `SECURITY.md`; do not open public issues
  containing exploit details or secrets.

## Docker conventions

- Use the declared Python baseline and slim official images.
- Run as a dedicated non-root user with an explicit working directory.
- Install dependencies before copying frequently changing source for cache reuse.
- Keep build context free of secrets and runtime data with `.dockerignore`.
- Set deterministic Python environment flags and use exec-form commands.
- Containers are immutable except for explicitly mounted runtime paths.
- Drop Linux capabilities and enable `no-new-privileges` where supported.
- Do not bake `config/local.yml`, SSH material, databases, or `.env` files into an
  image.
- Add a health check only when a real long-running service endpoint exists.

## Git workflow

- Branch from the current integration branch using `feature/<topic>`,
  `fix/<topic>`, `docs/<topic>`, or `chore/<topic>`.
- Keep commits focused, reviewable, and buildable. Use imperative Conventional
  Commit subjects such as `feat(config): add layered configuration loading`.
- Do not commit generated caches, runtime state, credentials, local configuration,
  or unrelated formatting changes.
- Rebase or merge the current target branch before review according to repository
  policy; never rewrite shared branch history without coordination.
- Pull requests must explain intent, architecture impact, security implications,
  dependency changes, migrations, tests, and operator-visible changes.
- Update `CHANGELOG.md` under `Unreleased` for meaningful changes.
