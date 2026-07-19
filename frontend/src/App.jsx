import { useState, useRef } from 'react'
import { MODES } from './constants.js'
import ModeToggle  from './components/ModeToggle.jsx'
import CodeEditor  from './components/CodeEditor.jsx'
import Controls    from './components/Controls.jsx'
import StatusBar   from './components/StatusBar.jsx'
import ResultPanel from './components/ResultPanel.jsx'

export default function App() {
  const [mode, setMode]       = useState('python')
  const [code, setCode]       = useState(MODES.python.defaultCode)
  const [ms,   setMs]         = useState(MODES.python.ms)
  const [palette, setPalette] = useState('dark')  // "dark" | "light" code card
  const [loading, setLoading] = useState(false)
  const [status, setStatus]   = useState(null)   // { text, type }
  const resultRef = useRef(null)

  const cfg = MODES[mode]
  const filename = mode === 'pandas' ? 'analysis.py' : 'main.py'

  // Switch mode: update code/ms to new defaults, clear result + status
  function handleModeChange(newMode) {
    if (newMode === mode) return
    setMode(newMode)
    setCode(MODES[newMode].defaultCode)
    setMs(MODES[newMode].ms)
    setStatus(null)
  }

  function handleGenerate() {
    if (!code.trim() || loading) return
    resultRef.current?.generate(code, ms, cfg.endpoint, palette)
  }

  return (
    <>
      <h1>code-explainer</h1>
      <p className="sub">{cfg.subtitle}</p>

      <ModeToggle mode={mode} onChange={handleModeChange} />

      <div className="layout">
        <div className="panel-bar">
          <span className="panel-bar-label">code</span>
          <div className="mode-toggle theme-toggle">
            <button
              className={palette === 'dark' ? 'active' : ''}
              onClick={() => setPalette('dark')}
            >
              Dark
            </button>
            <button
              className={palette === 'light' ? 'active' : ''}
              onClick={() => setPalette('light')}
            >
              Light
            </button>
          </div>
        </div>

        <CodeEditor
          value={code}
          onChange={setCode}
          filename={filename}
          palette={palette}
        />

        <Controls
          ms={ms}
          onMsChange={setMs}
          loading={loading}
          onGenerate={handleGenerate}
          hint={cfg.hint}
        />

        <StatusBar status={status} />

        <ResultPanel
          ref={resultRef}
          onStatus={setStatus}
          onLoading={setLoading}
        />
      </div>
    </>
  )
}
