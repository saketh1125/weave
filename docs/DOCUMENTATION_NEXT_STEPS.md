# Documentation follow-up

> Evidence files referenced in historical checkpoints are Sentra artifacts and are not included in independent Weave.

Updated 2026-11-12. Historical checkpoints below are superseded by the current
parallel maintenance implementation in [DOCUMENTATION_WORKFLOW.md](DOCUMENTATION_WORKFLOW.md).
The first approved real PR and three selected-file spot checks are verified.
Current remaining work: full-scope repository review quality and efficiency,
long-context/load/outage behavior, and the existing security/cloud release gates.

## Finding-to-PR flow verified

Weave recorded a published draft with two Markdown files from the completed Sentra review. [Sentra PR #3](https://github.com/saketh1125/Sentra/pull/3) is open and contains both documentation edits. Review the repeated phrase in the operations guide before merging. The earlier checkpoints below describe the state before publication.

## Full repository review completed — 2026-11-15

After the user selected a working Antigravity account, native verification passed. Fresh Sentra review `32bd1dfb-00a8-4d9f-a366-1d0af2f91855` completed all 14 indexed Markdown files using four file reviewers plus one static-triage reviewer. It saved 89 AI assessments (81 supported, six insufficient, two conflicting) and completed the 29-item triage queue. Observed runtime was 846.275 seconds and 11 native prompt turns; native provider calls/tokens remain unknown. The reviewer budget is now 900 seconds, with the existing 18-turn cumulative cap.

Live investigation fixed the MCP-only planner/session contract, incomplete triage schema, and an autoflush checkpoint bug that hid processed triage counts. Finalization also fixed reviewer-wide errors blocking an unrelated claimless file and telemetry changes being mistaken for semantic progress. The final backend suite passed 621 tests; deployed completed output passes the frontend parser and reports 14/14 plus 29/29. A final native fixture passed after finalization; the full real review was not repeated after those edge fixes.

All saved claim anchors and citation ranges were checked. This does not prove exhaustive or semantically correct review: a three-assessment semantic sample still found a conditional overclaim. The two conflict candidates concern a missing `/api` base-URL prefix in the frontend README and a documented retry UI control absent from inspected frontend code. Both warrant human-reviewed Markdown drafts; no draft or PR was generated during this test.

Evidence: `docs/evidence/documentation_full_review_2026-11-15.json`. Next: inspect the conflict candidates, generate narrowly scoped Markdown drafts, then exercise exact approval and PR publication. Larger-repository quality/performance and cloud readiness remain open. Historical limit/timeout investigations below are superseded by this successful run, not deleted.

## Latest completion investigation — 2026-11-15

A real review allocated four reviewers over 14 Markdown files, but paused with zero reviewer tool steps/claims after 18 cumulative prompt calls. A new invented fixture returned three end-turn responses, zero MCP calls, and a quota-related message hint. The user confirmed the same Gemini model currently reports a limit in Antigravity's own interface. This establishes a current availability blocker, not the cause of every historical failure.

Reviewers now continue within shared budgets, stop repeated no-progress/refusal responses, and expose safe native event/permission/stop metadata through existing polling. Tool timestamps no longer refresh when unchanged. Budget, timeout, and model-limit conditions have distinct reasons. Explicit model-limit text is a reported condition, not proof of an RPC 429. See `docs/evidence/documentation_completion_2026-11-15.json`.

Next: once Gemini allowance returns, verify the changed runtime in Settings and start a fresh whole-repository review. Do not silently resume incompatible sessions, reset spent budgets, or claim completion from mocked checks. Live retries stopped after the user confirmed the native limit.

## Current Weave acceptance gates (2026-11-15)

1. Model access: the local demo uses `codestral-2508` on the two Mistral accounts; Large 4 access was denied. Gemini 3.8 Flash passed synthetic native documentation, plain generation, and function-result checks. Settings now supports all existing workflow roles through the isolated native connection. Exact native token usage remains unavailable.
2. Reliability: a planner-progress race that could emit a plan without its reviewer entries is fixed and tested. A separate structured generation failure was not reproduced in a subsequent small probe; accepting valid mixed assistant text/tool calls is now tested, and result-shape diagnostics contain no source values. These checks do not establish whole-repository completion or factual accuracy.
3. Review quality: complete a fresh representative whole-repository review after runtime reverification. Compare claims/citations, static reuse, duplication, completion, calls, elapsed time, and bounded recovery. Earlier labeled two-file synthetic coverage remains documented in `docs/evidence/hybrid_synthetic_2026-11-14.json`.
4. Draft/publication: verify a new Weave draft and approved PR after that review passes, preserving Markdown-only changes, exact approval, base-commit guards, updated-finding checks, and idempotency.
5. Deployment: resolve scanner advisories, audit container/OS dependencies, exercise staging/restore, and choose cloud/account, private access, budget, and region. Antigravity remains a local testing connection; the bridge is disabled in production Compose.

See [Implementation status](IMPLEMENTATION_STATUS.md), [Model connections](MODEL_CONNECTIONS.md), and [Cloud release](CLOUD_RELEASE.md).

## Completed incident fix

The running migration-002 API returned unvalidated cached model JSON. Of 29
stored documents, 28 had object-valued `key_behaviors` entries. React attempted
to render those objects as children and could unmount the application view.
The frontend now validates incoming and retained workspace results before
rendering, shows unsupported-response errors visibly, and contains unexpected
Docs-route rendering failures behind a resettable boundary. Q&A code was not
changed.

With user approval, the existing database was backed up, the backup was
restored and migrated in isolation, then the running API was upgraded to the
tested backend and a worker was started. Migration 005 is active. Repositories,
files, chunks, embeddings and findings retained their counts. Migration 003
consolidated duplicate document cache rows, retaining the latest per chunk.
One user-approved PramanaGST generation request using Mistral succeeded and
returned valid string behaviors and coverage metadata.

## Batch rate-limit follow-up

The next user test produced two valid summaries before the model provider
returned a rate limit. Documentation now uses a request-scoped paced scheduler:
at least 1.1 seconds between attempt starts, at most 12 physical attempts, and
one retry per transient failure when the remaining scheduling budget permits.
Structured provider metadata separates temporary throttling from explicit
daily/billing quotas. Completed units remain committed and cache versions stay
unchanged. The UI shows saved progress, respects a supplied retry interval and
offers manual continuation. Shared Q&A retry defaults are preserved.

All 191 backend tests and four frontend contract tests passed; build passed,
with eight existing frontend lint warnings. A synthetic browser fixture
verified two visible summaries, a disabled Continue button during cooldown,
route navigation and successful manual continuation afterward. No additional
repository source was sent to Mistral. The tested API/worker image was applied
locally without a migration; authenticated reads and unchanged aggregate data
counts passed. Evidence: `docs/evidence/documentation_rate_handling.json`.

Pacing is per documentation request, so concurrent calls and token limits can
still throttle generation. The 30-second scheduling window is not a strict
wall-clock deadline for an in-flight HTTP call. This fix does not measure
representative summary factuality or establish the user's provider quota.

## Request-body alignment slice

Added explicit request body field presence, requiredness and literal-default
checks for conservatively resolved FastAPI/Pydantic declarations. Models can
be local or absolutely imported through indexed modules and mounted routers.
The checker leaves dynamic/ambiguous declarations unverified, preserves
document context and original line citations, and checks individual claims.
The API/UI now identify claim types and group unverified reasons separately
from checked claims. Q&A, findings and reindexing paths were not changed.

A reviewed synthetic corpus contains 20 cases and 21 expected claims. Its
version history records grammar, policy and citation corrections during
development. Both splits were available during implementation; they do not
constitute an independent accuracy estimate. Expected statuses and citations
match, with no unexpected or false-aligned results in this small corpus.
Representative real-repository assessment remains open.

See `REQUEST_BODY_ALIGNMENT.md` for supported syntax and limitations and
`evidence/request_alignment_corpus.json` for the provider-free evaluation.

## Approved direction: documentation-first agents

The user clarified the intended product: existing Markdown defines the scope.
Agents read documentation first and navigate code to verify its factual claims;
they do not generate documentation for undocumented code by default. Maintain
the working ingestion, Q&A and incoming PR-review paths. The approved sequence
is one LangGraph reviewer, bounded planner/parallel review, evidence-backed
Markdown drafts, then explicit user-approved PR publication. The user merges.

The first reviewer slice is implemented separately from code-unit summaries
and narrow deterministic checks. It persists immutable indexed snapshots,
section checkpoints, assessments and coverage; it has no file-writing or PR
publication tools. Repeated starts for the same commit/model/workflow version
reuse existing work. Indexed inventory is disclosed rather than presented as
proof of every tracked Markdown file. See `DOCUMENTATION_WORKFLOW.md`.

All 232 backend and eight frontend contract tests passed; build, actionlint
and dependency compatibility checks passed. A browser fixture verified the
review findings and restoration after reload. An isolated 005→006 restore and
migration rehearsal preserved existing data. A live synthetic-only Small 4
smoke was rate-limited on its first call; no real-model investigation result
or accuracy claim has been established. No private repository content was sent
to Mistral by this implementation's tests.

The later Large 4 follow-up completed one bounded live synthetic review with
the expected cited mismatch and correction. This account now selects Large 4;
Small 4 advertised a zero request allowance. Source reads extending past EOF
were corrected, and all 233 backend tests passed. See
`evidence/documentation_large4_validation.json`.

Next: evaluate ordinary documentation prose on labeled repository snapshots
before adding coordinator-controlled parallel review. Drafts and PR publication
remain unimplemented. Further expansion of regex claim families is supporting
work, not the primary route to broader documentation behavior.

## Independent evaluation and supporting checks

The provider-disabled PramanaGST check returned zero checked claims and 29
unverified references: nine lacked supported status claims, six were qualified,
seven were ambiguous, and seven were historical. This is uncertainty and
coverage information, not a finding that all documentation is wrong. The UI
now explains that distinction.

1. Freeze a labeled independent development/evaluation corpus before expanding support.
   Include supported, outdated, ambiguous, conditional, historical and missing
   evidence cases; label both documentation and code source ranges.
2. Evaluate the agent's document-first evidence gathering and reasoning. Keep
   dynamic/ambiguous cases unresolved when evidence is missing. Extend narrow
   deterministic checks only where they support observed review needs.
3. Preserve the checked claim type and unsupported-reason breakdown. A
   verified field default must not be presented as verification of an entire
   endpoint or workflow. Endpoint references alone must not be assigned a
   status code the documentation never claimed.
4. Keep broader behavioral review opt-in, with bounded passages, both source
   citations, commit guards and explicit model suggestions. Measure factuality
   and false-aligned results separately from extracted-reference counts.

## Remaining release work

- Evaluate documentation and alignment on independent repositories. The live
  one-unit success verifies the pipeline, not representative factual accuracy.
- Extend or explicitly retain partial generation above the 22,600-character
  per-unit cap; test provider limits and recovery on realistic batches.
- Resolve recorded scanner dependency advisories or review a concrete
  mitigation; finish the OS/base-image audit and full-workflow load/outage work.
- Select the cloud account, budget, access method and backup objectives before
  provisioning private staging. Preserve Q&A, findings and reindexing behavior
  while completing these slices.
