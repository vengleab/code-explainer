import { useState, useRef, useEffect, useCallback } from 'react'
import { MODES, CODE_PRESETS } from './constants.js'
import ModeToggle from './components/ModeToggle.jsx'
import CodeEditor from './components/CodeEditor.jsx'
import Controls from './components/Controls.jsx'
import StatusBar from './components/StatusBar.jsx'
import ResultPanel from './components/ResultPanel.jsx'

export default function App() {
  const [mode, setMode] = useState(() => {
    return localStorage.getItem('app_mode') || 'python'
  })
  const [theme, setTheme] = useState(() => {
    return localStorage.getItem('app_theme') || 'dark'
  })
  const [code, setCode] = useState(() => {
    const savedMode = localStorage.getItem('app_mode') || 'python'
    return localStorage.getItem('app_code_' + savedMode) || MODES[savedMode].defaultCode
  })
  const [ms, setMs] = useState(() => {
    const savedMs = localStorage.getItem('app_ms')
    const savedMode = localStorage.getItem('app_mode') || 'python'
    return savedMs ? Number(savedMs) : MODES[savedMode].ms
  })
  const [quality, setQuality] = useState(() => {
    return localStorage.getItem('app_quality') || 'medium'
  })
  const [layout, setLayout] = useState(() => {
    return localStorage.getItem('layout_mode') || 'split'
  })
  const [splitRatio, setSplitRatio] = useState(() => {
    return Number(localStorage.getItem('split_ratio')) || 33
  })
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null) // { text, type }
  const [isResizing, setIsResizing] = useState(false)

  const splitContainerRef = useRef(null)
  const resultRef = useRef(null)

  const cfg = MODES[mode]
  const filename = mode === 'pandas' ? 'analysis.py' : 'main.py'

  // Sync full-page data-theme attribute on html root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('app_theme', theme)
  }, [theme])

  // Sync mode to localStorage
  useEffect(() => {
    localStorage.setItem('app_mode', mode)
  }, [mode])

  // Sync code per mode to localStorage
  useEffect(() => {
    localStorage.setItem('app_code_' + mode, code)
  }, [code, mode])

  // Sync ms speed to localStorage
  useEffect(() => {
    localStorage.setItem('app_ms', ms)
  }, [ms])

  // Sync quality option to localStorage
  useEffect(() => {
    localStorage.setItem('app_quality', quality)
  }, [quality])

  // Sync layout mode to localStorage
  useEffect(() => {
    localStorage.setItem('layout_mode', layout)
  }, [layout])

  // Sync split ratio to localStorage
  useEffect(() => {
    localStorage.setItem('split_ratio', splitRatio)
  }, [splitRatio])

  // Switch mode: update code/ms to stored or default values, clear result + status
  function handleModeChange(newMode) {
    if (newMode === mode) return
    setMode(newMode)
    const savedCode = localStorage.getItem('app_code_' + newMode)
    setCode(savedCode || MODES[newMode].defaultCode)
    const savedMs = localStorage.getItem('app_ms')
    setMs(savedMs ? Number(savedMs) : MODES[newMode].ms)
    setStatus(null)
  }

  const handleGenerate = useCallback(() => {
    if (!code.trim() || loading) return
    resultRef.current?.generate(code, ms, cfg.endpoint, theme, quality)
  }, [code, loading, ms, cfg.endpoint, theme, quality])

  // ── Keyboard Shortcuts (Cmd/Ctrl + Enter) ──────────────────────────
  useEffect(() => {
    function handleKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        handleGenerate()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleGenerate])

  // ── Draggable Resizer Handler ──────────────────────────────────────
  const handleMouseDown = useCallback((e) => {
    e.preventDefault()
    setIsResizing(true)
  }, [])

  useEffect(() => {
    if (!isResizing) return

    function handleMouseMove(e) {
      if (!splitContainerRef.current) return
      const rect = splitContainerRef.current.getBoundingClientRect()
      const offsetX = e.clientX - rect.left
      const percentage = Math.min(65, Math.max(20, (offsetX / rect.width) * 100))
      setSplitRatio(Math.round(percentage))
    }

    function handleMouseUp() {
      setIsResizing(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isResizing])

  const handleResetSplit = () => setSplitRatio(33)

  return (
    <div className="app-container" data-theme={theme}>
      <header className="app-header">
        <div className="brand-badge">
          <span className="brand-dot" />
          Python & Pandas Execution Studio
        </div>
        <h1 className="app-title">Learn Code With Vengleab</h1>
        <p className="app-subtitle">{cfg.subtitle}</p>
      </header>

      <div className="toolbar">
        <ModeToggle mode={mode} onChange={handleModeChange} />

        <div className="toolbar-controls">
          <div className="layout-switch" role="group" aria-label="Layout toggle">
            <button
              type="button"
              className={`layout-btn ${layout === 'split' ? 'active' : ''}`}
              onClick={() => setLayout('split')}
              title="2-Column Split (Resizable)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="6" height="18" rx="1" />
                <rect x="12" y="3" width="9" height="18" rx="1" />
              </svg>
              Split ({splitRatio}/{100 - splitRatio})
            </button>
            <button
              type="button"
              className={`layout-btn ${layout === 'stacked' ? 'active' : ''}`}
              onClick={() => setLayout('stacked')}
              title="Stacked Layout"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="7" rx="1" />
                <rect x="3" y="13" width="18" height="8" rx="1" />
              </svg>
              Stacked
            </button>
          </div>

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
      </div>

      <main className="layout">
        {layout === 'split' ? (
          <div
            ref={splitContainerRef}
            className={`layout-split ${isResizing ? 'is-dragging' : ''}`}
            style={{
              gridTemplateColumns: `${splitRatio}fr 8px ${100 - splitRatio}fr`,
            }}
          >
            <div className="layout-left">
              <CodeEditor
                value={code}
                onChange={setCode}
                filename={filename}
                palette={theme}
                presets={CODE_PRESETS[mode]}
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
            </div>

            <div
              className="split-resizer"
              onMouseDown={handleMouseDown}
              onDoubleClick={handleResetSplit}
              title="Drag to resize columns • Double-click to reset (33/67)"
              role="separator"
              aria-orientation="vertical"
            >
              <div className="resizer-handle" />
            </div>

            <div className="layout-right">
              <ResultPanel
                ref={resultRef}
                onStatus={setStatus}
                onLoading={setLoading}
              />
            </div>
          </div>
        ) : (
          <>
            <CodeEditor
              value={code}
              onChange={setCode}
              filename={filename}
              palette={theme}
              presets={CODE_PRESETS[mode]}
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
          </>
        )}
      </main>
    </div>
  )
}

