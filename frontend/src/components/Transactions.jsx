import { useState } from 'react'

import { dateTime, moneyExact } from '../format.js'
import { TrashIcon } from './Icons.jsx'

export default function Transactions({ transactions, categories, onRecategorise, onDelete }) {
  const [editing, setEditing] = useState(null)

  if (!transactions) return <div className="empty">Loading…</div>
  if (!transactions.length) return <div className="card"><div className="empty">No transactions this month.</div></div>

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">All transactions</h2>
        <span className="card-sub">{transactions.length} this month</span>
      </div>

      <div className="rows">
        {transactions.map((t) => (
          <div key={t.id}>
            <div className="row">
              <div className="row-main">
                <div className="row-title">{t.merchant || 'Unknown'}</div>
                <div className="row-meta">
                  <button
                    className="chip"
                    onClick={() => setEditing(editing === t.id ? null : t.id)}
                    style={{ border: 0, cursor: 'pointer' }}
                    title="Change category"
                  >
                    {t.category}
                  </button>
                  <span>{dateTime(t.created_at)}</span>
                  {t.source === 'sms' && <span>· SMS</span>}
                </div>
              </div>

              <div className={`row-amount${t.direction === 'credit' ? ' credit' : ''}`}>
                {t.direction === 'credit' ? '+' : '−'}
                {moneyExact(t.amount)}
              </div>

              <button
                className="icon-btn"
                onClick={() => {
                  if (confirm(`Delete this ${moneyExact(t.amount)} transaction?`)) onDelete(t.id)
                }}
                aria-label="Delete transaction"
              >
                <TrashIcon />
              </button>
            </div>

            {editing === t.id && (
              <div style={{ paddingBottom: 12 }}>
                {t.raw_text && <div className="raw-sms">{t.raw_text}</div>}
                <div className="cat-grid">
                  {categories.map((c) => (
                    <button
                      key={c}
                      onClick={() => {
                        onRecategorise(t.id, c)
                        setEditing(null)
                      }}
                      style={c === t.category ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
                    >
                      {c}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
