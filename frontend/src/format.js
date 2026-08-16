const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const inrPaise = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** ₹1,23,456 — whole rupees, Indian digit grouping. */
export function money(value) {
  return `₹${inr.format(Math.round(value || 0))}`
}

/** Exact amount for transaction rows, where the paise matter. */
export function moneyExact(value) {
  return `₹${inrPaise.format(value || 0)}`
}

/** Compact form for the hero figure: ₹1.2L, ₹45.3K. */
export function moneyCompact(value) {
  const n = Math.round(Math.abs(value || 0))
  const sign = value < 0 ? '-' : ''
  if (n >= 10000000) return `${sign}₹${(n / 10000000).toFixed(1)}Cr`
  if (n >= 100000) return `${sign}₹${(n / 100000).toFixed(1)}L`
  return `${sign}₹${inr.format(n)}`
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']

export const monthName = (m) => MONTHS[m - 1] || ''

/** "17 Aug, 4:32 pm" — backend sends UTC, the browser renders it locally. */
export function dateTime(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
  })
}

export function dayLabel(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number)
  return `${d} ${MONTHS[m - 1]?.slice(0, 3)}`
}
