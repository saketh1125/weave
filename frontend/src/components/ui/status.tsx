import type { ReactNode } from "react"
import { Check, CircleDashed, Clock3, Cpu, Layers3, TriangleAlert, X } from "lucide-react"
import { statusLabel, statusTone } from "@/lib/status"

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const tone = statusTone(status)
  const toneClasses = { success: "status-success", danger: "status-danger", warning: "status-warning", info: "status-info", neutral: "status-neutral" }
  const Icon = tone === "success" ? Check : tone === "danger" ? X : tone === "warning" ? TriangleAlert : tone === "info" ? Clock3 : CircleDashed
  return <span className={`status-badge ${toneClasses[tone]}`} title={label ?? statusLabel(status)}><Icon size={13} aria-hidden="true" /><span>{label ?? statusLabel(status)}</span></span>
}

export function ModelBadge({ model }: { model?: string | null }) {
  return <span className="model-badge" title={model ?? "No model reported"}><Cpu size={14} aria-hidden="true" /><span className="model-label">Model</span><span>{model || "Not reported"}</span></span>
}

export function MetricTile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <div className="metric-tile"><span className="metric-label">{label}</span><div className="metric-value">{value}</div>{hint && <span className="metric-hint">{hint}</span>}</div>
}

export function PageIntro({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <header className="page-intro"><div><h1>{title}</h1><p className="page-description">{description}</p></div>{children && <div className="page-actions">{children}</div>}</header>
}

export function EmptyState({ title, description, icon, children }: { title: string; description: string; icon?: ReactNode; children?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon">{icon ?? <Layers3 size={25} aria-hidden="true" />}</div><h2>{title}</h2><p>{description}</p>{children && <div className="empty-actions">{children}</div>}</div>
}
