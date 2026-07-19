/**
 * Controls — Generate GIF button, ms/frame input, and hint text.
 *
 * Props:
 *   ms        — current ms value (number)
 *   onMsChange — callback(newMs: number)
 *   loading   — boolean, disables the button while generating
 *   onGenerate — callback() fired when button is clicked
 *   hint      — hint string to display below the controls row
 */
export default function Controls({ ms, onMsChange, loading, onGenerate, hint }) {
  return (
    <div style={{ display: 'grid', gap: '8px' }}>
      <div className="row">
        <button className="primary" onClick={onGenerate} disabled={loading}>
          {loading ? 'generating…' : 'Generate GIF'}
        </button>

        <label className="ms">
          ms/frame
          <input
            type="number"
            min={200}
            max={2000}
            step={50}
            value={ms}
            onChange={e => onMsChange(Number(e.target.value))}
          />
        </label>

        <span className="hint">{hint}</span>
      </div>
    </div>
  )
}
