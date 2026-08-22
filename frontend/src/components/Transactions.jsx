import { useState } from 'react'

import { dateTime, moneyExact } from '../format.js'
import { exportCSV } from '../export.js'
import { DownloadIcon, TrashIcon } from './Icons.jsx'
import MerchantLogo from './MerchantLogo.jsx'

// Money that left as spending vs. money that just moved or came back — shown
// instead of the category chip so a lend/top-up never reads as a purchase.
const KIND_LABEL = {
  income: 'Received',
  transfer: 'Transfer',
  topup: 'Wallet top-up',
  lend: 'Lent out',
  repayment: 'Repaid to you',
}
const INFLOW = new Set(['income', 'repayment'])

const SOURCE_LABEL = {
  sms: 'SMS',
  gmail: 'email',
  screenshot: 'screenshot',
  import: 'imported',
}

export default function Transactions({ transactions, categories, monthLabel, onRecategorise, onDelete }) {
  const [editing, setEditing] = useState(null)

  if (!transactions) return <div className="empty">Loading…</div>
  if (!transactions.length) return <div className="card"><div className="empty">No transactions this month.</div></div>

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">All transactions</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="card-sub">{transactions.length} this month</span>
          <button
            type="button"
            onClick={() => exportCSV(transactions, monthLabel)}
            className="icon-btn neutral"
            style={{ color: 'var(--accent)' }}
            title="Export CSV"
            aria-label="Export CSV"
          >
            <DownloadIcon />
          </button>
        </div>
      </div>

      <div className="rows">
        {transactions.map((t) => {
          const kindLabel = KIND_LABEL[t.kind]
          const isInflow = INFLOW.has(t.kind)
          const isMoved = t.kind === 'transfer' || t.kind === 'topup'
          return (
          <div key={t.id}>
            <div className="row">
              <MerchantLogo name={t.merchant} size={32} />
              <div className="row-main">
                <div className="row-title">
                  {t.merchant || 'Unknown'}
                  {/* The whole point of the note is being visible later — an
                      "Other" row without it is unidentifiable. */}
                  {t.note && <span style={{ color: 'var(--muted)', fontWeight: 400 }}> · {t.note}</span>}
                </div>
                <div className="row-meta">
                  {kindLabel ? (
                    <span className="chip">{kindLabel}</span>
                  ) : (
                    <button
                      className="chip"
                      onClick={() => setEditing(editing === t.id ? null : t.id)}
                      style={{ border: 0, cursor: 'pointer' }}
                      title="Change category"
                    >
                      {t.category}
                    </button>
                  )}
                  {t.counterparty && <span>{t.counterparty}</span>}
                  <span>{dateTime(t.created_at)}</span>
                  {/* Where a row came from. "screenshot" matters most: those
                      were read off a photo by a vision model rather than
                      quoted from a bank, so they're the ones worth a second
                      look if a figure seems wrong. */}
                  {SOURCE_LABEL[t.source] && <span>· {SOURCE_LABEL[t.source]}</span>}
                </div>
              </div>

              <div
                className={`row-amount${isInflow ? ' credit' : ''}`}
                style={isMoved ? { color: 'var(--muted)' } : undefined}
              >
                {isInflow ? '+' : isMoved ? '↔ ' : t.direction === 'credit' ? '+' : '−'}
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
          )
        })}
      </div>
    </section>
  )
}
