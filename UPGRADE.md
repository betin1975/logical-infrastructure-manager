# Upgrade Guide

LIM has not released a stable application or database schema, so no supported
upgrade path exists yet.

Before the first release, the project must define:

- Semantic versioning and compatibility guarantees.
- Ordered transactional SQLite migrations.
- Pre-upgrade backup and restore verification.
- Container image and configuration compatibility policy.
- Plugin API compatibility and deprecation periods.
- Rollback behavior for application and schema changes.

Until then, review `CHANGELOG.md` and repository changes before updating. Never
reuse production-like runtime data across incompatible development revisions
without a verified backup.
