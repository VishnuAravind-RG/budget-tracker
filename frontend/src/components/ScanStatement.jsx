import { useEffect, useRef, useState } from 'react'
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
 *
 * The whole screenshot goes up as ONE request and comes back as one batch,
 * which is what makes an import undoable. Rows used to be POSTed one at a
 * time as ordinary manual entries: indistinguishable afterwards from things
 * typed by hand, so there was no way to see what an upload brought in and no
 * way to take one back — and these rows come from a vision model reading a
 * photo, which is exactly the input worth being able to undo.
 */
export default function ScanStatement({ available, onImported }) {
  const [file, setFile] = useState(null)
  const [rows, setRows] = useState(null)
  const [picked, setPicked] = useState({})
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(null)
  const [history, setHistory] = useState([])
  const [undoing, setUndoing] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    api.imports().then(setHistory).catch(() => setHistory([]))
  }, [])

  function reset() {
    setFile(null); setRows(null); setPicked({}); setError(''); setDone(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  async function scan(f) {
    setBusy(true); setError(''); setDone(null)
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
      const result = await api.screenshotImport(chosen.map((t) => ({
        amount: t.amount,
        direction: t.direction,
        // Always sent as a plain expense. Deciding whether a "Transfer"
        // category is a genuine self-transfer belongs on the server — it
        // checks the merchant TEXT for actual self-transfer language before
        // trusting it, rather than treating the AI's category guess alone as
        // proof. That guess has been "Transfer" for an ordinary person's name
        // before (a real ₹120 payment to "S Sadashiva"), and trusting it here
        // would have silently excluded that money from every total with no
        // chance to catch it — worse than a wrong category, since a wrong
        // category is at least still counted as spending.
        kind: 'expense',
        category: t.category,
        merchant: t.merchant,
        occurred_on: t.occurred_on || undefined,
      })))
      setRows(null); setPicked({}); setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      setDone(result)
      setHistory(await api.imports().catch(() => history))
      onImported(result.added)
    } catch (err) {
      setError(err.message)
    } finally {
      setImporting(false)
    }
  }

  async function undo(batch) {
    if (!confirm('Undo this import?\n\nEvery row it added is removed. Anything logged separately is untouched.')) return
    setUndoing(batch)
    setError('')
    try {
      await api.undoImport(batch)
      setDone(null)
      setHistory(await api.imports().catch(() => []))
      onImported(0)
    } catch (err) {
      setError(err.message)
    } finally {
      setUndoing('')
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

  const recentImports = (
    history.length > 0 && (
      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Recent imports</h2>
          <span className="card-sub">undoable</span>
        </div>
        <div className="rows">
          {history.map((b) => (
            <div className="row" key={b.batch}>
              <div className="row-main">
                <div className="row-title">
                  {b.count} row{b.count === 1 ? '' : 's'} · {moneyExact(b.total)}
                </div>
                <div className="row-meta">
                  {b.at ? new Date(b.at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'unknown time'}
                </div>
              </div>
              <button
                type="button"
                disabled={undoing === b.batch}
                onClick={() => undo(b.batch)}
                style={{ background: 'none', border: 0, color: 'var(--accent)', fontSize: 13, padding: '0 6px' }}
              >
                {undoing === b.batch ? 'Undoing…' : 'Undo'}
              </button>
            </div>
          ))}
        </div>
      </section>
    )
  )

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
    <>
      <section className="card">
        <div className="card-head">
          <h2 className="card-title">From a screenshot</h2>
          <span className="card-sub">a whole list at once</span>
        </div>
        <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-2)' }}>
          Screenshot your GPay / PhonePe history or a bank statement. Every row is read
          with its own date — useful because banks don&apos;t alert on everything.
        </p>

        {/* Shown right where the import happened, not as a toast that's gone
            before you've read it. A screenshot is read by a vision model, so
            "that came out wrong" is a normal outcome, not an exception. */}
        {done && (
          <div className="banner ok" style={{ marginBottom: 12, display: 'block' }}>
            Imported {done.added} row{done.added === 1 ? '' : 's'} · {moneyExact(done.total)} of spending.
            <div style={{ marginTop: 6 }}>
              <button
                type="button"
                disabled={undoing === done.batch}
                onClick={() => undo(done.batch)}
                style={{ background: 'none', border: 0, padding: 0, color: 'inherit', fontSize: 13, textDecoration: 'underline' }}
              >
                {undoing === done.batch ? 'Undoing…' : 'Undo this import'}
              </button>
            </div>
          </div>
        )}

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

      {recentImports}
    </>
  )
}
