import { Component, useState, type ErrorInfo, type ReactNode } from "react"
import { useWorkspace } from "@/lib/workspace"

type BoundaryProps = {
  children: ReactNode
  onReset: () => void
}

type BoundaryState = {
  failed: boolean
}

class DocsErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false }

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Docs route rendering failed", error, info.componentStack)
  }

  render() {
    if (this.state.failed) {
      return (
        <section className="notice notice-error" role="alert">
          <h1 className="text-lg font-semibold">Docs could not be displayed</h1>
          <p className="mt-2 text-sm text-muted-foreground">The saved documentation result could not be rendered. Reset this view to clear its saved results and try again.</p>
          <button className="mt-4 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground" onClick={this.props.onReset}>
            Reset docs view
          </button>
        </section>
      )
    }
    return this.props.children
  }
}

export default function DocsRouteBoundary({ children }: { children: ReactNode }) {
  const { setWorkspace } = useWorkspace()
  const [resetKey, setResetKey] = useState(0)

  function reset() {
    setWorkspace(previous => ({ ...previous, docsResult: null, driftResult: null }))
    setResetKey(previous => previous + 1)
  }

  return <DocsErrorBoundary key={resetKey} onReset={reset}>{children}</DocsErrorBoundary>
}
