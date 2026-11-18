import { strict as assert } from "node:assert"
import { test } from "node:test"
import { INVALID_DOCUMENTATION_REVIEW, canCreateDocumentationDraft, parseDocumentationReview, parseLatestDocumentationReview, reviewRetryDeadline, reviewRetryRemaining, reviewReasonMessage } from "../src/lib/documentation-review.ts"

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

test("parses native planning and per-agent file progress", () => {
  const planning = { phase: "planning", files_total: 3, files_completed: 0, files_pending: 3, files_failed: 0, claims_assessed: 0, agents: [] }
  const plannedReview = { ...valid, coverage: { ...valid.coverage, files_total: 3, sections_total: 0, sections_completed: 0, sections_pending: 0, sections_failed: 0, claims_assessed: 0 }, assessments: [], native_progress: planning }
  assert.deepEqual(parseDocumentationReview(plannedReview).native_progress, { ...planning, triage: null })
  const staticChecks = { ...planning, phase: "static_checks" }
  assert.equal(parseDocumentationReview({ ...plannedReview, status: "running", native_progress: staticChecks }).native_progress?.phase, "static_checks")
  for (const phase of ["paused", "failed"]) {
    const stopped = { ...planning, phase }
    assert.deepEqual(parseDocumentationReview({ ...plannedReview, status: phase, native_progress: stopped }).native_progress, { ...stopped, triage: null })
  }
  const empty = { phase: "completed", files_total: 0, files_completed: 0, files_pending: 0, files_failed: 0, claims_assessed: 0, agents: [] }
  assert.deepEqual(parseDocumentationReview({ ...plannedReview, status: "completed", native_progress: empty }).native_progress, { ...empty, triage: null })

  const native_progress = { phase: "reviewing", files_total: 2, files_completed: 1, files_pending: 1, files_failed: 0, claims_assessed: 1,
    agents: [{ reviewer: 1, files: [{ path: "README.md", status: "completed", claims_assessed: 1 }] },
      { reviewer: 2, files: [{ path: "docs/guide.md", status: "running", claims_assessed: 0 }] }] }
  assert.deepEqual(parseDocumentationReview({ ...valid, native_progress }).native_progress, { ...native_progress, triage: null })
  assert.equal(parseDocumentationReview(valid).native_progress, null)
})

test("parses bounded runtime activity separately from MCP tool activity", () => {
  const native_progress = { phase: "reviewing", files_total: 1, files_completed: 0, files_pending: 1, files_failed: 0, claims_assessed: 0,
    agents: [{ reviewer: 1, files: [{ path: "README.md", status: "pending", claims_assessed: 0 }],
      mcp_activity: { steps: 4, errors: 1, claims_recorded: 0, updated_at: 1_800_000_000 } }], triage: null,
    runtime: { planner: { events: 2, last_event_at: 1_800_000_000, phase: "finished", prompt_turns: 1, last_stop_reason: "end_turn" },
      agents: [{ reviewer: 1, activity: { events: 5, last_event_at: 1_800_000_001, phase: "waiting", prompt_turns: 2,
        last_stop_reason: null, permission_requests: 1, permission_denials: 1 } }] } }
  const withProgress = { ...valid, assessments: [], coverage: { ...valid.coverage, sections_total: 0, sections_completed: 0, sections_pending: 0, sections_failed: 0, claims_assessed: 0 }, native_progress }
  assert.deepEqual(parseDocumentationReview(withProgress).native_progress, native_progress)
  for (const badActivity of [
    { ...native_progress.runtime.agents[0].activity, phase: "thinking" },
    { ...native_progress.runtime.agents[0].activity, events: 1_000_000_001 },
    { ...native_progress.runtime.agents[0].activity, permission_denials: -1 },
  ]) assert.throws(() => parseDocumentationReview({ ...withProgress, native_progress: { ...native_progress, runtime: {
    ...native_progress.runtime, agents: [{ reviewer: 1, activity: badActivity }] } } }), { message: INVALID_DOCUMENTATION_REVIEW })
  assert.throws(() => parseDocumentationReview({ ...withProgress, native_progress: { ...native_progress, agents: [{
    ...native_progress.agents[0], mcp_activity: { ...native_progress.agents[0].mcp_activity, updated_at: Infinity } }] } }), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("rejects incomplete or inconsistent native file allocations", () => {
  const progress = { phase: "reviewing", files_total: 2, files_completed: 1, files_pending: 1, files_failed: 0, claims_assessed: 1,
    agents: [{ reviewer: 1, files: [{ path: "README.md", status: "completed", claims_assessed: 1 }] },
      { reviewer: 2, files: [{ path: "docs/guide.md", status: "running", claims_assessed: 0 }] }] }
  const invalid = [
    { ...progress, agents: [{ reviewer: 1, files: [{ path: "README.md", status: "completed", claims_assessed: 1 }] }] },
    { ...progress, agents: [{ reviewer: 1, files: [progress.agents[0].files[0]] }, { reviewer: 2, files: [{ ...progress.agents[1].files[0], path: "README.md" }] }] },
    { ...progress, files_pending: 0 },
    { ...progress, agents: [{ reviewer: 1, files: [{ ...progress.agents[0].files[0], status: "other" }] }, progress.agents[1]] },
    { ...progress, agents: [{ reviewer: 2, files: progress.agents[0].files }, progress.agents[1]] },
  ]
  for (const native_progress of invalid) assert.throws(() => parseDocumentationReview({ ...valid, native_progress }), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("limits file reviewer agents and a separate triage owner to five total", () => {
  const agents = [1, 2, 3, 4].map(reviewer => ({ reviewer, files: [{ path: `docs/${reviewer}.md`, status: "running", claims_assessed: 0 }] }))
  const native_progress = { phase: "reviewing", files_total: 4, files_completed: 0, files_pending: 4, files_failed: 0, claims_assessed: 0,
    agents, triage: { reviewer: 5, status: "running", findings_total: 3, findings_processed: 1 } }
  const emptyClaims = { ...valid, assessments: [], coverage: { ...valid.coverage, sections_total: 0, sections_completed: 0, sections_pending: 0, sections_failed: 0, claims_assessed: 0 }, native_progress }
  assert.equal(parseDocumentationReview(emptyClaims).native_progress?.triage?.findings_processed, 1)
  assert.throws(() => parseDocumentationReview({ ...emptyClaims, native_progress: { ...native_progress, agents: [...agents, { reviewer: 5, files: [] }], triage: { ...native_progress.triage, reviewer: 6 } } }), { message: INVALID_DOCUMENTATION_REVIEW })
  assert.throws(() => parseDocumentationReview({ ...emptyClaims, native_progress: { ...native_progress, triage: { ...native_progress.triage, findings_processed: 4 } } }), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("parses static review leads and requires exact status counts", () => {
  const static_review = { version: 1, fingerprint: "a".repeat(64), items: [
    { id: "1".repeat(64), status: "aligned", claim_type: "feature", claim: "Does X", message: "Text matches source", doc: { file: "README.md", start_line: 1, end_line: 2 }, code: { file: "src/x.ts", start_line: 3, end_line: 4 }, context: { rule: "same-word" } },
    { id: "2".repeat(64), status: "unverified", claim_type: "version", claim: "Uses version 2", message: "No matching version found", doc: { file: "README.md", start_line: 4, end_line: 4 }, code: null },
  ], counts: { aligned: 1, outdated: 0, unverified: 1 } }
  assert.deepEqual(parseDocumentationReview({ ...valid, static_review }).static_review, static_review)
  assert.equal(parseDocumentationReview(valid).static_review, null)
  for (const invalid of [
    { ...static_review, version: 2 }, { ...static_review, fingerprint: "bad" },
    { ...static_review, counts: { ...static_review.counts, unverified: 0 } },
    { ...static_review, items: [static_review.items[0], { ...static_review.items[1], id: static_review.items[0].id }] },
    { ...static_review, items: [{ ...static_review.items[0], status: "error" }, static_review.items[1]] },
    { ...static_review, items: [{ ...static_review.items[0], code: "source snippet" }, static_review.items[1]] },
  ]) assert.throws(() => parseDocumentationReview({ ...valid, static_review: invalid }), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("merges exact claim and citation evidence while retaining distinct static matches", () => {
  const first = { id: "1".repeat(64), status: "unverified", claim_type: "feature", claim: "Uses X", message: "Static match A", doc: valid.assessments[0].doc, code: { file: "src/a.py", start_line: 4, end_line: 5 } }
  const second = { ...first, id: "2".repeat(64), status: "outdated", claim: "Uses   X", message: "Static match B", code: null }
  const static_review = { version: 1, fingerprint: "a".repeat(64), items: [first, second], counts: { aligned: 0, outdated: 1, unverified: 1 } }
  const merged_findings = [{ claim: "Uses X", doc: valid.assessments[0].doc,
    evidence: [{ origin: "static", finding: first }, { origin: "static", finding: second }, { origin: "ai", finding: valid.assessments[0] }] }]
  const parsed = parseDocumentationReview({ ...valid, static_review, merged_findings })
  assert.equal(parsed.merged_findings?.length, 1)
  assert.deepEqual(parsed.merged_findings?.[0].evidence.map(item => item.origin), ["static", "static", "ai"])
  assert.deepEqual(parsed.merged_findings?.[0].evidence.filter(item => item.origin === "static").map(item => item.finding.id), [first.id, second.id])

  const invalid = [
    [{ ...merged_findings[0], evidence: [{ origin: "static", finding: { ...first, id: "9".repeat(64) } }, { origin: "static", finding: second }, { origin: "ai", finding: valid.assessments[0] }] }],
    [{ ...merged_findings[0], claim: "Different claim" }],
    [{ ...merged_findings[0], doc: { ...merged_findings[0].doc, start_line: 7 } }],
  ]
  for (const findings of invalid) assert.throws(() => parseDocumentationReview({ ...valid, static_review, merged_findings: findings }), { message: INVALID_DOCUMENTATION_REVIEW })
})

test("parses native usage and review coverage status without inferring unknown provider or token counts", () => {
  const cost = { prompt_calls: 8, model_calls: null, input_tokens: null, output_tokens: 120, elapsed_seconds: 1.25,
    budget: { max_prompt_calls: 12, deadline_seconds: 90 }, incomplete: true }
  assert.deepEqual(parseDocumentationReview({ ...valid, cost }).cost, cost)
  assert.equal(parseDocumentationReview({ ...valid, cost: { ...cost, incomplete: false } }).cost?.incomplete, false)
  assert.equal(parseDocumentationReview(valid).cost, null)
  for (const invalid of [
    { ...cost, prompt_calls: -1 }, { ...cost, model_calls: "8" }, { ...cost, input_tokens: -1 },
    { ...cost, elapsed_seconds: Infinity }, { ...cost, budget: { ...cost.budget, deadline_seconds: -1 } },
    { ...cost, incomplete: "yes" },
  ]) assert.throws(() => parseDocumentationReview({ ...valid, cost: invalid }), { message: INVALID_DOCUMENTATION_REVIEW })
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

test("allows drafts from cited outdated static matches or conflicting model assessments only", () => {
  const review = parseDocumentationReview(valid)
  assert.equal(canCreateDocumentationDraft(review), true)
  const noConflict = parseDocumentationReview({ ...valid, assessments: [], coverage: { ...valid.coverage, sections_total: 0, sections_completed: 0, sections_pending: 0, sections_failed: 0, claims_assessed: 0 } })
  const staticItem = { id: "a".repeat(64), status: "outdated", claim_type: "feature", claim: "Claim", message: "Mismatch", doc: { file: "README.md", start_line: 1, end_line: 1 }, code: { file: "src/a.py", start_line: 2, end_line: 2 } }
  assert.equal(canCreateDocumentationDraft({ ...noConflict, static_review: { version: 1, fingerprint: "b".repeat(64), items: [staticItem], counts: { aligned: 0, outdated: 1, unverified: 0 } } }), true)
  assert.equal(canCreateDocumentationDraft({ ...noConflict, static_review: { version: 1, fingerprint: "b".repeat(64), items: [{ ...staticItem, code: null }], counts: { aligned: 0, outdated: 1, unverified: 0 } } }), false)
  assert.equal(canCreateDocumentationDraft({ ...noConflict, static_review: { version: 1, fingerprint: "b".repeat(64), items: [{ ...staticItem, status: "unverified" }], counts: { aligned: 0, outdated: 0, unverified: 1 } } }), false)
  assert.equal(canCreateDocumentationDraft({ ...noConflict, status: "failed", static_review: { version: 1, fingerprint: "b".repeat(64), items: [staticItem], counts: { aligned: 0, outdated: 1, unverified: 0 } } }), false)
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
  assert.match(reviewReasonMessage("unknown_reason"), /unrecognized reason/i)
  assert.match(reviewReasonMessage("x".repeat(201)), /unrecognized reason/i)
})

test("explains native continuation stop reasons without implying rate limits", () => {
  const noProgress = reviewReasonMessage("agent_no_progress")
  assert.match(noProgress, /no new review-tool progress/)
  assert.match(noProgress, /saved work is retained/i)
  assert.match(noProgress, /evidence and runtime/i)
  const limit = reviewReasonMessage("agent_continuation_limit")
  assert.match(limit, /automatic review session limit was reached/)
  assert.match(limit, /saved findings are retained/i)
  const incomplete = reviewReasonMessage("agent_incomplete")
  assert.match(incomplete, /native reviewer stopped before finishing a Markdown file/i)
  assert.match(incomplete, /saved findings are retained/i)
  assert.doesNotMatch(incomplete, /rate limit/i)
  assert.match(reviewReasonMessage("workflow_replaced"), /workflow changed/i)
  assert.match(reviewReasonMessage("native_prompt_budget_exhausted"), /prompt-turn budget/i)
  assert.match(reviewReasonMessage("native_prompt_budget_exhausted"), /saved findings are retained/i)
  assert.match(reviewReasonMessage("agent_cleanup_failed"), /could not be shut down cleanly/i)
  assert.match(reviewReasonMessage("agent_cleanup_failed"), /agent runtime/i)
  assert.match(reviewReasonMessage("agent_refused"), /declined to continue/i)
})


test("distinguishes a native reported model limit from a timeout", () => {
  assert.match(reviewReasonMessage("agent_model_limit_reported"), /runtime reported a model usage limit/)
})
