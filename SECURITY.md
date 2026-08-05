# Security Policy

## Supported versions

LIM has not made its first release. Only the latest commit on the active
development branch receives security fixes.

## Reporting a vulnerability

Do not open a public issue containing vulnerability details, credentials,
infrastructure identifiers, command output, database contents, or exploit code.

Use the repository's private GitHub security-advisory reporting channel. If that
channel is unavailable, contact a repository maintainer privately and request a
secure reporting method before sharing details.

Include, when safe:

- The affected commit or version.
- The impacted component and deployment assumptions.
- Reproduction steps using synthetic data.
- Expected and observed behavior.
- Potential impact and any known mitigations.

Maintainers should acknowledge receipt, establish a private remediation plan,
add regression tests, and coordinate disclosure after a fix is available.

## Sensitive data rules

- Never commit `.env` files, `config/local.yml`, private keys, credentials,
  SQLite databases, runtime artifacts, or real infrastructure data.
- Never include sensitive values in logs, exceptions, job results, fixtures,
  screenshots, or bug reports.
- Use synthetic hosts, addresses, usernames, keys, and inventories in tests and
  documentation.
- Treat SSH host-key changes as security events; never disable verification to
  work around them.
- Report an accidentally committed secret immediately and rotate it. Removing it
  from a later commit is not sufficient.

The detailed implementation rules are maintained in `AI_DEVELOPER.md`.

## SSH security model

- `SSHManager` is the only component permitted to invoke OpenSSH or manage host
  trust. It never accesses SQL, repositories, InventoryService, or
  DiscoveryService.
- Strict host-key checking is mandatory against LIM's application-owned
  `runtime/data/known_hosts`. Personal trust files and automatic
  trust-on-first-use are disabled.
- Trusting or replacing a key requires explicit SHA256 fingerprint confirmation
  against a fresh scan. Changed keys are reported distinctly and never replaced
  automatically.
- Admin and monitor keys must be read-only non-symlink regular files beneath the
  configured credential root. LIM never generates or modifies key material.
- SSH processes use argument arrays, no local shell, a minimal environment, null
  stdin, closed descriptors, bounded execution time, and bounded output.
- Password and keyboard-interactive authentication, arbitrary shell text,
  recursive file transfer, and automatic permission escalation are unsupported.

## Remote bootstrap security model

- Administrative public-key access, fingerprint-confirmed host trust, Linux,
  Python 3.9+, and non-interactive `sudo -n` are prerequisites. Bootstrap never
  enrolls admin access, accepts passwords, or changes trust.
- Only `BootstrapService` orchestrates remote provisioning, and every remote
  operation passes through `SSHManager`. The service has no SQL, repository,
  discovery, collector, or subprocess access.
- LIM deploys the monitor public key only. Admin and monitor private keys remain
  read-only local files and are never returned, transferred, or logged.
- The monitor account has a locked password and no configured privileged group.
  LIM's key line uses `restrict` and a forced absolute collector command; unrelated
  pre-existing authorized keys remain the operator's responsibility.
- Remote writes validate absolute configured paths, reject symlinks and unexpected
  file types, stage bounded data, fsync, set explicit owner/mode, and atomically
  replace the destination. Partial operations are idempotent and retryable.
- The standalone artifact accepts no arguments, uses fixed read-only commands,
  runs without a shell or third-party dependency, discards stderr, bounds runtime
  and output, and emits a versioned bounded JSON document.
- Bootstrap success is recorded only after account, group, password, ownership,
  mode, digest, direct execution, strict trust, monitor authentication,
  forced-command confinement, schema, and host-identity verification succeeds.
- Logs and typed failures exclude key bodies, authorized-key contents, remote
  stdout/stderr, collector JSON, credentials, and raw exception text.
