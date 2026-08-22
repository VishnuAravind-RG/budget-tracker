import { useRef, useState } from 'react'
import { api } from '../api.js'
import { moneyExact } from '../format.js'

/**
 * Screenshot of a transaction LIST -> many transactions at once.
 *
 * The alert-based capture is genuinely incomplete: a real GPay history showed
 * six payments (including a ₹7,500 gym fee) that the bank never emailed or
 * texted. None of these apps expose an API, so a screenshot is the only
 * machine-readable form of that history a person can actually obtain.
 *
 * Nothing is booked until Import is pressed. Rows already present are
 * detected server-side and unticked by default, which is what makes the same
 * screenshot safe to upload twice — otherwise a re-upload would silently
 * double every figure on it.
 */
export default function ScanStatement({ available, onImported }) {
  const [file, setFile] = useState(null)
  const [rows, setRows] = useState(null)
  const [picked, setPicked] = useState({})
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  function reset() {
    setFile(null); setRows(null); setPicked({}); setError('')
    if (inputRef.current) inputRef.current.value = ''
  }

  async function scan(f) {
    setBusy(true); setError('')
    try {
      const { transactions } = await api.scanStatement(f)
      if (!transactions.length) {
        setError("Couldn't find any transactions in that image.")
        return
      }
      setRows(transactions)
      // Pre-tick only what isn't already recorded — the safe default is to
      // add nothing twice, never to add something twice.
      setPicked(Object.fromEntries(transactions.map((t, i) => [i, !t.already_recorded])))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function importPicked() {
    const chosen = rows.filter((_, i) => picked[i])
    if (!chosen.length) return
    setImporting(true); setError('')
    try {
      // Sequential, not parallel: these all hit the same row-writing path and
      // the free-tier backend is single-worker.
      for (const t of chosen) {
        // Respect what the row actually is. Hardcoding 'expense' here booked
        // a GPay row literally labelled "Self transfer" as ₹10,000 of
        // spending — money moved between the owner's own accounts, counted
        // as a purchase. A credit is already handled by direction alone
        // (the server turns expense+credit into income).
        const kind = t.category === 'Transfer' ? 'self' : 'expense'
        await api.addManual({
          amount: t.amount,
          direction: t.direction,
          kind,
          // 'self' takes its category from the server (Transfer); sending one
          // here would be ignored anyway, and Lending/Transfer are rejected
          // by the manual-entry category picker for the same reason.
          category: kind === 'expense' ? t.category : undefined,
          merchant: t.merchant,
          occurred_on: t.occurred_on || undefined,
        })
      }
      reset()
      onImported(chosen.length)
    } catch (err) {
      setError(err.message)
    } finally {
      setImporting(false)
    }
  }

  if (!available) {
    return (
      <section className="card">
        <div className="empty">
          Screenshot scanning isn&apos;t set up on the server yet.
          <div style={{ marginTop: 6, fontSize: 13 }}>Needs a GEMINI_API_KEY.</div>
        </div>
      </section>
    )
  }

  if (rows) {
    const chosenCount = rows.filter((_, i) => picked[i]).length
    const chosenTotal = rows.reduce((s, t, i) => (picked[i] ? s + t.amount : s), 0)
    return (
      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Found {rows.length}</h2>
          <button className="card-sub" onClick={reset}
            style={{ background: 'none', border: 0, color: 'var(--accent)', padding: 0 }}>
            Start over
          </button>
        </div>
        <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-2)' }}>
          Rows that look like duplicates are unticked so nothing gets counted twice —
          the reason is shown against each. Tick one back on if it really is a separate
          payment.
        </p>

        <div className="rows">
          {rows.map((t, i) => (
            <label className="row" key={i} style={{ cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={!!picked[i]}
                onChange={(e) => setPicked({ ...picked, [i]: e.target.checked })}
                style={{ width: 18, height: 18, flex: 'none' }}
              />
              <div className="row-main">
                <div className="row-title">{t.merchant}</div>
                <div className="row-meta">
                  <span className="chip">{t.category}</span>
                  {t.occurred_on ? ` ${t.occurred_on}` : ' no date — will use today'}
                  {/* Say WHY, not just that it's flagged — "appears twice in
                      this screenshot" and "already recorded" call for
                      different judgement from the reader. */}
                  {t.duplicate_reason && (
                    <span style={{ color: 'var(--warn)' }}> · {t.duplicate_reason}</span>
                  )}
                </div>
              </div>
              <div className="row-amount">{moneyExact(t.amount)}</div>
            </label>
          ))}
        </div>

        {error && <div className="banner error" style={{ marginTop: 12 }}>{error}</div>}

        <button className="btn" style={{ marginTop: 12 }} onClick={importPicked}
          disabled={importing || chosenCount === 0}>
          {importing ? 'Importing…' : chosenCount === 0
            ? 'Nothing selected'
            : `Import ${chosenCount} · ${moneyExact(chosenTotal)}`}
        </button>
      </section>
    )
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">From a screenshot</h2>
        <span className="card-sub">a whole list at once</span>
      </div>
      <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-2)' }}>
        Screenshot your GPay / PhonePe history or a bank statement. Every row is read
        with its own date — useful because banks don&apos;t alert on everything.
      </p>

      <label className="scan-drop">
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) { setFile(f); scan(f) }
          }}
        />
        <span>{busy ? 'Reading the screenshot…' : 'Tap to choose a screenshot'}</span>
      </label>

      {file && busy && <div className="empty" style={{ marginTop: 10 }}>This can take up to a minute.</div>}
      {error && <div className="banner error" style={{ marginTop: 12 }}>{error}</div>}
    </section>
  )
}
