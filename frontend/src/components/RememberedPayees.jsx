import { useEffect, useState } from 'react'

import { api } from '../api.js'
import { TrashIcon } from './Icons.jsx'

/**
 * Every "who is this?" answer — and, now, a way to correct one.
 *
 * Forgetting an answer (the bin) only stops it being applied in future. That
 * was the only control here, and it isn't enough when the answer was wrong:
 * RADDLINS FOOD, a bakery, was answered as "a person", so its payments were
 * filed as money lent out and it sat in the who-owes-you list beside actual
 * friends. Because a remembered payee is never asked about again, nothing
 * would ever have surfaced it, and forgetting it would have fixed only the
 * next payment while leaving every wrong one in place.
 *
 * So: change the answer, and optionally re-file everything it already decided.
 */

const KINDS = [
  { kind: 'expense', label: 'A shop', hint: 'counts as spending' },
  { kind: 'friend', label: 'A person', hint: 'lending / repayment' },
  { kind: 'friend_settle', label: 'A person, settling', hint: 'a debt either way' },
  { kind: 'wallet', label: 'My wallet', hint: 'top-up, not spending' },
  { kind: 'self', label: 'My account', hint: 'internal transfer' },
]

const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.kind, k.label]))

function PayeeRow({ payee, categories, onSaved }) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState(payee.kind)
  const [category, setCategory] = useState(payee.default_category || 'Food & Dining')
  const [applyToPast, setApplyToPast] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    setBusy(true)
    setError('')
    try {
      const result = await api.updatePayee(payee.key, {
        kind,
        category: kind === 'expense' ? category : undefined,
        apply_to_past: applyToPast,
      })
      setOpen(false)
      onSaved(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function forget() {
    if (!confirm(`Forget "${payee.label}"?\n\nThe next transaction from them will ask who they are again. Existing transactions aren't changed.`)) return
    setBusy(true)
    try {
      await api.forgetPayee(payee.key)
      onSaved(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ borderTop: '1px solid var(--border)' }}>
      <div className="row" style={{ borderTop: 0 }}>
        <div className="row-main">
          <div className="row-title">{payee.label}</div>
          <div className="row-meta">
            {KIND_LABEL[payee.kind] || payee.kind}
            {payee.default_category ? ` · ${payee.default_category}` : ''}
            {payee.used_by > 0 && ` · decided ${payee.used_by} transaction${payee.used_by === 1 ? '' : 's'}`}
          </div>
          {/* Says WHY it looks wrong, so it can be checked against what you
              know rather than simply obeyed. */}
          {payee.business_hint && (
            <div style={{ marginTop: 4, fontSize: 12, color: 'var(--warn)' }}>
              Filed as a person, but {payee.business_hint}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          style={{ background: 'none', border: 0, color: 'var(--accent)', fontSize: 13, padding: '0 8px' }}
        >
          {open ? 'Cancel' : 'Change'}
        </button>
        <button className="icon-btn" aria-label={`Forget ${payee.label}`} disabled={busy} onClick={forget}>
          <TrashIcon />
        </button>
      </div>

      {open && (
        <div className="who-detail" style={{ paddingBottom: 12 }}>
          <div className="cat-grid">
            {KINDS.map((k) => (
              <button
                key={k.kind}
                type="button"
                aria-pressed={kind === k.kind}
                onClick={() => setKind(k.kind)}
                style={kind === k.kind ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
              >
                {k.label}
              </button>
            ))}
          </div>

          {kind === 'expense' && (
            <div className="cat-grid" style={{ marginTop: 8 }}>
              {categories.filter((c) => c !== 'Lending' && c !== 'Transfer').map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-pressed={category === c}
                  onClick={() => setCategory(c)}
                  style={category === c ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          {payee.used_by > 0 && (
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 12, fontSize: 13 }}>
              <input
                type="checkbox"
                checked={applyToPast}
                onChange={(e) => setApplyToPast(e.target.checked)}
                style={{ width: 18, height: 18, flex: 'none', marginTop: 1 }}
              />
              <span>
                Also fix the {payee.used_by} transaction{payee.used_by === 1 ? '' : 's'} this
                already decided. <span style={{ color: 'var(--text-2)' }}>This moves past totals.</span>
              </span>
            </label>
          )}

          {error && <div className="banner error" style={{ marginTop: 10 }}>{error}</div>}

          <button className="btn" style={{ marginTop: 10 }} onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save correction'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function RememberedPayees({ categories, onChanged }) {
  const [payees, setPayees] = useState(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    api.payees().then(setPayees).catch(() => setPayees([]))
  }, [])

  const suspect = (payees || []).filter((p) => p.business_hint)

  async function reload(result) {
    if (result?.updated) {
      setNote(`Re-filed ${result.updated} transaction${result.updated === 1 ? '' : 's'}.`)
    }
    setPayees(await api.payees().catch(() => payees))
    // Totals may have moved — the dashboard is showing the old ones.
    onChanged?.()
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Remembered</h2>
        <span className="card-sub">{payees ? `${payees.length} answered` : 'loading…'}</span>
      </div>
      <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-2)' }}>
        Every &quot;who is this?&quot; answer, saved for good — a shop, person, wallet, or your own
        account never gets asked about twice once it&apos;s here. Tap Change if one is wrong.
      </p>

      {suspect.length > 0 && (
        <div className="banner" style={{ marginBottom: 12 }}>
          {suspect.length === 1
            ? `${suspect[0].label} is filed as a person but looks like a shop.`
            : `${suspect.length} answers are filed as people but look like shops.`}{' '}
          Left as-is, every future payment there is counted as lending, not spending.
        </div>
      )}

      {note && <div className="banner ok" style={{ marginBottom: 12 }}>{note}</div>}

      {payees === null && <div className="empty">Loading…</div>}
      {payees && payees.length === 0 && <div className="empty">Nothing remembered yet.</div>}
      {payees && payees.length > 0 && (
        <div>
          {payees.map((p) => (
            <PayeeRow key={p.key} payee={p} categories={categories} onSaved={reload} />
          ))}
        </div>
      )}
    </section>
  )
}
