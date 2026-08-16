import { money } from '../format.js'
import { AlertIcon, CheckIcon } from './Icons.jsx'

/* Meter fill carries severity. Status colour never carries meaning on its own —
   every meter states the remaining amount in words, and the two extreme states
   also carry a glyph, so CVD/greyscale readers lose nothing. */
function severity(pct) {
  if (pct >= 100) return { fill: 'var(--crit)', cls: 'over' }
  if (pct >= 80) return { fill: 'var(--warn)', cls: 'close' }
  return { fill: 'var(--accent)', cls: 'ok' }
}

function Meter({ item }) {
  const pct = item.percent_used ?? 0
  const { fill, cls } = severity(pct)
  const width = Math.min(pct, 100)
  const over = item.remaining < 0
  const atLimit = !over && pct >= 100

  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-name">{item.category}</span>
        <span className="meter-amounts">
          {money(item.spent)} <span style={{ color: 'var(--muted)' }}>/ {money(item.limit)}</span>
        </span>
      </div>

      <div className="meter-track">
        <div
          className={`meter-fill${width >= 100 ? ' full' : ''}`}
          style={{ width: `${width}%`, background: fill }}
        />
      </div>

      <div className="meter-foot">
        {/* The glyph rides along with every critical/good state so the colour
            is never the only signal. */}
        {(over || atLimit) && <AlertIcon style={{ color: 'var(--crit)' }} />}
        {pct < 80 && <CheckIcon style={{ color: 'var(--good-text)' }} />}
        <span className={`state ${cls}`}>
          {over
            ? `${money(Math.abs(item.remaining))} over budget`
            : atLimit
              ? 'Budget used up'
              : `${money(item.remaining)} left`}
        </span>
        <span style={{ color: 'var(--muted)' }}>· {Math.round(pct)}% used</span>
      </div>
    </div>
  )
}

export default function BudgetMeters({ categories, onSetBudgets }) {
  const budgeted = categories.filter((c) => c.limit != null)
  const unbudgeted = categories.filter((c) => c.limit == null && c.spent > 0)

  return (
    <>
      {budgeted.length === 0 ? (
        <div className="empty">
          No budgets set yet.
          <div style={{ marginTop: 12 }}>
            <button className="btn secondary" onClick={onSetBudgets}>Set monthly limits</button>
          </div>
        </div>
      ) : (
        budgeted.map((item) => <Meter key={item.category} item={item} />)
      )}

      {unbudgeted.length > 0 && (
        <div style={{ marginTop: budgeted.length ? 14 : 0, paddingTop: 12, borderTop: '1px solid var(--grid)' }}>
          <div className="card-sub" style={{ marginBottom: 6 }}>No budget set</div>
          {unbudgeted.map((c) => (
            <div key={c.category} className="meter-head" style={{ marginBottom: 4 }}>
              <span className="meter-name" style={{ fontWeight: 400, color: 'var(--text-2)' }}>{c.category}</span>
              <span className="meter-amounts">{money(c.spent)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
