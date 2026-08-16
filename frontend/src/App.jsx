import { useCallback, useEffect, useState } from 'react'

import { api, clearToken, getToken, setToken } from './api.js'
import { monthName } from './format.js'
import AddExpense from './components/AddExpense.jsx'
import Budgets from './components/Budgets.jsx'
import Dashboard from './components/Dashboard.jsx'
import Login from './components/Login.jsx'
import Review from './components/Review.jsx'
import Transactions from './components/Transactions.jsx'
import { HomeIcon, ListIcon, PlusIcon, ReviewIcon, TargetIcon } from './components/Icons.jsx'

const TABS = [
  { id: 'home', label: 'Home', Icon: HomeIcon },
  { id: 'review', label: 'Review', Icon: ReviewIcon },
  { id: 'add', label: 'Add', Icon: PlusIcon },
  { id: 'history', label: 'History', Icon: ListIcon },
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
      const [s, tr, tx, rv, lm] = await Promise.all([
        api.summary(period.month, period.year),
        api.trend(period.month, period.year),
        api.transactions(period.month, period.year),
        api.needsReview(),
        api.budgetLimits(),
      ])
      setSummary(s)
      setTrend(tr)
      setTransactions(tx)
      setReview(rv)
      setLimits(lm)
    } catch (err) {
      if (err.status !== 401) setError(err.message)
    }
  }, [token, period.month, period.year])

  useEffect(() => {
    if (!token) return
    api.categories().then(setCategories).catch(() => {})
  }, [token])

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

  async function removeTransaction(id) {
    await api.deleteTransaction(id)
    setToast('Deleted')
    refresh()
  }

  async function addManual(payload) {
    await api.addManual(payload)
    setToast('Added')
    setTab('home')
    refresh()
  }

  async function saveBudget(category, limit) {
    await api.setBudget(category, limit)
    await refresh()
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
          <Dashboard
            summary={summary}
            trend={trend}
            reviewCount={reviewCount}
            onGoReview={() => setTab('review')}
            onGoBudgets={() => setTab('budgets')}
          />
        )}

        {tab === 'review' && (
          <Review items={review} categories={categories} onPick={recategorise} />
        )}

        {tab === 'add' && <AddExpense categories={categories} onAdd={addManual} />}

        {tab === 'history' && (
          <Transactions
            transactions={transactions}
            categories={categories}
            onRecategorise={recategorise}
            onDelete={removeTransaction}
          />
        )}

        {tab === 'budgets' && (
          <Budgets
            categories={categories}
            limits={limits}
            spentByCategory={spentByCategory}
            onSave={saveBudget}
          />
        )}

        {tab === 'budgets' && (
          <button
            className="btn secondary"
            onClick={() => {
              clearToken()
              setTok('')
            }}
          >
            Sign out
          </button>
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
