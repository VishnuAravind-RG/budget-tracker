import { useState } from 'react'
import { AccountIcon, PersonIcon, PinIcon, ShopIcon, WalletIcon } from './Icons.jsx'
import LocationConsent from './LocationConsent.jsx'
import { getLocationConsent, locationSupported } from '../location.js'

const KIND_CHOICES = [
  { kind: 'expense', label: 'A shop', hint: 'counts as spending', Icon: ShopIcon },
  { kind: 'friend', label: 'A person', hint: 'lending / repayment', Icon: PersonIcon },
  { kind: 'wallet', label: 'My wallet', hint: 'top-up, not spending', Icon: WalletIcon },
  { kind: 'self', label: 'My account', hint: 'internal transfer', Icon: AccountIcon },
]

export default function AddExpense({ categories, onAdd }) {
  const [amount, setAmount] = useState('')
  const [direction, setDirection] = useState('debit')
  const [kind, setKind] = useState('expense')
  const [category, setCategory] = useState('Food & Dining')
  const [merchant, setMerchant] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showLocationAsk, setShowLocationAsk] = useState(false)
  const [locationDecided, setLocationDecided] = useState(false)

  const value = Number.parseFloat(amount)
  const needsName = kind === 'friend'
  const valid = Number.isFinite(value) && value > 0 && (!needsName || merchant.trim().length > 0)

  async function submit(event) {
    event.preventDefault()
    if (!valid || busy) return
    setBusy(true)
    setError('')
    try {
      await onAdd({
        amount: value,
        direction,
        kind,
        category: kind === 'expense' ? category : undefined,
        merchant: merchant.trim() || null,
      })
      setAmount('')
      setMerchant('')
      setKind('expense')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Add manually</h2>
        <span className="card-sub">for cash &amp; missed SMS</span>
      </div>

      {kind === 'expense' && locationSupported() && getLocationConsent() === null && !showLocationAsk && !locationDecided && (
        <button type="button" className="location-toggle" onClick={() => setShowLocationAsk(true)}>
          <PinIcon />
          Auto-detect shop from location?
        </button>
      )}
      {showLocationAsk && (
        <LocationConsent
          onDecide={() => {
            setShowLocationAsk(false)
            setLocationDecided(true)
          }}
        />
      )}

      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="amount">Amount (₹)</label>
          <input
            id="amount"
            type="number"
            inputMode="decimal"
            step="0.01"
            min="0.01"
            placeholder="0.00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
            autoFocus
          />
        </div>

        <div className="field">
          <label>Direction</label>
          <div className="seg">
            <button type="button" aria-pressed={direction === 'debit'} onClick={() => setDirection('debit')}>
              Spent
            </button>
            <button type="button" aria-pressed={direction === 'credit'} onClick={() => setDirection('credit')}>
              Received
            </button>
          </div>
        </div>

        <div className="field">
          <label>What is this?</label>
          <div className="who-grid">
            {KIND_CHOICES.map(({ kind: k, label, hint, Icon }) => (
              <button key={k} type="button" className="who-btn" aria-pressed={kind === k} onClick={() => setKind(k)}>
                <Icon />
                <span className="who-label">{label}</span>
                <span className="who-hint">{k === 'friend' ? (direction === 'debit' ? 'money lent out' : 'paying me back') : hint}</span>
              </button>
            ))}
          </div>
        </div>

        {kind === 'expense' && (
          <div className="field">
            <label htmlFor="category">Category</label>
            <select id="category" value={category} onChange={(e) => setCategory(e.target.value)}>
              {categories.filter((c) => c !== 'Lending' && c !== 'Transfer').map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}

        <div className="field">
          <label htmlFor="merchant">
            {kind === 'friend' ? 'Their name' : kind === 'expense' ? 'Note / merchant' : 'Name this'}
            {kind !== 'friend' && <span style={{ color: 'var(--muted)' }}> (optional)</span>}
          </label>
          <input
            id="merchant"
            type="text"
            maxLength={60}
            placeholder={
              kind === 'friend'
                ? 'e.g. Rahul'
                : kind === 'expense' && getLocationConsent() === 'granted'
                  ? 'Leave blank to auto-detect from location'
                  : kind === 'expense'
                    ? 'e.g. Auto to office'
                    : 'e.g. Paytm wallet'
            }
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
            required={needsName}
          />
        </div>

        {error && <div className="banner error" style={{ marginBottom: 12 }}>{error}</div>}

        <button className="btn" type="submit" disabled={!valid || busy}>
          {busy ? 'Saving…' : 'Add transaction'}
        </button>
      </form>
    </section>
  )
}
