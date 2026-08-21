import { useEffect, useState } from 'react'
import { api } from '../api.js'
import AddExpense from './AddExpense.jsx'
import ScanReceipt from './ScanReceipt.jsx'
import PasteAlert from './PasteAlert.jsx'
import ScanStatement from './ScanStatement.jsx'

export default function AddTransaction({ categories, onAdd, onPasted, onImported }) {
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
        <button type="button" aria-pressed={mode === 'paste'} onClick={() => setMode('paste')}>
          Paste alert
        </button>
        <button type="button" aria-pressed={mode === 'shot'} onClick={() => setMode('shot')}>
          Screenshot
        </button>
      </div>

      {mode === 'manual' && <AddExpense categories={categories} onAdd={onAdd} />}
      {mode === 'photo' && <ScanReceipt available={scanAvailable} categories={categories} onAdd={onAdd} />}
      {mode === 'paste' && <PasteAlert onAdded={onPasted} />}
      {mode === 'shot' && <ScanStatement available={scanAvailable} onImported={onImported} />}
    </>
  )
}
