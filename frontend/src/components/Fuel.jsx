import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { money, moneyExact } from '../format.js'
import { BikeIcon, CarIcon, PlusIcon, TrashIcon } from './Icons.jsx'

const VEHICLE_ICON = { scooter: BikeIcon, motorcycle: BikeIcon, car: CarIcon }
const LAST_PRICE_KEY = 'bt_last_fuel_price'

function deriveLiters(amount, price) {
  const a = Number.parseFloat(amount)
  const p = Number.parseFloat(price)
  if (!Number.isFinite(a) || !Number.isFinite(p) || p <= 0 || a <= 0) return ''
  return (a / p).toFixed(2)
}

function FillForm({ vehicleId, onSaved }) {
  const [amount, setAmount] = useState('')
  const [price, setPrice] = useState(() => localStorage.getItem(LAST_PRICE_KEY) || '')
  const [liters, setLiters] = useState(() => deriveLiters('', localStorage.getItem(LAST_PRICE_KEY) || ''))
  const [litersEdited, setLitersEdited] = useState(false)
  const [odometer, setOdometer] = useState('')
  const [isFullTank, setIsFullTank] = useState(true)
  const [busy, setBusy] = useState(false)

  function onAmount(v) {
    setAmount(v)
    if (!litersEdited) setLiters(deriveLiters(v, price))
  }
  function onPrice(v) {
    setPrice(v)
    if (!litersEdited) setLiters(deriveLiters(amount, v))
  }

  async function save() {
    const amt = Number.parseFloat(amount)
    if (!Number.isFinite(amt) || amt <= 0 || busy) return
    setBusy(true)
    try {
      if (price) localStorage.setItem(LAST_PRICE_KEY, price)
      await api.addFuelFill({
        vehicle_id: vehicleId,
        amount: amt,
        liters: liters ? Number.parseFloat(liters) : undefined,
        odometer: odometer ? Number.parseFloat(odometer) : undefined,
        is_full_tank: isFullTank,
      })
      setAmount('')
      setLiters('')
      setLitersEdited(false)
      setOdometer('')
      onSaved()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fill-form">
      <input inputMode="decimal" placeholder="Amount ₹" value={amount} onChange={(e) => onAmount(e.target.value)} />
      <input inputMode="decimal" placeholder="Price ₹/L today" value={price} onChange={(e) => onPrice(e.target.value)} />
      <input
        inputMode="decimal"
        placeholder="Litres"
        value={liters}
        onChange={(e) => { setLiters(e.target.value); setLitersEdited(true) }}
        style={!litersEdited && price ? { color: 'var(--accent)' } : undefined}
      />
      <input inputMode="decimal" placeholder="Odometer km" value={odometer} onChange={(e) => setOdometer(e.target.value)} />
      <div className="full-row">
        <button type="button" className="seg" aria-pressed={isFullTank} onClick={() => setIsFullTank((v) => !v)}
          style={{ border: '1px solid var(--axis)', borderRadius: 9, padding: '10px', background: isFullTank ? 'var(--accent)' : 'var(--surface)', color: isFullTank ? '#fff' : 'var(--text-2)', fontSize: 13 }}>
          {isFullTank ? 'Full tank' : 'Partial'}
        </button>
        <button type="button" onClick={save} disabled={busy || !amount}
          style={{ width: 44, border: 0, borderRadius: 9, background: 'var(--good)', color: '#fff', display: 'grid', placeItems: 'center' }}>
          <PlusIcon width={16} height={16} />
        </button>
      </div>
    </div>
  )
}

export default function Fuel() {
  const [vehicles, setVehicles] = useState(null)
  const [selected, setSelected] = useState(null)
  const [mileage, setMileage] = useState(null)
  const [fills, setFills] = useState(null)

  useEffect(() => {
    api.vehicles().then((vs) => {
      setVehicles(vs)
      const active = vs.filter((v) => !v.archived)
      if (active.length) setSelected((s) => s || active[0].id)
    })
  }, [])

  async function refresh(vehicleId) {
    const [m, f] = await Promise.all([api.mileage(vehicleId), api.fuelFills(vehicleId)])
    setMileage(m)
    setFills(f.slice().reverse())
  }

  useEffect(() => {
    if (selected) refresh(selected)
  }, [selected])

  if (!vehicles) return <div className="empty">Loading…</div>

  const active = vehicles.filter((v) => !v.archived)
  const vehicle = active.find((v) => v.id === selected)

  return (
    <>
      <div className="vehicle-tabs">
        {active.map((v) => {
          const Icon = VEHICLE_ICON[v.type] || CarIcon
          return (
            <button key={v.id} className="vehicle-tab" aria-pressed={selected === v.id} onClick={() => setSelected(v.id)}>
              <Icon width={16} height={16} />
              {v.name}
            </button>
          )
        })}
      </div>

      {vehicle && mileage && (
        <>
          <section className="card">
            <div className="stat-grid">
              <div className="stat-tile">
                <div className="stat-tile-label">Mileage</div>
                <div className="stat-tile-value">{mileage.avg_mileage ? `${mileage.avg_mileage.toFixed(1)} km/L` : '—'}</div>
                <div className="stat-tile-sub">{mileage.legs.length ? `across ${mileage.legs.length} leg${mileage.legs.length === 1 ? '' : 's'}` : 'needs 2 full-tank fills'}</div>
              </div>
              <div className="stat-tile">
                <div className="stat-tile-label">Last fill</div>
                <div className="stat-tile-value">{mileage.last_mileage ? `${mileage.last_mileage.toFixed(1)} km/L` : '—'}</div>
                <div className="stat-tile-sub">most recent leg</div>
              </div>
              <div className="stat-tile">
                <div className="stat-tile-label">Fuel spend</div>
                <div className="stat-tile-value">{money(mileage.total_spent)}</div>
                <div className="stat-tile-sub">{mileage.total_liters.toFixed(1)} L total</div>
              </div>
              <div className="stat-tile">
                <div className="stat-tile-label">Avg price</div>
                <div className="stat-tile-value">{mileage.avg_price_per_liter ? `₹${mileage.avg_price_per_liter.toFixed(1)}/L` : '—'}</div>
                <div className="stat-tile-sub">{mileage.legs.length ? `₹${mileage.legs[mileage.legs.length - 1].cost_per_km.toFixed(2)}/km` : 'cost/km n/a'}</div>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2 className="card-title">Log a fill-up</h2>
            </div>
            <div className="card-sub" style={{ marginBottom: 0 }}>
              Price/L auto-computes litres from the amount. Litres + odometer on every <em>full-tank</em> fill is what makes mileage computable.
            </div>
            <FillForm vehicleId={vehicle.id} onSaved={() => refresh(vehicle.id)} />
          </section>

          <section className="card">
            <div className="card-head">
              <h2 className="card-title">Fill history</h2>
            </div>
            {!fills?.length ? (
              <div className="empty">No fills logged for {vehicle.name} yet</div>
            ) : (
              <div className="rows">
                {fills.map((f) => (
                  <div className="row" key={f.id}>
                    <div className="row-main">
                      <div className="row-title">{new Date(f.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</div>
                      <div className="row-meta">
                        {f.liters ? `${f.liters} L` : 'litres n/a'} · {f.is_full_tank ? 'full tank' : 'partial'}
                        {f.odometer ? ` · ${f.odometer} km` : ''}
                      </div>
                    </div>
                    <div className="row-amount">{moneyExact(f.amount)}</div>
                    <button
                      className="icon-btn"
                      onClick={async () => { await api.deleteFuelFill(f.id); refresh(vehicle.id) }}
                      aria-label="Delete fill"
                    >
                      <TrashIcon width={15} height={15} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </>
  )
}
