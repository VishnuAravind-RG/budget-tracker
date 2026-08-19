import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import AddExpense from './AddExpense.jsx'

/**
 * Photo -> preview -> confirm. Snap or pick a receipt/payment screenshot,
 * optionally add a note to correct or clarify what it's actually for (the
 * model treats that note as authoritative — "this was actually for a
 * friend's birthday, put it under Entertainment" overrides what the image
 * alone would suggest), and it reads the amount + merchant + category
 * directly from the photo.
 *
 * Nothing is booked yet at that point — the result feeds the same shop /
 * person / wallet / my account chooser as a manual entry (AddExpense),
 * because the model has no way to tell a screenshot of money sent to a
 * friend apart from an ordinary purchase. Confirming there is what actually
 * creates the transaction, via the normal manual-add path.
 */
export default function ScanReceipt({ available, categories, onAdd }) {
  const [file, setFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [scanned, setScanned] = useState(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!file) { setPreviewUrl(null); return }
    const url = URL.createObjectURL(file)
    setPreviewUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  function pickFile(e) {
    const f = e.target.files?.[0]
    if (f) {
      setFile(f)
      setScanned(null)
      setError('')
    }
  }

  function startOver() {
    setFile(null)
    setScanned(null)
    setNote('')
    setError('')
    if (inputRef.current) inputRef.current.value = ''
  }

  async function scan() {
    if (!file || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await api.scanReceipt(file, note.trim() || undefined)
      setScanned(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function confirm(payload) {
    await onAdd(payload)
    startOver()
  }

  if (!available) {
    return (
      <section className="card">
        <div className="empty">
          Photo scanning isn&apos;t set up on the server yet.
          <div style={{ marginTop: 6, fontSize: 13 }}>Needs a GEMINI_API_KEY — ask whoever runs this deployment.</div>
        </div>
      </section>
    )
  }

  if (scanned) {
    return (
      <>
        <div className="card-head" style={{ marginBottom: 8 }}>
          <h2 className="card-title">Check the details</h2>
          <button type="button" className="card-sub" style={{ background: 'none', border: 0, color: 'var(--accent)', padding: 0 }} onClick={startOver}>
            Start over
          </button>
        </div>
        <AddExpense
          categories={categories}
          onAdd={confirm}
          initial={{ amount: scanned.amount, direction: scanned.direction, category: scanned.category, merchant: scanned.merchant }}
          confidenceWarning={!scanned.confident}
        />
      </>
    )
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Scan a receipt</h2>
        <span className="card-sub">reads amount + merchant from a photo</span>
      </div>

      {!previewUrl ? (
        <label className="scan-drop">
          <input ref={inputRef} type="file" accept="image/*" onChange={pickFile} hidden />
          <span>Tap to choose a photo or screenshot</span>
        </label>
      ) : (
        <>
          <img src={previewUrl} alt="Receipt preview" className="scan-preview" />

          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="scan-note">
              Anything to correct? <span style={{ color: 'var(--muted)' }}>(optional)</span>
            </label>
            <textarea
              id="scan-note"
              rows={2}
              placeholder='e.g. "this is actually for entertainment, not food"'
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>

          {error && <div className="banner error" style={{ marginBottom: 12 }}>{error}</div>}

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="btn secondary"
              style={{ flex: 'none', width: 'auto', padding: '13px 18px' }}
              onClick={() => { setFile(null); setError('') }}
              disabled={busy}
            >
              Retake
            </button>
            <button type="button" className="btn" onClick={scan} disabled={busy}>
              {busy ? 'Reading…' : 'Scan'}
            </button>
          </div>
        </>
      )}
    </section>
  )
}
