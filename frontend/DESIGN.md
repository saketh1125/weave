# Weave workspace design

The private-preview interface uses graphite surfaces, muted jade actions and warm
system typography. Semantic status colors are independent of the brand accent:
rose for failures, amber for gaps, jade for successful results, blue-gray for
ongoing work, and neutral gray for unknown states. Icons and text accompany color.

Shared styling lives in `src/index.css`; shared controls and result components
live in `src/components/ui`. Use `PageIntro`, `StatusBadge`, `ModelBadge`,
`MetricTile` and `EmptyState` rather than rebuilding their styles in each page.
Status class names are explicit literals so Tailwind retains the corresponding
styles. Keep status interpretation in `src/lib/status.ts`.

Review progress counts completed sections only. Failed and pending work remain
separate; a high score or zero stored findings must not imply complete coverage.
Unknown provider states remain visible and neutral. Model names identify actual
returned metadata; the interface never invents a model for missing results.

Desktop navigation stays visible in a quiet sidebar. Compact screens use native
horizontal navigation and retain sign-out in the toolbar. Controls give immediate
press feedback without delaying actions. No gesture library or animation package
is required. Reduced-motion removes transforms/transitions; reduced-transparency
removes blur; increased contrast strengthens labels and borders.

The native finding dialog provides keyboard containment and Escape dismissal.
Draft approval and publication actions retain their existing exact-draft guards.
No model-provider or backend review behavior was changed by the redesign.

## Proposed workspace redesign

The application in this checkout is branded Weave. This proposal maps the
Sentra workflows it carries forward to the interaction patterns in
[Linear’s app refresh](https://linear.app/now/behind-the-latest-design-refresh)
and its [earlier UI redesign](https://linear.app/now/how-we-redesigned-the-linear-ui).
It is a wireframe for review. The sections above describe the current design.

### Where Linear’s patterns fit

| Sentra workflow | Pattern | Weave proposal |
| --- | --- | --- |
| Repositories | Compact, aligned rows and predictable view actions | Show repository, branch, indexed revision and status in a list. Keep Connect in the header. Selecting a repository updates the shared context. Put reindex and last job details in the selected repository’s controls. |
| Ask | Keep the content in focus and secondary metadata nearby | One question composer, answer first, expandable source citations next to the answer. Keep repository and session context visible while switching views. |
| Docs & Drift | Triage list with a focused detail view | Lead with run status and coverage. Group findings by verdict and open the selected claim with its documentation citation, source evidence and suggested update. Give drafts and diffs their own review surface. |
| Findings | Filterable verification queue | Use compact severity, source and location rows. Open the existing finding details from the list. Show scan coverage alongside counts. |
| PR Risk | Review header with stable action placement | Put PR title, reviewed revision and coverage up front. Separate summary, changed files, linked findings and PR questions. |
| Score | Summary with drill-down | Pair the score with analysis coverage. Keep module deductions, caps and scoring rules available just below. |
| Settings | Simple groups with inline feedback | Group connections, model routing and Antigravity controls. Show configuration and live test outcomes separately. |

### Desktop wireframes

```text
┌─────────────────┬──────────────────────────────────────────────────────────┐
│ Weave           │ Repositories / Sentra · main                           │
│                 ├──────────────────────────────────────────────────────────┤
│ Repositories    │ Repositories                       [Connect repository] │
│ Ask             │                                                          │
│ Docs & Drift    │ Repository          Branch     Indexed rev.     Status   │
│                 │ Sentra              main       ff13bdb0         Ready    │
│ Findings        │ Other repository    develop    a3d2…            Pending  │
│ PR Risk         │                                                          │
│ Score           │ Selected repository                                     │
│                 │ Latest job · Completed                   [Reindex]      │
│ Settings        │ [Ask] [Review docs] [View findings]                       │
│ Sign out        │                                                          │
└─────────────────┴──────────────────────────────────────────────────────────┘
```

```text
┌─────────────────┬──────────────────────────────────────────────────────────┐
│ Workspace nav   │ Sentra / Docs & Drift · main                              │
│                 ├──────────────────────────────────────────────────────────┤
│                 │ Review   Alignment   Code summaries       [Start review] │
│                 │ Completed · 14/14 files · 29/29 leads · 89 assessments   │
│                 │ [Run details]                              [Create draft] │
│                 ├───────────────────────┬──────────────────────────────────┤
│                 │ Findings              │ Selected finding                 │
│                 │ [Conflicts] [All]     │ Quoted claim                      │
│                 │ API base URL          │ Documentation citation           │
│                 │ Retry control         │ Code evidence and explanation    │
│                 │                       │ Suggested Markdown update        │
└─────────────────┴───────────────────────┴──────────────────────────────────┘
```

Counts above are from the
[recorded Sentra run](../docs/evidence/documentation_full_review_2026-11-15.json).
The detail area shows one finding at a time. Draft opens a full-width diff
review where the user approves the exact draft and explicitly opens a PR.
Once published, the result shows the PR link.

### UX decisions and checks

- Use two predictable header levels. The first locates the active repository
  and view. The second holds the view’s filters and main action. Keep the
  sidebar quiet and preserve the graphite and jade palette.
- Keep the three Docs & Drift tools separate because they produce different
  results. Within Review, distinguish AI verdicts, static leads and source
  coverage. Keep agent, model and usage details in the run details view.
- Show queued, running, paused, completed, failed and stale states in the
  review header. A paused run shows the reason, retained work and its available
  action. A stale draft prompts a fresh review before approval.
- When no repository is selected, make selection or connection the next
  action. Carry repository context between views and clear displayed results
  tied to a previous selection. Polling should update status without resetting
  the selected finding, keyboard focus or scroll position.
- On narrow screens, show the findings list and its detail in sequence, with
  a clear return action. Move the draft diff into a full-width review. Check
  keyboard navigation, focus return, reduced motion and readable statuses.

Start implementation with the shared shell and Repositories. Then prototype
Docs & Drift with a completed dense run and a paused run before applying the
same list and detail pattern to Findings. Use the existing browser fixture for
other routes and add representative review data for the dense-run check.


### Indicators and interaction details

| Signal | Placement and visual treatment | Behavior |
| --- | --- | --- |
| Current view and repository | Active nav row has a quiet filled surface. Selected repository row has a separate check and accessible selected state. The header shows the repository name and branch. | Hover and keyboard focus each have their own style. Changing repository updates context and clears results and approvals scoped to the previous repository. |
| Repository and run state | Use a compact icon with a text label. Gray dot for queued or unknown, blue-gray activity icon for running, jade check for ready or completed, amber pause for paused or stale, rose error icon for failed. | Preserve distinct labels for each state. Show the next available action beside paused, failed or stale results. Unknown states use neutral styling. |
| Review progress | Place a thin bar and completed/total file count below the run header. Display partial coverage and failures alongside it. Use a separate labeled count for static triage. | Advance only on saved completion. Show the current phase while counts stay unchanged. If the total is unavailable, show an indeterminate activity indicator and phase. |
| Finding verdict and severity | Put an icon and label at the start of each row. Use rose for conflicts and amber for insufficient or unverified evidence. Preserve severity text on scan findings and identify the evidence source as model or static. | Severity, verdict and review progress remain separate. A completed review can still contain unresolved evidence or conflicts. |
| Connection health and model | Small status dot and label in the workspace toolbar. Model name and test status sit with the relevant connection or result. | Separate service availability, connection configuration and test success. Unknown model or coverage values display as “Not reported.” |
| Loading and errors | Use a stable-height placeholder on first load. For a submitted action, use an inline busy label and progress icon on its button. Show errors beside the affected control or panel. | Keep saved output visible during a refresh. Prevent duplicate submissions and retain entered values on failure. Give a short recovery action when one exists. |
| Draft and PR publication | Draft header carries file count and base revision. Exact-diff approval sits beside the publish action. Show “Opening pull request…” in flight and “View pull request” after success. | Explain why publication is disabled. Clear approval if the draft changes. A stale draft offers a fresh review, and a recoverable publishing failure offers retry where supported. |

Use a consistent 16 px icon size for ordinary controls. Dots are secondary
signals paired with text. Use a visible focus ring and a 44 px minimum touch
target for standalone mobile controls. Use restrained motion for press feedback
and progress changes, and a static indicator with reduced motion enabled.
Announce meaningful state changes through a polite live region. Routine polling
and per-agent counters stay quiet.

Before coding, review the wireframes with real examples for an empty workspace,
a queued and active review, a pause with retained findings, a completed review
with conflicts, and a stale or published draft. Check a narrow layout and
keyboard flow through the finding detail and draft approval. Final token
contrast, spacing fit and behavior are checked in the browser during
implementation.

## Verification

Settings shows CLI connection controls for Antigravity, Codex, Claude Code, and OpenCode. The Antigravity dialog provides the local login command and uses the existing connection check. Browser-triggered login requires a host connector. The other three connections have setup dialogs pending their host connectors.

Run the production build and frontend contracts:

```powershell
npm run build
node --test test/docs-response.test.ts test/documentation-review.test.ts test/documentation-draft.test.ts test/model-settings.test.ts test/status.test.ts
```

For provider-free visual testing, run from `frontend`:

```powershell
$previousApiUrl = $env:VITE_API_URL
try {
  $env:VITE_API_URL = '/api'
  npm run build -- --outDir ../work/ui-preview-dist
} finally { $env:VITE_API_URL = $previousApiUrl }
python test/workspace-browser-fixture.py --root ../work/ui-preview-dist
```

The fixture accepts synthetic sign-in text and never contacts a model provider.
It includes a failed review with 163 sections, citation-backed answer data,
findings, partial score coverage and model settings. Do not treat fixture results
as analysis of a real repository.
