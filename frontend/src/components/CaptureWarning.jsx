import { useState } from 'react'

/**
 * "Nothing has arrived from the bank in a day and a half."
 *
 * The app's entire value rests on alerts arriving on their own, and when they
 * stop it fails *silently*: MacroDroid stopped forwarding SMS for two days
 * (Android revoked its permission under "pause app activity if unused") and
 * nothing looked wrong anywhere. The totals simply grew more slowly. The gap
 * is still visible in the history — a whole day with no transactions in a
 * month where every other day had several — and it was only noticed by
 * accident, days later, in the middle of doing something else.
 *
 * Dismissible, and dismissal lasts until the next silent day rather than
 * forever: a warning that can be permanently silenced would eventually be
 * silenced and then be worth nothing, but one that reappears every few hours
 * about a known outage is just noise you learn to ignore — which is the same
 * failure by a different route.
 */

const DISMISS_KEY = 'bt_capture_dismissed_until'

// Named for what the user has to go and check, not for the plumbing.
const CHANNEL_NAME = {
  sms: 'SMS forwarding (MacroDroid)',
  gmail: 'the Gmail poller',
}

function readDismissed() {
  try {
    return Number(localStorage.getItem(DISMISS_KEY) || 0)
  } catch {
    return 0
  }
}

function describe(hours) {
  if (hours < 48) return `${Math.round(hours)} hours`
  return `${Math.floor(hours / 24)} days`
}

export default function CaptureWarning({ capture }) {
  const [dismissedUntil, setDismissedUntil] = useState(readDismissed)

  if (!capture?.quiet) return null
  if (Date.now() < dismissedUntil) return null

  const { hours_since_last: hours, last_source: source, manual_since: manualSince, channels } = capture

  // Which pipe to actually go and check. With more than one channel, the one
  // that has been quiet longest is the one that broke — the other may still
  // be delivering, which is exactly the case a single "capture is down"
  // message would get wrong.
  const worst = channels?.length ? channels[channels.length - 1] : null
  const blame = CHANNEL_NAME[worst?.source || source] || 'automatic capture'

  function dismiss() {
    // Six hours: long enough to stop nagging while fixing it, short enough
    // that a forgotten outage resurfaces the same day.
    const until = Date.now() + 6 * 60 * 60 * 1000
    try {
      localStorage.setItem(DISMISS_KEY, String(until))
    } catch {
      /* storage disabled — the warning just won't stay dismissed */
    }
    setDismissedUntil(until)
  }

  return (
    <div className="banner error" style={{ display: 'block' }}>
      <strong>No bank alert in {describe(hours)}.</strong>{' '}
      {/* The tell that separates "quiet because you haven't spent anything"
          from "quiet because the pipe is dead". */}
      {manualSince > 0
        ? `You've logged ${manualSince} transaction${manualSince === 1 ? '' : 's'} by hand since the last one arrived, so spending is still happening — check ${blame}.`
        : `Check ${blame} is still running.`}
      <div style={{ marginTop: 8, display: 'flex', gap: 14, alignItems: 'center' }}>
        <button
          type="button"
          onClick={dismiss}
          style={{
            background: 'none', border: 0, padding: 0,
            color: 'inherit', opacity: 0.75, fontSize: 13, textDecoration: 'underline',
          }}
        >
          Dismiss for today
        </button>
        {channels?.length > 1 && (
          <span style={{ fontSize: 12, opacity: 0.75 }}>
            {channels.map((c) => `${c.source}: ${describe(c.hours_since)} ago`).join(' · ')}
          </span>
        )}
      </div>
    </div>
  )
}
