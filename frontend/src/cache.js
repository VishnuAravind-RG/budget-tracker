/**
 * Last-known-good snapshot of a month's data, kept in localStorage.
 *
 * The app fetches seven endpoints before it can draw anything, and on a free
 * hosting tier that's a few seconds of blank "Loading…" every single time it
 * opens — even when the numbers haven't changed since a minute ago. Painting
 * the previous snapshot immediately and refreshing behind it makes the app
 * feel instant while staying honest: the refresh still runs every time and
 * overwrites whatever it finds.
 *
 * Deliberately NOT a source of truth. It's only ever seeded from a successful
 * fetch, it never suppresses a refresh, and anything unreadable or from an
 * older shape is dropped rather than migrated — a stale cache must never be
 * able to show wrong money.
 */

const KEY = 'bt_cache_v1'
// Bump when the cached shape changes so old entries are discarded, not
// half-read into a newer UI that expects different fields.
const SHAPE = 1
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

const slot = (month, year) => `${year}-${month}`

export function readCache(month, year) {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed.shape !== SHAPE) return null
    const entry = parsed.months?.[slot(month, year)]
    if (!entry) return null
    if (Date.now() - entry.savedAt > MAX_AGE_MS) return null
    return entry.data
  } catch {
    // Private mode, cleared storage, corrupt JSON — all just mean "no cache".
    return null
  }
}

export function writeCache(month, year, data) {
  try {
    let parsed = { shape: SHAPE, months: {} }
    try {
      const existing = JSON.parse(localStorage.getItem(KEY) || 'null')
      if (existing?.shape === SHAPE) parsed = existing
    } catch { /* start fresh */ }

    parsed.months[slot(month, year)] = { savedAt: Date.now(), data }

    // Keep only the three most recent months — the whole point is a fast
    // first paint, not an offline archive, and localStorage quota is small.
    const slots = Object.entries(parsed.months)
      .sort((a, b) => b[1].savedAt - a[1].savedAt)
      .slice(0, 3)
    parsed.months = Object.fromEntries(slots)

    localStorage.setItem(KEY, JSON.stringify(parsed))
  } catch {
    // Quota exceeded or storage disabled: the app works fine without a cache.
  }
}

export function clearCache() {
  try {
    localStorage.removeItem(KEY)
  } catch { /* nothing to do */ }
}
