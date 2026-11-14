# Weave implementation status

**Migration verification: 2026-11-14.** Weave was established from the latest
local working files, including uncommitted documentation-review fixes. It has
fresh Git history and its own private remote. Earlier Sentra validation records
are historical context, not Weave production acceptance.

## Preserved capabilities

- Repository indexing, Markdown ingestion, local embeddings and source-cited Q&A.
- Static analysis and dependency findings, repository scores and PR review.
- Documentation-first claim investigation, evidence/citation validation, durable
  progress checkpoints and continuation of pending sections.
- Markdown-only update drafts, exact-draft approval and guarded PR publication.
- Provider-independent API connections and an optional isolated local ACP reviewer.
- Single-user authentication, bounded jobs, database migrations and recovery tools.

## Verified for this migration

- Backend image built from Weave's own application source and locked dependencies.
- **449 backend tests passed**, including disposable PostgreSQL integration tests.
- Frontend dependencies installed with `npm ci`; production build passed.
- **18 frontend contract tests passed.**
- Credential checks compared included files with existing real credential values
  and scanned token/private-key patterns. No matches were included.

The backend suite reports an existing pytest warning about an empty regular
expression match. Vite reports an existing configuration-loader warning.
Neither warning failed the checks.

## Configuration and remaining verification

No `.env`, database, model download, cloned repository, cached experiment output,
or authenticated runtime profile was copied. Set fresh credentials using the safe
configuration templates. API routes, database schema and `SENTRA_*` environment
keys remain compatible; see [Migration](MIGRATION.md).

Local ports are frontend 5174, API 8001, PostgreSQL 5433 and optional ACP bridge
9218. The internal container API/database ports remain 8000/5432. Compose uses
project `weave`, its own named volumes and default image `weave-api`.

Live model generation, native ACP authentication, scanning a new repository,
GitHub PR publication, and cloud deployment were not rerun for this migration.
The complete 165-section repository review was not rerun. Timeout recovery and
partial-claim persistence are covered by the backend suite; model-dependent
investigations may still pause at their configured deadline.

See [README](../README.md), [Architecture](ARCHITECTURE.md),
[Model connections](MODEL_CONNECTIONS.md), and [Migration](MIGRATION.md).
