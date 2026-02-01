import React, { useState, useEffect } from 'react'

/**
 * Table view component displaying all incidents with AI agent outputs.
 * Data persists during the session until the server is restarted.
 */
export function IncidentTable() {
  const [incidents, setIncidents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedRows, setExpandedRows] = useState(new Set())

  // Fetch incidents with complete details
  const fetchIncidents = async () => {
    try {
      setLoading(true)
      setError(null)
      const response = await fetch('/api/incidents/all/details')
      if (!response.ok) {
        throw new Error(`Failed to fetch incidents: ${response.statusText}`)
      }
      const data = await response.json()
      setIncidents(data.incidents || [])
    } catch (err) {
      console.error('Error fetching incidents:', err)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  // Fetch on mount and set up auto-refresh
  useEffect(() => {
    fetchIncidents()
    // Refresh every 5 seconds to get updates
    const interval = setInterval(fetchIncidents, 5000)
    return () => clearInterval(interval)
  }, [])

  const toggleRow = (incidentId) => {
    const newExpanded = new Set(expandedRows)
    if (newExpanded.has(incidentId)) {
      newExpanded.delete(incidentId)
    } else {
      newExpanded.add(incidentId)
    }
    setExpandedRows(newExpanded)
  }

  const formatDate = (dateString) => {
    if (!dateString) return '-'
    const date = new Date(dateString)
    return date.toLocaleString()
  }

  const formatDuration = (seconds) => {
    if (!seconds) return '-'
    if (seconds < 60) return `${seconds.toFixed(1)}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  }

  const getStatusBadge = (status) => {
    const statusColors = {
      active: 'bg-yellow-500/20 text-yellow-400',
      triaging: 'bg-blue-500/20 text-blue-400',
      fixing: 'bg-purple-500/20 text-purple-400',
      reviewing: 'bg-indigo-500/20 text-indigo-400',
      testing: 'bg-cyan-500/20 text-cyan-400',
      pr_created: 'bg-green-500/20 text-green-400',
      verifying: 'bg-emerald-500/20 text-emerald-400',
      fixed: 'bg-green-600/20 text-green-300',
      escalated: 'bg-red-500/20 text-red-400',
      filtered: 'bg-gray-500/20 text-gray-400',
    }
    return (
      <span className={`px-2 py-1 rounded text-xs font-medium ${statusColors[status] || 'bg-gray-500/20 text-gray-400'}`}>
        {status}
      </span>
    )
  }

  const getSeverityBadge = (severity) => {
    const severityColors = {
      critical: 'bg-red-500/20 text-red-400',
      high: 'bg-orange-500/20 text-orange-400',
      medium: 'bg-yellow-500/20 text-yellow-400',
      low: 'bg-green-500/20 text-green-400',
    }
    return (
      <span className={`px-2 py-1 rounded text-xs font-medium ${severityColors[severity] || 'bg-gray-500/20 text-gray-400'}`}>
        {severity?.toUpperCase() || '-'}
      </span>
    )
  }

  if (loading && incidents.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-8">
        <div className="text-center text-gray-400">Loading incidents...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-gray-800 rounded-lg p-8">
        <div className="text-center text-red-400">
          Error: {error}
          <button
            onClick={fetchIncidents}
            className="ml-4 px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (incidents.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg p-8">
        <div className="text-center text-gray-400">
          <p>No incidents found</p>
          <p className="text-sm mt-2">Incidents will appear here as they are detected</p>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-white">
          Incident Table ({incidents.length})
        </h2>
        <button
          onClick={fetchIncidents}
          className="px-3 py-1 bg-gray-700 text-gray-300 rounded hover:bg-gray-600 text-sm"
        >
          Refresh
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-gray-700">
              <th className="text-left p-3 text-sm font-semibold text-gray-300">ID</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Title</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Service</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Severity</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Status</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Triage</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Fix</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Test</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300">Created</th>
              <th className="text-left p-3 text-sm font-semibold text-gray-300"></th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((item) => {
              const incident = item.incident
              const triage = item.triage
              const fix = item.fix
              const test = item.test
              const verification = item.verification
              const isExpanded = expandedRows.has(incident.id)

              return (
                <React.Fragment key={incident.id}>
                  <tr className="border-b border-gray-700 hover:bg-gray-700/50 transition-colors">
                    <td className="p-3 text-sm font-mono text-gray-400">{incident.id}</td>
                    <td className="p-3 text-sm text-white max-w-xs truncate" title={incident.title}>
                      {incident.title}
                    </td>
                    <td className="p-3 text-sm text-gray-300">{incident.service_name}</td>
                    <td className="p-3">{getSeverityBadge(incident.severity)}</td>
                    <td className="p-3">{getStatusBadge(incident.status)}</td>
                    <td className="p-3 text-sm text-gray-400">
                      {triage ? (
                        <div>
                          <div className="font-medium text-white">{triage.classification}</div>
                          <div className="text-xs">{(triage.confidence * 100).toFixed(0)}%</div>
                        </div>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td className="p-3 text-sm text-gray-400">
                      {fix ? (
                        <div>
                          <div className="font-medium text-white">✓ Generated</div>
                          <div className="text-xs">Iteration {fix.iteration}</div>
                        </div>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td className="p-3 text-sm text-gray-400">
                      {test ? (
                        <div>
                          <div className={`font-medium ${test.passed ? 'text-green-400' : 'text-red-400'}`}>
                            {test.passed ? '✓ Passed' : '✗ Failed'}
                          </div>
                          <div className="text-xs">{test.tests_passed}/{test.tests_run}</div>
                        </div>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td className="p-3 text-sm text-gray-400">{formatDate(incident.created_at)}</td>
                    <td className="p-3">
                      <button
                        onClick={() => toggleRow(incident.id)}
                        className="text-blue-400 hover:text-blue-300 text-sm"
                      >
                        {isExpanded ? '▼' : '▶'}
                      </button>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td colSpan="10" className="p-4 bg-gray-900/50">
                        <div className="space-y-4">
                          {/* Incident Details */}
                          <div>
                            <h3 className="text-sm font-semibold text-white mb-2">Incident Details</h3>
                            <div className="grid grid-cols-2 gap-4 text-sm">
                              <div>
                                <span className="text-gray-400">Error Message:</span>
                                <div className="mt-1 p-2 bg-gray-800 rounded font-mono text-xs text-red-300 overflow-x-auto">
                                  {incident.error_message || '-'}
                                </div>
                              </div>
                              <div>
                                <span className="text-gray-400">File Path:</span>
                                <div className="mt-1 text-gray-300 font-mono text-xs">
                                  {incident.file_path || '-'}
                                </div>
                              </div>
                              {incident.resolved_at && (
                                <div>
                                  <span className="text-gray-400">Resolved At:</span>
                                  <div className="mt-1 text-gray-300 text-xs">
                                    {formatDate(incident.resolved_at)}
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>

                          {/* Triage Results */}
                          {triage && (
                            <div>
                              <h3 className="text-sm font-semibold text-white mb-2">Triage Results</h3>
                              <div className="grid grid-cols-2 gap-4 text-sm">
                                <div>
                                  <span className="text-gray-400">Root Cause:</span>
                                  <div className="mt-1 text-gray-300">{triage.root_cause}</div>
                                </div>
                                <div>
                                  <span className="text-gray-400">Confidence:</span>
                                  <div className="mt-1 text-gray-300">{(triage.confidence * 100).toFixed(1)}%</div>
                                </div>
                                {triage.suggested_fix && (
                                  <div className="col-span-2">
                                    <span className="text-gray-400">Suggested Fix:</span>
                                    <div className="mt-1 text-gray-300">{triage.suggested_fix}</div>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}

                          {/* Fix Results */}
                          {fix && (
                            <div>
                              <h3 className="text-sm font-semibold text-white mb-2">Fix Results</h3>
                              <div className="grid grid-cols-2 gap-4 text-sm">
                                <div>
                                  <span className="text-gray-400">File:</span>
                                  <div className="mt-1 text-gray-300 font-mono text-xs">{fix.file_path}</div>
                                </div>
                                <div>
                                  <span className="text-gray-400">Iteration:</span>
                                  <div className="mt-1 text-gray-300">{fix.iteration}</div>
                                </div>
                                <div className="col-span-2">
                                  <span className="text-gray-400">Explanation:</span>
                                  <div className="mt-1 text-gray-300">{fix.explanation}</div>
                                </div>
                                <div className="col-span-2">
                                  <span className="text-gray-400">Diff Summary:</span>
                                  <div className="mt-1 text-gray-300">{fix.diff_summary}</div>
                                </div>
                              </div>
                            </div>
                          )}

                          {/* Test Results */}
                          {test && (
                            <div>
                              <h3 className="text-sm font-semibold text-white mb-2">Test Results</h3>
                              <div className="grid grid-cols-3 gap-4 text-sm">
                                <div>
                                  <span className="text-gray-400">Status:</span>
                                  <div className={`mt-1 font-medium ${test.passed ? 'text-green-400' : 'text-red-400'}`}>
                                    {test.passed ? 'Passed' : 'Failed'}
                                  </div>
                                </div>
                                <div>
                                  <span className="text-gray-400">Tests Run:</span>
                                  <div className="mt-1 text-gray-300">{test.tests_run}</div>
                                </div>
                                <div>
                                  <span className="text-gray-400">Duration:</span>
                                  <div className="mt-1 text-gray-300">{formatDuration(test.duration_ms / 1000)}</div>
                                </div>
                              </div>
                            </div>
                          )}

                          {/* Verification Results */}
                          {verification && (
                            <div>
                              <h3 className="text-sm font-semibold text-white mb-2">Verification Results</h3>
                              <div className="grid grid-cols-2 gap-4 text-sm">
                                <div>
                                  <span className="text-gray-400">Status:</span>
                                  <div className={`mt-1 font-medium ${
                                    verification.status === 'success' ? 'text-green-400' :
                                    verification.status === 'partial' ? 'text-yellow-400' : 'text-red-400'
                                  }`}>
                                    {verification.status}
                                  </div>
                                </div>
                                <div>
                                  <span className="text-gray-400">Errors Before/After:</span>
                                  <div className="mt-1 text-gray-300">
                                    {verification.errors_before} → {verification.errors_after}
                                  </div>
                                </div>
                                {verification.pr_url && (
                                  <div className="col-span-2">
                                    <span className="text-gray-400">PR:</span>
                                    <div className="mt-1">
                                      <a
                                        href={verification.pr_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-blue-400 hover:text-blue-300"
                                      >
                                        {verification.pr_url}
                                      </a>
                                    </div>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default IncidentTable
