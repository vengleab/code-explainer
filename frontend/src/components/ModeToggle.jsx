import { MODES } from '../constants.js'

/**
 * ModeToggle — pill-shaped Python / Pandas switcher.
 *
 * Props:
 *   mode     — current active mode key ('python' | 'pandas')
 *   onChange — callback(newMode: string)
 */
export default function ModeToggle({ mode, onChange }) {
  return (
    <div className="mode-toggle">
      {Object.entries(MODES).map(([key, cfg]) => (
        <button
          key={key}
          className={mode === key ? 'active' : ''}
          onClick={() => onChange(key)}
        >
          {cfg.label}
        </button>
      ))}
    </div>
  )
}
