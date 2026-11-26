# Web pilot benchmark

Run started 2026-11-19 against
[vulnerable-python-app at bf9c954](https://github.com/ryanmcmorrowsnyk/vulnerable-python-app/tree/bf9c954b327a5ec872b7fd65642760b3837af531).
The app is a public Python/Flask scanner fixture with a labeled README,
pinned dependency versions, and existing remediation PRs. Weave imported
its default branch and checked the actual indexed SHA against this pin.
The fixture was scanned as source and was never started.

## Findings by severity

| Severity | Static analysis | Dependency scanning | Total |
| --- | ---: | ---: | ---: |
| Critical | 0 | 25 | 25 |
| High | 14 | 133 | 147 |
| Medium | 10 | 114 | 124 |
| Low | 0 | 24 | 24 |
| Unknown | 0 | 2 | 2 |
| **Total** | **24** | **298** | **322** |

All 322 findings were open on the rescan. Dependency results covered 51
package names. Job creation-to-terminal timings were 7.4 seconds for import,
3.9 seconds for reindex, 100.7 seconds for the first combined scan, and 85.2
seconds for the rescan. These measurements describe one local run.

## Results

| Workflow | Result and checks |
| --- | --- |
| Repository import and reindex | Both persisted jobs succeeded at the pinned SHA. |
| Static analysis | Semgrep returned 24 alerts. Rule and source-location inspection matched 10 of the fixture README’s 19 labeled scenarios. Multiple rules can point to the same scenario. This tests coverage on one fixture. |
| Dependency scanning | OSV returned 298 findings. All three sampled checks matched the pinned package/version and CVE: urllib3 1.26.5 (CVE-2023-43804), python-multipart 0.0.5 (CVE-2024-53981), and Brotli 1.0.9 (CVE-2025-6176). These are dependency advisories and do not establish runtime reachability. |
| Readiness score | 35/100 and persisted. Verified 100 minus the capped Semgrep deduction of 35 and the capped dependency deduction of 30. Both scanner modules reported complete for the pinned revision. |
| Repository Q&A | 2/2 sampled cases passed with Codestral. The first named Flask and the fixture’s purpose with supporting code and README citations. The second correctly abstained when asked about an undocumented backup interval. |
| Code summary | Codestral saved one summary of the root Flask handler. The description matched the route and its returned page. The response recorded all 1,405 source characters covered for that unit. |
| Documentation alignment | Deterministic alignment returned zero checked and 16 unverified references. This Flask fixture does not cover the supported FastAPI or Express HTTP checks. The optional behavioral pass used Codestral and processed one of 22 passages with continuation available. |
| Documentation review | Gemini 3.8 Flash completed the indexed README in about 690 seconds. It saved 50 assessments: 43 supported, six insufficient, and one potential conflict. The conflict concerned the README presenting its 19 listed examples as the application's total issue count. |
| Draft and approval | The first draft request received HTTP 429. It succeeded after the provider cooldown and persisted a one-line README patch. The edit proposes an exact lower bound that this run did not establish independently. An incorrect approval hash returned HTTP 409, and readback confirmed the draft stayed unpublished. |
| PR review | PRs #8 and #11 each returned the correct head and one complete manifest diff. Both initial summaries named the correct package and version change. Both initially linked zero dependency findings because the scanner stored absolute checkout paths. After rescan on the rebuilt worker, each PR linked the expected dependency among 298 advisories on the changed manifest. Linking is file-level, so the other advisories are not evidence that the PR changed or fixed their packages. |
| PR Q&A | PR #8’s first answer matched the package and both versions (3/4 criteria). It mislabeled the manifest edit as an application source change. After adding the distinction to the evidence prompt, the same live question passed all four checks. |
| Settings and browser | A live Settings write with an empty patch matched readback. Edge rendered scanner details, readiness score, saved workflow routing, and the stored alignment result. |

Static scenario matches were hardcoded secrets, SQL injection, command injection,
cross-site scripting, server-side request forgery, unsafe evaluation, insecure
deserialization, YAML deserialization, open redirect, and debug mode.
The nine README labels without a scored match remain useful for a follow-up
coverage audit. The 24 alerts include overlapping rules and have not been
reviewed for precision.

PR ground truth comes from [PR #8](https://github.com/ryanmcmorrowsnyk/vulnerable-python-app/pull/8)
and [PR #11](https://github.com/ryanmcmorrowsnyk/vulnerable-python-app/pull/11).
Dependency checks used the [pinned manifest](https://github.com/ryanmcmorrowsnyk/vulnerable-python-app/blob/bf9c954b327a5ec872b7fd65642760b3837af531/requirements.txt)
and published advisory records for [urllib3](https://github.com/advisories/GHSA-v845-jxx5-vc9f),
[python-multipart](https://github.com/advisories/GHSA-59g5-xgcq-4qw3), and
[Brotli](https://github.com/google/brotli/issues/1411).

The rebuilt backend image passed 625 tests against a disposable
PostgreSQL/pgvector instance, including publication with a mocked GitHub
writer. The production frontend build and 33 frontend tests passed.
Live publication was outside this run on a third-party repository.

## Desktop screenshots

Seven 1440 × 1080 desktop captures show static and dependency finding details,
the readiness score, repository Q&A, PR review, the completed documentation
review, and the Markdown draft and approval dialog. The captured Q&A returned
five citations. Q&A and PR review replay the recorded benchmark responses in
Weave. The draft still awaits approval. The screenshot bundle and raw run
artifacts are local under `.migration/`.

## Pilot boundaries

Use this result as a local web workflow baseline. The cloud pilot still needs
verification on its release image and hosted configuration. The direction is to
carry forward Postgres/pgvector with a rehearsed backup and restore. Before
inviting users, confirm account authentication, authorization and data access
boundaries, and present the privacy policy for explicit acceptance. Contact
details, raw provider replies, and credentials are kept out of this record.
