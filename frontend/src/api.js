const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')
const TOKEN_KEY = 'bt_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(path, { method = 'GET', body, token } = {}) {
  const auth = token ?? getToken()
  let response
  try {
    response = await fetch(BASE + path, {
      method,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError("Can't reach the server. Check your connection.", 0)
  }

  if (response.status === 401) {
    // Token was revoked or wrong — kick back to the login screen.
    window.dispatchEvent(new CustomEvent('bt:unauthorized'))
    throw new ApiError('Not authorised', 401)
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
      else if (Array.isArray(data.detail) && data.detail[0]?.msg) detail = data.detail[0].msg
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return null
  return response.json()
}

const qs = (params) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) search.set(k, v)
  })
  const s = search.toString()
  return s ? `?${s}` : ''
}

export const api = {
  verifyToken: (token) => request('/me', { token }),
  categories: () => request('/categories'),
  transactions: (month, year) => request(`/transactions${qs({ month, year })}`),
  needsReview: () => request('/transactions/needs-review'),
  addManual: (payload) => request('/transactions/manual', { method: 'POST', body: payload }),
  setCategory: (id, category) =>
    request(`/transactions/${id}/category`, { method: 'PATCH', body: { category } }),
  deleteTransaction: (id) => request(`/transactions/${id}`, { method: 'DELETE' }),
  summary: (month, year) => request(`/budget/summary${qs({ month, year })}`),
  trend: (month, year) => request(`/stats/daily${qs({ month, year })}`),
  budgetLimits: () => request('/budget/limits'),
  setBudget: (category, monthly_limit) =>
    request('/budget/set', { method: 'POST', body: { category, monthly_limit } }),
}
