import { useState, useRef, useEffect } from 'react'
import { MODES } from './constants.js'
import ModeToggle from './components/ModeToggle.jsx'
import CodeEditor from './components/CodeEditor.jsx'
import Controls from './components/Controls.jsx'
import StatusBar from './components/StatusBar.jsx'
import ResultPanel from './components/ResultPanel.jsx'

export default function App() {
  const [mode, setMode] = useState('python')
  const [code, setCode] = useState(MODES.python.defaultCode)
  const [ms, setMs] = useState(MODES.python.ms)
  const [quality, setQuality] = useState('medium')   // "low" | "medium" | "high"
  const [theme, setTheme] = useState('dark') // "dark" | "light" full-page theme
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null) // { text, type }
  const resultRef = useRef(null)

  const cfg = MODES[mode]
  const filename = mode === 'pandas' ? 'analysis.py' : 'main.py'

  // Sync full-page data-theme attribute on html root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

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
    resultRef.current?.generate(code, ms, cfg.endpoint, theme, quality)
  }

  return (
    <div className="app-container" data-theme={theme}>
      <header className="app-header">
        <div className="brand-badge">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
          Interactive Code Visualizer
        </div>
        <h1 className="app-title">code-explainer</h1>
        <p className="app-subtitle">{cfg.subtitle}</p>
      </header>

      <div className="toolbar">
        <ModeToggle mode={mode} onChange={handleModeChange} />

        <div className="theme-switch" role="group" aria-label="Theme toggle">
          <button
            type="button"
            className={`theme-btn ${theme === 'dark' ? 'active' : ''}`}
            onClick={() => setTheme('dark')}
            title="Dark Theme"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
            </svg>
            Dark
          </button>
          <button
            type="button"
            className={`theme-btn ${theme === 'light' ? 'active' : ''}`}
            onClick={() => setTheme('light')}
            title="Light Theme"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="5" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
            </svg>
            Light
          </button>
        </div>
      </div>

      <main className="layout">
        <CodeEditor
          value={code}
          onChange={setCode}
          filename={filename}
          palette={theme}
        />

        <Controls
          ms={ms}
          onMsChange={setMs}
          quality={quality}
          onQuality={setQuality}
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
      </main>
    </div>
  )
}

