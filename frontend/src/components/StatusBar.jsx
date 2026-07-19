/**
 * StatusBar — shows status badges with icons for ok, error, and dim states.
 *
 * Props:
 *   status — { text: string, type: 'ok' | 'error' | 'dim' | '' }
 */
export default function StatusBar({ status }) {
  if (!status?.text) return <div className="status-container" />

  const type = status.type ?? 'dim'

  return (
    <div className="status-container">
      <div className={`status-badge ${type}`}>
        {type === 'ok' && (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ width: 14, height: 14 }}>
            <polyline points="20 6 9 17 4 12" />
          </svg>
        )}
        {type === 'error' && (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ width: 14, height: 14 }}>
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        )}
        {type === 'dim' && <span className="spinner" aria-hidden="true" />}
        <span>{status.text}</span>
      </div>
    </div>
  )
}

