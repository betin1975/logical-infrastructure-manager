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
