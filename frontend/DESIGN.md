# Sentra workspace design

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

## Verification

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
