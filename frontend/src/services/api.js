/**
 * API Service layer for Code Explainer backend endpoints.
 * Centralizes all direct HTTP/fetch calls so components do not make direct API calls.
 */

export const ENDPOINTS = {
  NUMPY: '/api/visualize-numpy',
  PANDAS: '/api/visualize-pandas',
  GENERATE: '/api/generate',
  GENERATE_PANDAS: '/api/generate-pandas',
};

/**
 * Fetch NumPy visualizer model from the backend.
 *
 * @param {string} code - The NumPy code snippet to execute and visualize
 * @returns {Promise<{ viz: object|null, error: string|null }>}
 */
export async function fetchNumpyModel(code) {
  let response;
  try {
    response = await fetch(ENDPOINTS.NUMPY, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
  } catch {
    return { viz: null, error: 'could not reach the server — is the backend running?' };
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    return { viz: null, error: `server returned ${response.status} with an unreadable body` };
  }
  if (!response.ok) return { viz: null, error: payload.error || `server returned ${response.status}` };
  if (!payload.target || !payload.arrays) return { viz: null, error: 'server returned an unexpected payload' };
  return { viz: payload, error: null };
}

/**
 * Fetch Pandas visualizer model from the backend.
 *
 * @param {string} code - The Pandas code snippet to execute and visualize
 * @returns {Promise<{ viz: object|null, error: string|null }>}
 */
export async function fetchPandasModel(code) {
  let response;
  try {
    response = await fetch(ENDPOINTS.PANDAS, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
  } catch {
    return { viz: null, error: 'could not reach the server — is the backend running?' };
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    return { viz: null, error: `server returned ${response.status} with an unreadable body` };
  }
  if (!response.ok) return { viz: null, error: payload.error || `server returned ${response.status}` };
  if (!payload.target || !payload.dfs) return { viz: null, error: 'server returned an unexpected payload' };
  return { viz: payload, error: null };
}
