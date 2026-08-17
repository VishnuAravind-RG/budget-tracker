import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { moneyExact } from '../format.js'

/**
 * Photo -> transaction. Snap or pick a receipt/payment screenshot, optionally
 * add a note to correct or clarify what it's actually for (the model treats
 * that note as authoritative — "this was for a friend's birthday, put it
 * under Entertainment" overrides what the image alone would suggest), and it
 * reads the amount + merchant + category directly from the photo.
 */
export default function ScanReceipt({ available, onAdded }) {
  const [file, setFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
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
      setResult(null)
      setError('')
    }
  }

  async function submit() {
    if (!file || busy) return
    setBusy(true)
    setError('')
    try {
      const txn = await api.scanReceipt(file, note.trim() || undefined)
      setResult(txn)
      setFile(null)
      setNote('')
      if (inputRef.current) inputRef.current.value = ''
      onAdded()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
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

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Scan a receipt</h2>
        <span className="card-sub">reads amount + merchant from a photo</span>
      </div>

      {!previewUrl ? (
        <label className="scan-drop">
          <input ref={inputRef} type="file" accept="image/*" capture="environment" onChange={pickFile} hidden />
          <span>Tap to take a photo or choose an image</span>
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
            <button type="button" className="btn" onClick={submit} disabled={busy}>
              {busy ? 'Reading…' : 'Scan & add'}
            </button>
          </div>
        </>
      )}

      {result && (
        <div className="banner ok" style={{ marginTop: 14 }}>
          Added {moneyExact(result.amount)} · {result.merchant} · {result.category}
          {result.needs_review && ' (wasn’t fully sure — check Review)'}
        </div>
      )}
    </section>
  )
}
