# Documentation-first maintenance workflow

Current connection behavior (2026-11-13): planner, reviewer lanes and Markdown
drafting can use separate server-managed model connections. Mistral remains the
default. [Model connections](MODEL_CONNECTIONS.md) describes quotas, capability
limits and incompatible-session rejection; [CLI connections](CLI_AGENT_CONNECTIONS.md)
describes the separate, currently disabled agent extension. The dated delivery
notes below preserve implementation history.

The starting point is existing repository Markdown. The reviewer reads a
documentation section, identifies its factual claims, and navigates code to
verify them. Undocumented code is not a reason to generate new documentation.
Existing documents define the scope; code supplies evidence for corrections.

## Delivery order

1. One LangGraph reviewer with read/search tools, persisted progress and
   evidence-backed assessments of indexed Markdown.
2. A coordinator choosing one to four total agents and assigning exclusive
   documentation ownership. Source code may be shared evidence.
3. Markdown-only update drafts grounded in selected drift findings, with an
   exact preview against the reviewed base commit. Preserve unaffected text.
4. Explicit Open PR action creating a documentation branch/commit/PR after
   revision checks. The user merges it. Publication retries reconcile existing
   branches/PRs instead of duplicating them.

The current implementation work starts with step 1. Later steps must not be
represented by working-looking frontend controls until implemented and tested.
Code-unit summaries are a separate existing feature, not document updates.

## First reviewer boundaries

- Review indexed Markdown before investigating source. Inventory scope is
  explicit: earlier ingestion may omit unreadable files, so indexed inventory
  alone does not establish that every tracked Markdown file is represented.
- Capture immutable, hash-checked indexed source at one repository commit;
  report missing or incomplete source instead of silently filling gaps.
- Give each documentation section one owner and preserve its original lines.
  A document with no factual claims can finish with zero assessments.
- Allow file listing, literal/semantic source search and bounded source reads. No
  arbitrary commands, repository writes or code execution are exposed.
- Record supported, conflicting or insufficient assessments with a verbatim
  documentation excerpt and code citations that were actually read. Markdown
  cannot serve as code evidence for its own claims. Citation validity does not
  prove the model's reasoning is correct.
- Checkpoint serializable graph state in Sentra's database after steps, with
  the worker's current lease fencing writes. This first slice uses application
  checkpoint callbacks, not LangGraph's native PostgreSQL checkpointer.
- Bound model calls, context and tool results. Retain completed assessments
  through provider throttling; continuation is explicit and cannot reset the
  cumulative budget. Report partial or failed coverage honestly.
- Keep secrets in the server/worker environment. Treat repository text as
  untrusted data. Do not change Q&A, ingestion or incoming PR-review behavior.

## Model configuration

`SENTRA_DOCUMENTATION_MODEL` selects `mistral-large-4` (default), with the legacy option
`mistral-small-2603`. This setting selects the documentation reviewer; other Mistral workflows now default to Large 4. The same
`MISTRAL_API_KEY` is supplied to the worker; no silent model fallback occurs.
The user confirmed that both models are enabled on their Experiment account.
Monthly allowance does not eliminate per-request/throughput limits. Actual
entitlement and representative assessment quality still require verification.

## Acceptance before expansion

Verify a complete fake-provider tool loop from Markdown to code to a recorded
mismatch; absence of sufficient evidence; rejection of invalid/unread citations
and circular Markdown evidence; bounded loops; rate-limit resume; worker crash
recovery; stale index rejection; persistence after browser reload; and unchanged
existing feature tests. Live tests should use synthetic content first. Tests
with mocks establish orchestration contracts, not real-model accuracy.

## First slice checkpoint — 2026-11-12

The real LangGraph loop, API/worker persistence, non-overlapping section tasks,
source reads and semantic discovery are implemented. State is fenced by lease
token/attempt/expiry and the indexed commit. The frontend supports start,
progress, cited assessments and manual continuation. Matching existing runs
are reused so repeated starts do not repeat completed model work.

Limits: 16 model calls per invocation, 32 per section, 200 per run; 200,000
context bytes; 1,000,000 reserved units per section and 4,000,000 per run. Budget
reservations use UTF-8 input bytes plus output-token allowance as a conservative
bound, not measured billed tokens. Exhaustion is explicit. This is not yet a
large-repository performance or assessment-quality acceptance result.

232 backend tests and eight frontend contract tests passed. The backend checks
include a simulated provider through the real graph, API creation/resume,
persisted citations, repeated-start reuse, stale commits and replaced leases.
Browser evidence is synthetic. A single live synthetic-only Small 4 call
returned 429 and paused before any assessment; no automatic retry was made.
The local API/worker use schema 006 after a verified backup/restore rehearsal.
No draft/edit/publish capability is implemented in this slice.

## Large 4 follow-up — 2026-11-12

The user tested both models: Small 4 returned a zero requests/minute allowance,
while Large 4 returned HTTP 200 with observed limits of 50 requests/minute and
20,000 tokens/minute. This local account now selects `mistral-large-4` through
the ignored `.env`; the generic default and Q&A configuration are unchanged.

Live investigation exposed an overly strict source reader: requesting the first
20 lines of a one-line file returned an error. Valid bounded reads now clip at
EOF and return the true source range. Four snapshot tests and all 233 backend
tests passed. A complete invented Markdown/source fixture then produced the
expected cited mismatch and correction in seven model calls. Earlier exploratory
probes made thirteen calls without a completed assessment. No private repository
content was sent to the provider. This validates one simple live tool loop,
not representative accuracy. Shared throughput scheduling remains a prerequisite
for parallel reviewers. Evidence: `evidence/documentation_large4_validation.json`.

## Parallel review and publication checkpoint — 2026-11-12

The planner inventories existing indexed Markdown sections and assigns every
section exactly once to one or two reviewer lanes. Each lane runs the existing
LangGraph investigation loop. The plan, section progress and cumulative budgets
survive continuation. Planner calls count toward the run budget. No independent
long-running planner agent is added beyond the two concurrent reviewers.

`MISTRAL_API_KEY` and `MISTRAL_API_KEY2` represent the user's two separate
accounts. Each account has PostgreSQL-backed reservations shared across worker
processes: 50 requests and 20,000 tokens per 60-second window, with 1.25-second
request spacing and independent provider cooldowns. Input UTF-8 bytes plus output
allowance provide conservative pre-request reservations; valid provider usage
reconciles successful calls. Failed calls retain reservations. These limits are
account observations, not an assertion about all Mistral free-tier accounts.
Q&A and older workflows rely on provider throttling and do not use this new
documentation quota coordinator.

New documentation sections target 6,000 bytes / 80 lines. Older discovery
results can be compacted while preserving tool IDs, assigned documentation,
source reads and assessments. Oversized context still fails explicitly; this is
not unlimited-context review or a large-repository performance acceptance.
Coverage remains indexed files only. Model judgments need human review.

Update documentation generates a persisted diff from cited conflicting claims.
The model writes replacement prose, while deterministic validation restricts
changes to unique original claim spans in existing `.md` files. Code and unrelated
Markdown bytes remain unchanged. Repeated generation restores the existing draft.
The interface displays the diff and requires approval tied to that draft's hash.

Open pull request is a separate, explicit action. GitHub publication validates
base head and original blobs, creates a dedicated branch with one Markdown-only
commit, then opens a PR. It never merges or writes to the default branch.
Publication identity is saved before branch creation; retries recover matching
branches and PRs after ambiguous failures. A moved base head blocks new work,
while an already-created matching PR can be recovered. GitHub HTTPS and direct
write permissions are required; fork publication is not implemented.

Validation: 248 backend tests and 12 frontend contract tests passed; frontend
build passed with eight existing lint warnings. The browser fixture verified
review progress, draft preview, disabled publication before approval, reload,
and recovery from a simulated failed publication. No real PR was opened by
these checks. Restore/migration rehearsal from schema 006 to 008 preserved the
149 indexed files, 1,200 chunks, 1,092 embeddings and 27 findings.

The live invented-content test completed with two separate Mistral accounts:
one planner call, eleven reviewer calls, and one draft call. Both deliberate
mismatches were cited and corrected in two Markdown files; code was unchanged.
Semantic discovery was stubbed to invented locations; snapshot reads, provider
calls and the LangGraph loop were real. This is a small functional smoke test,
not a real-repository accuracy estimate. The local API and worker now run schema
008; authenticated reads and readiness passed. All Mistral model selections are
Large 4. No real pull request was created. Detailed evidence is in
`evidence/documentation_parallel_maintenance.json`.

During rollout, migration CLI ignored `DATABASE_URL` and initially attempted the
localhost fallback. No migration ran on that attempt. Alembic now honors the
environment setting; the subsequent migration and full regression suite passed.


## Real-repository validation improvements — 2026-11-12

User-authorized repositories: Argus, PramanaGST and Civic-Link; selected Markdown
and relevant source may be sent to Mistral. Markdown draft approval and PR
creation are authorized; merge remains a user action. Real validation starts
with explicitly scoped files; this is not completion of all repository docs.

All 64 tracked Markdown files across these repositories were indexed/readable.
The exercise exposed that Go and Dart implementations were absent from the
parser index. Review snapshots now supplement those languages from bounded,
regular Git blobs at the exact indexed commit using existing clones. No Q&A or
embedding migration is required. Missing checkout/source coverage is explicit.
Future ingestion fails before database writes if tracked Markdown is omitted
or cannot be decoded; empty Markdown remains supported.

The planner now uses short section IDs and a deduplicated file inventory while
persisting original task IDs. Sections carry their heading context; current
behavior is distinguished from plans and historical records. The review API can
select named existing Markdown files while the default remains all Markdown.

Live Large 4 responses exposed reasoning-budget limitations. The transport now
forces single-tool planner/draft actions, uses low reasoning there and medium
in reviewer loops, and reserves 4,096 output tokens. Truncation is an explicit
failure. Complete reasoning history is retained, following the provider's
[reasoning guidance](https://docs.mistral.ai/inference/conversations/reasoning).
Provider usage now binds an immutable prompt-plus-assistant prefix; subsequent
calls reserve a conservative incremental allowance with byte fallback when
that binding changes or usage is missing. Tests cover unchanged thinking across
resume, prefix/model/tool tampering, byte fallback and output reservations.

261 backend tests passed after these changes, including selected-file review
reuse, tracked Markdown omission, commit-pinned supplemental source reads,
heading context, compact planning and reasoning-budget contracts. Live accuracy
and publication outcomes are recorded separately once verified.


### Live validation checkpoint refinements

Real runs exposed planner format failures, oversized histories, and repeated
assessment rejection with unhelpful errors. The planner has one bounded repair
turn, preserving assistant/tool protocol. Markdown sections now follow all ATX
heading levels outside code fences, keeping parent context and exact byte/line
coverage. Source search distinguishes literal lookup from semantic discovery;
documentation cannot crowd out implementation matches. Supplemental source now
includes curated build manifests, excluding `.env` and lockfiles.

After six navigation actions, the graph requests a structured assessment. The
model chooses its verdict; insufficient evidence is allowed. Strict verbatim
claims and read-source citation checks remain. Rejection feedback identifies the
specific error and includes bounded cited text when a claim was paraphrased.
The first real accepted finding was PramanaGST's stale TailwindCSS stack claim,
independently checked against its frontend manifest, HTML and tracked source.

Reviews may explicitly opt into a draft of saved findings while paused using
`allow_partial: true`; the frontend warns that pending claims are not covered.
This is not whole-document acceptance. New actionable findings invalidate cached
draft reuse. The current limits are 64 model calls / 4 million byte reservation
units per section and 200 calls / 16 million units per run. Account rate limits
are unchanged; unsent local pauses do not consume physical call budgets.

276 backend tests and 13 frontend contracts passed; frontend build passed.
The full PramanaGST overview investigation before these refinements failed with
context_budget_exceeded and is retained as failed evidence. Real repository
quality is not established by the synthetic test suite or this single finding.


### First real publication checkpoint

PramanaGST review `bbae2a4a-4702-4f18-a63b-5e165cbc2ab5` accepted the stale
TailwindCSS stack claim with documentation and manifest citations. Independent
inspection confirmed it. A real Mistral-generated partial draft removed only
TailwindCSS from one Markdown line. The exact draft hash was approved and
published through Sentra as [PramanaGST PR #1](https://github.com/saketh1125/PramanaGST/pull/1).
The PR changes one Markdown file (+1/-1), remains unmerged, and its CI passed.
Repeating publication returned the same PR. The live frontend restored the
published draft, partial-scope notice, diff and PR link after reload.

Argus produced an insufficient assessment and Civic-Link a supported Geography
column assessment. Neither was edited. These three accepted claims are spot
checks; all three repository reviews remain partial. They do not establish a
whole-document or repository-wide accuracy result. The initial all-Markdown PramanaGST planner failure and later whole-overview
context-limit failure remain in the database as evidence.

Final checks: 277 backend tests, 13 frontend contracts and frontend build passed.
The eight existing frontend lint warnings remain. Original repository chunk
identity is unchanged after adding the two new repositories. Current source
inventory exposes all 64 tracked Markdown files, 56 Go files, 36 Dart files and
curated build manifests, subject to the documented source limits. A local backup
includes the publication identity. See
`evidence/documentation_real_repositories.json` for exact results and limits.
