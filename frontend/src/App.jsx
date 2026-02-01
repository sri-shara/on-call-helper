import React, { useState } from 'react'
import { IncidentProvider, useIncidents } from './context/IncidentContext'
import { MetricsPanel } from './components/MetricsPanel'
import { IncidentFeed } from './components/IncidentFeed'
import { IncidentTable } from './components/IncidentTable'
import { AgentThinking } from './components/AgentThinking'
import { CodeDiff } from './components/CodeDiff'
import { SandboxStatus } from './components/SandboxStatus'
import { VerificationStatus } from './components/VerificationStatus'

/**
 * Main dashboard layout component.
 */
function Dashboard() {
  const { incidents, isConnected, error } = useIncidents()
  const connectionStatus = isConnected ? 'connected' : 'disconnected'
  const [selectedIncidentId, setSelectedIncidentId] = useState(null)
  const [viewMode, setViewMode] = useState('dashboard') // 'dashboard' or 'table'

  const selectedIncident = selectedIncidentId ? incidents[selectedIncidentId] : null

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700 px-6 py-4">
        <div className="flex items-center justify-between max-w-screen-2xl mx-auto">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-bold">On-Call Helper</h1>
            <span className="text-sm text-gray-400">AI-Powered Incident Response</span>
          </div>
          <div className="flex items-center gap-4">
            {/* Connection Status */}
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  connectionStatus === 'connected'
                    ? 'bg-green-500'
                    : connectionStatus === 'connecting'
                    ? 'bg-yellow-500 animate-pulse'
                    : 'bg-red-500'
                }`}
              />
              <span className="text-sm text-gray-400">
                {connectionStatus === 'connected'
                  ? 'Connected'
                  : connectionStatus === 'connecting'
                  ? 'Connecting...'
                  : 'Disconnected'}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div className="bg-red-900/50 border-b border-red-700 px-6 py-3">
          <div className="max-w-screen-2xl mx-auto text-red-200 text-sm">
            Connection error: {error}
          </div>
        </div>
      )}

      {/* Main Content */}
      <main className="p-6 max-w-screen-2xl mx-auto">
        {/* Metrics Row */}
        <div className="mb-6">
          <MetricsPanel />
        </div>

        {/* View Mode Toggle */}
        <div className="mb-6 flex items-center gap-4">
          <button
            onClick={() => setViewMode('dashboard')}
            className={`px-4 py-2 rounded-lg font-medium transition-colors ${
              viewMode === 'dashboard'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Dashboard View
          </button>
          <button
            onClick={() => setViewMode('table')}
            className={`px-4 py-2 rounded-lg font-medium transition-colors ${
              viewMode === 'table'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Table View
          </button>
        </div>

        {/* Table View */}
        {viewMode === 'table' && (
          <div className="mb-6">
            <IncidentTable />
          </div>
        )}

        {/* Dashboard View */}
        {viewMode === 'dashboard' && (
          <div className="grid grid-cols-12 gap-6">
          {/* Left Column - Incidents & Agent Activity */}
          <div className="col-span-12 lg:col-span-4 space-y-6">
            <IncidentFeed
              onSelectIncident={setSelectedIncidentId}
              selectedIncidentId={selectedIncidentId}
            />
            <AgentThinking />
          </div>

          {/* Right Column - Details */}
          <div className="col-span-12 lg:col-span-8 space-y-6">
            {/* Incident Header */}
            {selectedIncident && (
              <div className="bg-gray-800 rounded-lg p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-3">
                      <h2 className="text-lg font-semibold">{selectedIncident.title}</h2>
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          selectedIncident.severity === 'critical'
                            ? 'bg-red-500/20 text-red-400'
                            : selectedIncident.severity === 'high'
                            ? 'bg-orange-500/20 text-orange-400'
                            : selectedIncident.severity === 'medium'
                            ? 'bg-yellow-500/20 text-yellow-400'
                            : 'bg-green-500/20 text-green-400'
                        }`}
                      >
                        {selectedIncident.severity?.toUpperCase()}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 mt-2 text-sm text-gray-400">
                      <span className="font-mono">{selectedIncident.id}</span>
                      <span>{selectedIncident.service}</span>
                      {selectedIncident.errorType && (
                        <span className="font-mono text-red-400">{selectedIncident.errorType}</span>
                      )}
                    </div>
                  </div>
                  <div className="text-right">
                    <span
                      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm ${
                        selectedIncident.status === 'fixed' || selectedIncident.status === 'verified'
                          ? 'bg-green-500/20 text-green-400'
                          : selectedIncident.status === 'escalated'
                          ? 'bg-red-500/20 text-red-400'
                          : 'bg-blue-500/20 text-blue-400'
                      }`}
                    >
                      <div
                        className={`w-1.5 h-1.5 rounded-full ${
                          selectedIncident.status === 'fixed' || selectedIncident.status === 'verified'
                            ? 'bg-green-400'
                            : selectedIncident.status === 'escalated'
                            ? 'bg-red-400'
                            : 'bg-blue-400 animate-pulse'
                        }`}
                      />
                      {selectedIncident.status}
                    </span>
                  </div>
                </div>

                {/* Error Message */}
                {selectedIncident.errorMessage && (
                  <div className="mt-4 p-3 bg-gray-900 rounded font-mono text-sm text-red-300 overflow-x-auto">
                    {selectedIncident.errorMessage}
                  </div>
                )}

                {/* Triage Info */}
                {selectedIncident.triage && (
                  <div className="mt-4 grid grid-cols-3 gap-4">
                    <div className="p-3 bg-gray-700 rounded">
                      <div className="text-xs text-gray-400">Classification</div>
                      <div className="text-sm font-medium mt-1">
                        {selectedIncident.triage.classification}
                      </div>
                    </div>
                    <div className="p-3 bg-gray-700 rounded">
                      <div className="text-xs text-gray-400">Confidence</div>
                      <div className="text-sm font-medium mt-1">
                        {Math.round((selectedIncident.triage.confidence || 0) * 100)}%
                      </div>
                    </div>
                    <div className="p-3 bg-gray-700 rounded">
                      <div className="text-xs text-gray-400">Root Cause</div>
                      <div className="text-sm font-medium mt-1 truncate">
                        {selectedIncident.triage.rootCause || 'Analyzing...'}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Code Diff */}
            <CodeDiff incident={selectedIncident} />

            {/* Status Panels */}
            <div className="grid grid-cols-2 gap-6">
              <SandboxStatus incident={selectedIncident} />
              <VerificationStatus incident={selectedIncident} />
            </div>
          </div>
        </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 px-6 py-4 mt-8">
        <div className="max-w-screen-2xl mx-auto text-center text-sm text-gray-500">
          On-Call Helper - Nucleus MDR AI Incident Response
        </div>
      </footer>
    </div>
  )
}

/**
 * App root with providers.
 */
export default function App() {
  return (
    <IncidentProvider>
      <Dashboard />
    </IncidentProvider>
  )
}
