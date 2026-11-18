export type DocumentationCitation = { file: string; start_line: number; end_line: number }
export type DocumentationAssessment = {
  id: string
  verdict: "conflicting" | "supported" | "insufficient"
  claim: string
  explanation: string
  suggested_update: string | null
  doc: DocumentationCitation
  code_citations: DocumentationCitation[]
}
export type DocumentationSourceCoverage = {
  status: "available" | "partial" | "unavailable"
  reason: string | null
  unavailable_paths: { path: string; reason: string }[]
  supplemented_paths: string[]
  missing_indexed_markdown: string[]
}
export type NativeReviewProgress = {
  phase: "static_checks" | "planning" | "reviewing" | "paused" | "completed" | "failed"
  files_total: number
  files_completed: number
  files_pending: number
  files_failed: number
  claims_assessed: number
  agents: { reviewer: number; files: { path: string; status: "pending" | "running" | "completed" | "failed"; claims_assessed: number }[]; mcp_activity?: { steps: number; errors: number; claims_recorded: number; updated_at: number } }[]
  triage: { reviewer: number; status: "pending" | "running" | "completed" | "failed"; findings_total: number; findings_processed: number } | null
  runtime?: { planner: NativeRuntimeActivity | null; agents: { reviewer: number; activity: NativeRuntimeActivity }[] }
}
export type NativeRuntimeActivity = {
  events: number
  last_event_at: number | null
  phase: "starting" | "responding" | "tool_running" | "waiting" | "finished"
  prompt_turns: number
  last_stop_reason: "end_turn" | "max_tokens" | "refusal" | "cancelled" | "max_turn_requests" | "unknown" | null
  permission_requests?: number
  permission_denials?: number
}
export type StaticReview = {
  version: 1
  fingerprint: string
  items: { id: string; status: "aligned" | "outdated" | "unverified"; claim_type: string; claim: string; message: string; doc: DocumentationCitation; code: DocumentationCitation | null; context?: Record<string, unknown> }[]
  counts: { aligned: number; outdated: number; unverified: number }
}
export type DocumentationMergedFinding = {
  claim: string
  doc: DocumentationCitation
  evidence: ({ origin: "static"; finding: StaticReview["items"][number] } | { origin: "ai"; finding: DocumentationAssessment })[]
}
export type NativeReviewCost = {
  prompt_calls: number
  model_calls: number | null
  input_tokens: number | null
  output_tokens: number | null
  elapsed_seconds: number
  budget: { max_prompt_calls: number; deadline_seconds: number }
  incomplete: boolean
}
export type DocumentationReview = {
  activity: { path: string; start_line: number; end_line: number; steps: number; errors: number; claims_recorded: number; phase: "investigating" | "paused"; updated_at: number } | null
  plan: { reviewer_count: number; assignments: { reviewer: number; task_ids: string[] }[] } | null
  native_progress: NativeReviewProgress | null
  static_review: StaticReview | null
  merged_findings: DocumentationMergedFinding[] | null
  cost: NativeReviewCost | null
  run_id: string
  job_id: string | null
  commit_sha: string
  status: "queued" | "running" | "paused" | "completed" | "failed" | "stale"
  model: string
  coverage: { files_total: number; sections_total: number; sections_completed: number; sections_pending: number; sections_failed: number; claims_assessed: number; unreadable_files: string[]; scope: string }
  source_coverage: DocumentationSourceCoverage | null
  assessments: DocumentationAssessment[]
  reason: string | null
  retry_after_seconds?: number
}

const INVALID_REVIEW = "Documentation review returned an unsupported response. Refresh and try again."
const MAX_TEXT = 20_000
const MAX_ITEMS = 2_000
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)
const text = (value: unknown, max = MAX_TEXT): value is string => typeof value === "string" && value.length <= max
const nonnegativeInt = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0
const finiteNonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0

function parseCitation(value: unknown): DocumentationCitation {
  if (!object(value) || !text(value.file, 2000) || !value.file.trim() || !nonnegativeInt(value.start_line) || value.start_line < 1 ||
    !nonnegativeInt(value.end_line) || value.end_line < value.start_line) throw new Error(INVALID_REVIEW)
  return { file: value.file, start_line: value.start_line, end_line: value.end_line }
}

function parseSourceCoverage(value: unknown): DocumentationSourceCoverage | null {
  if (value === undefined || value === null) return null
  if (!object(value) || !["available", "partial", "unavailable"].includes(String(value.status)) ||
    !(value.reason === null || text(value.reason, 5000)) || !Array.isArray(value.unavailable_paths) || value.unavailable_paths.length > MAX_ITEMS ||
    !value.unavailable_paths.every(item => object(item) && text(item.path, 2000) && !!item.path.trim() && text(item.reason, 5000)) ||
    !Array.isArray(value.supplemented_paths) || value.supplemented_paths.length > MAX_ITEMS ||
    !value.supplemented_paths.every(path => text(path, 2000) && !!path.trim()) ||
    !Array.isArray(value.missing_indexed_markdown) || value.missing_indexed_markdown.length > MAX_ITEMS ||
    !value.missing_indexed_markdown.every(path => text(path, 2000) && !!path.trim())) throw new Error(INVALID_REVIEW)
  return {
    status: value.status as DocumentationSourceCoverage["status"],
    reason: value.reason as string | null,
    unavailable_paths: value.unavailable_paths.map(item => ({ path: (item as Record<string, string>).path, reason: (item as Record<string, string>).reason })),
    supplemented_paths: [...value.supplemented_paths] as string[],
    missing_indexed_markdown: [...value.missing_indexed_markdown] as string[],
  }
}

function parseNativeProgress(value: unknown): NativeReviewProgress | null {
  if (value === undefined || value === null) return null
  if (!object(value) || !["static_checks", "planning", "reviewing", "paused", "completed", "failed"].includes(String(value.phase)) ||
    !["files_total", "files_completed", "files_pending", "files_failed", "claims_assessed"].every(key => nonnegativeInt(value[key])) ||
    !Array.isArray(value.agents) || value.agents.length > 5) throw new Error(INVALID_REVIEW)
  const files = new Set<string>()
  let completed = 0, pending = 0, failed = 0, claims = 0
  const agents = value.agents.map((agent, index) => {
    if (!object(agent) || agent.reviewer !== index + 1 || !Array.isArray(agent.files) || agent.files.length > MAX_ITEMS) throw new Error(INVALID_REVIEW)
    const parsedFiles = agent.files.map(file => {
      if (!object(file) || !text(file.path, 2000) || !file.path.trim() ||
        !["pending", "running", "completed", "failed"].includes(String(file.status)) || !nonnegativeInt(file.claims_assessed) || files.has(file.path)) throw new Error(INVALID_REVIEW)
      files.add(file.path)
      if (file.status === "completed") completed++
      else if (file.status === "failed") failed++
      else pending++
      claims += file.claims_assessed
      return { path: file.path, status: file.status as NativeReviewProgress["agents"][number]["files"][number]["status"], claims_assessed: file.claims_assessed }
    })
    let mcp_activity: NativeReviewProgress["agents"][number]["mcp_activity"]
    if (agent.mcp_activity !== undefined) {
      const item = agent.mcp_activity
      if (!object(item) || !nonnegativeInt(item.steps) || item.steps > 1_000_000_000 || !nonnegativeInt(item.errors) || item.errors > 1_000_000_000 ||
        !nonnegativeInt(item.claims_recorded) || item.claims_recorded > MAX_ITEMS || !finiteNonnegative(item.updated_at) || item.updated_at > 8_640_000_000) throw new Error(INVALID_REVIEW)
      mcp_activity = { steps: item.steps, errors: item.errors, claims_recorded: item.claims_recorded, updated_at: item.updated_at }
    }
    return { reviewer: agent.reviewer, files: parsedFiles, ...(mcp_activity === undefined ? {} : { mcp_activity }) }
  })
  let triage: NativeReviewProgress["triage"] = null
  if (value.triage !== undefined && value.triage !== null) {
    const item = value.triage
    if (!object(item) || !nonnegativeInt(item.reviewer) || item.reviewer < 1 || item.reviewer > 5 ||
      !["pending", "running", "completed", "failed"].includes(String(item.status)) ||
      !nonnegativeInt(item.findings_total) || !nonnegativeInt(item.findings_processed) || item.findings_processed > item.findings_total ||
      agents.length >= 5 || agents.some(agent => agent.reviewer === item.reviewer)) throw new Error(INVALID_REVIEW)
    triage = { reviewer: item.reviewer, status: item.status as NonNullable<NativeReviewProgress["triage"]>["status"],
      findings_total: item.findings_total, findings_processed: item.findings_processed }
  }
  const unassigned = agents.length === 0
  const emptyPhase = value.phase === "static_checks" || value.phase === "planning" || value.phase === "paused" || value.phase === "failed" || (value.phase === "completed" && value.files_total === 0)
  if (unassigned
    ? !emptyPhase || triage !== null || value.files_completed !== 0 || value.files_failed !== 0 || value.files_pending !== value.files_total || value.claims_assessed !== 0
    : value.files_completed !== completed || value.files_pending !== pending || value.files_failed !== failed || value.files_total !== files.size || value.claims_assessed !== claims) throw new Error(INVALID_REVIEW)
  let runtime: NativeReviewProgress["runtime"]
  if (value.runtime !== undefined) {
    const runtimeValue = value.runtime
    const parseActivity = (item: unknown): NativeRuntimeActivity => {
      if (!object(item) || !nonnegativeInt(item.events) || item.events > 1_000_000_000 ||
        !(item.last_event_at === null || finiteNonnegative(item.last_event_at) && item.last_event_at <= 8_640_000_000) ||
        !["starting", "responding", "tool_running", "waiting", "finished"].includes(String(item.phase)) ||
        !nonnegativeInt(item.prompt_turns) || item.prompt_turns > 100 ||
        !(item.last_stop_reason === null || ["end_turn", "max_tokens", "refusal", "cancelled", "max_turn_requests", "unknown"].includes(String(item.last_stop_reason)))) throw new Error(INVALID_REVIEW)
      if ((item.permission_requests !== undefined && (!nonnegativeInt(item.permission_requests) || item.permission_requests > 1_000_000_000)) ||
        (item.permission_denials !== undefined && (!nonnegativeInt(item.permission_denials) || item.permission_denials > 1_000_000_000))) throw new Error(INVALID_REVIEW)
      return { events: item.events, last_event_at: item.last_event_at, phase: item.phase as NativeRuntimeActivity["phase"],
        prompt_turns: item.prompt_turns, last_stop_reason: item.last_stop_reason as NativeRuntimeActivity["last_stop_reason"],
        ...(item.permission_requests === undefined ? {} : { permission_requests: item.permission_requests }),
        ...(item.permission_denials === undefined ? {} : { permission_denials: item.permission_denials }) }
    }
    if (!object(runtimeValue) || !(runtimeValue.planner === null || object(runtimeValue.planner)) ||
      !Array.isArray(runtimeValue.agents) || runtimeValue.agents.length > 5) throw new Error(INVALID_REVIEW)
    const runtimeAgentsValue = runtimeValue.agents
    const runtimeAgents = runtimeAgentsValue.map((item, index) => {
      if (!object(item) || !nonnegativeInt(item.reviewer) || item.reviewer < 1 || item.reviewer > 5 ||
        runtimeAgentsValue.slice(0, index).some((previous: unknown) => object(previous) && previous.reviewer === item.reviewer)) throw new Error(INVALID_REVIEW)
      return { reviewer: item.reviewer, activity: parseActivity(item.activity) }
    })
    runtime = { planner: runtimeValue.planner === null ? null : parseActivity(runtimeValue.planner), agents: runtimeAgents }
  }
  return { phase: value.phase as NativeReviewProgress["phase"], files_total: value.files_total as number, files_completed: value.files_completed as number,
    files_pending: value.files_pending as number, files_failed: value.files_failed as number, claims_assessed: value.claims_assessed as number, agents, triage,
    ...(runtime === undefined ? {} : { runtime }) }
}

function parseAssessment(value: unknown): DocumentationAssessment {
  if (!object(value) || !text(value.id, 500) || !value.id || !["conflicting", "supported", "insufficient"].includes(String(value.verdict)) ||
    !text(value.claim) || !text(value.explanation) || !(value.suggested_update === null || text(value.suggested_update)) ||
    !Array.isArray(value.code_citations) || value.code_citations.length > MAX_ITEMS) throw new Error(INVALID_REVIEW)
  return { id: value.id, verdict: value.verdict as DocumentationAssessment["verdict"], claim: value.claim, explanation: value.explanation,
    suggested_update: value.suggested_update as string | null, doc: parseCitation(value.doc), code_citations: value.code_citations.map(parseCitation) }
}

function parseStaticReviewItem(value: unknown): StaticReview["items"][number] {
  if (!object(value) || !text(value.id, 64) || !/^[0-9a-f]{64}$/i.test(value.id) ||
    !["aligned", "outdated", "unverified"].includes(String(value.status)) || !text(value.claim_type, 200) || !text(value.claim) || !text(value.message) ||
    !(value.code === null || object(value.code)) || (value.context !== undefined && !object(value.context))) throw new Error(INVALID_REVIEW)
  return { id: value.id, status: value.status as StaticReview["items"][number]["status"], claim_type: value.claim_type,
    claim: value.claim, message: value.message, doc: parseCitation(value.doc), code: value.code === null ? null : parseCitation(value.code),
    ...(value.context === undefined ? {} : { context: value.context }) }
}

function parseStaticReview(value: unknown): StaticReview | null {
  if (value === undefined || value === null) return null
  if (!object(value)) throw new Error(INVALID_REVIEW)
  const countsValue = value.counts
  if (value.version !== 1 || !text(value.fingerprint, 64) || !/^[0-9a-f]{64}$/i.test(value.fingerprint) ||
    !Array.isArray(value.items) || value.items.length > MAX_ITEMS || !object(countsValue)) throw new Error(INVALID_REVIEW)
  if (!["aligned", "outdated", "unverified"].every(key => nonnegativeInt(countsValue[key]))) throw new Error(INVALID_REVIEW)
  const ids = new Set<string>()
  let aligned = 0, outdated = 0, unverified = 0
  const items = value.items.map(item => {
    const parsed = parseStaticReviewItem(item)
    if (ids.has(parsed.id)) throw new Error(INVALID_REVIEW)
    ids.add(parsed.id)
    if (parsed.status === "aligned") aligned++
    else if (parsed.status === "outdated") outdated++
    else unverified++
    return parsed
  })
  const counts = countsValue as Record<string, unknown>
  if (counts.aligned !== aligned || counts.outdated !== outdated || counts.unverified !== unverified) throw new Error(INVALID_REVIEW)
  return { version: 1, fingerprint: value.fingerprint, items,
    counts: { aligned, outdated, unverified } }
}

function parseMergedFindings(value: unknown, staticReview: StaticReview | null, assessments: DocumentationAssessment[]): DocumentationMergedFinding[] | null {
  if (value === undefined || value === null) return null
  if (!Array.isArray(value) || value.length > MAX_ITEMS) throw new Error(INVALID_REVIEW)
  const staticById = new Map((staticReview?.items ?? []).map(item => [item.id, item]))
  const aiById = new Map(assessments.map(item => [item.id, item]))
  if (aiById.size !== assessments.length) throw new Error(INVALID_REVIEW)
  const seen = new Set<string>(), groups = new Set<string>()
  const normalizeClaim = (claim: string) => claim.trim().split(/\s+/).join(" ")
  const sameCitation = (left: DocumentationCitation, right: DocumentationCitation) =>
    left.file === right.file && left.start_line === right.start_line && left.end_line === right.end_line
  const result = value.map(group => {
    if (!object(group) || !text(group.claim) || !Array.isArray(group.evidence) || group.evidence.length === 0 || group.evidence.length > MAX_ITEMS) throw new Error(INVALID_REVIEW)
    const groupClaim = group.claim as string
    const doc = parseCitation(group.doc)
    const key = JSON.stringify([normalizeClaim(groupClaim), doc.file, doc.start_line, doc.end_line])
    if (groups.has(key)) throw new Error(INVALID_REVIEW)
    groups.add(key)
    const evidence = group.evidence.map(entry => {
      if (!object(entry) || (entry.origin !== "static" && entry.origin !== "ai")) throw new Error(INVALID_REVIEW)
      if (entry.origin === "static") {
        const parsed = parseStaticReviewItem(entry.finding)
        const known = staticById.get(parsed.id)
        const evidenceKey = `static:${parsed.id}`
        if (!known || seen.has(evidenceKey) || normalizeClaim(groupClaim) !== normalizeClaim(parsed.claim) || !sameCitation(doc, parsed.doc)) throw new Error(INVALID_REVIEW)
        seen.add(evidenceKey)
        return { origin: "static" as const, finding: known }
      }
      const parsed = parseAssessment(entry.finding)
      const known = aiById.get(parsed.id)
      const evidenceKey = `ai:${parsed.id}`
      if (!known || seen.has(evidenceKey) || normalizeClaim(groupClaim) !== normalizeClaim(parsed.claim) || !sameCitation(doc, parsed.doc)) throw new Error(INVALID_REVIEW)
      seen.add(evidenceKey)
      return { origin: "ai" as const, finding: known }
    })
    return { claim: groupClaim, doc, evidence }
  })
  if (seen.size !== staticById.size + aiById.size) throw new Error(INVALID_REVIEW)
  return result
}

function parseNativeReviewCost(value: unknown): NativeReviewCost | null {
  if (value === undefined || value === null) return null
  const optionalCount = (item: unknown): item is number | null => item === null || nonnegativeInt(item)
  if (!object(value) || !nonnegativeInt(value.prompt_calls) || !optionalCount(value.model_calls) || !optionalCount(value.input_tokens) ||
    !optionalCount(value.output_tokens) || !finiteNonnegative(value.elapsed_seconds) || typeof value.incomplete !== "boolean" || !object(value.budget) ||
    !nonnegativeInt(value.budget.max_prompt_calls) || !finiteNonnegative(value.budget.deadline_seconds)) throw new Error(INVALID_REVIEW)
  return { prompt_calls: value.prompt_calls, model_calls: value.model_calls, input_tokens: value.input_tokens, output_tokens: value.output_tokens,
    elapsed_seconds: value.elapsed_seconds, budget: { max_prompt_calls: value.budget.max_prompt_calls, deadline_seconds: value.budget.deadline_seconds }, incomplete: value.incomplete }
}

/** Validate API and persisted review data before rendering it. */
export function parseDocumentationReview(value: unknown): DocumentationReview {
  const coverageValue = object(value) ? value.coverage : undefined
  const sourceCoverageValue = object(value) ? value.source_coverage : undefined
  const planValue = object(value) ? value.plan : undefined
  const nativeProgressValue = object(value) ? value.native_progress : undefined
  const staticReviewValue = object(value) ? value.static_review : undefined
  const mergedFindingsValue = object(value) ? value.merged_findings : undefined
  const costValue = object(value) ? value.cost : undefined
  const activityValue = object(value) ? value.activity : undefined
  if (!object(value) || !text(value.run_id, 500) || !value.run_id || !(value.job_id === null || (text(value.job_id, 500) && value.job_id.length > 0)) ||
    !text(value.commit_sha, 200) || !text(value.model, 200) ||
    !["queued", "running", "paused", "completed", "failed", "stale"].includes(String(value.status)) ||
    !(activityValue === undefined || activityValue === null || (object(activityValue) && text(activityValue.path, 2000) && !!activityValue.path.trim() &&
      nonnegativeInt(activityValue.start_line) && activityValue.start_line >= 1 && nonnegativeInt(activityValue.end_line) && activityValue.end_line >= activityValue.start_line &&
      nonnegativeInt(activityValue.steps) && activityValue.steps <= 64 && nonnegativeInt(activityValue.errors) && activityValue.errors <= 10000 && nonnegativeInt(activityValue.claims_recorded) && activityValue.claims_recorded <= 30 &&
      (activityValue.phase === "investigating" || activityValue.phase === "paused") && typeof activityValue.updated_at === "number" &&
      Number.isFinite(activityValue.updated_at) && activityValue.updated_at >= 0 && activityValue.updated_at <= 8_640_000_000)) ||
    !(planValue === undefined || planValue === null || (object(planValue) && nonnegativeInt(planValue.reviewer_count) &&
      Array.isArray(planValue.assignments) && planValue.assignments.length <= 2 &&
      planValue.assignments.every((assignment, index) => object(assignment) && assignment.reviewer === index + 1 &&
        Array.isArray(assignment.task_ids) && assignment.task_ids.length <= MAX_ITEMS && assignment.task_ids.every(id => text(id, 500) && id.length > 0)) &&
      planValue.reviewer_count === planValue.assignments.length)) ||
    !object(coverageValue) || !["files_total", "sections_total", "sections_completed", "sections_pending", "sections_failed"].every(key => nonnegativeInt(coverageValue[key])) ||
    !nonnegativeInt(coverageValue.claims_assessed) || !Array.isArray(coverageValue.unreadable_files) || coverageValue.unreadable_files.length > MAX_ITEMS ||
    !coverageValue.unreadable_files.every(path => text(path, 2000)) || !text(coverageValue.scope, 5000) ||
    !Array.isArray(value.assessments) || value.assessments.length > MAX_ITEMS || !(value.reason === null || text(value.reason, 5000)) ||
    (value.retry_after_seconds !== undefined && value.retry_after_seconds !== null && !nonnegativeInt(value.retry_after_seconds))) throw new Error(INVALID_REVIEW)
  const assessments = value.assessments.map(parseAssessment)
  const coverage = coverageValue as Record<string, unknown> & Record<string, number>
  if (coverage.sections_completed + coverage.sections_pending + coverage.sections_failed !== coverage.sections_total || coverage.claims_assessed !== assessments.length) throw new Error(INVALID_REVIEW)
  const plan = object(planValue) ? { reviewer_count: planValue.reviewer_count as number, assignments: (planValue.assignments as Record<string, unknown>[]).map(item => ({ reviewer: item.reviewer as number, task_ids: item.task_ids as string[] })) } : null
  const source_coverage = parseSourceCoverage(sourceCoverageValue)
  const native_progress = parseNativeProgress(nativeProgressValue)
  const static_review = parseStaticReview(staticReviewValue)
  const merged_findings = parseMergedFindings(mergedFindingsValue, static_review, assessments)
  const cost = parseNativeReviewCost(costValue)
  if (native_progress && (native_progress.claims_assessed !== assessments.length || native_progress.claims_assessed !== coverage.claims_assessed)) throw new Error(INVALID_REVIEW)
  return { plan, native_progress, static_review, merged_findings, cost, activity: (activityValue ?? null) as DocumentationReview["activity"], run_id: value.run_id, job_id: value.job_id as string | null, commit_sha: value.commit_sha, status: value.status as DocumentationReview["status"], model: value.model,
    coverage: { files_total: coverage.files_total, sections_total: coverage.sections_total, sections_completed: coverage.sections_completed, sections_pending: coverage.sections_pending, sections_failed: coverage.sections_failed,
      claims_assessed: coverage.claims_assessed, unreadable_files: coverageValue.unreadable_files as string[], scope: coverageValue.scope as string },
    source_coverage, assessments, reason: value.reason as string | null, ...(value.retry_after_seconds == null ? {} : { retry_after_seconds: value.retry_after_seconds as number }) }
}

export function parseLatestDocumentationReview(value: unknown): DocumentationReview | null {
  return value === null ? null : parseDocumentationReview(value)
}

export function canCreateDocumentationDraft(review: DocumentationReview | null): boolean {
  return !!review && (review.status === "completed" || review.status === "paused") &&
    (review.assessments.some(item => item.verdict === "conflicting") ||
      !!review.static_review?.items.some(item => item.status === "outdated" && item.code !== null))
}

export const INVALID_DOCUMENTATION_REVIEW = INVALID_REVIEW
const MAX_COOLDOWN_SECONDS = 3600
export function reviewRetryDeadline(now: number, seconds: number | undefined): number | undefined {
  if (!Number.isSafeInteger(now) || now <= 0 || !Number.isSafeInteger(seconds) || seconds === undefined || seconds <= 0) return undefined
  const deadline = now + Math.min(seconds, MAX_COOLDOWN_SECONDS) * 1000
  return Number.isSafeInteger(deadline) ? deadline : undefined
}
export function reviewRetryRemaining(deadline: number | undefined, now: number): number {
  if (!Number.isSafeInteger(deadline) || deadline === undefined || deadline <= 0 || !Number.isSafeInteger(now) || now <= 0) return 0
  return Math.min(MAX_COOLDOWN_SECONDS, Math.max(0, Math.ceil((deadline - now) / 1000)))
}

export function reviewReasonMessage(reason: string): string {
  switch (reason) {
    case "model_access_denied": return "The configured model is not available to this account. Choose an accessible configured model or check the account's model entitlement, then start a new review."
    case "provider_access_denied": return "The provider denied access. Check the account's entitlement or choose an accessible configured model, then start a new review."
    case "invalid_credentials": return "The configured credentials were rejected. Check the connection credentials, then start a new review."
    case "invalid_model": return "The configured model was not found or is invalid. Choose an accessible configured model, then start a new review."
    case "provider_invalid_request": return "The provider rejected the review request. Check the model connection configuration before starting a new review."
    case "provider_unavailable": return "The provider is temporarily unavailable. Continue the review when it is available."
    case "timeout": return "The provider did not respond in time. Continue the review to retry."
    case "provider_connection_failed": return "The provider connection failed. Check the connection and continue the review to retry."
    case "run_budget_exhausted": return "The review reached its run limit before all sections were completed."
    case "agent_timeout": return "The native workflow reached its time limit. Saved findings are retained. Inspect the last activity, then continue the review."
    case "agent_model_limit_reported": return "The native runtime reported a model usage limit before making further review-tool progress. Saved findings are retained. Check the model allowance before retrying."
    case "agent_no_progress": return "Automatic continuation stopped because it produced no new review-tool progress. Saved work is retained. Review the evidence and runtime before continuing manually."
    case "agent_refused": return "The native reviewer declined to continue. Saved findings are retained; review the evidence and runtime before continuing manually."
    case "invalid_agent_result": return "The native reviewer returned a result that could not be validated. Any previously saved findings are retained."
    case "agent_continuation_limit": return "The automatic review session limit was reached. Saved findings are retained."
    case "agent_incomplete": return "The native reviewer stopped before finishing a Markdown file. Saved findings are retained. Review the evidence and runtime before continuing manually."
    case "native_prompt_budget_exhausted": return "The native review reached its prompt-turn budget before all files were complete. Saved findings are retained."
    case "agent_cleanup_failed": return "A native review agent could not be shut down cleanly, so the review stopped. Saved findings are retained. Check the agent runtime before continuing."
    case "workflow_replaced": return "This review was stopped because the documentation review workflow changed. Start a new review to use the updated workflow."
    default: return "The review stopped for an unrecognized reason. Saved findings are retained; check the review status and runtime before continuing."
  }
}
