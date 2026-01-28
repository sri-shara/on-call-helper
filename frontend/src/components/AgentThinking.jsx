import React from 'react'
import { useIncidents } from '../context/IncidentContext'

/**
 * Displays real-time agent activity and thinking messages.
 */
export function AgentThinking() {
  const { events } = useIncidents()

  // Filter to agent-related events
  const agentEvents = events.filter(
    (e) =>
      e.type === 'agent_thinking' ||
      e.type === 'triage_started' ||
      e.type === 'triage_complete' ||
      e.type === 'fix_started' ||
      e.type === 'fix_generated' ||
      e.type === 'review_started' ||
      e.type === 'review_complete'
  ).slice(0, 20) // Show last 20

  if (agentEvents.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white mb-4">Agent Activity</h2>
        <div className="text-center py-4 text-gray-400 text-sm">
          Waiting for agent activity...
        </div>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-white mb-4">Agent Activity</h2>
      <div className="space-y-2 max-h-64 overflow-y-auto">
        {agentEvents.map((event) => (
          <AgentEventCard key={event.id} event={event} />
        ))}
      </div>
    </div>
  )
}

function AgentEventCard({ event }) {
  const formatTime = (timestamp) => {
    if (!timestamp) return ''
    const date = new Date(timestamp)
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  const getEventIcon = (type) => {
    switch (type) {
      case 'triage_started':
        return { icon: '?', color: 'bg-blue-500' }
      case 'triage_complete':
        return { icon: '!', color: 'bg-blue-600' }
      case 'fix_started':
        return { icon: '>', color: 'bg-purple-500' }
      case 'fix_generated':
        return { icon: '+', color: 'bg-purple-600' }
      case 'review_started':
        return { icon: '@', color: 'bg-indigo-500' }
      case 'review_complete':
        return { icon: '*', color: 'bg-indigo-600' }
      case 'agent_thinking':
        return { icon: '~', color: 'bg-gray-600' }
      default:
        return { icon: '-', color: 'bg-gray-600' }
    }
  }

  const getEventMessage = (event) => {
    const { type, data } = event

    switch (type) {
      case 'triage_started':
        return `Analyzing incident ${data.incident_id}...`
      case 'triage_complete':
        return `Triage complete: ${data.classification} (${Math.round((data.confidence || 0) * 100)}% confidence)`
      case 'fix_started':
        return `Generating fix for ${data.incident_id}...`
      case 'fix_generated':
        return `Fix generated: ${data.diff_summary || 'Code updated'} (iteration ${data.iteration || 1})`
      case 'review_started':
        return `CodeRabbit reviewing fix...`
      case 'review_complete':
        return `Review complete: ${data.passed ? 'Passed' : 'Issues found'}`
      case 'agent_thinking':
        return `[${data.agent}] ${data.message}`
      default:
        return type
    }
  }

  const { icon, color } = getEventIcon(event.type)

  return (
    <div className="flex items-start gap-2 p-2 rounded bg-gray-700/50 animate-slide-in">
      <div className={`w-5 h-5 rounded flex items-center justify-center ${color} text-xs font-mono text-white flex-shrink-0`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-200">{getEventMessage(event)}</p>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xs text-gray-500">{formatTime(event.timestamp)}</span>
          {event.data?.incident_id && (
            <span className="text-xs text-gray-400 font-mono">{event.data.incident_id}</span>
          )}
        </div>
      </div>
    </div>
  )
}

export default AgentThinking
