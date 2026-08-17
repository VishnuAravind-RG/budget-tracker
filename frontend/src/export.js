/** CSV export — the month currently loaded in the app, one row per transaction. */
export function exportCSV(transactions, monthLabel) {
  const header = 'Date,Merchant,Category,Kind,Amount,Direction,Source\n'
  const rows = transactions
    .map((t) => {
      const date = new Date(t.created_at).toLocaleString('en-IN', {
        day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
      })
      const merchant = `"${(t.merchant || '').replace(/"/g, '""')}"`
      return [date, merchant, t.category, t.kind, t.amount, t.direction, t.source].join(',')
    })
    .join('\n')

  const blob = new Blob([header + rows], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `budget-${monthLabel.replace(/\s+/g, '-').toLowerCase()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
