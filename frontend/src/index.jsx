import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { IncidentProvider } from './context/IncidentContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <IncidentProvider>
        <App />
      </IncidentProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
