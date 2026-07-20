export const CODE_PRESETS = {
  python: [
    {
      name: 'Loop & Print',
      code: `fruits = ['apple', 'banana', 'cherry']\n\nfor fruit in fruits:\n    print("-------------------")\n    print(fruit)`,
    },
    {
      name: 'Bubble Sort',
      code: `arr = [5, 2, 8, 1, 3]\n\nfor i in range(len(arr)):\n    for j in range(len(arr) - 1 - i):\n        if arr[j] > arr[j + 1]:\n            arr[j], arr[j + 1] = arr[j + 1], arr[j]\nprint("Sorted:", arr)`,
    },
    {
      name: 'Fibonacci',
      code: `def fib(n):\n    if n <= 1:\n        return n\n    return fib(n - 1) + fib(n - 2)\n\nseq = [fib(i) for i in range(6)]\nprint("Fibonacci:", seq)`,
    },
    {
      name: 'Frequency Counter',
      code: `words = ['cat', 'dog', 'cat', 'bird', 'dog', 'cat']\ncounts = {}\n\nfor w in words:\n    counts[w] = counts.get(w, 0) + 1\nprint(counts)`,
    },
  ],
  pandas: [
    {
      name: 'GroupBy & Bonus',
      code: `import pandas as pd\n\ndf = pd.DataFrame({\n    'name': ['Ann', 'Bo', 'Cy', 'Di'],\n    'dept': ['eng', 'eng', 'sales', 'sales'],\n    'salary': [90, 60, 50, 55],\n})\ndf['bonus'] = df['salary'] * 0.1\nhigh = df[df['salary'] > 55]`,
    },
    {
      name: 'Filter & Sort',
      code: `import pandas as pd\n\ndf = pd.DataFrame({\n    'item': ['Laptop', 'Phone', 'Tablet', 'Monitor'],\n    'price': [1200, 800, 450, 300],\n    'stock': [15, 30, 8, 25]\n})\ntop_items = df[df['price'] >= 450].sort_values(by='price', ascending=False)`,
    },
    {
      name: 'Missing Values',
      code: `import pandas as pd\nimport numpy as np\n\ndf = pd.DataFrame({\n    'score_a': [95, np.nan, 88, 70],\n    'score_b': [80, 85, np.nan, 90]\n})\ndf['filled_a'] = df['score_a'].fillna(df['score_a'].mean())`,
    },
  ],
}

/** Per-mode defaults and API routing. */
export const MODES = {
  python: {
    label: 'Python',
    endpoint: '/api/generate',
    ms: 900,
    subtitle: 'paste a small Python snippet — get back an execution GIF (code, variables, output, step by step)',
    hint: 'only a small set of stdlib imports is allowed (math, random, itertools, …) — press ⌘+Enter to generate',
    defaultCode: CODE_PRESETS.python[0].code,
  },
  pandas: {
    label: 'Pandas',
    endpoint: '/api/generate-pandas',
    ms: 1100,
    subtitle: 'paste pandas code — get back a step-by-step GIF with real DataFrame tables and diff highlighting',
    hint: 'pandas + numpy are available — stdlib imports allowed too — press ⌘+Enter to generate',
    defaultCode: CODE_PRESETS.pandas[0].code,
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
