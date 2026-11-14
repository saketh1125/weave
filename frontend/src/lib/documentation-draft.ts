export type DocumentationDraftFile = { path: string; old_hash: string; new_hash: string; original: string; content: string }
export type DocumentationDraft = {
  id: string
  review_id: string
  base_commit: string
  draft_hash: string
  status: "draft" | "publishing" | "published" | "stale"
  diff: string
  files: DocumentationDraftFile[]
  branch: string | null
  pr_url: string | null
}

export const INVALID_DOCUMENTATION_DRAFT = "Documentation draft returned an unsupported response. Refresh and try again."
const MAX_TEXT = 2_000_000
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)
const str = (value: unknown, max = MAX_TEXT): value is string => typeof value === "string" && value.length <= max

export function parseDocumentationDraft(value: unknown): DocumentationDraft {
  if (!object(value) || !str(value.id, 500) || !value.id || !str(value.review_id, 500) || !value.review_id ||
      !str(value.base_commit, 40) || !/^[0-9a-f]{40}$/.test(value.base_commit) ||
      !str(value.draft_hash, 64) || !/^[0-9a-f]{64}$/.test(value.draft_hash) ||
      !["draft", "publishing", "published"].includes(String(value.status)) || !str(value.diff) ||
      !Array.isArray(value.files) || value.files.length > 100 || !value.files.length ||
      !(value.branch === null || str(value.branch, 500)) || !(value.pr_url === null || str(value.pr_url, 2000))) throw new Error(INVALID_DOCUMENTATION_DRAFT)
  const files = value.files.map(file => {
    if (!object(file) || !str(file.path, 2000) || !file.path ||
        !str(file.old_hash, 64) || !/^[0-9a-f]{64}$/.test(file.old_hash) ||
        !str(file.new_hash, 64) || !/^[0-9a-f]{64}$/.test(file.new_hash) ||
        !str(file.original) || !str(file.content)) throw new Error(INVALID_DOCUMENTATION_DRAFT)
    return { path: file.path, old_hash: file.old_hash, new_hash: file.new_hash, original: file.original, content: file.content }
  })
  let prUrl: URL | null = null
  if (value.pr_url !== null) {
    try { prUrl = new URL(value.pr_url as string) } catch { throw new Error(INVALID_DOCUMENTATION_DRAFT) }
    if (prUrl.protocol !== "https:" || prUrl.hostname !== "github.com" || prUrl.username || prUrl.password) throw new Error(INVALID_DOCUMENTATION_DRAFT)
  }
  if (value.status === "published" && (!value.branch || !prUrl)) throw new Error(INVALID_DOCUMENTATION_DRAFT)
  return { id: value.id, review_id: value.review_id, base_commit: value.base_commit, draft_hash: value.draft_hash,
    status: value.status as DocumentationDraft["status"], diff: value.diff, files, branch: value.branch as string | null, pr_url: value.pr_url as string | null }
}

export function parseLatestDocumentationDraft(value: unknown): DocumentationDraft | null {
  return value === null ? null : parseDocumentationDraft(value)
}
