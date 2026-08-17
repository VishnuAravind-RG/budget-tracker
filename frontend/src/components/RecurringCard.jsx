import { money } from '../format.js'
import MerchantLogo from './MerchantLogo.jsx'

export default function RecurringCard({ items }) {
  if (!items || items.length === 0) return null

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Likely recurring</h2>
        <span className="card-sub">seen in 2+ months</span>
      </div>
      <div className="rows">
        {items.slice(0, 5).map((r) => (
          <div className="row" key={`${r.merchant}-${r.category}`}>
            <MerchantLogo name={r.merchant} size={30} />
            <div className="row-main">
              <div className="row-title">{r.merchant}</div>
              <div className="row-meta">{r.category} · {r.months} months</div>
            </div>
            <div className="row-amount">~{money(r.avgAmount)}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
