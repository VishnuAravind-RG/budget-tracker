import { useState } from 'react'

export default function AddExpense({ categories, onAdd }) {
  const [amount, setAmount] = useState('')
  const [direction, setDirection] = useState('debit')
  const [category, setCategory] = useState('Food & Dining')
  const [merchant, setMerchant] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const value = Number.parseFloat(amount)
  const valid = Number.isFinite(value) && value > 0

  async function submit(event) {
    event.preventDefault()
    if (!valid || busy) return
    setBusy(true)
    setError('')
    try {
      await onAdd({
        amount: value,
        direction,
        category,
        merchant: merchant.trim() || null,
      })
      setAmount('')
      setMerchant('')
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
          <label htmlFor="category">Category</label>
          <select id="category" value={category} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        <div className="field">
          <label htmlFor="merchant">Note / merchant <span style={{ color: 'var(--muted)' }}>(optional)</span></label>
          <input
            id="merchant"
            type="text"
            maxLength={60}
            placeholder="e.g. Auto to office"
            value={merchant}
            onChange={(e) => setMerchant(e.target.value)}
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
