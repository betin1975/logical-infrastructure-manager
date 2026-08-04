# Upgrade Guide

LIM has not released a stable application or domain schema, so no supported
production upgrade path exists yet. The persistence foundation now applies
ordered internal migrations automatically during `python -m app`; the only
current migrations create LIM's migration-history metadata and normalized server
inventory schema. They do not create SSH, job, plugin, user, alert, or audit
tables.

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
