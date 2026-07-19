/** Per-mode defaults and API routing. */
export const MODES = {
  python: {
    label: 'Python',
    endpoint: '/api/generate',
    ms: 900,
    subtitle: 'paste a small Python snippet — get back an execution GIF (code, variables, output, step by step)',
    hint: 'only a small set of stdlib imports is allowed (math, random, itertools, …) — no file/network/process access',
    defaultCode: `fruits = ['apple', 'banana', 'cherry']\n\nfor fruit in fruits:\n    print("-------------------")\n    print(fruit)`,
  },
  pandas: {
    label: 'Pandas',
    endpoint: '/api/generate-pandas',
    ms: 1100,
    subtitle: 'paste pandas code — get back a step-by-step GIF with real DataFrame tables and diff highlighting',
    hint: 'pandas + numpy are available — stdlib imports allowed too — no file/network/process access',
    defaultCode: `import pandas as pd\n\ndf = pd.DataFrame({\n    'name': ['Ann', 'Bo', 'Cy', 'Di'],\n    'dept': ['eng', 'eng', 'sales', 'sales'],\n    'salary': [90, 60, 50, 55],\n})\ndf['bonus'] = df['salary'] * 0.1\nhigh = df[df['salary'] > 55]`,
  },
}

/** Convert a UTF-8 string to a base64url-encoded string (for GET ?c= links). */
export function codeToB64Url(str) {
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Draw the current frame of an <img> onto a canvas and return a PNG Blob. */
export function frameToPngBlob(imgEl) {
  return new Promise((resolve, reject) => {
    const canvas = document.createElement('canvas')
    canvas.width = imgEl.naturalWidth
    canvas.height = imgEl.naturalHeight
    canvas.getContext('2d').drawImage(imgEl, 0, 0)
    canvas.toBlob(b => (b ? resolve(b) : reject(new Error('canvas export failed'))), 'image/png')
  })
}
