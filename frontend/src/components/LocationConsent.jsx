import { PinIcon } from './Icons.jsx'
import { setLocationConsent } from '../location.js'

/**
 * Asks once, plainly, for what will actually happen — because "location
 * access" reads as tracking unless the scope is spelled out up front. Sits
 * inline with Add rather than as a native browser popup on load, so it only
 * fires when the user is in the middle of doing the thing it's for.
 */
export default function LocationConsent({ onDecide }) {
  return (
    <div className="location-consent">
      <PinIcon className="location-consent-icon" />
      <div style={{ flex: 1 }}>
        <p>
          <strong>Auto-detect the shop from where you are?</strong> Right after you add an expense with
          no merchant typed, your location is checked for a few seconds, matched to the nearest named
          place, then switched off. Never checked at any other time, never sent anywhere except to look
          up the place name.
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button
            type="button"
            className="btn"
            style={{ width: 'auto', padding: '7px 14px', fontSize: 13 }}
            onClick={() => { setLocationConsent('granted'); onDecide(true) }}
          >
            Enable
          </button>
          <button
            type="button"
            className="btn secondary"
            style={{ width: 'auto', padding: '7px 14px', fontSize: 13 }}
            onClick={() => { setLocationConsent('denied'); onDecide(false) }}
          >
            No thanks
          </button>
        </div>
      </div>
    </div>
  )
}
