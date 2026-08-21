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
  // FormData (image upload) must NOT be JSON-stringified and must NOT get an
  // explicit Content-Type — the browser sets multipart/form-data with the
  // right boundary itself; setting it by hand breaks the boundary.
  const isForm = body instanceof FormData
  const send = () => fetch(BASE + path, {
    method,
    headers: {
      ...(body && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
    },
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  })

  let response
  try {
    response = await send()
  } catch {
    // One retry, GET only. The backend is on a free tier that can drop the
    // first connection while waking, and a single transient failure showing
    // "Can't reach the server" on an app that's actually fine is worse than
    // a second of extra wait. Never retried for writes: a POST that failed
    // *after* the server processed it would double-book the transaction.
    if (method !== 'GET') throw new ApiError("Can't reach the server. Check your connection.", 0)
    await new Promise((r) => setTimeout(r, 1200))
    try {
      response = await send()
    } catch {
      throw new ApiError("Can't reach the server. Check your connection.", 0)
    }
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
  // Manual fallback for the same pipe MacroDroid uses — paste a bank SMS or
  // email-alert body in by hand when no automation caught it (e.g. a bank
  // that only emails, never texts, for a given transaction).
  pasteAlert: (text) => request('/sms/ingest', { method: 'POST', body: { text } }),
  setCategory: (id, category) =>
    request(`/transactions/${id}/category`, { method: 'PATCH', body: { category } }),
  // The Review tab's richer "who is this?" answer — merchant/friend/wallet/
  // self, resolved server-side into the right kind (lend vs. repayment etc.)
  // and, by default, remembered so the same counterparty is never asked again.
  classifyTransaction: (id, payload) =>
    request(`/transactions/${id}/classify`, { method: 'PATCH', body: payload }),
  renameMerchant: (id, merchant) =>
    request(`/transactions/${id}/merchant`, { method: 'PATCH', body: { merchant } }),
  deleteTransaction: (id) => request(`/transactions/${id}`, { method: 'DELETE' }),
  aiStatus: () => request('/ai/status'),
  // `note` is the user's correction/context alongside the photo — e.g.
  // "this was for a friend's birthday, categorise as Entertainment" —
  // treated server-side as authoritative over what the image alone shows.
  scanReceipt: (file, note) => {
    const form = new FormData()
    form.append('image', file)
    if (note) form.append('note', note)
    return request('/ai/scan-receipt', { method: 'POST', body: form })
  },
  summary: (month, year) => request(`/budget/summary${qs({ month, year })}`),
  trend: (month, year) => request(`/stats/daily${qs({ month, year })}`),
  budgetLimits: () => request('/budget/limits'),
  setBudget: (category, monthly_limit) =>
    request('/budget/set', { method: 'POST', body: { category, monthly_limit } }),

  // ---- fuel & vehicles ----
  vehicles: () => request('/vehicles'),
  fuelFills: (vehicleId) => request(`/fuel/fills${qs({ vehicle_id: vehicleId })}`),
  addFuelFill: (payload) => request('/fuel/fills', { method: 'POST', body: payload }),
  deleteFuelFill: (id) => request(`/fuel/fills/${id}`, { method: 'DELETE' }),
  mileage: (vehicleId) => request(`/fuel/mileage${qs({ vehicle_id: vehicleId })}`),

  // ---- to-dos ----
  todos: () => request('/todos'),
  addTodo: (text) => request('/todos', { method: 'POST', body: { text } }),
  updateTodo: (id, payload) => request(`/todos/${id}`, { method: 'PATCH', body: payload }),
  deleteTodo: (id) => request(`/todos/${id}`, { method: 'DELETE' }),
  clearCompletedTodos: () => request('/todos/clear-completed', { method: 'POST' }),

  // ---- memory ----
  payees: () => request('/payees'),
  forgetPayee: (key) => request(`/payees/${encodeURIComponent(key)}`, { method: 'DELETE' }),

  // ---- lending ----
  lending: () => request('/lending'),
  // Repaid outside the bank (cash) — the app can't see that on its own.
  markRepaid: (person, amount) =>
    request(`/lending/${encodeURIComponent(person)}/repaid${qs({ amount })}`, { method: 'POST' }),
  snoozeLendingReminder: (person, days = 3) =>
    request(`/lending/${encodeURIComponent(person)}/snooze${qs({ days })}`, { method: 'POST' }),
  clearLendingReminder: (person) =>
    request(`/lending/${encodeURIComponent(person)}/reminder`, { method: 'DELETE' }),
}
