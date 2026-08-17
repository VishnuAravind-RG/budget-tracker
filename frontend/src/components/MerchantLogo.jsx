import { BRAND_ICONS } from '../brandIcons.js'
import { detectMerchant } from '../merchants.js'

/** Perceived brightness (0–1) of a #rrggbb colour, for contrast decisions. */
function luminance(hex) {
  const n = parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/**
 * Brand logo for a recognised merchant, a plain monogram otherwise. Looks up
 * the merchant by matching the transaction's free-text name/note against
 * src/merchants.js — logos are bundled at build time (see
 * scripts/gen-brand-icons.mjs), not fetched, so this works fully offline and
 * never leaks a request to a favicon service.
 */
export default function MerchantLogo({ name, size = 32 }) {
  const merchant = detectMerchant(name)
  const brand = merchant ? BRAND_ICONS[merchant.key] : undefined
  const initial = (name || '?').trim().slice(0, 1).toUpperCase()

  if (!brand) {
    return (
      <span className="merchant-logo" style={{ width: size, height: size, fontSize: size * 0.4 }}>
        {initial}
      </span>
    )
  }

  // Near-black marks vanish on the dark surface (and near-white on the light
  // one) — swap to plain ink at the extremes rather than showing a smudge.
  const l = luminance(brand.hex)
  const ink = l < 0.06 || l > 0.85 ? 'var(--text-1)' : brand.hex

  return (
    <span
      className="merchant-logo"
      style={{ width: size, height: size, background: `color-mix(in oklab, ${ink} 14%, transparent)` }}
      title={merchant.label}
    >
      {brand.path ? (
        <svg viewBox="0 0 24 24" width={size * 0.54} height={size * 0.54} fill={ink} aria-hidden="true">
          <path d={brand.path} />
        </svg>
      ) : (
        <span style={{ fontSize: size * 0.4, color: ink, fontWeight: 600 }}>{merchant.label.slice(0, 1).toUpperCase()}</span>
      )}
    </span>
  )
}
