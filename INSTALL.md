# Installation

LIM is currently a development foundation rather than a deployable service.
These instructions install and verify the implemented foundation subsystems.

## Local development

Install CPython 3.12, then from the repository root run:

```shell
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

Optional local configuration can be created with:

```shell
cp config/local.yml.example config/local.yml
```

Do not put passwords, tokens, or private keys in that file. Prefer environment
variables and keep filesystem permissions restrictive.

Verify the installation:

```shell
python -m app
python -m pytest --cov=app
ruff check .
```

Successful startup creates and validates the configured runtime tree, initializes
`runtime/data/lim.sqlite3`, applies pending internal migrations, and writes the
resulting schema version to `runtime/logs/application.log`. Repeated startup is
idempotent. The default log rotation is 10 MiB with five backups. Override
logging or SQLite policies through `config/local.yml` or `LIM_...` environment
variables; never place credentials in configuration.

Database and backup files use mode `0600`. Back up through the Python
`BackupManager` API so WAL state is copied consistently; never copy a live SQLite
file directly. Restore validation does not replace the active database. Production
retention, restore orchestration, and disaster-recovery procedures remain pending.

Schema version 3 includes normalized authoritative inventory tables and
non-authoritative discovery observation history. Startup inserts neither
inventory nor discovery records. Application code must use `InventoryService`
and `DiscoveryService`; direct SQL and manual database edits are unsupported.
Discovery cleanup deletes only explicitly expired observations older than a
caller-provided retention cutoff.

## Docker

Docker Compose currently builds a one-shot foundation initialization image:

```shell
docker compose build
docker compose run --rm lim
```

The bind-mounted `runtime/` and `ssh/` directories must be writable by container
UID/GID `10001` on Linux hosts. Do not add SSH credentials to an image.

## Production status

There is no supported production deployment until inventory migrations,
`SSHManager`, the job engine, a long-running application entry point, health
checks, backup retention and destructive restore procedures, and
authentication/authorization requirements are implemented and reviewed.
