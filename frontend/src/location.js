/**
 * Location is used strictly as a one-shot lookup: grabbed for a few seconds
 * right after logging a transaction, resolved to a place name, then never
 * touched again. There is no background tracking, no watchPosition, no
 * location history — the browser's location indicator should only ever
 * flash briefly, right after you spend something.
 */

const CONSENT_KEY = 'bt_location_consent' // 'granted' | 'denied' | unset

export function locationSupported() {
  return typeof navigator !== 'undefined' && 'geolocation' in navigator
}

export function getLocationConsent() {
  const v = localStorage.getItem(CONSENT_KEY)
  return v === 'granted' || v === 'denied' ? v : null
}

export function setLocationConsent(v) {
  localStorage.setItem(CONSENT_KEY, v)
}

/** One-shot position fetch — no watch, times out fast rather than hanging. */
function getPositionOnce(timeoutMs = 6000) {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: timeoutMs,
      maximumAge: 0,
    })
  })
}

/**
 * Reverse-geocodes via OpenStreetMap Nominatim — no API key, and it's the
 * only free reverse-geocoder that returns named points of interest (shops,
 * restaurants) rather than just streets, which is the whole point here.
 * Usage policy requires a descriptive UA and caps to ~1 req/sec, both fine
 * for "once per transaction, on demand".
 */
async function reverseGeocode(lat, lng) {
  const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}&zoom=18&addressdetails=0`
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) return undefined
  const data = await res.json()
  return data?.name || data?.display_name?.split(',')[0]
}

/**
 * Grabs the current position once and resolves it to a place name. Fails
 * soft: any error (denied, timeout, offline) resolves to `null` rather than
 * throwing, so adding an expense never blocks on location.
 */
export async function captureLocationOnce() {
  if (!locationSupported()) return null
  try {
    const pos = await getPositionOnce()
    const { latitude: lat, longitude: lng } = pos.coords
    const placeName = await reverseGeocode(lat, lng).catch(() => undefined)
    return placeName ? { lat, lng, placeName } : null
  } catch {
    return null
  }
}
