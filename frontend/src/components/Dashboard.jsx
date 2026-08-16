import { money, moneyCompact, monthName } from '../format.js'
import BudgetMeters from './BudgetMeters.jsx'
import TrendChart from './TrendChart.jsx'

export default function Dashboard({ summary, trend, reviewCount, onGoReview, onGoBudgets }) {
  if (!summary) return <div className="empty">Loading…</div>

  const budgetLeft = summary.total_budget - summary.total_spent

  const today = new Date()
  const isCurrentMonth =
    summary.month === today.getMonth() + 1 && summary.year === today.getFullYear()

  // Stop the cumulative line at today, otherwise the rest of the month renders
  // as a flat run that reads as "spending stopped" rather than "hasn't happened".
  const trendDays = trend?.days
    ? isCurrentMonth ? trend.days.slice(0, today.getDate()) : trend.days
    : null

  const spentToday = isCurrentMonth ? trend?.days?.[today.getDate() - 1]?.spent ?? 0 : 0

  return (
    <>
      {reviewCount > 0 && (
        <button className="banner error" onClick={onGoReview} style={{ width: '100%', border: 0, textAlign: 'left' }}>
          {reviewCount} transaction{reviewCount > 1 ? 's need' : ' needs'} a category →
        </button>
      )}

      {/* The one hero figure on this view. */}
      <section className="card">
        <div className="hero-label">
          Spent in {monthName(summary.month)} {summary.year}
        </div>
        <div className="hero-value">{moneyCompact(summary.total_spent)}</div>
        <div className="hero-meta">
          {summary.total_budget > 0 && (
            <span>
              <span className={budgetLeft >= 0 ? 'pos' : 'neg'}>
                {budgetLeft >= 0 ? money(budgetLeft) : `${money(-budgetLeft)} over`}
              </span>{' '}
              {budgetLeft >= 0 ? 'left of' : 'of'} {money(summary.total_budget)}
            </span>
          )}
          {summary.total_income > 0 && <span>{money(summary.total_income)} in</span>}
          {isCurrentMonth && <span>{money(spentToday)} today</span>}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Spending trend</h2>
          <span className="card-sub">cumulative</span>
        </div>
        {trendDays ? <TrendChart days={trendDays} budget={summary.total_budget} /> : <div className="empty">Loading…</div>}
      </section>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Budget vs actual</h2>
          <button
            className="card-sub"
            onClick={onGoBudgets}
            style={{ background: 'none', border: 0, color: 'var(--accent)', padding: 0 }}
          >
            Edit
          </button>
        </div>
        <BudgetMeters categories={summary.categories} onSetBudgets={onGoBudgets} />
      </section>
    </>
  )
}
