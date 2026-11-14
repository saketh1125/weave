export type StatusTone = "success" | "danger" | "warning" | "info" | "neutral"

export function statusTone(status: string): StatusTone {
  const value = status.trim().toLowerCase().replace(/[\s-]+/g, "_")
  if (["passed", "completed", "complete", "ready", "supported", "aligned", "verified", "configured", "success", "healthy", "generated", "published", "low"].includes(value)) return "success"
  if (["failed", "error", "conflicting", "outdated", "critical", "high"].includes(value)) return "danger"
  if (["paused", "rate_limited", "unverified", "insufficient", "partial", "incomplete", "stale", "medium", "unavailable", "authentication_required"].includes(value)) return "warning"
  if (["running", "analyzing", "scanning", "indexing", "pending", "queued", "publishing", "thinking"].includes(value)) return "info"
  return "neutral"
}

export function statusLabel(status: string): string {
  const label = status.trim().replace(/_/g, " ")
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : "Unknown"
}
