import { useState, useRef, useImperativeHandle, forwardRef } from 'react'
import { codeToB64Url, frameToPngBlob } from '../constants.js'

/**
 * ResultPanel — fetches the GIF from the backend, displays it in a modern card,
 * and provides Copy GIF / Download GIF / Copy URL for Google Slides actions.
 *
 * Exposed via ref.generate(code, ms, endpoint) so the parent can trigger
 * generation without prop-drilling a callback chain.
 *
 * Props:
 *   onStatus — callback({ text, type }) to update the parent status bar
 *   onLoading — callback(boolean)
 */
const ResultPanel = forwardRef(function ResultPanel({ onStatus, onLoading }, ref) {
  const [gifUrl, setGifUrl]     = useState(null)
  const [gifBlob, setGifBlob]   = useState(null)
  const [lastParams, setLastParams] = useState(null)   // { code, ms, endpoint, palette }
  const imgRef = useRef(null)

  // ── Exposed API ──────────────────────────────────────────────────────
  useImperativeHandle(ref, () => ({
    async generate(code, ms, endpoint, palette = 'dark') {
      setGifUrl(null)
      setGifBlob(null)
      setLastParams({ code, ms, endpoint, palette })
      onStatus({ text: 'Generating visualization…', type: 'dim' })
      onLoading(true)

      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, ms, palette }),
        })

        if (!res.ok) {
          const err = await res.json().catch(() => ({ error: 'unknown error' }))
          onStatus({ text: err.error || `Request failed (${res.status})`, type: 'error' })
          return
        }

        const blob = await res.blob()
        const url  = URL.createObjectURL(blob)
        setGifBlob(blob)
        setGifUrl(url)
        onStatus({ text: 'GIF generated successfully', type: 'ok' })
      } catch (e) {
        onStatus({ text: String(e), type: 'error' })
      } finally {
        onLoading(false)
      }
    },
  }))

  // ── Actions ──────────────────────────────────────────────────────────
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
        // Fall back to the current frame as PNG
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
    const { code, ms, endpoint, palette } = lastParams
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

  if (!gifUrl) return null

  return (
    <div className="result-card">
      <div className="result-image-wrapper">
        <img ref={imgRef} src={gifUrl} alt="execution GIF" />
      </div>

      <div className="actions-row">
        <button type="button" className="secondary" onClick={handleCopyGif}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
          Copy GIF
        </button>

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

