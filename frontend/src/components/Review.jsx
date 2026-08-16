import { dateTime, moneyExact } from '../format.js'

export default function Review({ items, categories, onPick }) {
  if (!items) return <div className="empty">Loading…</div>

  if (!items.length) {
    return (
      <section className="card">
        <div className="empty">
          Nothing to review.
          <div style={{ marginTop: 6, fontSize: 13 }}>
            Transactions land here when the AI isn&apos;t confident about the category.
          </div>
        </div>
      </section>
    )
  }

  return (
    <>
      <div className="banner error">
        {items.length} transaction{items.length > 1 ? 's' : ''} need a category
      </div>

      {items.map((t) => (
        <section className="card" key={t.id}>
          <div className="row" style={{ padding: 0, borderTop: 0 }}>
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

          <div className="cat-grid">
            {categories.map((c) => (
              <button key={c} onClick={() => onPick(t.id, c)}>{c}</button>
            ))}
          </div>
        </section>
      ))}
    </>
  )
}
