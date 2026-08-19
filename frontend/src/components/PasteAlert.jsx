import { useState } from 'react'
import { api } from '../api.js'

export default function PasteAlert({ onAdded }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await api.pasteAlert(text.trim())
      if (result.status === 'ignored') {
        setError("Didn't look like a transaction — nothing added.")
        return
      }
      if (result.status === 'duplicate') {
        setError('Already have this one — skipped as a duplicate.')
        return
      }
      setText('')
      onAdded(result.transaction)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Paste an alert</h2>
        <span className="card-sub">for SMS or email your automation missed</span>
      </div>

      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="alert-text">Bank SMS or email text</label>
          <textarea
            id="alert-text"
            rows={5}
            placeholder="Paste the full alert text here…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            required
          />
        </div>

        {error && <div className="banner error" style={{ marginBottom: 12 }}>{error}</div>}

        <button className="btn" type="submit" disabled={!text.trim() || busy}>
          {busy ? 'Parsing…' : 'Parse & add'}
        </button>
      </form>
    </section>
  )
}
