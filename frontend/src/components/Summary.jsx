import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { money, moneyCompact } from '../format.js'
import MerchantLogo from './MerchantLogo.jsx'

const PERIODS = [
  { id: 'day', label: 'Day' },
  { id: 'week', label: 'Week' },
  { id: 'month', label: 'Month' },
]

/**
 * Day / week / month review, with the comparison against the period before —
 * which is the part that makes a number mean anything. "₹554 today" says
 * little on its own; "₹554, down 38% on yesterday" is an actual signal.
 */
export default function Summary() {
  const [period, setPeriod] = useState('week')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    setData(null); setError('')
    api.statsSummary(period)
      .then((d) => { if (live) setData(d) })
      .catch((e) => { if (live) setError(e.message) })
    // Guards against a slow request for the previous period landing after a
    // fast one for the period the user has since switched to.
    return () => { live = false }
  }, [period])

  const biggest = data?.categories?.[0]?.spent || 0

  return (
    <>
      <div className="seg" style={{ marginBottom: 14 }}>
        {PERIODS.map((p) => (
          <button key={p.id} type="button" aria-pressed={period === p.id} onClick={() => setPeriod(p.id)}>
            {p.label}
          </button>
        ))}
      </div>

      {error && <div className="banner error">{error}</div>}
      {!data && !error && <div className="empty">Loading…</div>}

      {data && (
        <>
          <section className="card">
            <div className="hero-label">{data.label}</div>
            <div className="hero-value">{moneyCompact(data.total_spent)}</div>
            <div className="hero-meta">
              {data.delta_pct === null ? (
                <span>nothing spent {data.previous_label.toLowerCase()} to compare with</span>
              ) : (
                <span>
                  <span className={data.delta_pct > 0 ? 'neg' : 'pos'}>
                    {data.delta_pct > 0 ? '↑' : '↓'} {Math.abs(data.delta_pct)}%
                  </span>{' '}
                  vs {data.previous_label.toLowerCase()} ({money(data.previous_spent)})
                </span>
              )}
              <span>{data.transaction_count} transaction{data.transaction_count === 1 ? '' : 's'}</span>
              {data.total_income > 0 && <span>{money(data.total_income)} in</span>}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2 className="card-title">Where it went</h2>
              <span className="card-sub">{data.categories.length} categories</span>
            </div>
            {data.categories.length === 0 ? (
              <div className="empty">Nothing spent in this period.</div>
            ) : (
              data.categories.map((c) => (
                <div key={c.category} style={{ marginBottom: 10 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                    <span>{c.category}</span>
                    <span style={{ color: 'var(--text-2)' }}>{money(c.spent)}</span>
                  </div>
                  {/* Bars are relative to the largest category, not to a budget:
                      this view is about proportion, not about limits. */}
                  <div style={{ height: 6, borderRadius: 3, background: 'var(--surface-2)' }}>
                    <div style={{
                      height: '100%', borderRadius: 3, background: 'var(--accent)',
                      width: `${biggest ? Math.max(2, (c.spent / biggest) * 100) : 0}%`,
                    }} />
                  </div>
                </div>
              ))
            )}
          </section>

          {data.merchants.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2 className="card-title">Top merchants</h2>
                <span className="card-sub">most spent first</span>
              </div>
              <div className="rows">
                {data.merchants.map((m) => (
                  <div className="row" key={m.merchant}>
                    <MerchantLogo name={m.merchant} size={30} />
                    <div className="row-main">
                      <div className="row-title">{m.merchant}</div>
                      <div className="row-meta">{m.count} time{m.count === 1 ? '' : 's'}</div>
                    </div>
                    <div className="row-amount">{money(m.spent)}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </>
  )
}
