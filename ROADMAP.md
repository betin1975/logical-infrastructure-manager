# Roadmap

The roadmap is ordered by architectural dependency, not promised release dates.

## Foundation

- Establish configuration, documentation, quality tooling, and container policy.
- Define module ownership and security boundaries.
- Keep Python 3.12 validation green.

## Inventory core

- Approve inventory use cases and schema.
- Implement migrations and SQLite repositories.
- Add safe backup, restore, and upgrade workflows.

## Remote access and plugins

- Define and implement the sole `SSHManager`.
- Define the versioned plugin contract and manifest.
- Add one reference plugin only after contract tests exist.

## Job execution

- Implement durable job state, workers, cancellation, recovery, and retention.
- Integrate jobs through application services and injected dependencies.

## Operator interface

- Select and threat-model the initial CLI, API, or UI.
- Implement authentication, authorization, audit, health, and lifecycle behavior.
- Publish supported deployment and disaster-recovery guidance.

Detailed open design and engineering work is tracked in the Technical Debt
section of `ARCHITECTURE.md`.
