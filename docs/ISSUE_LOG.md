# Weave issue log

Use this file for defects, deployment blockers, and gaps in verification. Mark
an item fixed after checking the reported behavior. Link the change and test in
the [development log](DEVELOPMENT_LOG.md).

| ID | Status | Issue or gap | Closure evidence |
| --- | --- | --- | --- |
| W-001 | Fixed locally | Settings showed Antigravity as unavailable even though the CLI had been signed in. The API could no longer reach the stopped bridge. | Restarted the bridge, confirmed API readback showed the saved model verification, and checked that the UI refresh preserves unsaved edits. A new live model prompt was outside this check. |
| W-002 | Fixed locally | Settings could refuse an unrelated save when an existing Antigravity route was unavailable. The UI hid the reason. | Commit dbaac1b allows unrelated saves, requires verification for new Antigravity assignments, and shows the server’s reason when a save fails. The regression test, browser fixture, and local API readback passed. |
| W-003 | Open · web pilot gate | Local checks and fixtures cover individual paths. The pilot needs a recorded end-to-end run on its actual web release candidate. | Verify sign-in, repository onboarding and indexing, cited Q&A, docs review through approved draft publication, findings, PR review, score, and persistent Settings. Include an interrupted job and a failed request. Record the tested revision, environment, and any exceptions. |
| W-004 | Open · hosted access gate | The existing release runbook assumes one private user. A shared pilot requires an explicit access and data boundary. | Choose isolated instances or shared tenancy. Prove repository, job, search, and artifact access stays within the approved boundary. Confirm HTTPS, retention and deletion, backups with a restore rehearsal, and user-facing support for failures. See the [cloud release runbook](CLOUD_RELEASE.md). |
| W-005 | Deferred to desktop | Settings can show setup instructions and verify the experimental local Antigravity bridge. One-click desktop sign-in and supported provider coverage still need acceptance. | For each shipped provider, verify first sign-in, expired session, cancellation, reconnect, and a successful bounded job. Record where credentials and repository context go. See the [sign-in decision](decisions/WEAVE_DECISIONS.md#w003--defer-hosted-cli-sign-in-for-the-pilot). |
| W-006 | Fixed locally | Dependency findings used absolute worker checkout paths, so PR review missed matching manifests. | Normalized scanner paths relative to the repository. Rescan and both PR retests restored file-level links. See the [benchmark](PILOT_BENCHMARK.md). |
| W-007 | Open · PR relevance | A one-package update in a shared manifest links all of that file's dependency findings. | Distinguish affected packages from other findings on the same file. Verify on two updates that touch the same manifest and change different packages. |
| W-008 | Open · draft feedback | A provider cooldown returned HTTP 429 with a generic draft error. Retrying after the cooldown worked. | Show that the draft request is rate-limited and tell the user when to retry if the provider supplied a delay. Verify the saved review survives the retry. |
| W-009 | Open · scan revision integrity | Source review found that a failed reindex can leave the checkout and recorded index on different revisions. | Exercise two commits and a failed reindex. Require a matching checkout before recording scan coverage. |
| W-010 | Open · dependency coverage | A synthetic check reproduced lost dependency versions or manifest paths when one advisory appeared more than once. | Verify alias deduplication preserves affected versions and all manifest paths without duplicate score deductions. |
| W-011 | Open · scan progress | Findings refreshes once after four seconds. A slower or failed scan can leave the view stale. | Verify delayed and failed scans in the browser. Refresh on completion and show failure status. |
| W-012 | Open · readiness verdict | The score helper returned `100 / Ready` with unknown coverage for empty history. | Verify the API and UI expose an incomplete verdict until required scans complete for the indexed revision. |
| W-013 | Open · draft accuracy | The benchmark draft proposed a numerical lower bound that independent evidence did not establish. | Show evidence with each proposed edit and measure unsupported edits during maintainer review. |
| W-014 | Open · PR evidence revision | PR review checks the head revision during fetch. Base changes and finding scan provenance still need verification. | Exercise a changed base with an unchanged head and show which scan revision supports a linked finding. |
| W-015 | Open · worker visibility | API readiness can succeed while jobs back up or the worker is unavailable. | Exercise a stopped worker and growing queue. Surface worker availability and oldest queued work. |
| W-016 | Open · import resource limits | Parsed-file limits apply after checkout. | Rehearse an oversized import and verify a checkout storage budget and cleanup. |

Keep prior evidence dated. A fixture, a provider probe, and a pilot run each
answer different questions. See [implementation status](IMPLEMENTATION_STATUS.md)
for earlier checkpoints and [feature acceptance](FEATURE_ACCEPTANCE.md) for
existing feature checks.
