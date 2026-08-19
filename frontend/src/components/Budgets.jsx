import { useEffect, useState } from 'react'

import { api } from '../api.js'
import { money } from '../format.js'

const PAYEE_KIND_LABEL = {
  expense: 'Shop',
  friend: 'Person (lending)',
  friend_settle: 'Person (settling a debt)',
  wallet: 'Wallet',
  self: 'My account',
}

export default function Budgets({ categories, limits, spentByCategory, onSave, onSignOut }) {
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [payees, setPayees] = useState(null)

  useEffect(() => {
    const next = {}
    limits.forEach((b) => { next[b.category] = String(b.monthly_limit) })
    setDraft(next)
  }, [limits])

  useEffect(() => {
    api.payees().then(setPayees).catch(() => setPayees([]))
  }, [])

  const total = Object.values(draft).reduce((sum, v) => sum + (Number.parseFloat(v) || 0), 0)

  async function save() {
    setBusy(true)
    setSaved(false)
    try {
      const current = new Map(limits.map((b) => [b.category, b.monthly_limit]))
      const changes = []
      categories.forEach((c) => {
        const next = Number.parseFloat(draft[c] ?? '') || 0
        const prev = current.get(c) ?? 0
        if (next !== prev) changes.push([c, next])
      })
      // Sequential rather than parallel — SQLite locks on concurrent writes.
      for (const [category, value] of changes) await onSave(category, value)
      setSaved(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Monthly limits</h2>
          <span className="card-sub">total {money(total)}</span>
        </div>

        <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-2)' }}>
          Leave a category blank or set it to 0 to stop tracking a budget for it.
        </p>

        {categories.map((c) => {
          const spent = spentByCategory[c] || 0
          return (
            <div className="field" key={c} style={{ marginBottom: 10 }}>
              <label htmlFor={`b-${c}`}>
                {c}
                {spent > 0 && <span style={{ color: 'var(--muted)', fontWeight: 400 }}> · {money(spent)} spent</span>}
              </label>
              <input
                id={`b-${c}`}
                type="number"
                inputMode="numeric"
                min="0"
                step="100"
                placeholder="0"
                value={draft[c] ?? ''}
                onChange={(e) => setDraft({ ...draft, [c]: e.target.value })}
              />
            </div>
          )
        })}

        {saved && <div className="banner ok" style={{ marginBottom: 12 }}>Budgets saved.</div>}

        <button className="btn" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : 'Save budgets'}
        </button>
      </section>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Remembered</h2>
          <span className="card-sub">{payees ? `${payees.length} answered` : 'loading…'}</span>
        </div>
        <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-2)' }}>
          Every "who is this?" answer, saved for good — a shop, person, wallet, or your own
          account never gets asked about twice once it's here.
        </p>

        {payees === null && <div className="empty">Loading…</div>}
        {payees && payees.length === 0 && <div className="empty">Nothing remembered yet.</div>}
        {payees && payees.length > 0 && (
          <div className="rows">
            {payees.map((p) => (
              <div className="row" key={p.key}>
                <div className="row-main">
                  <div className="row-title">{p.label}</div>
                  <div className="row-meta">
                    {PAYEE_KIND_LABEL[p.kind] || p.kind}
                    {p.default_category ? ` · ${p.default_category}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {onSignOut && (
        <div style={{ marginTop: 8, paddingTop: 16, textAlign: 'center' }}>
          <button
            onClick={onSignOut}
            style={{
              background: 'none', border: 0, padding: '6px 10px',
              color: 'var(--muted)', fontSize: 13, textDecoration: 'underline',
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </>
  )
}
