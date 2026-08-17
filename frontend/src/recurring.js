/**
 * Groups expenses by merchant+category and flags the ones seen in 2+
 * distinct calendar months as likely-recurring — subscriptions, rent, a
 * regular grocery run. Needs a wider transaction window than a single month
 * to have anything to compare across (see App.jsx's recurringSource fetch).
 */
export function detectRecurring(transactions) {
  const groups = new Map()

  for (const t of transactions) {
    if (t.kind !== 'expense') continue
    const name = (t.merchant || '').trim()
    if (!name || name === 'Unknown') continue
    const key = `${name.toLowerCase()}::${t.category}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(t)
  }

  const recurring = []
  for (const txs of groups.values()) {
    const months = new Set(txs.map((t) => new Date(t.created_at).toISOString().slice(0, 7)))
    if (months.size < 2) continue
    const avgAmount = txs.reduce((s, t) => s + t.amount, 0) / txs.length
    const latest = txs.reduce((a, t) => (t.created_at > a.created_at ? t : a), txs[0])
    recurring.push({ merchant: latest.merchant, category: latest.category, avgAmount, months: months.size })
  }

  return recurring.sort((a, b) => b.avgAmount - a.avgAmount)
}
