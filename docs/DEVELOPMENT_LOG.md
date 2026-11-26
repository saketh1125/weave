# Weave development log

Add a short dated entry for meaningful changes and investigations. Record the
revision, what changed, why it changed, what was checked, and what remains open.
Use the [issue log](ISSUE_LOG.md) for open work and the
[decision log](decisions/WEAVE_DECISIONS.md) for product and architecture rationale.

## 2026-11-22 · Engineering audit

Reviewed repository revision integrity, scanner persistence, PR relevance,
job feedback, readiness, and operator boundaries. A synthetic two-manifest
check reproduced lost dependency locations. A score helper check returned
`100 / Ready` alongside unknown coverage for empty history. Checkout revision
and Findings refresh concerns came from source inspection. The
[issue log](ISSUE_LOG.md) records closure checks. The full web workflow and
cloud recovery were outside this audit.

## 2026-11-19 · Web pilot benchmark

Imported and reindexed a pinned public Flask fixture, then ran the scanners,
repository Q&A, summary generation, alignment, PR review, PR Q&A, and score.
The run exposed absolute dependency finding paths that prevented PR joins and
a PR answer that treated a manifest-only edit as application source. See the
[benchmark record](PILOT_BENCHMARK.md) for rubric, fixes, and live retests.

## 2026-11-19 · Product direction checkpoint

Agreed to stabilize the web experience first, then ship a desktop app connected
to a Weave-operated backend. The first three
[Weave decisions](decisions/WEAVE_DECISIONS.md) record the rationale, the
proposed local/server split, and the pilot sign-in boundary.

## 2026-11-19 · Settings save and connection refresh

- **Revision** — `dbaac1b` on `frontend-redesign`, pushed to the Weave remote.
- **Problem** — A stopped local bridge appeared as an unavailable connection.
  Save validation treated existing Antigravity routes as new assignments and
  could reject unrelated changes.
- **Fix** — Restarted the bridge and added connection refresh that preserves
  unsaved edits. A successful save uses the response from the write, and
  failed saves surface the server’s reason. New Antigravity assignments still
  require verification.
- **Checked** — 13 targeted backend tests, 33 frontend tests, production build,
  browser fixtures, and local Settings API readback. The live API recognized
  the saved verification for `gemini-3.8-flash`. The user confirmed Settings
  worked before the push.
- **Open** — Hosted pilot acceptance remains a separate check. A fresh live
  model prompt was outside this fix.

## 2026-11-19 · Web interface and interaction pass

- **Revisions** — `88b42fb`, `aad3612`, and `a26b5f9` on `frontend-redesign`.
- **Changed** — Consolidated workspace navigation and documentation review.
  The current design uses cool charcoal, lavender, self-hosted Inter, and
  denser layouts. Added loading indicators tied to real requests, pressed and
  hover feedback, and accessible status changes for review progress.
- **Checked** — Production build, 33 frontend tests, and desktop and mobile
  browser fixtures, including reduced-motion behavior and exact-draft approval.
- **Open** — The browser runs used fixture responses. Pilot acceptance needs the
  hosted candidate with its real integrations.
