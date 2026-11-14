# API reference

All routes in this reference are served under the `/api` prefix. For example,
`GET /repos` is `GET /api/repos`. The OpenAPI document is `/api/openapi.json`;
API routes require a signed bearer session except `/api/health`, `/api/ready`,
and the single-user login exchange at `/api/auth/token`.

Base URL for local development: `http://127.0.0.1:8000`. JSON request and
response bodies are used unless stated otherwise. Repository identifiers are
UUIDs. The running OpenAPI schema is available at `/api/openapi.json`.

## Health

| Method and path | Description |
|---|---|
| `GET /health` | Process health. Returns `{"status":"ok"}`. |
| `GET /ready` | Database readiness. Returns `ready` when `SELECT 1` succeeds. |

## Repositories

| Method and path | Description |
|---|---|
| `POST /repos` | Register and asynchronously ingest a repository. Body: `remote_url`, optional `name`, optional `branch` (defaults to `main`). |
| `GET /repos` | List repositories and their ingest status. |
| `GET /repos/{repo_id}` | Get repository metadata. |
| `POST /repos/{repo_id}/ingest` | Retry clone/update, parsing, and embedding. |
| `POST /repos/{repo_id}/embed` | Queue embedding of unembedded chunks. Returns `job_id` and `queued` status. |

Repository status commonly moves through `pending`, `ingesting`, `embedding`,
and `ready`; failures use `failed`. Scan failures use `scan_failed`.

Repository creation, re-ingestion, embedding, and scans enqueue persisted work
for the Linux worker. `GET /repos/{repo_id}/jobs` returns up to 50 recent jobs
with status, attempts, and a safe error message. Identical active requests reuse
the existing job; conflicting work returns HTTP 409. A worker must be running.

## Retrieval and documentation

| Method and path | Description |
|---|---|
| `POST /repos/{repo_id}/ask` | Body: `question`, optional `top_k` (clamped to 1–20). Returns answer (when generated), retrieval candidates, and citations. |
| `POST /repos/{repo_id}/docs` | Optional `limit` (1–50, default 10) and `force`. Prioritizes unfinished units, validates and versions summaries, persists completed units, and discloses source coverage. Concurrent generation for the same repository returns 409. |
| `POST /repos/{repo_id}/drift` | Checks repository Markdown against indexed Python FastAPI code and stores the result with the checked commit. |
| `GET /repos/{repo_id}/drift` | Returns the latest stored alignment result, or `null` before the first run. |

Ask returns ranked source candidates even when model generation is unavailable.
The candidates include source content; clients should treat them as code data.
Docs results can contain `ok`, `partial`, `empty`, `disabled`, `rate_limited`, `provider_unavailable`, `budget_exhausted`, or `error`
status values.

`partial` means some units failed while others were returned or saved; `error`
means all attempted units failed. Per-unit errors and an aggregate `failed`
count accompany these results. Failed units remain eligible for retry.

Generation reviews up to eight overlapping source sections per code unit
(3,000 characters each, 200-character overlap), then combines the validated
section summaries. Each request allows at most 12 physical provider attempts; a unit is
started only if its section and combination calls fit the remaining budget.
`budget_exhausted` preserves completed units; repeat the request to continue.
Documentation requests space attempt starts by at least 1.1 seconds and retry a
transient error once when the remaining attempt/time budget permits. They use
the shared HTTP client's single-attempt option, preventing nested retries.
Scheduling stops after 30 seconds; the remaining time also bounds each HTTP
socket-inactivity timeout. This is not a strict wall-clock deadline for an
in-flight HTTP call. Pacing applies within one documentation request; concurrent
requests and token-based limits can still cause provider throttling.
`generated` counts new summaries saved in this response. Completed units are
committed individually and valid caches are reused on continuation. A terminal
provider limit returns a safe reason and optional `retry_after_seconds` (whole
seconds, capped at 3,600). The UI respects this cooldown and requires a manual
continuation. Explicit daily/billing quota errors stop immediately, without a
short-retry hint. Q&A retains the shared client's existing defaults.
`doc.coverage` reports `source_characters`, `processed_characters` (distinct
source characters), and `sections_processed`. `context_complete` is true only
when the reviewed sections cover the full unit, not a guarantee of summary
factuality. Units beyond 22,600 characters remain explicitly partial. Only the
final validated summary is cached; intermediate section results are not saved.

Drift checks explicit HTTP success status claims that identify a method and
path, explicit environment-default claims, and supported FastAPI request-body
field presence, requiredness and literal defaults. It reports aligned, outdated, and
unverified claims with document context and source citations. Python FastAPI
and a narrow same-file Express adapter are supported. A repository must finish
indexing before a run.

Each item includes `claim_type`; body-field items also include a `claim` with
method, path, field, attribute and expected value. `coverage.by_claim_type`
reports checked/aligned/outdated/unverified counts, and
`coverage.unverified_reasons` groups abstentions. A matching individual claim
does not establish endpoint or whole-document correctness. Existing stored
runs may omit these additive fields. See [request body alignment](REQUEST_BODY_ALIGNMENT.md)
for exact Markdown grammar, supported declarations and abstention rules.

Optional drift body `behavioral: true` requests a bounded Mistral assessment
of one Markdown passage, using the shared code retrieval path. Each assessment
remains unverified and includes the model verdict and available citations.
`behavioral_coverage.next_offset` continues the passage sequence using
`passage_offset`; send `expected_commit` to reject a changed index. Selected
documentation and code are sent to Mistral only when this option is requested.

## Documentation-first agent review

This workflow starts from indexed Markdown and reads source only to investigate
its claims. A bounded planner assigns each section to one of up to two LangGraph
reviewers. Markdown drafts and explicitly approved PR publication are separate actions. Code-unit summaries and the existing deterministic drift endpoint remain
separate features.

| Method and path | Description |
|---|---|
| `POST /repos/{repo_id}/documentation/reviews` | Capture a ready indexed commit and enqueue a persistent review. Optional JSON body `{"paths":["README.md"]}` selects existing Markdown files; absent/null paths reviews all indexed Markdown. Selection is persisted and included in deduplication. |
| `GET /repos/{repo_id}/documentation/reviews/latest` | Return the latest saved review, or `null` before one exists. |
| `GET /repos/{repo_id}/documentation/reviews/{run_id}` | Return progress, coverage, model, base commit, assessments, citations and safe failure/pause reasons. |
| `POST /repos/{repo_id}/documentation/reviews/{run_id}/resume` | Continue a paused review after cooldown. Completed sections and cumulative budgets are retained. |

Coverage reports indexed Markdown files and processed sections, not a proof
that every tracked file or factual claim was reviewed. Unreadable indexed
files are disclosed. Assessments are `supported`, `conflicting`, or
`insufficient`; all are model assessments. A valid citation is not a proof of
reasoning accuracy. `suggested_update` is text, not an applied Markdown patch.

Read/search tools operate against hash-checked immutable snapshots. Existing checkout Git objects supplement Go and Dart source at the indexed commit; source coverage is returned separately. Markdown sections retain heading context, and oversized lines are reported as incomplete. Semantic
search reuses existing code embeddings as discovery hints; the reviewer must
read source before citing it. Repository revision changes make a review stale.
The worker receives `MISTRAL_API_KEY` and optional `MISTRAL_API_KEY2`.
These slots represent separate accounts and maintain independent durable quota
reservations. Large 4 (`mistral-large-4`) is the default for documentation, Q&A,
code summaries, behavioral drift, and PR analysis. See [workflow scope](DOCUMENTATION_WORKFLOW.md).

## Markdown drafts and approved publication

| Method and path | Description |
|---|---|
| `POST /repos/{repo_id}/documentation/reviews/{run_id}/drafts` | Generate or reuse a saved Markdown draft from conflicting assessments in a current ready review. Completed reviews require no body; paused reviews require explicit `{"allow_partial":true}` and cover saved findings only. Concurrent creation returns 409. |
| `GET /repos/{repo_id}/documentation/reviews/{run_id}/drafts/latest` | Latest saved draft, or `null`. |
| `GET /repos/{repo_id}/documentation/drafts/{draft_id}` | Read the full diff, files, SHA256 hashes, base commit, status and publication result. |
| `POST /repos/{repo_id}/documentation/drafts/{draft_id}/publish` | Explicit approval body: `{"draft_hash":"<64 lowercase hex characters>"}`. Creates a dedicated branch and PR, or recovers a previous result. Never merges. |

Draft edits replace unique, cited claim spans in existing Markdown files only.
Unrelated bytes are preserved. Drafts are model suggestions requiring human
review; citation validation does not prove the replacement is factually correct.
Draft status is `draft`, `publishing`, or `published`. Publication checks the
approval hash, indexed revision, remote base head, original Git blob identity,
file mode, and existing publication branch. A changed repository requires a fresh
review. An interrupted publication can be retried with the same approval hash;
existing matching PRs are recovered without creating duplicates.

Publication currently supports GitHub HTTPS repositories with a configured
`SENTRA_GH_TOKEN` authorized to write repository contents and pull requests.
It does not create forks or modify the local clone. Missing permissions and
unsupported repository hosts fail explicitly. No approval is inferred from
review or draft generation.

## Scanning, findings, and score

| Method and path | Description |
|---|---|
| `POST /repos/{repo_id}/scan` | Queues requested scanner modules (`m2` for Semgrep, `m4` for OSV); defaults to both. Returns the persisted worker job identifier. |
| `GET /repos/{repo_id}/findings` | List findings. Optional `module` and `severity` filters. |
| `GET /repos/{repo_id}/score` | Return the latest saved score snapshot, or compute the current score from findings. |
| `POST /repos/{repo_id}/score` | Calculate and persist a score snapshot. |

## Pull requests

| Method and path | Description |
|---|---|
| `POST /repos/{repo_id}/pr-review` | Body: `pr_url`, optional `summarize` (default true). Paginates GitHub PR files up to 3,000, joins changed paths to open findings, and saves the review with its head revision and coverage. Head changes during the fetch return 409. |
| `POST /repos/{repo_id}/pr-review/{pr_number}/ask` | Body: `question` (1–2000 characters). Fetches PR context and returns a grounded Mistral answer when configured. Review the PR first. |
| `GET /repos/{repo_id}/pr-analyses` | List saved PR analyses. |

PR Q&A uses PR metadata and available diff patches; GitHub may omit patches for
large or binary files. A missing answer can indicate Mistral rate limiting.


### Large 4 review budgeting

Planner and draft calls force their single structured tool and use low reasoning;
reviewer investigation uses medium reasoning with a 4,096-token output budget.
Truncated completions report `output_budget_exhausted`. Full assistant reasoning
messages are retained. After successful calls, provider-reported prompt and
completion usage is bound to an immutable history prefix; future reservations
add a conservative byte allowance only for newly appended content. Changed
prefixes, models, or tool schemas invalidate that optimization. Missing usage
falls back to full byte reservations. This bounds throughput without treating
all repeated reasoning-history bytes as new tokens.


A successful assessment checkpoint is required after six navigation actions;
the model still chooses the claim and verdict, including insufficient evidence.
Invalid assessment feedback identifies schema, unread citation, or verbatim-quote
errors and supplies bounded cited text for correction. It never relaxes evidence
checks. Current investigation limits are 16 calls per invocation, 64 per section,
and 200 per run. Byte reservations remain capped at 4,000,000 per section and
16,000,000 per run; these cumulative units are not billed token measurements.
Local quota rejections refund unsent call reservations; real provider attempts
remain charged to the call ledger. Saved draft provenance tracks actionable
assessment IDs, so new findings invalidate draft reuse rather than silently
returning an older proposal.
