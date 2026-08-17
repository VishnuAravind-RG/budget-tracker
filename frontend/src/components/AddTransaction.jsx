import { useEffect, useState } from 'react'
import { api } from '../api.js'
import AddExpense from './AddExpense.jsx'
import ScanReceipt from './ScanReceipt.jsx'

export default function AddTransaction({ categories, onAdd, onScanned }) {
  const [mode, setMode] = useState('manual')
  const [scanAvailable, setScanAvailable] = useState(null)

  useEffect(() => {
    api.aiStatus().then((s) => setScanAvailable(s.receipt_scan_available)).catch(() => setScanAvailable(false))
  }, [])

  return (
    <>
      <div className="seg" style={{ marginBottom: 14 }}>
        <button type="button" aria-pressed={mode === 'manual'} onClick={() => setMode('manual')}>
          Manual
        </button>
        <button type="button" aria-pressed={mode === 'photo'} onClick={() => setMode('photo')}>
          From photo
        </button>
      </div>

      {mode === 'manual' ? (
        <AddExpense categories={categories} onAdd={onAdd} />
      ) : (
        <ScanReceipt available={scanAvailable} onAdded={onScanned} />
      )}
    </>
  )
}
