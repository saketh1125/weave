import { strict as assert } from "node:assert"
import { readFileSync } from "node:fs"
import { test } from "node:test"

const review = readFileSync(new URL("../src/components/DocumentationReview.tsx", import.meta.url), "utf8")
const settings = readFileSync(new URL("../src/pages/Settings.tsx", import.meta.url), "utf8")

test("restored retry deadline is set before loading an optional draft", () => {
  const restoration = review.slice(review.indexOf("const restored ="), review.indexOf("}).catch(e =>"))
  assert.ok(restoration.indexOf("setRetryUntil(") < restoration.indexOf("const savedDraft ="))
  assert.ok(restoration.indexOf("setRetryUntil(") !== -1)
})

test("polling recovery clears only the polling error", () => {
  const polling = review.slice(review.indexOf("const timer = window.setInterval"), review.indexOf("}, [active, repoId, runId])"))
  assert.match(polling, /setPollError\(""\)/)
  assert.match(polling, /setPollError\(e instanceof Error/)
  assert.doesNotMatch(polling, /setError\(/)
})

test("the empty model placeholder cannot be selected", () => {
  assert.match(settings, /<option value="" disabled>Select a model<\/option>/)
})

test("draft review directs users to compare replacements with cited evidence", () => {
  assert.match(review, /Compare each proposed replacement against its cited source before approval/)
  assert.match(review, /Review the findings and citations above/)
})
