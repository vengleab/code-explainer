/**
 * Controls — Generate GIF button, ms/frame input with step buttons, quality
 * selector, and hint callout.
 *
 * Props:
 *   ms          — current ms value (number)
 *   onMsChange  — callback(newMs: number)
 *   quality     — "low" | "medium" | "high"
 *   onQuality   — callback(newQuality: string)
 *   loading     — boolean, disables the button while generating
 *   onGenerate  — callback() fired when button is clicked
 *   hint        — hint string to display below the controls row
 */

const QUALITY_OPTIONS = [
  { value: 'low',    label: 'Low',    title: 'Small file, fast render' },
  { value: 'medium', label: 'Medium', title: 'Balanced quality (default)' },
  { value: 'high',   label: 'High',   title: 'Crisp detail, larger file' },
]

export default function Controls({ ms, onMsChange, quality = 'medium', onQuality, loading, onGenerate, hint }) {
  const handleStep = (delta) => {
    const nextVal = Math.min(2000, Math.max(200, ms + delta))
    onMsChange(nextVal)
  }

  return (
    <div className="controls-card">
      <div className="controls-row">
        <div className="controls-left">
          <button type="button" className="primary" onClick={onGenerate} disabled={loading}>
            {loading ? (
              <>
                <span className="spinner" aria-hidden="true" />
                Generating GIF…
              </>
            ) : (
              <>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ width: 16, height: 16 }}>
                  <polygon points="5 3 19 12 5 21 5 3" fill="currentColor" />
                </svg>
                Generate GIF
              </>
            )}
          </button>

          <div className="ms-control">
            <span>Speed:</span>
            <div className="ms-input-wrapper">
              <button
                type="button"
                className="ms-btn"
                onClick={() => handleStep(-50)}
                title="Decrease frame delay (-50ms)"
              >
                -
              </button>
              <input
                type="number"
                className="ms-input"
                min={200}
                max={2000}
                step={50}
                value={ms}
                onChange={e => onMsChange(Number(e.target.value))}
              />
              <button
                type="button"
                className="ms-btn"
                onClick={() => handleStep(50)}
                title="Increase frame delay (+50ms)"
              >
                +
              </button>
            </div>
            <span>ms/frame</span>
          </div>

          {/* Quality selector */}
          <div className="quality-control">
            <span>Quality:</span>
            <div className="quality-seg" role="group" aria-label="GIF quality">
              {QUALITY_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  className={`quality-btn${quality === opt.value ? ' active' : ''}`}
                  title={opt.title}
                  onClick={() => onQuality?.(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {hint && (
        <div className="hint-badge">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <span>{hint}</span>
        </div>
      )}
    </div>
  )
}
