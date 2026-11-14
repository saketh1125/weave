import { strict as assert } from "node:assert"
import { test } from "node:test"
import { INVALID_DOCUMENTATION_REVIEW, parseDocumentationReview, parseLatestDocumentationReview, reviewRetryDeadline, reviewRetryRemaining, reviewReasonMessage } from "../src/lib/documentation-review.ts"

const valid = {
  run_id: "run-1", job_id: "job-1", commit_sha: "abc123", status: "completed", model: "mistral-large", retry_after_seconds: null,
  coverage: { files_total: 2, sections_total: 3, sections_completed: 2, sections_pending: 0, sections_failed: 1, claims_assessed: 1,
    unreadable_files: ["docs/unreadable.md"], scope: "indexed files only; files omitted during ingestion are not represented" },
  assessments: [{ id: "a1", verdict: "conflicting", claim: "Uses X", explanation: "Code uses Y", suggested_update: "Say Y instead.",
    doc: { file: "README.md", start_line: 8, end_line: 9 }, code_citations: [{ file: "src/a.py", start_line: 4, end_line: 5 }] }],
  reason: null,
}

test("accepts and normalizes a completed review contract", () => {
  const parsed = parseDocumentationReview(valid)
  assert.equal(parsed.run_id, "run-1")
  assert.equal(parsed.coverage.sections_failed, 1)
  assert.deepEqual(parsed.coverage.unreadable_files, ["docs/unreadable.md"])
  assert.equal(parsed.retry_after_seconds, undefined)
  assert.equal(parsed.assessments[0].verdict, "conflicting")
  assert.equal(parsed.assessments[0].code_citations[0].file, "src/a.py")
  assert.equal(parsed.source_coverage, null)
})

test("accepts bounded source coverage and stays compatible with missing or null coverage", () => {
  const source_coverage = {
    status: "partial", reason: "Some source could not be read",
    unavailable_paths: [{ path: "src/bad.go", reason: "Invalid UTF-8" }],
    supplemented_paths: ["src/main.go", "lib/main.dart"],
    missing_indexed_markdown: ["docs/omitted.md"],
  }
  assert.deepEqual(parseDocumentationReview({ ...valid, source_coverage }).source_coverage, source_coverage)
  assert.equal(parseDocumentationReview({ ...valid, source_coverage: null }).source_coverage, null)
  assert.equal(parseDocumentationReview(valid).source_coverage, null)
})

test("accepts bounded planner assignments and rejects inconsistent plans", () => {
  const plan = { reviewer_count: 2, assignments: [{ reviewer: 1, task_ids: ["t1"] }, { reviewer: 2, task_ids: ["t2", "t3"] }] }
  assert.deepEqual(parseDocumentationReview({ ...valid, plan }).plan, plan)
  for (const invalidPlan of [{ ...plan, reviewer_count: 3 }, { ...plan, assignments: [{ reviewer: 2, task_ids: ["t1"] }] }, { ...plan, assignments: [{ reviewer: 1, task_ids: [null] }, plan.assignments[1]] }]) {
    assert.throws(() => parseDocumentationReview({ ...valid, plan: invalidPlan }), { message: INVALID_DOCUMENTATION_REVIEW })
  }
})

test("validates optional investigation activity", () => {
  const activity = { path: "docs/guide.md", start_line: 2, end_line: 10, steps: 4, errors: 1, claims_recorded: 2, phase: "paused", updated_at: 1_700_000_000.5 }
  assert.deepEqual(parseDocumentationReview({ ...valid, activity }).activity, activity)
  assert.equal(parseDocumentationReview(valid).activity, null)
  assert.equal(parseDocumentationReview({ ...valid, activity: null }).activity, null)
  assert.deepEqual(parseDocumentationReview({ ...valid, activity: { ...activity, steps: 64, errors: 10000, claims_recorded: 30 } }).activity, { ...activity, steps: 64, errors: 10000, claims_recorded: 30 })
  for (const invalidActivity of [
    { ...activity, path: " " }, { ...activity, path: "x".repeat(2001) },
    { ...activity, start_line: 0 }, { ...activity, end_line: 1 },
    { ...activity, steps: -1 }, { ...activity, errors: 1.5 }, { ...activity, claims_recorded: Number.MAX_SAFE_INTEGER + 1 },
    { ...activity, steps: 65 }, { ...activity, errors: 10001 }, { ...activity, claims_recorded: 31 },
    { ...activity, phase: "complete" }, { ...activity, updated_at: Infinity }, { ...activity, updated_at: 8_640_000_001 },
  ]) assert.throws(() => parseDocumentationReview({ ...valid, activity: invalidActivity }), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("accepts a latest lookup with no saved review outside the parser", () => {
  assert.equal(parseLatestDocumentationReview(null), null)
  assert.throws(() => parseDocumentationReview(null), { message: INVALID_DOCUMENTATION_REVIEW })
  assert.equal(parseDocumentationReview({ ...valid, job_id: null }).job_id, null)
})

test("rejects malformed counts, statuses, citations, and nested objects", () => {
  const invalid = [
    { ...valid, status: "unknown" },
    { ...valid, coverage: { ...valid.coverage, sections_pending: 1.5 } },
    { ...valid, coverage: { ...valid.coverage, sections_pending: 1 } },
    { ...valid, coverage: { ...valid.coverage, unreadable_files: "none" } },
    { ...valid, coverage: { ...valid.coverage, claims_assessed: 2 } },
    { ...valid, assessments: [{ ...valid.assessments[0], verdict: "maybe" }] },
    { ...valid, assessments: [{ ...valid.assessments[0], doc: { file: "x.md", start_line: 0, end_line: 2 } }] },
    { ...valid, assessments: [{ ...valid.assessments[0], code_citations: [null] }] },
    { ...valid, assessments: "wrong" },
    { ...valid, reason: {} },
    { ...valid, retry_after_seconds: Number.MAX_SAFE_INTEGER + 1 },
    { ...valid, source_coverage: { status: "unknown", reason: null, unavailable_paths: [], supplemented_paths: [], missing_indexed_markdown: [] } },
    { ...valid, source_coverage: { status: "partial", reason: null, unavailable_paths: [{ path: "", reason: "bad" }], supplemented_paths: [], missing_indexed_markdown: [] } },
    { ...valid, source_coverage: { status: "partial", reason: null, unavailable_paths: [], supplemented_paths: ["x".repeat(2001)], missing_indexed_markdown: [] } },
    { ...valid, source_coverage: { status: "partial", reason: null, unavailable_paths: [], supplemented_paths: [], missing_indexed_markdown: "none" } },
  ]
  for (const value of invalid) assert.throws(() => parseDocumentationReview(value), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("bounds paused-review cooldowns and handles unsafe clocks", () => {
  assert.equal(reviewRetryDeadline(10_000, 30), 40_000)
  assert.equal(reviewRetryDeadline(10_000, 99_999), 3_610_000)
  assert.equal(reviewRetryDeadline(Number.MAX_SAFE_INTEGER, 1), undefined)
  assert.equal(reviewRetryRemaining(40_000, 10_000), 30)
  assert.equal(reviewRetryRemaining(40_000, 40_000), 0)
  assert.equal(reviewRetryRemaining(Number.MAX_SAFE_INTEGER + 1, 10_000), 0)
})

test("explains review failures and bounds unknown reasons", () => {
  for (const reason of ["model_access_denied", "provider_access_denied"]) {
    assert.match(reviewReasonMessage(reason), /entitlement/)
    assert.match(reviewReasonMessage(reason), /accessible configured model/)
  }
  for (const reason of ["timeout", "provider_unavailable", "provider_connection_failed", "agent_timeout"]) {
    assert.match(reviewReasonMessage(reason), /continue the review/i)
  }
  for (const reason of ["invalid_credentials", "invalid_model", "provider_invalid_request", "run_budget_exhausted"]) {
    assert.notEqual(reviewReasonMessage(reason), reason)
  }
  assert.equal(reviewReasonMessage("unknown_reason"), "unknown_reason")
  assert.equal(reviewReasonMessage("x".repeat(201)).length, 200)
})
