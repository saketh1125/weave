import { useEffect, useRef, type ReactNode } from "react"

export function Dialog({ children, onClose, labelledBy }: { children: ReactNode; onClose: () => void; labelledBy: string }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = ref.current
    element?.showModal()
    return () => { element?.close() }
  }, [])
  return <dialog ref={ref} aria-labelledby={labelledBy} className="workspace-dialog" onCancel={event => { event.preventDefault(); onClose() }} onClick={event => { if (event.target === event.currentTarget) onClose() }}>{children}</dialog>
}
