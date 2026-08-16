import { useState } from 'react'

import { api } from '../api.js'

export default function Login({ onSuccess }) {
  const [token, setTokenInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    const value = token.trim()
    if (!value || busy) return
    setBusy(true)
    setError('')
    try {
      await api.verifyToken(value)
      onSuccess(value)
    } catch (err) {
      setError(err.status === 401 ? 'That token was rejected.' : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login-box">
        <img className="login-mark" src="/icon-192.png" alt="" width="56" height="56" />
        <h1>Budget</h1>
        <p>Enter the access token you set as <code>AUTH_TOKEN</code> on the backend.</p>

        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="token">Access token</label>
            <input
              id="token"
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="paste your token"
              required
            />
          </div>

          {error && <div className="banner error" style={{ marginBottom: 12 }}>{error}</div>}

          <button className="btn" type="submit" disabled={!token.trim() || busy}>
            {busy ? 'Checking…' : 'Unlock'}
          </button>
        </form>
      </div>
    </div>
  )
}
