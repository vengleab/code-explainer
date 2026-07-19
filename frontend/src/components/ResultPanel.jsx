import { useState, useRef, useImperativeHandle, forwardRef } from 'react'
import { codeToB64Url, frameToPngBlob } from '../constants.js'

/**
 * ResultPanel — fetches the GIF from the backend, displays it, and
 * provides Copy GIF / Download GIF / Copy URL for Google Slides actions.
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
      onStatus({ text: 'running…', type: 'dim' })
      onLoading(true)

      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, ms, palette }),
        })

        if (!res.ok) {
          const err = await res.json().catch(() => ({ error: 'unknown error' }))
          onStatus({ text: err.error || `request failed (${res.status})`, type: 'error' })
          return
        }

        const blob = await res.blob()
        const url  = URL.createObjectURL(blob)
        setGifBlob(blob)
        setGifUrl(url)
        onStatus({ text: 'done ✓', type: 'ok' })
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
      onStatus({ text: 'clipboard copy is not supported in this browser', type: 'error' })
      return
    }
    try {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/gif': gifBlob })])
        onStatus({ text: 'GIF copied to clipboard', type: 'ok' })
      } catch {
        // Fall back to the current frame as PNG
        const pngBlob = await frameToPngBlob(imgRef.current)
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngBlob })])
        onStatus({
          text: "browser doesn't support copying animated GIFs — copied current frame as PNG",
          type: 'ok',
        })
      }
    } catch (e) {
      onStatus({ text: 'copy failed: ' + e, type: 'error' })
    }
  }

  async function handleCopySlidesUrl() {
    if (!lastParams) return
    const { code, ms, endpoint, palette } = lastParams
    const url = `${location.origin}${endpoint}?c=${codeToB64Url(code)}&ms=${ms}&pal=${palette || 'dark'}`
    try {
      await navigator.clipboard.writeText(url)
      onStatus({
        text: 'URL copied — in Google Slides: Insert → Image → By URL, then paste (animation is kept)',
        type: 'ok',
      })
    } catch (e) {
      onStatus({ text: 'copy failed: ' + e, type: 'error' })
    }
  }

  if (!gifUrl) return <div className="result" />

  return (
    <div className="result">
      <img ref={imgRef} src={gifUrl} alt="execution GIF" />

      <div className="row">
        <button className="secondary" onClick={handleCopyGif}>
          Copy GIF
        </button>

        <a
          className="secondary"
          href={gifUrl}
          download="code-explainer.gif"
        >
          Download GIF
        </a>

        <button className="secondary" onClick={handleCopySlidesUrl}>
          Copy URL for Google Slides
        </button>
      </div>
    </div>
  )
})

export default ResultPanel
