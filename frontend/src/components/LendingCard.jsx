import { money } from '../format.js'
import { BellIcon } from './Icons.jsx'

export default function LendingCard({ lending, onSnooze, onClearReminder, onRepaid }) {
  const owing = (lending || []).filter((p) => p.outstanding > 0)
  if (!lending) return null

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Money lent out</h2>
        {owing.length > 0 && (
          <span className="card-sub">{money(owing.reduce((s, p) => s + p.outstanding, 0))}</span>
        )}
      </div>

      {owing.length === 0 ? (
        <div className="empty" style={{ padding: '8px 0' }}>
          Nobody owes you anything. Mark a payment as &quot;A person&quot; in Review to track lending.
        </div>
      ) : (
        owing.map((p) => {
          const due = p.next_reminder_at && new Date(p.next_reminder_at).getTime() <= Date.now()
          return (
            <div className="lending-row" key={p.person}>
              <div className="lending-avatar">{p.person.slice(0, 1).toUpperCase()}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row-title" style={{ fontSize: 14 }}>{p.person}</div>
                {p.next_reminder_at ? (
                  <button
                    className={`lending-remind${due ? ' due' : ''}`}
                    onClick={() => (due ? onSnooze(p.person) : onClearReminder(p.person))}
                  >
                    <BellIcon />
                    {due ? 'Ask again? Tap to snooze 3 days' : `reminder ${new Date(p.next_reminder_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`}
                  </button>
                ) : (
                  <button className="lending-remind" onClick={() => onSnooze(p.person)}>
                    <BellIcon />
                    remind me every 3 days
                  </button>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 'none' }}>
                <div className="row-amount">{money(p.outstanding)}</div>
                {/* Cash repayments are invisible to the app — there's no bank
                    alert for them — so without this the debt never clears. */}
                <button className="lending-repaid" onClick={() => onRepaid(p)}>
                  repaid
                </button>
              </div>
            </div>
          )
        })
      )}
    </section>
  )
}
