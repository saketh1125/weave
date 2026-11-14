import { strict as assert } from "node:assert"
import { test } from "node:test"
import { INVALID_DOCUMENTATION_DRAFT, parseDocumentationDraft, parseLatestDocumentationDraft } from "../src/lib/documentation-draft.ts"

const valid = { id: "draft-1", review_id: "review-1", base_commit: "a".repeat(40), draft_hash: "b".repeat(64), status: "draft", diff: "--- a/README.md\n+++ b/README.md", branch: null, pr_url: null,
  files: [{ path: "README.md", old_hash: "c".repeat(64), new_hash: "d".repeat(64), original: "Old text", content: "Updated text" }] }

test("parses a persisted markdown draft and its preview safely", () => {
  const parsed = parseDocumentationDraft(valid)
  assert.equal(parsed.id, "draft-1")
  assert.equal(parsed.files[0].content, "Updated text")
  assert.equal(parseLatestDocumentationDraft(null), null)
})

test("accepts a completed publication only with a GitHub pull request link", () => {
  const parsed = parseDocumentationDraft({ ...valid, status: "published", branch: "docs/review-1", pr_url: "https://github.com/org/repo/pull/42" })
  assert.equal(parsed.pr_url, "https://github.com/org/repo/pull/42")
  for (const pr_url of [null, "https://evil.example/pr", "http://github.com/org/repo/pull/42"]) {
    assert.throws(() => parseDocumentationDraft({ ...valid, status: "published", branch: "docs/review-1", pr_url }), { message: INVALID_DOCUMENTATION_DRAFT })
  }
})

test("rejects malformed drafts, files, status, hashes, and unsafe links", () => {
  const invalid = [null, { ...valid, status: "success" }, { ...valid, status: "stale" }, { ...valid, draft_hash: "" }, { ...valid, draft_hash: "z".repeat(64) },
    { ...valid, files: [{ ...valid.files[0], content: {} }] }, { ...valid, files: [{ ...valid.files[0], path: "" }] },
    { ...valid, files: [{ ...valid.files[0], old_hash: "old" }] },
    { ...valid, pr_url: "javascript:alert(1)" }]
  for (const value of invalid) assert.throws(() => parseDocumentationDraft(value), { message: INVALID_DOCUMENTATION_DRAFT })
})
