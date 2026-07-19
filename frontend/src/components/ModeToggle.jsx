import { MODES } from '../constants.js'

/**
 * ModeToggle — pill-shaped Python / Pandas switcher with vector icons.
 *
 * Props:
 *   mode     — current active mode key ('python' | 'pandas')
 *   onChange — callback(newMode: string)
 */
export default function ModeToggle({ mode, onChange }) {
  return (
    <div className="mode-toggle-group" role="tablist" aria-label="Mode switcher">
      {Object.entries(MODES).map(([key, cfg]) => {
        const isActive = mode === key
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={`mode-btn ${isActive ? 'active' : ''}`}
            onClick={() => onChange(key)}
          >
            {key === 'python' ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="3" y1="9" x2="21" y2="9" />
                <line x1="3" y1="15" x2="21" y2="15" />
                <line x1="9" y1="3" x2="9" y2="21" />
                <line x1="15" y1="3" x2="15" y2="21" />
              </svg>
            )}
            {cfg.label}
          </button>
        )
      })}
    </div>
  )
}

