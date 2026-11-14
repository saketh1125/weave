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

## Current model and deployment status

On 2026-11-14 both configured Mistral keys returned HTTP 403 `tier_not_allowed`
for `mistral-large-4`: account/model access must be resolved. This differs from
HTTP 429 rate limiting. The Weave Antigravity bridge was unavailable or
unconfigured and needs independent setup and verification. No automatic model
or provider fallback is assumed.

Documentation review run ceilings are configurable with
`SENTRA_DOCUMENTATION_MAX_RUN_CALLS` and `SENTRA_DOCUMENTATION_MAX_RUN_TOKENS`
(example defaults 200 and 16,000,000). A review stopped by a limit or deadline
is incomplete, including when it covers only part of a repository.

Deployment remains limited by single-user authentication, private deployment
requirements, unverified whole-repository review scale and an unset cloud
provider/deployment configuration.

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

## Recovery sprint checkpoint: 2026-11-14

The combined recovery candidate passed **477 backend tests**, including disposable PostgreSQL tests, **19 frontend contract tests**, and a frontend production build. The API and worker were recreated locally using that candidate. This supersedes migration-only counts above, not the outstanding live-model acceptance gates.

Changes include normalized and redacted provider errors, recoverable transient outages, bounded configurable review budgets, citation admission only for delivered source, stricter expired-worker leases, and protection against incomplete source ingestion deleting indexed records. Settings now offers a synthetic API connection probe. Publication guards have additional stale-base/branch/head regression coverage.

Both configured Mistral accounts rejected synthetic Large 4 requests with HTTP 403 `tier_not_allowed`. Large 4 remains selected at the user's request. Changing retries or run budgets cannot grant account access. The 165-section test is an orchestration simulation; real-model review quality and end-to-end completion remain unverified.

### Final recovery verification

The final candidate (`sha256:2542661432de45178230de60f9bb3d5ec7d72ecb2678a874f4e0a307ffd46bb1`) passed **487 backend tests** against disposable PostgreSQL, **23 frontend checks**, and the frontend production build. Frontend checks include source contracts and do not replace browser interaction testing. Older unsafe API reviewer conversations now require a fresh review; saved findings are retained. Publication checks updated findings before its first attempt and preserves recovery of an already-started publication.

The authenticated synthetic Settings probe reports Mistral `model_access_denied` with HTTP 403 `tier_not_allowed`. The independent Antigravity bridge is enabled and reachable, but its synthetic test returns `authentication_required`. No original authenticated profile was copied and no repository content was sent in these probes.

A final inclusion scan found no common token/private-key patterns in 270 candidate files. An exact-value scan against local credential values also found no matches. The original 274 Sentra source files, working-tree status, application branches/remotes, HEAD, Git configuration and index match the migration baseline; Codex desktop bookkeeping references changed automatically while this chat remained attached to Sentra.

A real whole-repository model review, a new Weave PR publication, browser interaction verification, and cloud deployment remain unverified. Six scanner advisories remain open because upstream Semgrep constraints exclude the patched dependencies.

## Native hybrid whole-file review candidate — 2026-11-14

The candidate replaces native heading-section scheduling with one native planner and at most five concurrent file-owning reviewers, including any optional static-triage lane. Static checks run first; reusable instances, unresolved leads and broader AI reasoning remain distinguishable. Source-bound fingerprints, saved file/triage progress, cumulative prompt budgets, exact citations, Markdown-only drafts and approved publication remain in place without a database migration.

The integrated backend passed **579 tests**, and all **31 frontend checks** and the frontend production build passed. A labeled synthetic hybrid run covered its five expected claims, reused two static results, and produced no false-confident labels or duplicate claims in that fixture. It used three native prompt turns over 183.214 seconds; provider-internal call/token counts were not available. Live large-queue triage and whole-repository accuracy remain unverified.

For the demo, both configured Mistral accounts passed JSON/tool tests using `codestral-2508`, and the actual Q&A prompt builder passed a synthetic documentation/code grounding check. This configuration was applied to the prior tested image. The hybrid candidate remains separate until its final rollout check. No new Antigravity login is required; runtime changes require a synthetic re-verification.

### Approved local rollout

The candidate was applied to Weave after explicit approval. The API readiness check passed, the existing Antigravity profile passed live synthetic re-verification, and the worker was recreated using the same tested image. No database migration or new login was required. Old section-based conversations remain saved and require a fresh review for the new whole-file policy.

A live Codestral draft smoke changed only an invented README HTTP status from 200 to 202, matching the cited FastAPI declaration; source code remained unchanged. It used one request, 596 prompt tokens and 114 completion tokens, taking 1.33 seconds. No generated documentation PR was published.

Sentra demo PR #2 is separately open against its main branch for the user to analyze; it contains a Q&A embedding-cache fix and concurrency regression test. The original local Sentra checkout remains unchanged. A real whole-repository native review and browser interaction acceptance have not been completed.
