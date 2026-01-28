import React from 'react'
import { useIncidents } from '../context/IncidentContext'

/**
 * Displays a real-time feed of incidents.
 */
export function IncidentFeed({ onSelectIncident, selectedIncidentId }) {
  const { incidents } = useIncidents()

  // Sort incidents by creation time (newest first)
  const sortedIncidents = Object.values(incidents).sort((a, b) => {
    return new Date(b.createdAt) - new Date(a.createdAt)
  })

  if (sortedIncidents.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white mb-4">Incidents</h2>
        <div className="text-center py-8 text-gray-400">
          <p>No incidents yet</p>
          <p className="text-sm mt-2">Waiting for errors...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-white mb-4">
        Incidents ({sortedIncidents.length})
      </h2>
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {sortedIncidents.map((incident) => (
          <IncidentCard
            key={incident.id}
            incident={incident}
            isSelected={incident.id === selectedIncidentId}
            onClick={() => onSelectIncident?.(incident.id)}
          />
        ))}
      </div>
    </div>
  )
}

function IncidentCard({ incident, isSelected, onClick }) {
  const statusColors = {
    active: 'bg-yellow-500',
    triaging: 'bg-blue-500 animate-pulse',
    triaged: 'bg-blue-600',
    fixing: 'bg-purple-500 animate-pulse',
    fixed_generated: 'bg-purple-600',
    reviewing: 'bg-indigo-500 animate-pulse',
    reviewed: 'bg-indigo-600',
    testing: 'bg-cyan-500 animate-pulse',
    tested: 'bg-cyan-600',
    pr_created: 'bg-green-500',
    verifying: 'bg-emerald-500 animate-pulse',
    verified: 'bg-emerald-600',
    fixed: 'bg-green-600',
    escalated: 'bg-red-500',
  }

  const statusLabels = {
    active: 'Active',
    triaging: 'Triaging...',
    triaged: 'Triaged',
    fixing: 'Generating Fix...',
    fixed_generated: 'Fix Generated',
    reviewing: 'Code Review...',
    reviewed: 'Reviewed',
    testing: 'Testing...',
    tested: 'Tests Passed',
    pr_created: 'PR Created',
    verifying: 'Verifying...',
    verified: 'Verified',
    fixed: 'Fixed',
    escalated: 'Escalated',
  }

  const severityColors = {
    critical: 'text-red-400',
    high: 'text-orange-400',
    medium: 'text-yellow-400',
    low: 'text-green-400',
  }

  const formatTime = (timestamp) => {
    if (!timestamp) return ''
    const date = new Date(timestamp)
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div
      className={`p-3 rounded-lg cursor-pointer transition-colors animate-slide-in ${
        isSelected
          ? 'bg-gray-600 ring-2 ring-blue-500'
          : 'bg-gray-700 hover:bg-gray-600'
      }`}
      onClick={onClick}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-gray-400">{incident.id}</span>
            <span className={`text-xs font-medium ${severityColors[incident.severity] || 'text-gray-400'}`}>
              {incident.severity?.toUpperCase()}
            </span>
          </div>
          <p className="text-sm text-white mt-1 truncate">{incident.title}</p>
          <p className="text-xs text-gray-400 mt-1">{incident.service}</p>
        </div>
        <div className="flex flex-col items-end gap-1 ml-2">
          <div className="flex items-center gap-1">
            <div className={`w-2 h-2 rounded-full ${statusColors[incident.status] || 'bg-gray-500'}`} />
            <span className="text-xs text-gray-300">
              {statusLabels[incident.status] || incident.status}
            </span>
          </div>
          <span className="text-xs text-gray-500">
            {formatTime(incident.createdAt)}
          </span>
        </div>
      </div>

      {/* Progress indicator for active incidents */}
      {incident.thinking && (
        <div className="mt-2 text-xs text-gray-400 flex items-center gap-2">
          <span className="animate-pulse">...</span>
          <span>{incident.thinking.message}</span>
        </div>
      )}

      {/* PR link if created */}
      {incident.pr?.url && (
        <div className="mt-2">
          <a
            href={incident.pr.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-400 hover:text-blue-300"
            onClick={(e) => e.stopPropagation()}
          >
            PR #{incident.pr.number}
          </a>
        </div>
      )}
    </div>
  )
}

export default IncidentFeed
