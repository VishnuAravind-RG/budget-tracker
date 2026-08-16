import React from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.jsx'
import './styles.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

// Only register in production — in dev the SW would serve stale bundles.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* PWA install is a bonus; the app works fine without it */
    })
  })
}
