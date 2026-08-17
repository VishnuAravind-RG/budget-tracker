import { useCallback, useEffect, useState } from 'react'

import { api, clearToken, getToken, setToken } from './api.js'
import { monthName } from './format.js'
import { captureLocationOnce, getLocationConsent } from './location.js'
import { detectRecurring } from './recurring.js'
import AddTransaction from './components/AddTransaction.jsx'
import Budgets from './components/Budgets.jsx'
import Dashboard from './components/Dashboard.jsx'
import Login from './components/Login.jsx'
import Review from './components/Review.jsx'
import Transactions from './components/Transactions.jsx'
import Fuel from './components/Fuel.jsx'
import Todos from './components/Todos.jsx'
import LendingCard from './components/LendingCard.jsx'
import RecurringCard from './components/RecurringCard.jsx'
import { FuelIcon, HomeIcon, ListIcon, PlusIcon, ReviewIcon, TargetIcon, TodoIcon } from './components/Icons.jsx'

const TABS = [
  { id: 'home', label: 'Home', Icon: HomeIcon },
  { id: 'review', label: 'Review', Icon: ReviewIcon },
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
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')

  // A 401 anywhere means the token is dead — drop straight back to login.
  useEffect(() => {
    const onUnauthorized = () => {
      clearToken()
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
    try {
      const [s, tr, tx, rv, lm, ln, recentAll] = await Promise.all([
        api.summary(period.month, period.year),
        api.trend(period.month, period.year),
        api.transactions(period.month, period.year),
        api.needsReview(),
        api.budgetLimits(),
        api.lending(),
        // No month/year filter — the last 200 transactions across however
        // many months that spans, purely to spot a merchant repeating
        // across months. The month-scoped `tx` above can't do that alone.
        api.transactions(),
      ])
      setSummary(s)
      setTrend(tr)
      setTransactions(tx)
      setReview(rv)
      setLimits(lm)
      setLending(ln)
      setRecurring(detectRecurring(recentAll))
    } catch (err) {
      if (err.status !== 401) setError(err.message)
    }
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

        {tab === 'home' && (
          <>
            <Dashboard
              summary={summary}
              trend={trend}
              reviewCount={reviewCount}
              onGoReview={() => setTab('review')}
              onGoBudgets={() => setTab('budgets')}
            />
            <LendingCard lending={lending} onSnooze={snoozeLending} onClearReminder={clearLendingReminder} />
            <RecurringCard items={recurring} />
          </>
        )}

        {tab === 'review' && (
          <Review items={review} categories={categories} onClassify={classify} />
        )}

        {tab === 'add' && (
          <AddTransaction
            categories={categories}
            onAdd={addManual}
            onScanned={() => {
              setToast('Added')
              // Same reasoning as addManual() above — a photo scan can take
              // up to ~30s (Gemini vision), a much wider window for the user
              // to have already navigated elsewhere while it was running.
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

        {tab === 'fuel' && <Fuel />}

        {tab === 'todos' && <Todos />}

        {tab === 'budgets' && (
          <Budgets
            categories={categories}
            limits={limits}
            spentByCategory={spentByCategory}
            onSave={saveBudget}
            onSignOut={() => {
              // Deliberate action only — a stray tap must never log the user out.
              if (confirm("Sign out? You'll need to re-enter your access token to get back in.")) {
                clearToken()
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
            {id === 'review' && reviewCount > 0 && <span className="tab-badge">{reviewCount}</span>}
          </button>
        ))}
      </nav>
    </div>
  )
}
