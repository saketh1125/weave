export type DocsResult = {
  status: string
  count?: number
  generated?: number
  retry_after_seconds?: number
  docs?: Array<{
    chunk_id: string
    doc?: {
      summary?: string
      key_behaviors?: string[]
      coverage?: { source_characters: number; processed_characters: number; sections_processed: number }
    }
    cached?: boolean
    context_complete?: boolean
    error?: string
  }>
  reason?: string
  error?: string
}

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
const optionalString = (value: unknown) => value === undefined || typeof value === "string"
const optionalBoolean = (value: unknown) => value === undefined || typeof value === "boolean"
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0
export const INVALID_DOCS_RESPONSE = "Documentation returned an unsupported format. Update the API and regenerate documentation."
const MAX_RETRY_SECONDS = 3600

/** Convert a server retry interval to a bounded deadline that survives route changes. */
export function docsRetryDeadline(now: number, retryAfterSeconds: number | undefined): number | undefined {
  if (!Number.isSafeInteger(now) || now <= 0 || !Number.isSafeInteger(retryAfterSeconds) || retryAfterSeconds === undefined || retryAfterSeconds <= 0) return undefined
  const deadline = now + Math.min(retryAfterSeconds, MAX_RETRY_SECONDS) * 1000
  return Number.isSafeInteger(deadline) ? deadline : undefined
}

/** Return a safe countdown, avoiding invalid clocks and oversized browser timers. */
export function docsRetryRemainingSeconds(deadline: number | undefined, now: number): number {
  if (!Number.isSafeInteger(deadline) || deadline === undefined || deadline <= 0 || !Number.isSafeInteger(now) || now <= 0) return 0
  return Math.min(MAX_RETRY_SECONDS, Math.max(0, Math.ceil((deadline - now) / 1000)))
}

/** Validate untrusted JSON before it becomes renderable workspace state. */
export function parseDocsResult(value: unknown): DocsResult {
  const validDoc = (item: unknown): boolean => {
    if (!object(item) || typeof item.chunk_id !== "string" || !optionalBoolean(item.cached) ||
        !optionalBoolean(item.context_complete) || !optionalString(item.error)) return false
    if (item.doc === undefined) return true
    if (!object(item.doc) || !optionalString(item.doc.summary) ||
        (item.doc.key_behaviors !== undefined && (!Array.isArray(item.doc.key_behaviors) ||
          !item.doc.key_behaviors.every((behavior: unknown) => typeof behavior === "string")))) return false
    const coverage = item.doc.coverage
    return coverage === undefined || (object(coverage) && count(coverage.source_characters) &&
      count(coverage.processed_characters) && count(coverage.sections_processed))
  }
  if (!object(value) || typeof value.status !== "string" || !optionalString(value.reason) ||
      !optionalString(value.error) || (value.count !== undefined && !count(value.count)) ||
      (value.generated !== undefined && !count(value.generated)) ||
      (value.retry_after_seconds !== undefined && !count(value.retry_after_seconds)) ||
      (value.docs !== undefined && (!Array.isArray(value.docs) || !value.docs.every(validDoc)))) {
    throw new Error(INVALID_DOCS_RESPONSE)
  }
  return value as DocsResult
}

/** Workspace state can outlive a component reload, so validate it as well. */
export function readStoredDocs(value: unknown, repoId: string | null): { result: DocsResult | null; retryUntil: number | undefined; invalid: boolean } {
  if (!object(value) || value.repoId !== repoId || repoId === null) return { result: null, retryUntil: undefined, invalid: false }
  try {
    const result = parseDocsResult(value.result)
    const retryUntil = Number.isSafeInteger(value.retryUntil) && (value.retryUntil as number) > 0 ? value.retryUntil as number : undefined
    return { result, retryUntil, invalid: false }
  } catch {
    return { result: null, retryUntil: undefined, invalid: true }
  }
}
