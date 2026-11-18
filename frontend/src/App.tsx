import { useEffect, useState, type FormEvent, type PointerEvent } from "react"
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from "react-router-dom"
import { BookOpen, ChevronRight, FolderGit2, Gauge, GitBranch, GitPullRequest, LockKeyhole, LogOut, MessageSquare, Settings2, Shield, ShieldAlert } from "lucide-react"
import { api, clearAccessToken, setAccessToken } from "@/lib/api"
import Findings from "@/pages/Findings"
import PrRisk from "@/pages/PrRisk"
import Repos from "@/pages/Repos"
import Score from "@/pages/Score"
import Ask from "@/pages/Ask"
import Docs from "@/pages/Docs"
import Settings from "@/pages/Settings"
import DocsRouteBoundary from "@/components/docs-error-boundary"
import { WorkspaceProvider, useWorkspace } from "@/lib/workspace"
import { StatusBadge } from "@/components/ui/status"
import { Button } from "@/components/ui/button"
import { LoadingState } from "@/components/ui/loading"

function trackHover(event: PointerEvent<HTMLDivElement>) {
  if (event.pointerType !== "mouse" || !(event.target instanceof Element)) return
  const control = event.target.closest<HTMLElement>("[data-pointer-glow]")
  if (!control) return
  const bounds = control.getBoundingClientRect()
  control.style.setProperty("--hover-x", `${event.clientX - bounds.left}px`)
  control.style.setProperty("--hover-y", `${event.clientY - bounds.top}px`)
}

const nav = [
  { to: "/", label: "Repositories", icon: FolderGit2 },
  { to: "/ask", label: "Ask", icon: MessageSquare },
  { to: "/docs", label: "Docs & Drift", icon: BookOpen },
  { to: "/findings", label: "Findings", icon: ShieldAlert },
  { to: "/pr-risk", label: "PR Risk", icon: GitPullRequest },
  { to: "/score", label: "Score", icon: Gauge },
  { to: "/settings", label: "Settings", icon: Settings2 },
]

function HealthBadge() {
  const [status, setStatus] = useState<string>("…")
  useEffect(() => { api.health().then(r => setStatus(r.status)).catch(() => setStatus("offline")) }, [])
  return <StatusBadge status={status === "ok" ? "healthy" : status === "offline" ? "failed" : "pending"} label={status === "ok" ? "API connected" : status === "offline" ? "API offline" : "Connecting"} />
}

function WorkspaceChrome({ authenticated, logout, children }: { authenticated: boolean; logout: () => void; children: React.ReactNode }) {
  const location = useLocation()
  const current = nav.find(item => item.to === location.pathname)
  const { repoId } = useWorkspace()
  const [context, setContext] = useState<{ id: string; name: string; branch: string } | null>(null)
  useEffect(() => {
    if (!authenticated || !repoId) return
    let current = true
    void api.get<{ name: string; branch: string }>(`/repos/${encodeURIComponent(repoId)}`).then(repo => {
      if (current) setContext({ id: repoId, name: repo.name, branch: repo.branch })
    }).catch(() => { if (current) setContext(null) })
    return () => { current = false }
  }, [authenticated, repoId])
  const repository = context?.id === repoId ? context : null
  return <div className={authenticated ? "app-shell" : "auth-shell"} onPointerMove={trackHover}>
    {authenticated && <aside className="workspace-sidebar" aria-label="Workspace sidebar">
      <NavLink to="/" className="brand"><span className="brand-mark"><Shield size={22} strokeWidth={1.8} aria-hidden="true" /></span><span><strong>Weave</strong><small>Repository intelligence</small></span></NavLink>
      <nav className="workspace-nav" aria-label="Main navigation">{nav.map((item, index) => <div className="workspace-nav-item" key={item.to}>{index === 0 && <p className="sidebar-label">Workspace</p>}{index === 3 && <p className="sidebar-label">Verification</p>}{index === 6 && <p className="sidebar-label">Preferences</p>}<NavLink to={item.to} end={item.to === "/"} data-pointer-glow="" className={({ isActive }) => `workspace-nav-link${isActive ? " is-active" : ""}`}><item.icon size={18} strokeWidth={1.8} aria-hidden="true" /><span>{item.label}</span></NavLink></div>)}</nav>
      <div className="sidebar-footer"><div className="workspace-privacy"><LockKeyhole size={15} aria-hidden="true" /><div><strong>Private workspace</strong></div></div><button onClick={logout} className="signout-button"><LogOut size={15} aria-hidden="true" />Sign out</button></div>
    </aside>}
    <div className={authenticated ? "app-main" : "auth-main"}>
      <header className="workspace-toolbar">{authenticated ? <div className="breadcrumb"><Link to="/" className="toolbar-repository"><FolderGit2 size={14} aria-hidden="true" /><span>{repository?.name || (repoId ? "Repository" : "Select repository")}</span></Link><ChevronRight size={13} aria-hidden="true" /><strong>{current?.label ?? "Weave"}</strong>{repository?.branch && <span className="toolbar-branch"><GitBranch size={13} aria-hidden="true" />{repository.branch}</span>}</div> : <span className="auth-brand"><Shield size={20} aria-hidden="true" /> Weave</span>}<div className="toolbar-actions"><HealthBadge />{authenticated && <button className="mobile-signout" onClick={logout} aria-label="Sign out"><LogOut size={16} aria-hidden="true" /></button>}</div></header>
      <main id="main-content" className={authenticated ? "workspace-content" : "auth-content"}>{children}</main>
    </div>
  </div>
}

export default function App() {
  const [token, setToken] = useState("")
  const [authenticated, setAuthenticated] = useState(false)
  const [checking, setChecking] = useState(true)
  const [signingIn, setSigningIn] = useState(false)
  const [authError, setAuthError] = useState("")

  useEffect(() => {
    const saved = sessionStorage.getItem("weave_access_token") ?? ""
    if (!saved) { setChecking(false); return }
    setAccessToken(saved)
    api.checkAuth().then(() => setAuthenticated(true)).catch(() => {
      clearAccessToken()
      setAuthError("The saved access token was rejected. Enter the current token.")
    }).finally(() => setChecking(false))
  }, [])

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (signingIn) return
    setSigningIn(true)
    setAuthError("")
    try {
      const session = await api.createSession(token)
      setAccessToken(session.access_token)
      await api.checkAuth()
      setAuthenticated(true)
      setToken("")
    } catch {
      clearAccessToken()
      setAuthError("Authentication failed. Check the access token and try again.")
    } finally { setSigningIn(false) }
  }

  function logout() {
    clearAccessToken()
    setAuthenticated(false)
    setAuthError("")
  }

  return (
    <WorkspaceProvider><BrowserRouter>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <WorkspaceChrome authenticated={authenticated} logout={logout}>
          {checking ? <LoadingState label="Opening your workspace…" screen /> : !authenticated ? <section className="sign-in-card sentra-card">
            <span className="brand-mark sign-in-mark"><Shield size={30} aria-hidden="true" /></span>
            <p className="eyebrow">YOUR PRIVATE WORKSPACE</p>
            <h1>Clarity for your code.</h1>
              <p className="page-description">Understand your repositories, keep documentation current, and review changes with confidence.</p>
            <form className="mt-5 space-y-3" onSubmit={login}>
              <label className="block text-sm font-medium" htmlFor="access-token">Access token</label>
              <input id="access-token" type="password" autoComplete="current-password" value={token} onChange={e => setToken(e.target.value)} required className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
              {authError && <p role="alert" className="notice notice-error">{authError}</p>}
              <Button pending={signingIn} className="w-full">{signingIn ? "Signing in…" : <>Open workspace<ChevronRight size={16} aria-hidden="true" /></>}</Button>
            </form>
            <p className="sign-in-footnote"><LockKeyhole size={13} aria-hidden="true" />Only a short-lived session is stored in this tab.</p>
          </section> : <Routes>
            <Route path="/" element={<Repos />} />
            <Route path="/ask" element={<Ask />} />
            <Route path="/docs" element={<DocsRouteBoundary><Docs /></DocsRouteBoundary>} />
            <Route path="/findings" element={<Findings />} />
            <Route path="/pr-risk" element={<PrRisk />} />
            <Route path="/score" element={<Score />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>}
      </WorkspaceChrome>
    </BrowserRouter></WorkspaceProvider>
  )
}
