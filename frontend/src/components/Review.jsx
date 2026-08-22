import { useState } from 'react'
import { dateTime, moneyExact } from '../format.js'
import { AccountIcon, PersonIcon, ShopIcon, WalletIcon } from './Icons.jsx'
import MerchantLogo from './MerchantLogo.jsx'

const CHOICES = [
  { kind: 'expense', label: 'A shop', hint: 'counts as spending', Icon: ShopIcon },
  { kind: 'friend', label: 'A person', hint: 'lending / repayment', Icon: PersonIcon },
  { kind: 'wallet', label: 'My wallet', hint: 'top-up, not spending', Icon: WalletIcon },
  { kind: 'self', label: 'My account', hint: 'internal transfer', Icon: AccountIcon },
]

function ReviewItem({ t, categories, onClassify }) {
  const [choice, setChoice] = useState(null)
  const [label, setLabel] = useState(t.merchant === 'Unknown' ? '' : t.merchant || '')
  const [category, setCategory] = useState(t.category !== 'Uncategorized' ? t.category : 'Food & Dining')
  // Only meaningful when choice === 'friend' — see AddExpense.jsx for the
  // full reasoning: 'settle' sends kind='friend_settle' so paying back a
  // debt you owe (or someone lending to you) doesn't get filed as if you
  // were the one lending money out.
  const [personIntent, setPersonIntent] = useState('lend')
  // Only asked for when the category is "Other", which explains nothing on
  // its own — "HARI HARAN AGENCIES 3 · Other" tells you nothing months later.
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  async function save() {
    if (!choice || busy) return
    setBusy(true)
    try {
      await onClassify(t.id, {
        kind: choice === 'friend' && personIntent === 'settle' ? 'friend_settle' : choice,
        label: label.trim() || undefined,
        category: choice === 'expense' ? category : undefined,
        note: note.trim() || undefined,
        remember: true,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="row" style={{ padding: 0, borderTop: 0 }}>
        <MerchantLogo name={t.merchant} size={34} />
        <div className="row-main">
          <div className="row-title">{t.merchant || 'Unknown'}</div>
          <div className="row-meta">{dateTime(t.created_at)}</div>
        </div>
        <div className="row-amount">
          {t.direction === 'credit' ? '+' : '−'}
          {moneyExact(t.amount)}
        </div>
      </div>

      {t.raw_text && <div className="raw-sms">{t.raw_text}</div>}

      <div className="who-grid">
        {CHOICES.map(({ kind, label: choiceLabel, hint, Icon }) => (
          <button
            key={kind}
            type="button"
            className="who-btn"
            aria-pressed={choice === kind}
            onClick={() => setChoice(kind)}
          >
            <Icon />
            <span className="who-label">{choiceLabel}</span>
            <span className="who-hint">
              {kind === 'friend' ? (t.direction === 'debit' ? 'money lent out' : 'paying me back') : hint}
            </span>
          </button>
        ))}
      </div>

      {/* Answering "a person" for a shop is the expensive mistake: it files
          the payment as money lent out rather than spending, puts the shop in
          the who-owes-you list, and — because a remembered answer is never
          asked about again — keeps doing so silently forever. A bakery spent
          weeks in there. The hint says WHY it looks like a business, so it can
          be checked against what you actually know, not just obeyed. */}
      {choice === 'friend' && t.business_hint && (
        <div className="banner" style={{ marginTop: 10 }}>
          This looks like a shop, not a person — {t.business_hint}. Sure?
        </div>
      )}

      {choice && (
        <div className="who-detail">
          {choice === 'friend' && (
            <div className="seg" style={{ marginBottom: 10 }}>
              <button type="button" aria-pressed={personIntent === 'lend'} onClick={() => setPersonIntent('lend')}>
                {t.direction === 'debit' ? 'Lending them money' : "They're paying me back"}
              </button>
              <button type="button" aria-pressed={personIntent === 'settle'} onClick={() => setPersonIntent('settle')}>
                {t.direction === 'debit' ? 'Paying back what I owe' : "They're lending me money"}
              </button>
            </div>
          )}

          <input
            type="text"
            placeholder={choice === 'friend' ? 'Their name' : 'Name this'}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={80}
          />

          {choice === 'expense' && (
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

          {choice === 'expense' && category === 'Other' && (
            <input
              type="text"
              style={{ marginTop: 8 }}
              placeholder="What was this? e.g. hardware, gift, repair"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={200}
            />
          )}

          <button className="btn" style={{ marginTop: 10 }} onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save & remember'}
          </button>
        </div>
      )}
    </section>
  )
}

export default function Review({ items, categories, onClassify }) {
  if (!items) return <div className="empty">Loading…</div>

  if (!items.length) {
    return (
      <section className="card">
        <div className="empty">
          Nothing to review.
          <div style={{ marginTop: 6, fontSize: 13 }}>
            New counterparties land here — answer once and it's remembered for good.
          </div>
        </div>
      </section>
    )
  }

  return (
    <>
      <div className="banner error">
        {items.length} transaction{items.length > 1 ? 's' : ''} — who {items.length > 1 ? 'are these' : 'is this'}?
      </div>

      {items.map((t) => (
        <ReviewItem key={t.id} t={t} categories={categories} onClassify={onClassify} />
      ))}
    </>
  )
}
