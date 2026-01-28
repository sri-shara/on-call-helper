import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { IncidentProvider } from './context/IncidentContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <IncidentProvider>
      <App />
    </IncidentProvider>
  </React.StrictMode>,
)
