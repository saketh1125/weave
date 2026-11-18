export function Spinner() {
  return <svg className="loading-spinner" viewBox="0 0 24 24" aria-hidden="true">
    {Array.from({ length: 8 }, (_, i) => <path key={i} d="M12 3v4" transform={`rotate(${i * 45} 12 12)`} opacity={(i + 1) / 8} />)}
  </svg>
}

export function LoadingState({ label, screen = false }: { label: string; screen?: boolean }) {
  return <div role="status" className={screen ? "loading-state loading-screen" : "loading-state"}>
    <Spinner /><span>{label}</span>
  </div>
}
