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
export type DocumentationReview = {
  activity: { path: string; start_line: number; end_line: number; steps: number; errors: number; claims_recorded: number; phase: "investigating" | "paused"; updated_at: number } | null
  plan: { reviewer_count: number; assignments: { reviewer: number; task_ids: string[] }[] } | null
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

/** Validate API and persisted review data before rendering it. */
export function parseDocumentationReview(value: unknown): DocumentationReview {
  const coverageValue = object(value) ? value.coverage : undefined
  const sourceCoverageValue = object(value) ? value.source_coverage : undefined
  const planValue = object(value) ? value.plan : undefined
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
  const assessments = value.assessments.map(item => {
    if (!object(item) || !text(item.id, 500) || !item.id || !["conflicting", "supported", "insufficient"].includes(String(item.verdict)) ||
      !text(item.claim) || !text(item.explanation) || !(item.suggested_update === null || text(item.suggested_update)) ||
      !Array.isArray(item.code_citations) || item.code_citations.length > MAX_ITEMS) throw new Error(INVALID_REVIEW)
    return { id: item.id, verdict: item.verdict as DocumentationAssessment["verdict"], claim: item.claim, explanation: item.explanation,
      suggested_update: item.suggested_update as string | null, doc: parseCitation(item.doc), code_citations: item.code_citations.map(parseCitation) }
  })
  const coverage = coverageValue as Record<string, unknown> & Record<string, number>
  if (coverage.sections_completed + coverage.sections_pending + coverage.sections_failed !== coverage.sections_total || coverage.claims_assessed !== assessments.length) throw new Error(INVALID_REVIEW)
  const plan = object(planValue) ? { reviewer_count: planValue.reviewer_count as number, assignments: (planValue.assignments as Record<string, unknown>[]).map(item => ({ reviewer: item.reviewer as number, task_ids: item.task_ids as string[] })) } : null
  const source_coverage = parseSourceCoverage(sourceCoverageValue)
  return { plan, activity: (activityValue ?? null) as DocumentationReview["activity"], run_id: value.run_id, job_id: value.job_id as string | null, commit_sha: value.commit_sha, status: value.status as DocumentationReview["status"], model: value.model,
    coverage: { files_total: coverage.files_total, sections_total: coverage.sections_total, sections_completed: coverage.sections_completed, sections_pending: coverage.sections_pending, sections_failed: coverage.sections_failed,
      claims_assessed: coverage.claims_assessed, unreadable_files: coverageValue.unreadable_files as string[], scope: coverageValue.scope as string },
    source_coverage, assessments, reason: value.reason as string | null, ...(value.retry_after_seconds == null ? {} : { retry_after_seconds: value.retry_after_seconds as number }) }
}

export function parseLatestDocumentationReview(value: unknown): DocumentationReview | null {
  return value === null ? null : parseDocumentationReview(value)
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
