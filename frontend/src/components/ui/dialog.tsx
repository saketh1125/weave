import { useEffect, useRef, type ReactNode } from "react"

export function Dialog({ children, onClose, labelledBy, wide = false }: { children: ReactNode; onClose: () => void; labelledBy: string; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = ref.current
    element?.showModal()
    return () => { element?.close() }
  }, [])
  return <dialog ref={ref} aria-labelledby={labelledBy} className={wide ? "workspace-dialog workspace-dialog-wide" : "workspace-dialog"} onCancel={event => { event.preventDefault(); onClose() }} onClick={event => { if (event.target === event.currentTarget) onClose() }}>{children}</dialog>
}
