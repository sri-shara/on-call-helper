import React from 'react'
import { useIncidents } from '../context/IncidentContext'

/**
 * Displays key metrics about incident processing.
 */
export function MetricsPanel() {
  const { metrics, isConnected } = useIncidents()

  const formatMTTR = (seconds) => {
    if (!seconds) return 'N/A'
    if (seconds < 60) return `${Math.round(seconds)}s`
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`
    return `${(seconds / 3600).toFixed(1)}h`
  }

  const formatRate = (rate) => {
    if (rate === null || rate === undefined) return 'N/A'
    return `${Math.round(rate)}%`
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-white">Metrics</h2>
        <div className="flex items-center gap-2">
          <div
            className={`w-2 h-2 rounded-full ${
              isConnected ? 'bg-green-500 animate-pulse-dot' : 'bg-red-500'
            }`}
          />
          <span className="text-xs text-gray-400">
            {isConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard
          label="Total Incidents"
          value={metrics.total_incidents}
          color="text-white"
        />
        <MetricCard
          label="Auto-Fixed"
          value={metrics.auto_fixed}
          color="text-green-400"
        />
        <MetricCard
          label="Escalated"
          value={metrics.escalated}
          color="text-red-400"
        />
        <MetricCard
          label="Processing"
          value={metrics.processing}
          color="text-blue-400"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 mt-4 pt-4 border-t border-gray-700">
        <MetricCard
          label="MTTR"
          value={formatMTTR(metrics.mttr_seconds)}
          color="text-yellow-400"
          subtitle="Mean Time to Resolve"
        />
        <MetricCard
          label="Success Rate"
          value={formatRate(metrics.success_rate)}
          color="text-green-400"
          subtitle="Auto-fix success"
        />
      </div>
    </div>
  )
}

function MetricCard({ label, value, color, subtitle }) {
  return (
    <div className="text-center">
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      <div className="text-xs text-gray-400">{label}</div>
      {subtitle && (
        <div className="text-xs text-gray-500 mt-1">{subtitle}</div>
      )}
    </div>
  )
}

export default MetricsPanel
