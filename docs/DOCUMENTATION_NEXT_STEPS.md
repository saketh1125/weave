# Documentation follow-up

> Evidence files referenced in historical checkpoints are Sentra artifacts and are not included in independent Weave.

Updated 2026-11-12. Historical checkpoints below are superseded by the current
parallel maintenance implementation in [DOCUMENTATION_WORKFLOW.md](DOCUMENTATION_WORKFLOW.md).
The first approved real PR and three selected-file spot checks are verified.
Current remaining work: full-scope repository review quality and efficiency,
long-context/load/outage behavior, and the existing security/cloud release gates.

## Current Weave acceptance gates (2026-11-14)

The detailed entries below are historical Sentra checkpoints, not current Weave model or deployment acceptance. Independent Weave retains the documentation-first reviewer, parallel API assignments, Markdown drafts and exact-approved publication.

1. Resolve model availability: both configured Mistral Large 4 accounts currently return HTTP 403 `tier_not_allowed`. Large 4 stays selected. The independent local ACP bridge is reachable but its synthetic test returns `authentication_required`; no authenticated profile was copied from Sentra.
2. Once a configured provider works, complete a bounded labeled review, compare actual claims/citations against the source, then run a representative whole-repository review. The 165-section rehearsal mocks model decisions and only verifies orchestration. Measure factuality, duplicate claims, completion, token/call use and recovery separately.
3. Validate one new Weave draft and approved PR end to end after live review passes. Preserve Markdown-only edits, exact approval, base-commit guards, updated-finding checks and idempotent publication. Earlier Sentra PR checks are historical.
4. Before deployment, resolve the open scanner advisory disposition, audit the container/OS, exercise Weave staging/backup restoration, and select cloud/account, private access, budget and region. API call/token ceilings do not measure native-agent token usage.

See [Implementation status](IMPLEMENTATION_STATUS.md), [Workload validation](WORKLOAD_VALIDATION.md), and [Cloud release](CLOUD_RELEASE.md).

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
