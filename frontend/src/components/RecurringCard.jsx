import { money } from '../format.js'
import MerchantLogo from './MerchantLogo.jsx'

/**
 * Monthly repeats — and, for each, whether this month's has happened yet.
 *
 * Detecting recurrence was already here, but it only ever described the past:
 * "you pay this every month" is a fact, not a prompt, and a card full of facts
 * is one you stop reading. The useful half is the one this adds — the maid's
 * salary, the gym fee, the rent, each either ticked off or still outstanding
 * with the date it usually lands on.
 *
 * Ordering comes from the server (overdue first), because that is the only
 * part of this list that needs anything doing about it.
 */

const STATUS = {
  overdue: { tone: 'var(--crit)', mark: '!' },
  due: { tone: 'var(--warn)', mark: '·' },
  paid: { tone: 'var(--muted)', mark: '✓' },
}

function ordinal(day) {
  if (day % 10 === 1 && day !== 11) return `${day}st`
  if (day % 10 === 2 && day !== 12) return `${day}nd`
  if (day % 10 === 3 && day !== 13) return `${day}rd`
  return `${day}th`
}

function describe(item) {
  const { status, days_until: days, typical_day: day } = item
  if (status === 'paid') return 'paid this month'
  if (day == null) return 'expected this month'
  if (status === 'overdue') return `usually by the ${ordinal(day)} — ${Math.abs(days)} days ago`
  if (days === 0) return `usually today, the ${ordinal(day)}`
  if (days < 0) return `usually the ${ordinal(day)}`
  return `usually the ${ordinal(day)} — in ${days} day${days === 1 ? '' : 's'}`
}

export default function RecurringCard({ items }) {
  if (!items || items.length === 0) return null

  const outstanding = items.filter((r) => r.status !== 'paid')
  // Everything already paid: worth confirming at a glance, not worth a list.
  // Collapsing it keeps the card about what's actually left to do.
  const settled = items.length - outstanding.length
  const owed = outstanding.reduce((sum, r) => sum + r.typical_amount, 0)

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Every month</h2>
        <span className="card-sub">
          {outstanding.length === 0 ? 'all paid' : `${money(owed)} still expected`}
        </span>
      </div>

      <div className="rows">
        {(outstanding.length ? outstanding : items).slice(0, 6).map((r) => {
          const { tone, mark } = STATUS[r.status] || STATUS.due
          return (
            <div className="row" key={`${r.merchant}-${r.category}`}>
              <MerchantLogo name={r.merchant} size={30} />
              <div className="row-main">
                <div className="row-title">{r.merchant}</div>
                <div className="row-meta">
                  <span style={{ color: tone }}>{mark} {describe(r)}</span>
                </div>
              </div>
              <div className="row-amount" style={r.status === 'paid' ? { color: 'var(--muted)' } : undefined}>
                ~{money(r.typical_amount)}
              </div>
            </div>
          )
        })}
      </div>

      {outstanding.length > 0 && settled > 0 && (
        <div style={{ marginTop: 10, fontSize: 13, color: 'var(--text-2)' }}>
          {settled} other{settled === 1 ? '' : 's'} already paid this month.
        </div>
      )}
    </section>
  )
}
