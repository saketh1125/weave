# Documentation UI regression

The full workspace visual fixture is documented in [DESIGN.md](../DESIGN.md).
It exercises the shared graphite/jade presentation with synthetic data and no
model calls. Include `test/status.test.ts` and `test/model-settings.test.ts` when
running all frontend response and status contracts.

Run the response-contract checks with:

```powershell
node --test test/docs-response.test.ts test/documentation-review.test.ts test/documentation-draft.test.ts
```

For a provider-free browser check, build from `frontend` with a relative API
URL, then start the synthetic fixture server:

```powershell
$env:VITE_API_URL='/api'
npm run build
python test/docs-browser-fixture.py
```

Open the printed loopback URL. Any synthetic access-token text signs into the
fixture; no real credentials or repository data are used. Select
**Documentation regression fixture**, open **Docs & Drift**, and click
**Generate documentation** three times. The first response contains the legacy
object-valued behavior that caused the reported crash; it should show an error
while preserving the page. The second response should render a valid summary
and coverage. The third returns two completed summaries with a synthetic
rate-limit response and a three-second cooldown; the button should say
**Continue documentation**, show the countdown, and remain disabled until it
expires. Click **Continue documentation** after the countdown to receive the
synthetic success response.

Select **Render recovery fixture** and open Docs to exercise a deliberately
malformed alignment response. The route boundary should preserve navigation
and offer **Reset docs view**. Resetting should recover the view.

For the publication recovery flow, select **Documentation regression fixture**,
open Docs, and start a documentation-first review. After the completed review
shows a planner assignment and conflicting claim, click **Update documentation**.
Inspect the Markdown preview and unified diff, check the approval box, and click
**Open pull request**. The fixture simulates an interrupted first request after
the server has saved a publishing state; no pull request is reported as opened.
Reload the Docs page to restore the saved draft, recheck the exact-draft
approval, and click **Retry opening pull request**. The fixture should then show
only its synthetic GitHub pull request link.

This exercises the built React UI with synthetic responses. It does not test
real provider quality or replace an API/database integration check. Stop the
fixture server after verification.
