import { useMemo, useState } from 'react'

import { dayLabel, money } from '../format.js'
import { useElementWidth } from './useElementWidth.js'

const PAD = { top: 14, right: 14, bottom: 22, left: 44 }
const HEIGHT = 168

// A fine-grained ladder: a coarse 1/2/5 one turns a 57k max into a 100k axis
// and wastes half the plot area.
const STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]

/** Smallest round number at or above `value`. */
function niceCeil(value) {
  if (value <= 0) return 100
  const mag = 10 ** Math.floor(Math.log10(value))
  const norm = value / mag
  return (STEPS.find((s) => norm <= s + 1e-9) ?? 10) * mag
}

/**
 * Cumulative spend through the month — one series, so no legend: the card
 * title names what's plotted. The budget line is an annotation, not a series.
 */
export default function TrendChart({ days, budget }) {
  const [wrapRef, width] = useElementWidth(320)
  const [hover, setHover] = useState(null)

  const points = useMemo(() => {
    let running = 0
    return days.map((d) => {
      running += d.spent
      return { date: d.date, spent: d.spent, total: Math.round(running * 100) / 100 }
    })
  }, [days])

  const innerW = Math.max(width - PAD.left - PAD.right, 10)
  const innerH = HEIGHT - PAD.top - PAD.bottom

  const dataMax = points.length ? points[points.length - 1].total : 0
  const yMax = niceCeil(Math.max(dataMax, budget || 0)) || 100

  const x = (i) => PAD.left + (points.length < 2 ? innerW / 2 : (i / (points.length - 1)) * innerW)
  const y = (v) => PAD.top + innerH - (v / yMax) * innerH

  const linePath = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.total).toFixed(1)}`).join(' ')
  const areaPath = points.length
    ? `${linePath} L${x(points.length - 1).toFixed(1)},${(PAD.top + innerH).toFixed(1)} L${x(0).toFixed(1)},${(PAD.top + innerH).toFixed(1)} Z`
    : ''

  const ticks = [0, yMax / 2, yMax]
  const lastIndex = points.length - 1

  // First / middle / last day only — a label per day would be unreadable.
  const xLabelIdx = points.length > 2 ? [0, Math.floor(lastIndex / 2), lastIndex] : points.map((_, i) => i)

  function locate(event) {
    const rect = event.currentTarget.getBoundingClientRect()
    const px = event.clientX - rect.left
    const ratio = (px - PAD.left) / innerW
    const i = Math.round(ratio * (points.length - 1))
    if (i < 0 || i > lastIndex || Number.isNaN(i)) return setHover(null)
    setHover(i)
  }

  if (!points.length) return <p className="empty">No data for this month.</p>

  const active = hover === null ? null : points[hover]

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg
        className="chart"
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`Cumulative spending this month, ending at ${money(dataMax)}`}
        onPointerMove={locate}
        onPointerDown={locate}
        onPointerLeave={() => setHover(null)}
      >
        {/* Gridlines: hairline, solid, recessive. */}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)}
              stroke="var(--grid)" strokeWidth="1" shapeRendering="crispEdges"
            />
            <text x={PAD.left - 8} y={y(t) + 3.5} textAnchor="end" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {t >= 1000 ? `${Math.round(t / 1000)}k` : Math.round(t)}
            </text>
          </g>
        ))}

        <line
          x1={PAD.left} x2={width - PAD.right} y1={PAD.top + innerH} y2={PAD.top + innerH}
          stroke="var(--axis)" strokeWidth="1" shapeRendering="crispEdges"
        />

        {/* Budget threshold — an annotation, dashed so it never reads as data. */}
        {budget > 0 && budget <= yMax && (
          <g>
            <line
              x1={PAD.left} x2={width - PAD.right} y1={y(budget)} y2={y(budget)}
              stroke="var(--muted)" strokeWidth="1" strokeDasharray="4 4"
            />
            <text x={width - PAD.right} y={y(budget) - 5} textAnchor="end">
              budget {money(budget)}
            </text>
          </g>
        )}

        <path d={areaPath} fill="var(--accent)" opacity="0.1" />
        <path
          d={linePath} fill="none" stroke="var(--accent)"
          strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
        />

        {/* End marker with a surface ring so it stays legible over the line. */}
        <circle cx={x(lastIndex)} cy={y(dataMax)} r="4.5" fill="var(--accent)" stroke="var(--surface)" strokeWidth="2" />

        {xLabelIdx.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={HEIGHT - 6}
            textAnchor={i === 0 ? 'start' : i === lastIndex ? 'end' : 'middle'}
          >
            {dayLabel(points[i].date)}
          </text>
        ))}

        {active && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} stroke="var(--axis)" strokeWidth="1" />
            <circle cx={x(hover)} cy={y(active.total)} r="4.5" fill="var(--accent)" stroke="var(--surface)" strokeWidth="2" />
          </g>
        )}
      </svg>

      {active && (
        <div
          className="chart-tip"
          style={{
            left: `${Math.min(Math.max(x(hover), 56), width - 56)}px`,
            top: `${y(active.total) - 10}px`,
          }}
        >
          <strong>{dayLabel(active.date)}</strong> · {money(active.total)} total
          <br />
          {active.spent > 0 ? `${money(active.spent)} that day` : 'no spending'}
        </div>
      )}
    </div>
  )
}
