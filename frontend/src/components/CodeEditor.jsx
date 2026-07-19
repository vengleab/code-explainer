import { useRef, useEffect, useCallback } from 'react'

// ── Syntax highlighter (Python regex tokenizer) ─────────────────────────
const PY_KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
  'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
  'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
  'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
  'try', 'while', 'with', 'yield',
])
const PY_BUILTINS = new Set([
  'print', 'len', 'range', 'int', 'str', 'float', 'list', 'dict', 'set',
  'tuple', 'bool', 'abs', 'all', 'any', 'enumerate', 'zip', 'map',
  'filter', 'sorted', 'reversed', 'sum', 'min', 'max', 'open', 'input',
  'isinstance', 'type', 'super', 'self',
])
const TOKEN_RE =
  /(#.*)|('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|'''[\s\S]*?'''|"""[\s\S]*?""")|(\b\d+\.?\d*\b)|(@[A-Za-z_]\w*)|([A-Za-z_]\w*)/g

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function highlightPython(code) {
  let out = '', last = 0, prevWord = '', m
  TOKEN_RE.lastIndex = 0
  while ((m = TOKEN_RE.exec(code))) {
    out += escHtml(code.slice(last, m.index))
    const [, comment, string, number, decorator, ident] = m
    if (comment)   out += `<span class="tok-com">${escHtml(comment)}</span>`
    else if (string)    out += `<span class="tok-str">${escHtml(string)}</span>`
    else if (number)    out += `<span class="tok-num">${number}</span>`
    else if (decorator) out += `<span class="tok-dec">${escHtml(decorator)}</span>`
    else if (ident) {
      if (PY_KEYWORDS.has(ident))               out += `<span class="tok-kw">${ident}</span>`
      else if (prevWord === 'def' || prevWord === 'class') out += `<span class="tok-def">${ident}</span>`
      else if (PY_BUILTINS.has(ident))          out += `<span class="tok-builtin">${ident}</span>`
      else                                       out += ident
      prevWord = ident
    }
    last = TOKEN_RE.lastIndex
  }
  out += escHtml(code.slice(last))
  return out
}

// ── Tab / Shift+Tab indent helpers ──────────────────────────────────────
const TAB = '    '

function applyTab(textarea, shiftKey) {
  const { value, selectionStart: start, selectionEnd: end } = textarea

  if (start === end && !shiftKey) {
    // Simple insertion at cursor
    textarea.setRangeText(TAB, start, end, 'end')
    return
  }

  const lineStart = value.lastIndexOf('\n', start - 1) + 1
  let lineEnd = end > start && value[end - 1] === '\n' ? end - 1 : value.indexOf('\n', end)
  if (lineEnd === -1) lineEnd = value.length

  const block = value.slice(lineStart, lineEnd)
  const lines = block.split('\n')

  if (shiftKey) {
    let firstDelta = 0
    const newLines = lines.map((line, i) => {
      let removed = 0
      if (line.startsWith(TAB))  removed = TAB.length
      else if (line.startsWith('\t')) removed = 1
      else { const mx = line.match(/^ {1,3}/); if (mx) removed = mx[0].length }
      if (i === 0) firstDelta = -removed
      return line.slice(removed)
    })
    const newBlock = newLines.join('\n')
    textarea.setRangeText(newBlock, lineStart, lineEnd, 'select')
    textarea.selectionStart = Math.max(lineStart, start + firstDelta)
    textarea.selectionEnd   = end + (newBlock.length - block.length)
  } else {
    const newBlock = lines.map(l => TAB + l).join('\n')
    textarea.setRangeText(newBlock, lineStart, lineEnd, 'select')
    textarea.selectionStart = start + TAB.length
    textarea.selectionEnd   = end + (newBlock.length - block.length)
  }
}

// ── Component ────────────────────────────────────────────────────────────
/**
 * CodeEditor — textarea with a syntax-highlight overlay and Tab-indent support.
 *
 * Props:
 *   value    — controlled code string
 *   onChange — callback(newCode: string)
 */
export default function CodeEditor({ value, onChange }) {
  const preRef      = useRef(null)
  const textareaRef = useRef(null)

  // Update highlight overlay whenever value changes
  useEffect(() => {
    if (preRef.current) {
      preRef.current.innerHTML = highlightPython(value) + '\n'
    }
  }, [value])

  const handleScroll = useCallback(() => {
    if (preRef.current && textareaRef.current) {
      preRef.current.scrollTop  = textareaRef.current.scrollTop
      preRef.current.scrollLeft = textareaRef.current.scrollLeft
    }
  }, [])

  const handleKeyDown = useCallback((e) => {
    if (e.key !== 'Tab') return
    e.preventDefault()
    applyTab(e.target, e.shiftKey)
    // Notify React of the new value via a synthetic input event
    onChange(e.target.value)
  }, [onChange])

  return (
    <div className="code-wrap">
      <pre ref={preRef} aria-hidden="true"><code /></pre>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={e => onChange(e.target.value)}
        onScroll={handleScroll}
        onKeyDown={handleKeyDown}
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        autoCorrect="off"
      />
    </div>
  )
}
