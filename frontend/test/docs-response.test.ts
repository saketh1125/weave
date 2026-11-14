import { strict as assert } from "node:assert"
import { test } from "node:test"
import { docsRetryDeadline, docsRetryRemainingSeconds, INVALID_DOCS_RESPONSE, parseDocsResult, readStoredDocs } from "../src/lib/docs-response.ts"

test("accepts documentation endpoint status and generated result shapes", () => {
  for (const result of [
    { status: "disabled", reason: "Not configured" },
    { status: "empty", count: 0 },
    { status: "error", error: "Generation failed" },
    { status: "ok", docs: [{ chunk_id: "legacy-empty", doc: { summary: "Summary", key_behaviors: [] } }] },
    { status: "partial", count: 2, docs: [
      { chunk_id: "1", doc: { summary: "Summary", key_behaviors: ["Does work"], coverage: { source_characters: 100, processed_characters: 100, sections_processed: 1 } }, cached: false, context_complete: true },
      { chunk_id: "2", error: "Model returned invalid documentation" },
    ] },
    { status: "rate_limited", count: 2, generated: 1, retry_after_seconds: 45, docs: [{ chunk_id: "one", doc: { summary: "Saved" } }] },
    { status: "provider_unavailable", generated: 0 },
    { status: "budget_exhausted", retry_after_seconds: 0 },
  ]) assert.equal(parseDocsResult(result), result)
})

test("rejects values that would crash or corrupt rendering", () => {
  for (const result of [
    null, { status: {} }, { status: "ok", docs: {} },
    { status: "ok", docs: [{ chunk_id: "1", doc: { summary: {} } }] },
    { status: "ok", docs: [{ chunk_id: "1", doc: { key_behaviors: "wrong" } }] },
    { status: "ok", docs: [{ chunk_id: "legacy", doc: { summary: "Summary", key_behaviors: [{ behavior: "Example" }] } }] },
    { status: "ok", docs: [{ chunk_id: "legacy-mixed", doc: { summary: "Summary", key_behaviors: [{ behavior: "Example" }, "Example"] } }] },
    { status: "ok", docs: [{ chunk_id: "1", doc: { coverage: {} } }] },
    { status: "rate_limited", generated: -1 },
    { status: "rate_limited", generated: 1.5 },
    { status: "rate_limited", generated: Number.MAX_SAFE_INTEGER + 1 },
    { status: "rate_limited", retry_after_seconds: -1 },
    { status: "rate_limited", retry_after_seconds: 0.5 },
    { status: "rate_limited", retry_after_seconds: Number.MAX_SAFE_INTEGER + 1 },
  ]) assert.throws(() => parseDocsResult(result), { message: INVALID_DOCS_RESPONSE })
})

test("hides invalid workspace results retained across a component reload", () => {
  const legacy = { repoId: "repo", result: { status: "ok", docs: [{ chunk_id: "1", doc: { key_behaviors: [{ behavior: "Example" }] } }] } }
  assert.deepEqual(readStoredDocs(legacy, "repo"), { result: null, retryUntil: undefined, invalid: true })
  assert.deepEqual(readStoredDocs(legacy, "another-repo"), { result: null, retryUntil: undefined, invalid: false })
  const valid = { repoId: "repo", result: { status: "empty", count: 0 } }
  assert.deepEqual(readStoredDocs(valid, "repo"), { result: valid.result, retryUntil: undefined, invalid: false })
  assert.deepEqual(readStoredDocs({ ...valid, retryUntil: 1_000_000 }, "repo"), { result: valid.result, retryUntil: 1_000_000, invalid: false })
})

test("bounds cooldown deadlines and handles invalid clocks safely", () => {
  assert.equal(docsRetryDeadline(10_000, 30), 40_000)
  assert.equal(docsRetryDeadline(10_000, 10_000), 3_610_000)
  assert.equal(docsRetryDeadline(10_000, 0), undefined)
  assert.equal(docsRetryDeadline(Number.MAX_SAFE_INTEGER, 1), undefined)
  assert.equal(docsRetryRemainingSeconds(40_000, 10_000), 30)
  assert.equal(docsRetryRemainingSeconds(40_000, 40_000), 0)
  assert.equal(docsRetryRemainingSeconds(Number.MAX_SAFE_INTEGER + 1, 10_000), 0)
  assert.equal(docsRetryRemainingSeconds(40_000, Number.POSITIVE_INFINITY), 0)
})
