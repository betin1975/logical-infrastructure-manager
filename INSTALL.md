# Installation

LIM is currently a development foundation rather than a deployable service.
These instructions install and verify the implemented configuration subsystem.

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

Successful startup creates and validates the configured runtime tree and writes
the foundation event to `runtime/logs/application.log`. The default rotation is
10 MiB with five backups. Override logging behavior through `config/local.yml` or
`LIM_LOGGING__...` environment variables; never place credentials in logging
configuration.

## Docker

Docker Compose currently builds a one-shot configuration validation image:

```shell
docker compose build
docker compose run --rm lim
```

The bind-mounted `runtime/` and `ssh/` directories must be writable by container
UID/GID `10001` on Linux hosts. Do not add SSH credentials to an image.

## Production status

There is no supported production deployment until inventory migrations,
`SSHManager`, the job engine, an application entry point, health checks, backup
and restore procedures, and authentication/authorization requirements are
implemented and reviewed.
