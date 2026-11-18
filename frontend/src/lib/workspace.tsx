import { createContext, useContext, useState, type Dispatch, type ReactNode, type SetStateAction } from "react"

export type Workspace = {
  findings: unknown[]
  score: unknown | null
  prReview: unknown | null
  prAnswer: unknown | null
  prUrl: string
  prQuestion: string
  askResult: unknown | null
  askQuestion: string
  docsResult: unknown | null
  driftResult: unknown | null
}

type WorkspaceContextValue = {
  repoId: string | null
  selectRepository: (id: string) => void
  workspace: Workspace
  setWorkspace: Dispatch<SetStateAction<Workspace>>
}

const initial: Workspace = {
  findings: [], score: null, prReview: null, prAnswer: null, prUrl: "", prQuestion: "",
  askResult: null, askQuestion: "", docsResult: null, driftResult: null,
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspace, setWorkspace] = useState(initial)
  const [repoId, setRepoId] = useState(() => localStorage.getItem("weave.repoId"))
  function selectRepository(id: string) {
    if (id === repoId) return
    localStorage.setItem("weave.repoId", id)
    setRepoId(id)
    setWorkspace(initial)
  }
  return <WorkspaceContext.Provider value={{ repoId, selectRepository, workspace, setWorkspace }}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext)
  if (!value) throw new Error("useWorkspace must be used within WorkspaceProvider")
  return value
}
