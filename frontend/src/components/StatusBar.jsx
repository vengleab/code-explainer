/**
 * StatusBar — shows error / ok / running status text.
 *
 * Props:
 *   status — { text: string, type: 'ok' | 'error' | 'dim' | '' }
 */
export default function StatusBar({ status }) {
  if (!status?.text) return <div className="status" />

  return (
    <div className={`status ${status.type ?? ''}`}>
      {status.text}
    </div>
  )
}
