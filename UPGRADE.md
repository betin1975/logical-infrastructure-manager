# Upgrade Guide

LIM has not released a stable application or domain schema, so no supported
production upgrade path exists yet. The persistence foundation now applies
ordered internal migrations automatically during `python -m app`; the only
current migrations create migration metadata, normalized authoritative inventory,
and normalized discovery history. SSHManager adds configuration and mounted-file
requirements but no database table. Job, plugin, user, alert, and audit tables do
not exist.

Bootstrap adds no database migration. It adds required `bootstrap` configuration,
a read-only monitor public-key input, and a versioned standalone collector
artifact. Existing development containers must mount
`ssh/monitor_ed25519.pub` read-only in addition to the two private identities.
Targets must provide Python 3.9 or newer, pre-established admin public-key access,
confirmed LIM host trust, and non-interactive sudo before an explicit bootstrap
request. Startup validates these local inputs but never changes a target.

Re-running bootstrap upgrades or repairs only the LIM-marked authorized-key line
and collector artifact after validating existing account ownership. It preserves
unrelated key lines and records the inventory bootstrap timestamp only after full
post-verification. Review collector schema/version changes before upgrading; a
cross-version compatibility window is not yet guaranteed.

Existing development deployments must add the complete `ssh` configuration,
provide separate read-only admin and monitor identities, and move application
host trust to the configured runtime-data `known_hosts`. A historical writable
`ssh/` mount is unsupported; do not copy private keys into runtime data.

Before the first release, the project must define:

- Semantic versioning and compatibility guarantees.
- Domain migration compatibility and downgrade policy.
- Mandatory pre-upgrade backup, retention, and destructive restore orchestration.
- Container image and configuration compatibility policy.
- Plugin API compatibility and deprecation periods.
- Rollback behavior for application and schema changes.

Until then, review `CHANGELOG.md` and repository changes before updating. Create
and validate a SQLite-consistent backup before testing a new revision. Candidate
validation does not authorize or perform replacement of the active database.
Never reuse production-like runtime data across incompatible development
revisions without a tested recovery plan.
