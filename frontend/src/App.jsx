import { useCallback, useEffect, useState } from 'react'

import { api, clearToken, getToken, setToken } from './api.js'
import { clearCache, readCache, writeCache } from './cache.js'
import { monthName } from './format.js'
import { captureLocationOnce, getLocationConsent } from './location.js'
import AddTransaction from './components/AddTransaction.jsx'
import Budgets from './components/Budgets.jsx'
import Dashboard from './components/Dashboard.jsx'
import Login from './components/Login.jsx'
import Review from './components/Review.jsx'
import Transactions from './components/Transactions.jsx'
import Fuel from './components/Fuel.jsx'
import Todos from './components/Todos.jsx'
import Summary from './components/Summary.jsx'
import LendingCard from './components/LendingCard.jsx'
import RecurringCard from './components/RecurringCard.jsx'
import { ChartIcon, FuelIcon, HomeIcon, ListIcon, PlusIcon, TargetIcon, TodoIcon } from './components/Icons.jsx'

// Review has no tab of its own: now that obvious businesses classify
// themselves, the queue is empty almost all the time, and a permanent tab for
// a usually-empty screen is wasted space in a bar that has to scroll.
//
// The screen itself still exists and is reached from the dashboard banner,
// which only appears when something genuinely needs answering. Losing the
// screen entirely would be a correctness problem, not a cosmetic one — it's
// the only place to say "this was a friend, not a shop", and without that
// money lent out silently counts as spending.
const TABS = [
  { id: 'home', label: 'Home', Icon: HomeIcon },
  { id: 'summary', label: 'Summary', Icon: ChartIcon },
  { id: 'add', label: 'Add', Icon: PlusIcon },
  { id: 'history', label: 'History', Icon: ListIcon },
  { id: 'fuel', label: 'Fuel', Icon: FuelIcon },
  { id: 'todos', label: 'To-do', Icon: TodoIcon },
  { id: 'budgets', label: 'Budgets', Icon: TargetIcon },
]

export default function App() {
  const [token, setTok] = useState(getToken())
  const [tab, setTab] = useState('home')
  const now = new Date()
  const [period, setPeriod] = useState({ month: now.getMonth() + 1, year: now.getFullYear() })

  const [categories, setCategories] = useState([])
  const [summary, setSummary] = useState(null)
  const [trend, setTrend] = useState(null)
  const [transactions, setTransactions] = useState(null)
  const [review, setReview] = useState(null)
  const [limits, setLimits] = useState([])
  const [lending, setLending] = useState(null)
  const [recurring, setRecurring] = useState([])
  const [capture, setCapture] = useState(null)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [wakingUp, setWakingUp] = useState(false)

  // A 401 anywhere means the token is dead — drop straight back to login.
  useEffect(() => {
    const onUnauthorized = () => {
      clearToken()
      // Drop the snapshot too: whoever signs in next must not be shown the
      // previous session's figures while their own first fetch is in flight.
      clearCache()
      setTok('')
    }
    window.addEventListener('bt:unauthorized', onUnauthorized)
    return () => window.removeEventListener('bt:unauthorized', onUnauthorized)
  }, [])

  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(''), 2200)
    return () => clearTimeout(id)
  }, [toast])

  const refresh = useCallback(async () => {
    if (!token) return
    setError('')
    // The backend runs on Render's free tier. It keeps itself awake now (see
    // main.py's keep-alive), but a genuine cold start still costs ~60s — and
    // a bare "Loading…" for a minute reads as a broken app rather than a
    // waking one. If nothing has come back after 3s, say what's happening.
    const slowTimer = setTimeout(() => setWakingUp(true), 3000)
    try {
      const [s, tr, tx, rv, lm, ln, rec, cap] = await Promise.all([
        api.summary(period.month, period.year),
        api.trend(period.month, period.year),
        api.transactions(period.month, period.year),
        api.needsReview(),
        api.budgetLimits(),
        api.lending(),
        // Recurrence is worked out server-side now. It used to be derived here
        // from an extra unfiltered fetch of the last 200 transactions — an
        // arbitrary window that silently truncated the history the detection
        // depends on, and a second full transaction list down the wire on
        // every refresh purely to look for repeats.
        api.recurring(),
        api.captureHealth(),
      ])
      setSummary(s)
      setTrend(tr)
      setTransactions(tx)
      setReview(rv)
      setLimits(lm)
      setLending(ln)
      setRecurring(rec)
      setCapture(cap)
      // Snapshot only after everything succeeded, so a half-failed refresh
      // can never persist a partial month and show wrong totals next open.
      writeCache(period.month, period.year, {
        summary: s, trend: tr, transactions: tx, review: rv,
        limits: lm, lending: ln, recurring: rec,
        // `capture` is deliberately NOT cached. It is a live status, not a
        // description of the month: a snapshot saying "nothing has arrived in
        // two days" is only true at the moment it was taken, and painting it
        // from storage put a stale outage warning at the top of the dashboard
        // on every open until the refresh landed — raising an alarm about a
        // problem that may have been fixed days ago.
      })
    } catch (err) {
      if (err.status !== 401) setError(err.message)
    } finally {
      clearTimeout(slowTimer)
      setWakingUp(false)
    }
  }, [token, period.month, period.year])

  // Paint the selected month from its own snapshot, then let refresh()
  // overwrite it. Assigned OUTRIGHT, never merged with what's on screen:
  // keeping the existing value when a month has no snapshot is what made
  // stepping back to July show August's ₹36,750 under a "July 2026" header
  // until the fetch landed — a figure that is simply wrong for the period
  // it's captioned with. A blank while loading is honest; a confident wrong
  // number is not.
  useEffect(() => {
    if (!token) return
    const cached = readCache(period.month, period.year)
    setSummary(cached?.summary ?? null)
    setTrend(cached?.trend ?? null)
    setTransactions(cached?.transactions ?? null)
    setReview(cached?.review ?? null)
    setLending(cached?.lending ?? null)
    setLimits(cached?.limits ?? [])
    setRecurring(cached?.recurring ?? [])
    // Never restored from cache — see writeCache above. Null means "we have
    // not heard yet", which renders nothing at all, and that is the honest
    // state until a fresh answer arrives.
    setCapture(null)
  }, [token, period.month, period.year])

  useEffect(() => {
    if (!token) return
    api.categories().then(setCategories).catch(() => {})
  }, [token])

  // MacroDroid posts new SMS to the API in the background — the phone can
  // have this PWA sitting open (or backgrounded) when that happens, and
  // nothing else here re-fetches on its own. Re-checking whenever the tab
  // regains focus/visibility means reopening the app is enough to see it,
  // without polling constantly while it's actually in the background.
  useEffect(() => {
    if (!token) return
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [token, refresh])

  useEffect(() => { refresh() }, [refresh])

  if (!token) {
    return (
      <Login
        onSuccess={(value) => {
          setToken(value)
          setTok(value)
        }}
      />
    )
  }

  const shiftMonth = (delta) => {
    setPeriod(({ month, year }) => {
      const m = month + delta
      if (m < 1) return { month: 12, year: year - 1 }
      if (m > 12) return { month: 1, year: year + 1 }
      return { month: m, year }
    })
  }

  const isCurrentMonth = period.month === now.getMonth() + 1 && period.year === now.getFullYear()

  async function recategorise(id, category) {
    await api.setCategory(id, category)
    setToast(`Moved to ${category}`)
    refresh()
  }

  async function classify(id, payload) {
    await api.classifyTransaction(id, payload)
    setToast('Saved & remembered')
    // Answering the LAST item used to leave you sitting on "Nothing to
    // review" with no tab highlighted — Review has no tab to step away by,
    // and the banner that brought you here is gone the moment the queue
    // empties. Finishing the queue should return you where you came from.
    const wasLast = (review?.length ?? 0) <= 1
    if (wasLast) setTab((current) => (current === 'review' ? 'home' : current))
    refresh()
  }

  async function removeTransaction(id) {
    await api.deleteTransaction(id)
    setToast('Deleted')
    refresh()
  }

  async function addManual(payload) {
    const created = await api.addManual(payload)
    setToast('Added')
    // Functional update, not setTab('home') directly: the add can take a
    // couple of seconds on Render's free tier, and if the user has already
    // navigated to a different tab while waiting, this resolving afterward
    // shouldn't yank them back to Home out from under them. Only jump to
    // Home if they're still sitting on Add when the response lands.
    setTab((current) => (current === 'add' ? 'home' : current))
    refresh()

    // Location is opt-in and never blocks the add above — it resolves in the
    // background afterward, only filling in a merchant name if none was
    // typed, only for an actual shop expense (never for lending/wallet/
    // transfer, where a nearby place name isn't the answer to anything),
    // and only once consent was explicitly given.
    if (payload.kind === 'expense' && !payload.merchant && getLocationConsent() === 'granted') {
      captureLocationOnce().then((place) => {
        if (!place?.placeName) return
        api.renameMerchant(created.id, place.placeName).then(() => {
          setToast(`Detected: ${place.placeName}`)
          refresh()
        })
      })
    }
  }

  async function saveBudget(category, limit) {
    await api.setBudget(category, limit)
    await refresh()
  }

  async function snoozeLending(person) {
    await api.snoozeLendingReminder(person, 3)
    refresh()
  }

  async function clearLendingReminder(person) {
    await api.clearLendingReminder(person)
    refresh()
  }

  async function markRepaid(p) {
    // Ask rather than assume the full amount — a partial cash repayment is
    // common, and silently clearing the whole debt would lose real money.
    const raw = prompt(
      `How much did ${p.person} pay back?\n\nOutstanding: ₹${p.outstanding}\nLeave as-is to settle it fully.`,
      String(p.outstanding),
    )
    if (raw === null) return
    const value = Number.parseFloat(raw)
    if (!Number.isFinite(value) || value <= 0) return
    try {
      await api.markRepaid(p.person, value)
      setToast(value >= p.outstanding ? 'Settled up' : 'Part repayment recorded')
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const spentByCategory = Object.fromEntries((summary?.categories || []).map((c) => [c.category, c.spent]))
  const reviewCount = review?.length || 0

  return (
    <div className="app">
      <header className="topbar">
        <h1>Budget</h1>
        <div className="month-nav">
          <button onClick={() => shiftMonth(-1)} aria-label="Previous month">‹</button>
          <span className="label">{monthName(period.month)} {period.year}</span>
          <button onClick={() => shiftMonth(1)} disabled={isCurrentMonth} aria-label="Next month">›</button>
        </div>
      </header>

      <main>
        {error && <div className="banner error">{error}</div>}
        {/* Only when there's genuinely nothing on screen — with a cached month
            already painted, this banner would imply the visible figures are
            broken rather than simply a few seconds old. */}
        {wakingUp && !error && !summary && (
          <div className="banner">
            Waking up the server — the free hosting tier sleeps when idle, so this
            first load can take up to a minute. It&apos;s quick after that.
          </div>
        )}

        {tab === 'home' && (
          <>
            <Dashboard
              summary={summary}
              trend={trend}
              capture={capture}
              reviewCount={reviewCount}
              onGoReview={() => setTab('review')}
              onGoBudgets={() => setTab('budgets')}
            />
            <LendingCard
              lending={lending}
              onSnooze={snoozeLending}
              onClearReminder={clearLendingReminder}
              onRepaid={markRepaid}
            />
            {/* Only on the current month: "due on the 5th" and "already paid"
                are statements about now, and captioning them with a month
                you're merely browsing would make them false. */}
            {isCurrentMonth && <RecurringCard items={recurring} />}
          </>
        )}

        {/* Reached from the dashboard banner, not the tab bar. It therefore
            carries its own way out — otherwise answering the last item leaves
            you on an empty screen with no tab highlighted and no obvious
            route back. */}
        {tab === 'review' && (
          <>
            <button
              type="button"
              className="banner"
              onClick={() => setTab('home')}
              style={{ width: '100%', border: 0, textAlign: 'left', cursor: 'pointer' }}
            >
              ← Back to Home
            </button>
            <Review items={review} categories={categories} onClassify={classify} />
          </>
        )}

        {tab === 'add' && (
          <AddTransaction
            categories={categories}
            onAdd={addManual}
            onImported={(count) => {
              // Deliberately stays on the Add tab. The import result carries an
              // Undo button, and jumping to Home would take it away before it
              // could be read — these rows come from a vision model reading a
              // photo, so being able to take one back matters more than
              // tidily returning home.
              setToast(count > 0
                ? `Imported ${count} transaction${count === 1 ? '' : 's'}`
                : 'Import undone')
              refresh()
            }}
            onPasted={(txn) => {
              // A pasted alert can land needing review (unrecognised payee)
              // rather than being confirmed outright — say so either way.
              setToast(txn?.needs_review ? 'Added — needs review' : 'Added')
              setTab((current) => (current === 'add' ? 'home' : current))
              refresh()
            }}
          />
        )}

        {tab === 'history' && (
          <Transactions
            transactions={transactions}
            categories={categories}
            monthLabel={`${monthName(period.month)} ${period.year}`}
            onRecategorise={recategorise}
            onDelete={removeTransaction}
          />
        )}

        {tab === 'summary' && <Summary />}

        {tab === 'fuel' && <Fuel />}

        {tab === 'todos' && <Todos />}

        {tab === 'budgets' && (
          <Budgets
            categories={categories}
            limits={limits}
            spentByCategory={spentByCategory}
            onSave={saveBudget}
            onRefresh={refresh}
            onSignOut={() => {
              // Deliberate action only — a stray tap must never log the user out.
              if (confirm("Sign out? You'll need to re-enter your access token to get back in.")) {
                clearToken()
                clearCache()
                setTok('')
              }
            }}
          />
        )}
      </main>

      {toast && <div className="toast">{toast}</div>}

      <nav className="tabbar">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            aria-current={tab === id ? 'page' : undefined}
          >
            <Icon />
            {label}
            {/* The pending count rides on Home now that Review has no tab —
                otherwise nothing in the bar would ever hint that something
                needs answering. */}
            {id === 'home' && reviewCount > 0 && <span className="tab-badge">{reviewCount}</span>}
          </button>
        ))}
      </nav>
    </div>
  )
}
