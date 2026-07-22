import { useState, useRef, useEffect, useImperativeHandle, forwardRef } from 'react'
import { codeToB64Url, frameToPngBlob } from '../constants.js'

function b64ToBlob(b64Data, contentType = 'image/gif') {
  const byteCharacters = atob(b64Data)
  const byteNumbers = new Array(byteCharacters.length)
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i)
  }
  const byteArray = new Uint8Array(byteNumbers)
  return new Blob([byteArray], { type: contentType })
}

/**
 * ResultPanel — fetches GIF + frame steps from the backend, displays an
 * interactive slide-controller player, and provides Copy/Download actions.
 *
 * Exposed via ref.generate(code, ms, endpoint, palette)
 */
const ResultPanel = forwardRef(function ResultPanel({ onStatus, onLoading }, ref) {
  const [gifUrl, setGifUrl]         = useState(null)
  const [gifBlob, setGifBlob]       = useState(null)
  const [frames, setFrames]         = useState(null)     // string[] data URLs
  const [durations, setDurations]   = useState(null)     // number[] ms per frame
  const [frameIndex, setFrameIndex] = useState(0)
  const [isPlaying, setIsPlaying]   = useState(true)
  const [lastParams, setLastParams] = useState(null)     // { code, ms, endpoint, palette, quality }
  const imgRef = useRef(null)

  // ── Frame Auto-Play Effect ───────────────────────────────────────────
  useEffect(() => {
    if (!isPlaying || !frames || frames.length <= 1) return

    const currentDur = durations?.[frameIndex] ?? 900
    const timer = setTimeout(() => {
      setFrameIndex(prev => (prev >= frames.length - 1 ? 0 : prev + 1))
    }, currentDur)

    return () => clearTimeout(timer)
  }, [isPlaying, frameIndex, frames, durations])

  // ── Exposed API ──────────────────────────────────────────────────────
  useImperativeHandle(ref, () => ({
    async generate(code, ms, endpoint, palette = 'dark', quality = 'medium') {
      setGifUrl(null)
      setGifBlob(null)
      setFrames(null)
      setDurations(null)
      setFrameIndex(0)
      setIsPlaying(true)
      setLastParams({ code, ms, endpoint, palette, quality })
      onStatus({ text: 'Generating visualization…', type: 'dim' })
      onLoading(true)

      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, ms, palette, quality, format: 'json' }),
        })

        if (!res.ok) {
          const err = await res.json().catch(() => ({ error: 'unknown error' }))
          onStatus({ text: err.error || `Request failed (${res.status})`, type: 'error' })
          return
        }

        const data = await res.json()
        
        // Parse GIF blob
        let blob = null
        if (data.gif) {
          blob = b64ToBlob(data.gif, 'image/gif')
          setGifBlob(blob)
          setGifUrl(URL.createObjectURL(blob))
        }

        // Parse frames
        if (data.frames && Array.isArray(data.frames)) {
          const parsedFrames = data.frames.map(f =>
            f.startsWith('data:') ? f : `data:image/gif;base64,${f}`
          )
          setFrames(parsedFrames)
          setDurations(data.durations || null)
        }

        onStatus({ text: 'GIF & step frames ready ✓', type: 'ok' })
      } catch (e) {
        onStatus({ text: String(e), type: 'error' })
      } finally {
        onLoading(false)
      }
    },
  }))

  // ── Slide Controls ───────────────────────────────────────────────────
  const handleTogglePlay = () => {
    if (frames && frameIndex >= frames.length - 1 && !isPlaying) {
      setFrameIndex(0)
    }
    setIsPlaying(prev => !prev)
  }

  const handleStepPrev = () => {
    setIsPlaying(false)
    setFrameIndex(prev => Math.max(0, prev - 1))
  }

  const handleStepNext = () => {
    setIsPlaying(false)
    if (!frames) return
    setFrameIndex(prev => Math.min(frames.length - 1, prev + 1))
  }

  const handleJumpFirst = () => {
    setIsPlaying(false)
    setFrameIndex(0)
  }

  const handleJumpLast = () => {
    setIsPlaying(false)
    if (frames) setFrameIndex(frames.length - 1)
  }

  const handleSliderChange = (e) => {
    setIsPlaying(false)
    setFrameIndex(Number(e.target.value))
  }

  // ── Slide-style pointer navigation ───────────────────────────────────
  // Clicking the frame advances to the next step, like clicking through a
  // slide deck (does nothing once the last step is reached).
  const handleFrameClick = () => {
    if (!frames || frames.length <= 1) return
    handleStepNext()
  }

  // Keyboard / presenter-remote navigation, matching slideshow conventions:
  // ArrowRight/PageDown → next, ArrowLeft/PageUp → prev, Space → play/pause,
  // Home/End → first/last. PageUp/PageDown are what presentation clickers
  // ("pointers") emit, so the stepper works with a remote like real slides.
  useEffect(() => {
    if (!frames || frames.length <= 1) return

    function handleKeyDown(e) {
      const target = e.target
      if (
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'INPUT' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable
      ) return

      switch (e.key) {
        case 'ArrowRight':
        case 'PageDown':
          e.preventDefault()
          handleStepNext()
          break
        case 'ArrowLeft':
        case 'PageUp':
          e.preventDefault()
          handleStepPrev()
          break
        case ' ':
          e.preventDefault()
          handleTogglePlay()
          break
        case 'Home':
          e.preventDefault()
          handleJumpFirst()
          break
        case 'End':
          e.preventDefault()
          handleJumpLast()
          break
        default:
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [frames, frameIndex, isPlaying])

  // ── Copy / Download Actions ──────────────────────────────────────────
  async function handleCopyGif() {
    if (!gifBlob) return
    if (!navigator.clipboard || !window.ClipboardItem) {
      onStatus({ text: 'Clipboard copy is not supported in this browser', type: 'error' })
      return
    }
    try {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/gif': gifBlob })])
        onStatus({ text: 'GIF copied to clipboard!', type: 'ok' })
      } catch {
        const pngBlob = await frameToPngBlob(imgRef.current)
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngBlob })])
        onStatus({
          text: "Browser doesn't support copying animated GIFs — copied current frame as PNG",
          type: 'ok',
        })
      }
    } catch (e) {
      onStatus({ text: 'Copy failed: ' + e, type: 'error' })
    }
  }

  async function handleCopySlidesUrl() {
    if (!lastParams) return
    const { code, ms, endpoint, palette, quality = 'medium' } = lastParams
    const url = `${location.origin}${endpoint}?c=${codeToB64Url(code)}&ms=${ms}&pal=${palette || 'dark'}`
    try {
      await navigator.clipboard.writeText(url)
      onStatus({
        text: 'URL copied — In Google Slides: Insert → Image → By URL, then paste',
        type: 'ok',
      })
    } catch (e) {
      onStatus({ text: 'Copy failed: ' + e, type: 'error' })
    }
  }

  if (!gifUrl && (!frames || frames.length === 0)) return null

  const hasStepper = frames && frames.length > 1
  const displaySrc = hasStepper ? frames[frameIndex] : gifUrl

  return (
    <div className="result-card">
      <div
        className={`result-image-wrapper ${hasStepper ? 'clickable' : ''}`}
        onClick={hasStepper ? handleFrameClick : undefined}
        title={hasStepper ? 'Click to advance to the next step (← → keys also work)' : undefined}
      >
        <img ref={imgRef} src={displaySrc} alt="execution step frame" />
      </div>

      {/* Slide & Frame Stepper Controller */}
      {hasStepper && (
        <div className="slide-controller">
          <div className="player-toolbar">
            <button
              type="button"
              className="player-btn"
              onClick={handleJumpFirst}
              disabled={frameIndex === 0}
              title="First Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z" />
              </svg>
            </button>

            <button
              type="button"
              className="player-btn"
              onClick={handleStepPrev}
              disabled={frameIndex === 0}
              title="Previous Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
              </svg>
            </button>

            <button
              type="button"
              className="player-btn play-pause-btn"
              onClick={handleTogglePlay}
              title={isPlaying ? "Pause" : "Play"}
            >
              {isPlaying ? (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </button>

            <button
              type="button"
              className="player-btn"
              onClick={handleStepNext}
              disabled={frameIndex === frames.length - 1}
              title="Next Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
              </svg>
            </button>

            <button
              type="button"
              className="player-btn"
              onClick={handleJumpLast}
              disabled={frameIndex === frames.length - 1}
              title="Last Step"
            >
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M16 6h2v12h-2zM6 18l8.5-6L6 6z" />
              </svg>
            </button>
          </div>

          <div className="player-scrubber">
            <span className="step-counter">
              Step <strong>{frameIndex + 1}</strong> of {frames.length}
            </span>
            <input
              type="range"
              className="player-slider"
              min={0}
              max={frames.length - 1}
              value={frameIndex}
              onChange={handleSliderChange}
            />
          </div>
        </div>
      )}

      {/* Export / Copy Actions */}
      <div className="actions-row">
        <button type="button" className="secondary" onClick={handleCopyGif}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
          Copy GIF
        </button>

        {gifUrl && (
          <a
            className="secondary"
            href={gifUrl}
            download="code-explainer.gif"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Download GIF
          </a>
        )}

        <button type="button" className="secondary" onClick={handleCopySlidesUrl}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
          </svg>
          Copy URL for Google Slides
        </button>
      </div>
    </div>
  )
})

export default ResultPanel


