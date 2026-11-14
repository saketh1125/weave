# Feature acceptance and baseline

> **Inherited Sentra record.** This document preserves earlier technical context,
> not a fresh Weave acceptance result. Original experiment artifacts and personal
> session records are excluded. See [Migration](MIGRATION.md) for current verification.
> References below to evidence files describe historical Sentra artifacts; those files are not included in independent Weave.

**Status:** Phase 0 working draft, 2026-11-11. Thresholds below are provisional
and are not results. This document records code and test inspection plus the
current figures already stated in `EXECUTION_PLAN.md`; it does not claim a new
runtime, provider, scanner, browser, or cloud test.

The matrix describes the starting baseline at commit 4e2ffdd. Work in the
current batch adds contextual claim extraction and generation progress fixes.
Measured fixture results are recorded in `docs/evidence/alignment_baseline.json`.

## Evidence rules

- **Implemented** means code and a user-facing path exist. It does not mean
  production-ready or currently passing.
- **Verified** is reserved for a named test or a run with reproducible evidence.
- **Partial** means the supported path is narrower than the intended feature.
- Historical runtime statements in `docs/IMPLEMENTATION_STATUS.md` describe
  the dated environment there. They are not current verification.
- `EXECUTION_PLAN.md` reports the current Sentra alignment run as 27 unverified
  references and zero verified claims. This is recorded as a prior plan claim;
  no run artifact or live runtime was inspected for this document.

## Feature matrix

This table preserves the starting baseline. For the current working-tree
implementation and verification, see the dated checkpoint below.

| Feature | Intended acceptance | Current implementation and surface | Evidence available | Gap / acceptance still needed |
|---|---|---|---|---|
| Repository ingestion | Ingest supported Python and JavaScript/TypeScript source plus Markdown; update changed files and remove deleted files; record analyzed commit; handle private repositories by configured credential. | `POST /api/repos`, `/ingest`, `/embed`; `app/ingest.py` parses/chunks tracked source and Markdown, updates indexed paths and records `commit_sha`. Repos screen selects repositories and shows status. | Tests: `tests/test_markdown_ingest.py` covers offsets, Unicode, fences, tracked Markdown and exclusions. `docs/IMPLEMENTATION_STATUS.md` reports a historical end-to-end ingestion; `EXECUTION_PLAN.md` says current API local migration runs, but not a fresh ingestion check. | **Partial.** No fixture suite for mixed-language edits, rename/delete, unsafe remotes, symlinks, size limits, or concurrent ingestion. Complete security boundaries and persistent/recoverable jobs. Establish bounded timing and memory baselines on a pinned fixture. |
| Retrieval and cited Q&A | Answer from selected repository evidence, cite exact sources, distinguish docs from implementation, state unknowns, and avoid unsupported claims. | `POST /api/repos/{id}/ask`; `Ask.tsx`; separately retrieves Markdown/code, reranks by intent, returns candidate excerpts and line citations, optionally asks Mistral to compose answer. | `tests/test_ask_retrieval.py` tests ranking, docs-vs-code preference and overlap collapse. `EXECUTION_PLAN.md` reports Q&A implemented and pushed. Historical live Mistral exercise is in dated implementation status only. | **Partial.** No current held-out answer/citation quality measurement; provider unavailable/rate-limit path can return `answer: null`. Need fixed questions, expected facts/source spans, citation correctness, abstention and contradictory-evidence measures. |
| Generated documentation | Progress deterministically across eligible code, produce grounded structured summaries, cache against all generation inputs, and resume partial work. | `POST /api/repos/{id}/docs`; Docs screen. Current endpoint selects up to requested `limit` (UI asks for 10), truncates each source to 3,000 characters, caches by source hash, and calls Mistral. | `docs/evidence/S5_*` records historical spike outcomes, including blocked cases; they are not a present app evaluation. Implementation can be inspected in `app/routers/docs.py`. | **Partial.** Query has no stable ordering/progress cursor; cache omits model/prompt/schema versions; output JSON parse failures become free text; writes commit after batch; no durable resume/concurrency guard. Measure summary factuality and source coverage after these are addressed. |
| Documentation/code alignment | Check supported claims against code, persist run and analyzed commit, cite both sides, classify unsupported or uncertain claims without false confidence. | `POST/GET /api/repos/{id}/drift`; Docs screen. Checker currently handles same-line HTTP success status claims in Markdown against Python FastAPI routes, including mounted routers and supported literal status forms. Results persist in `DriftRun`; reindexing does not replace the run. | `tests/test_doc_alignment.py` covers same→changed→corrected status, missing status, unknown route, duplicate/ambiguous route, dynamic response, qualified claims and citation locations. `tests/test_drift_snapshot.py` covers reconstruction and GitHub links. Plan reports current run: 27 unverified, 0 verified. | **Partial, narrow checker.** No general behavior, input/default/config/response-field, TypeScript framework, historical-document role, or behavioral assessment. Current reported 0 verified / 27 unverified means no measurable claim coverage on that run. Broader claims require extraction/retrieval and negative/conditional/moved-file fixtures. |
| Security and dependency scans | Preserve actionable findings and status across source/dependency changes; report severity/version/fix details accurately; remain useful during provider failure. | `POST /api/repos/{id}/scan`; Findings screen; Semgrep module `m2`, OSV module `m4`, finding list with status and details. | `docs/IMPLEMENTATION_STATUS.md` has dated runtime claims; historical `docs/evidence/S2*` and `S3_osv.json` describe spikes. No dedicated current scanner-normalization test file appears in `tests/`. | **Partial / current app path unverified.** Need current scanner output fixtures for normalization, dedup, fixed/affected versions, resolved findings and rescans; disclose language/ruleset/registry limits. AI review/triage is not established by the inspected scan endpoint. |
| PR review | Analyze all changed files or clearly disclose omissions, link existing findings, tie analysis and follow-up Q&A to the exact PR head, and preserve usable deterministic output without a model. | `POST /api/repos/{id}/pr-review`, per-PR ask, analyses list; PR Risk screen displays changed files, summary, matched existing findings, and Q&A. | Endpoint code and screen inspected. Historical implementation status reports live PR review/Q&A, but no current PR test file is present in `tests/`. | **Partial.** Implementation status and plan identify first-100-file limit; available/binary/large-file diff omissions and head-commit freshness need explicit accounting. Add mocked GitHub fixtures and tests for pagination, unavailable patches, stale head, findings links, and provider outage. |
| Readiness score | Deterministic score from current eligible findings, without duplicate penalties; explain every deduction and identify stale/missing inputs. | `GET/POST /api/repos/{id}/score`; Score screen shows severity weights, caps and module breakdown. Score snapshot stores config snapshot. | `app/score.py` inspected; `docs/SCORING.md` documents rule design. No current score test file appears in `tests/`. Historical status says score snapshots were exercised. | **Partial.** Does not record analysis provenance/freshness; finding states and deduplication policy must be verified end-to-end. Add unit cases for each severity/cap/state, fixed-input determinism, stale/missing module behavior and duplicate findings. |
| Authentication and API security | Protect all sensitive routes, reject invalid/expired credentials, constrain browser origins, and meet private-repository and production security needs. | Single-user access-key login issues signed expiring bearer session; frontend login/logout; sensitive `/api/*` protected, health public. | `tests/test_auth_security.py` contains explicit route coverage, expiry, malformed token, secret missing, CORS and health/readiness cases. `docs/IMPLEMENTATION_STATUS.md` records a historical security-suite pass. | **Partial.** Existing checks are source-level/current test artifacts but were not run in this task. No multi-tenant isolation, login throttling, token revocation/rotation workflow, demonstrated session invalidation, or complete production threat tests. Public onboarding is out of scope until tenant isolation milestone. |
| Jobs and recovery | Persist jobs with bounded concurrency, retries, progress, restart recovery and per-repository coordination; never publish partial indexes as complete. | Ingestion uses `asyncio.create_task`; scans use FastAPI `BackgroundTasks`. Status fields are returned to UI. | Route/code inspection only; no persistent job model or worker found in inspected app paths. | **Missing for release acceptance.** Add durable queue/job records and restart/concurrency/retry tests before relying on long-running operations. |
| Cloud release | Deploy the tested release to staging and production with private data services, HTTPS, limits, monitoring, backup restore and rollback evidence. | `docker-compose.prod.yml`, Dockerfile and Nginx/Caddy configuration exist as preparation artifacts. | `EXECUTION_PLAN.md` explicitly says production Compose/TLS are preparation, not a verified release. Dated implementation status says Docker was unavailable for its recorded verification. | **Not verified / not deployed.** Provider, region, budget, domain, retention and availability choices are prerequisites. No cloud provisioning or service calls were made. |

## Baseline fixture and split

`tests/fixtures/alignment_baseline.json` is a small deterministic checker fixture,
not a representative repository corpus. It has nine one-claim cases: four
development and five separate evaluation cases. The development set includes
the aligned → outdated after a code change → aligned after documentation
correction progression; the evaluation set includes supported controls and
ambiguous/dynamic cases that must remain unverified. The checker currently
supports only the narrow Python FastAPI/status-claim semantics represented.

Do not tune implementation decisions against evaluation cases. Add new
evaluation cases only before running/tuning, or version the fixture and preserve
the old set. Record checker revision and fixture hash with metric results. Never
count `unverified` as aligned; report coverage separately from correctness on
claims the checker attempts to resolve.

## Provisional acceptance thresholds (not measured)

These are bounded starting gates for discussion and baseline collection. A
threshold is not a pass until the runner, fixture version, revision, and result
are recorded.

| Evaluation | Bounded sample | Provisional gate |
|---|---:|---|
| Alignment fixture exact statuses | 5 separate evaluation claims in the JSON fixture | 5/5 exact expected statuses; 0 false `aligned` results among negative/ambiguous cases. Also report aligned/outdated coverage and unverified count. This very small sample catches regressions only. |
| Alignment development regression | 4 development claims | 4/4 exact statuses, including the same→changed→corrected transition. |
| Retrieval and cited answers | 12 authored questions across Python, TypeScript, Markdown and mixed repos; 8 development / 4 held out | At least 11/12 expected evidence facts retrieved in top 5, at least 11/12 answers supported by cited source spans, zero unsupported critical claims, and explicit abstention on all 4 no-evidence/ambiguous prompts. Report counts, not percentages alone. |
| Documentation generation | 12 code units; 8 development / 4 held out | At least 11/12 summaries contain no contradicted code facts; at least 10/12 cover all labeled key behaviors. Report parse/schema failures and source truncation separately. |
| Scanner normalization | 12 labeled findings/controls per scanner; 8 development / 4 held out | Exact stable identity/status/severity/fixed-version rendering on all applicable held-out fixture records; no missing raw scanner finding in preserved input. Scanner detection recall is a separate measure and requires a larger corpus before release claims. |
| PR analysis | 8 mocked PRs; 4 development / 4 held out | 8/8 changed-file counts or explicit omission reasons match fixture metadata; all returned risks link to an existing finding at the expected path; head mismatch is visibly stale. |
| Score | 12 fixed finding sets, including caps, resolved states and duplicate identities | Exact deterministic score and breakdown for all 12; no duplicate penalty; every deduction traceable to a finding and config snapshot. |
| Ingestion and recovery | 3 pinned fixture repos, 2 repeated ingests each; one restart/interruption case | File/chunk inventory exactly matches expected state after edits, rename and deletion; no mixed-commit index; restart either resumes or clearly fails without marking ready. Record wall time and peak memory; set resource ceilings only after first reproducible baseline. |

All thresholds are provisional until reviewed before tuning. Split by fixture
identity, not by random chunk/claim from the same source repository, to reduce
leakage. Synthetic fixtures establish contract behavior; release confidence also
requires a separate, pinned repository sample and a current full workflow run.

## Historical checkpoint — 2026-11-11

The latest local candidate passed **161 Linux tests**, including PostgreSQL
integration and new workload fixture checks. The API and frontend images
build. The committed clean export at `6ce1abe` passed its 151-test suite and
both image builds; hosted CI subsequently passed all 154 tests and the frontend
for `ff60637`. The user's existing stack has not been upgraded.

| Area | Added behavior and verified evidence | Remaining release gate |
|---|---|---|
| Ingestion/retrieval | Remote/path/symlink and size guards, durable queue, model/render compatibility, bounded embeddings and shared retrieval. Synthetic supported retrieval 9/9; Sentra development retrieval 10/10 expected files in top five. | Mixed edit/rename/delete and realistic repository restart/load campaign; held-out answer factuality, contradictory evidence and abstention. Retrieval success alone does not establish answer quality. |
| Alignment | Heading/example/history/condition context, FastAPI and narrow same-file Express checks, literal environment defaults, optional behavioral review with strict citation validation and commit guard. Nine small deterministic cases pass. One synthetic live provider review passed. Browser shows the seeded mismatch and both citations. | Broader language/framework and behavioral claim coverage; representative labeled assessment quality. Model suggestions intentionally remain unverified. |
| Generation | Versioned cache, bounded overlapping sections and combined summary, exact coverage validation, stable unfinished-unit selection, per-item progress and concurrency lock. Mock-provider PostgreSQL failure/retry/cache checks pass. | Full coverage caps at 22,600 distinct source characters per unit. Representative provider summary factuality remains open; the two-case live smoke stopped at a provider rate/quota limit with no completed summaries. |
| Scans/findings | Failure rejection, status-preserving reconciliation, resolve/reopen, ecosystem-aware identities, bounded Semgrep concurrency, explicit OSV call-analysis disable. Real tiny Semgrep/OSV protocol smokes passed. | Realistic scanner detection/normalization corpus, outage/load measurements, retained release dependency/image vulnerability scan. |
| PR/score | Shared paginated PR context, head guards and omission disclosure; deterministic score freshness and current-revision scan provenance. Tests cover these contracts; browser identifies incomplete analysis. | Deployed live GitHub workflow and representative PR cases; scoring remains a finding penalty, not proof of release safety. |
| Security/jobs | PostgreSQL/Linux tests and hosted CI pass. A three-worker disposable rehearsal verified lock contention, SIGKILL/reclaim and result integrity with a test-only operation. | Natural lease/heartbeat and database-outage campaigns, real-operation load, independent security review and multi-user isolation if scope expands. |
| Release/recovery | Local staging workflows, 002→005 upgrade, matching-snapshot restore, and prior API readiness/authenticated repository reads verified. | Cloud provider/access/cost decisions, TLS, encrypted scheduled backups, full frontend/deployment rollback, alerts and observation. No cloud deployment. |

Detailed run identity and limitations: `docs/evidence/release_verification.json`.
The subsequent documentation/scanner batch is recorded separately in
`docs/evidence/documentation_completion.json` and supersedes the earlier
generation prefix limit and test count.
CI and the later operational rehearsals are recorded in
`docs/evidence/hosted_ci.json` and `docs/evidence/operational_validation.json`.

The package audit subsequently found five advisories in two scanner-only
dependencies. Both tested and latest published Semgrep releases constrain them
to affected versions. Production promotion is blocked pending a compatible fix
or reviewed mitigation. See `docs/evidence/dependency_audit.json`; no finding in
the application/frontend inputs is not a full image-security clearance.

## Current checkpoint — 2026-11-12

The local API and worker now run migration 005 and the tested request-body
alignment image. The latest candidate passed 216 backend tests, including
PostgreSQL persistence/coverage/citation checks. Four frontend contract tests,
the frontend build and a synthetic coverage browser check passed; lint has
eight existing warnings and no errors. Q&A, findings and reindexing code paths
were not changed in this slice.

Documentation generation has validated cache rendering, provider pacing and
manual continuation. Request-body field presence/required/default checks now
extend the HTTP-status and environment-default checks. All 21 expected claims
in the reviewed 20-case synthetic corpus match statuses and citations. This is
a small development regression corpus, not an independent quality estimate.

The current PramanaGST documentation contains no supported new body-field
claims and remains 0 checked / 29 unverified. Broader prose/behavior assessment
and independent real-repository evaluation remain required. Scanner dependency
advisories, OS/image audit, real-operation load/outage testing and cloud access,
cost, backup and staging decisions continue to block production promotion.

Evidence: `docs/evidence/request_alignment_corpus.json` and
`docs/evidence/request_alignment_validation.json`.


## Real documentation workflow checkpoint — 2026-11-12

Verified a real documentation-first conflicting claim, a Mistral-generated
Markdown draft, exact-diff approval and native Sentra GitHub publication:
[PramanaGST PR #1](https://github.com/saketh1125/PramanaGST/pull/1), one Markdown
line changed, CI passed, unmerged. Retry returned the same PR and the live UI
restored the published partial draft after reload. Argus abstained on one claim;
Civic-Link matched one Geography declaration claim. All 64 tracked Markdown
files in the three repositories are indexed and readable.

277 backend tests, 13 frontend contracts and the frontend build passed. These
are partial selected-file spot checks, not whole-repository acceptance or an
independent accuracy estimate. Detailed source, runtime and failure provenance:
real repository evidence (historical artifact excluded from Weave).


## Model connection checkpoint — 2026-11-13

Mistral defaults and custom OpenAI-compatible connections now share workflow
selection, durable quota groups and session/cache identity guards. The built
candidate passed 331 backend tests, 13 frontend contracts and frontend build.
These are adapter/fixture and disposable-database checks, including one real
local HTTP fixture; they do not certify an external provider's capabilities,
account terms or model quality. The running stack remains unchanged.

Antigravity is assessed, not enabled. See [model configuration](MODEL_CONNECTIONS.md),
[CLI feasibility](CLI_AGENT_CONNECTIONS.md), and
validation evidence (historical artifact excluded from Weave).
