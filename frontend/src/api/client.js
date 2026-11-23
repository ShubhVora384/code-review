const BASE = '/api/v1'

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function request(path, init) {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })

  if (!res.ok) {
    let message = `Request failed (${res.status})`
    let detail
    try {
      const body = await res.json()
      // FastAPI validation errors arrive as a list under `detail`.
      if (Array.isArray(body?.detail)) {
        message = body.detail.map((d) => `${d.loc?.join('.')}: ${d.msg}`).join('; ')
      } else {
        message = body?.message || body?.detail || message
        detail = body?.detail && body?.message ? body.detail : undefined
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message, detail)
  }

  if (res.status === 204) return undefined
  return res.json()
}

export const api = {
  health: () => request('/health'),
  budget: () => request('/budget'),
  keys: () => request('/keys'),

  browse: (path = '', showHidden = false) =>
    request(`/browse?path=${encodeURIComponent(path)}` + (showHidden ? '&show_hidden=true' : '')),

  listReviews: (limit = 30) => request(`/reviews?limit=${limit}`),
  getReview: (id) => request(`/reviews/${id}`),
  getEvents: (id, after = 0) => request(`/reviews/${id}/events?after=${after}`),

  /**
   * @param {{source_type: 'git'|'local', source_ref: string, branch?: string|null,
   *          access_token?: string|null, options: object}} body
   */
  createReview: (body) =>
    request('/reviews', { method: 'POST', body: JSON.stringify(body) }),

  uploadReview: (file, options) => {
    const form = new FormData()
    form.append('file', file)
    form.append('options', JSON.stringify(options))
    return request('/reviews/upload', { method: 'POST', body: form })
  },

  cancelReview: (id) => request(`/reviews/${id}/cancel`, { method: 'POST' }),

  enableShare: (id) => request(`/reviews/${id}/share`, { method: 'POST' }),
  revokeShare: (id) => request(`/reviews/${id}/share`, { method: 'DELETE' }),
  getShared: (token) => request(`/share/${token}`),
  deleteReview: (id) => request(`/reviews/${id}`, { method: 'DELETE' }),

  reportUrl: (id) => `${BASE}/reviews/${id}/report.md`,
  streamUrl: (id) => `${BASE}/reviews/${id}/stream`,
}
