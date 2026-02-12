import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { useIncidents } from './context/IncidentContext'

/**
 * Get display info for an incident status
 */
function getStatusDisplay(status, classification) {
  // Use classification from escalation data if available
  const cls = classification

  // Processing states
  if (status === 'active' || status === 'triaging') {
    return { label: 'Analyzing', color: 'blue', animate: true }
  }
  if (status === 'fixing') {
    return { label: 'Generating Fix', color: 'purple', animate: true }
  }
  if (status === 'testing') {
    return { label: 'Testing', color: 'cyan', animate: true }
  }
  if (status === 'reviewing') {
    return { label: 'Reviewing', color: 'indigo', animate: true }
  }
  if (status === 'verifying') {
    return { label: 'Verifying', color: 'amber', animate: true }
  }

  // Success states
  if (status === 'fixed' || status === 'pr_created') {
    return { label: 'Fixed', color: 'green', animate: false }
  }

  // Escalated - check classification for specific label
  if (status === 'escalated') {
    if (cls === 'transient') {
      return { label: 'Self-Healing', color: 'slate', animate: false }
    }
    if (cls === 'infra_issue') {
      return { label: 'Infra Issue', color: 'amber', animate: false }
    }
    return { label: 'Needs Review', color: 'red', animate: false }
  }

  // Default
  return { label: status || 'Active', color: 'blue', animate: true }
}

/**
 * Color utility for status badges
 */
const statusColors = {
  green: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  blue: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  purple: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  cyan: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
  indigo: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30',
  amber: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  red: 'bg-red-500/15 text-red-400 border-red-500/30',
  slate: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
}

const dotColors = {
  green: 'bg-emerald-400',
  blue: 'bg-blue-400',
  purple: 'bg-purple-400',
  cyan: 'bg-cyan-400',
  indigo: 'bg-indigo-400',
  amber: 'bg-amber-400',
  red: 'bg-red-400',
  slate: 'bg-slate-400',
}

/**
 * Status badge component
 */
function StatusBadge({ status, classification, size = 'md' }) {
  const { label, color, animate } = getStatusDisplay(status, classification)

  const sizeClasses = size === 'sm'
    ? 'px-2 py-0.5 text-xs gap-1'
    : 'px-2.5 py-1 text-xs gap-1.5'

  const dotSize = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2'

  return (
    <span className={`inline-flex items-center ${sizeClasses} rounded-md border font-medium ${statusColors[color]}`}>
      <span className={`${dotSize} rounded-full ${dotColors[color]} ${animate ? 'animate-pulse' : ''}`} />
      {label}
    </span>
  )
}

/**
 * Severity indicator
 */
function SeverityDot({ severity }) {
  const colors = {
    critical: 'bg-red-500',
    high: 'bg-orange-500',
    medium: 'bg-yellow-500',
    low: 'bg-slate-500',
  }
  return <span className={`w-2 h-2 rounded-full flex-shrink-0 ${colors[severity] || colors.medium}`} />
}

/**
 * Time ago formatter
 */
function timeAgo(date) {
  if (!date) return ''
  const seconds = Math.floor((new Date() - new Date(date)) / 1000)
  if (seconds < 60) return 'Just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

/**
 * Extract short service name from full service path
 */
function shortServiceName(service) {
  if (!service) return 'Unknown'
  // Handle URLs like "alloydb.googleapis.com" -> "alloydb"
  if (service.includes('.googleapis.com')) {
    return service.split('.')[0]
  }
  // Handle paths like "nucleus-worker" -> "nucleus-worker"
  if (service.includes('/')) {
    return service.split('/').pop()
  }
  return service
}

/**
 * Clean up incident title for display
 */
function cleanTitle(title, service) {
  if (!title) return 'No description'
  let cleaned = title
  // Remove service name prefix like "[alloydb.googleapis.com]"
  if (service) {
    cleaned = cleaned.replace(new RegExp(`^\\[${service.replace('.', '\\.')}\\]\\s*`, 'i'), '')
  }
  // Remove timestamp prefixes like "2026-02-03 02:32:40.463 UTC"
  cleaned = cleaned.replace(/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[\d.]*\s*(UTC)?\s*/i, '')
  // Trim and return
  return cleaned.trim() || title
}

/**
 * Build a GCP Cloud Logging console URL from log name.
 * gcp_log_name format: "projects/PROJECT_ID/logs/LOG_NAME"
 */
function buildCloudLoggingUrl(gcpLogName, gcpResourceType) {
  if (!gcpLogName) return null
  const match = gcpLogName.match(/^projects\/([^/]+)\/logs\/(.+)$/)
  if (!match) return null
  const [, projectId] = match
  const query = `resource.type="${gcpResourceType || 'cloud_run_revision'}"\nlogName="${gcpLogName}"`
  return `https://console.cloud.google.com/logs/query;query=${encodeURIComponent(query)}?project=${projectId}`
}

/**
 * Build a Cloud Logging search URL from a service name (for GChat incidents)
 */
function buildServiceLoggingUrl(serviceName) {
  if (!serviceName || serviceName === 'unknown') return null
  const svcName = serviceName.endsWith('-prod') ? serviceName : `${serviceName}-prod`
  const query = `resource.type="cloud_run_revision"\nresource.labels.service_name="${svcName}"\nseverity>=ERROR`
  return `https://console.cloud.google.com/logs/query;query=${encodeURIComponent(query)};timeRange=PT1H?project=nucleus-449303`
}

/**
 * Small external link icon for Cloud Logging
 */
function CloudLoggingLink({ gcpLogName, gcpResourceType, loggingUrl, serviceName }) {
  const url = loggingUrl || buildCloudLoggingUrl(gcpLogName, gcpResourceType) || buildServiceLoggingUrl(serviceName)
  if (!url) return null
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title="View in Cloud Logging"
      className="inline-flex items-center text-blue-400/70 hover:text-blue-400 transition-colors"
      onClick={(e) => e.stopPropagation()}
    >
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
      </svg>
    </a>
  )
}

/**
 * Section header component
 */
function SectionHeader({ children }) {
  return (
    <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
      {children}
    </h3>
  )
}

/**
 * Incident list item
 */
function IncidentListItem({ incident, isSelected, onClick }) {
  // Get classification from triage or escalation data
  const classification = incident.triage?.classification || incident.escalation?.classification
  const serviceName = shortServiceName(incident.service)
  const title = cleanTitle(incident.title, incident.service)
  const isAnalyzing = !classification && ['active', 'triaging'].includes(incident.status)

  return (
    <div
      onClick={onClick}
      className={`group px-3 py-2.5 cursor-pointer transition-all border-l-2 ${
        isSelected
          ? 'bg-slate-800/80 border-l-blue-500'
          : 'border-l-transparent hover:bg-slate-800/40 hover:border-l-slate-600'
      }`}
    >
      {/* Top row: Service name + Status badge */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <SeverityDot severity={incident.severity} />
          {isAnalyzing ? (
            <span className="flex items-center gap-1.5 text-sm text-slate-400 italic">
              <span className="w-3 h-3 border border-blue-500/40 border-t-blue-400 rounded-full animate-spin flex-shrink-0" />
              Analyzing...
            </span>
          ) : (
            <span className="text-sm font-medium text-slate-200 truncate">
              {serviceName}
            </span>
          )}
          {incident.occurrenceCount > 1 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium flex-shrink-0">
              ×{incident.occurrenceCount}
            </span>
          )}
        </div>
        <StatusBadge
          status={incident.status}
          classification={classification}
          size="sm"
        />
      </div>
      {/* Error message preview */}
      <p className="text-xs text-slate-400 truncate mb-1.5 ml-4">
        {isAnalyzing ? 'Waiting for triage...' : title}
      </p>
      {/* Metadata row */}
      <div className="flex items-center gap-1.5 ml-4 text-[10px] text-slate-600">
        <span className="font-mono">{incident.id?.slice(0, 12)}</span>
        <CloudLoggingLink gcpLogName={incident.gcpLogName} gcpResourceType={incident.gcpResourceType} loggingUrl={incident.gchatMetadata?.logging_url} serviceName={incident.service} />
        <span>·</span>
        <span className="text-slate-500">{timeAgo(incident.createdAt)}</span>
      </div>
    </div>
  )
}

/**
 * Classification badge for detail view
 */
function ClassificationBadge({ classification, confidence }) {
  const configs = {
    fixable: {
      icon: '✓',
      label: 'Auto-Fixable',
      bg: 'bg-emerald-500/10',
      border: 'border-emerald-500/30',
      text: 'text-emerald-400'
    },
    transient: {
      icon: '↻',
      label: 'Self-Healing',
      bg: 'bg-slate-500/10',
      border: 'border-slate-500/30',
      text: 'text-slate-400'
    },
    infra_issue: {
      icon: '⚠',
      label: 'Infrastructure Issue',
      bg: 'bg-amber-500/10',
      border: 'border-amber-500/30',
      text: 'text-amber-400'
    },
    needs_human: {
      icon: '!',
      label: 'Human Review Required',
      bg: 'bg-red-500/10',
      border: 'border-red-500/30',
      text: 'text-red-400'
    },
  }

  const config = configs[classification] || configs.needs_human

  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border ${config.bg} ${config.border}`}>
      <span className={`text-sm ${config.text}`}>{config.icon}</span>
      <span className={`text-sm font-medium ${config.text}`}>{config.label}</span>
      {confidence && (
        <span className="text-xs text-slate-500 ml-1">
          {Math.round(confidence * 100)}%
        </span>
      )}
    </div>
  )
}

/**
 * Info card component
 */
function InfoCard({ label, value, mono = false }) {
  if (!value) return null
  return (
    <div>
      <dt className="text-[10px] font-medium text-slate-500 uppercase tracking-wide mb-1">{label}</dt>
      <dd className={`text-sm text-slate-300 ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </div>
  )
}

/**
 * Code block component
 */
function CodeBlock({ code, variant = 'neutral', maxHeight = '200px' }) {
  const variants = {
    neutral: 'bg-slate-950 border-slate-800',
    error: 'bg-red-950/30 border-red-900/40',
    success: 'bg-emerald-950/30 border-emerald-900/40',
    before: 'bg-red-950/20 border-red-900/30',
    after: 'bg-emerald-950/20 border-emerald-900/30',
  }

  const textColors = {
    neutral: 'text-slate-400',
    error: 'text-red-300',
    success: 'text-emerald-300',
    before: 'text-red-300',
    after: 'text-emerald-300',
  }

  return (
    <pre
      className={`p-3 rounded-lg border text-xs font-mono overflow-auto whitespace-pre-wrap break-words ${variants[variant]}`}
      style={{ maxHeight }}
    >
      <code className={`${textColors[variant]} break-words`}>{code}</code>
    </pre>
  )
}

/**
 * Incident detail panel
 */
function IncidentDetail({ incidentId }) {
  const { incidents } = useIncidents()
  const [details, setDetails] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [feedbackSending, setFeedbackSending] = useState(false)
  const [feedbackSent, setFeedbackSent] = useState(false)
  const lastFetchedStatus = useRef(null)

  // Fetch full details from API
  const fetchDetails = useCallback((id) => {
    if (!id) return
    fetch(`/api/incidents/${id}`)
      .then(res => {
        if (!res.ok) throw new Error('Failed to load incident')
        return res.json()
      })
      .then(data => {
        setDetails(data)
        lastFetchedStatus.current = data?.incident?.status || null
        if (data?.incident?.feedback_given) setFeedbackSent(true)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  // Initial fetch when incident selection changes
  useEffect(() => {
    if (!incidentId) {
      setDetails(null)
      lastFetchedStatus.current = null
      return
    }
    setLoading(true)
    setError(null)
    setFeedbackSent(false)
    fetchDetails(incidentId)
  }, [incidentId, fetchDetails])

  // Re-fetch when the incident's status changes via WebSocket
  const contextStatus = incidents[incidentId]?.status
  useEffect(() => {
    if (!incidentId || loading) return
    // Only re-fetch if status actually changed from what we last fetched
    // This prevents loops where stuck "triaging" incidents keep triggering re-fetches
    if (contextStatus && contextStatus !== lastFetchedStatus.current) {
      lastFetchedStatus.current = contextStatus
      fetchDetails(incidentId)
    }
  }, [contextStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleNotNeedsHuman = async () => {
    if (!incidentId || feedbackSending || feedbackSent) return

    setFeedbackSending(true)
    try {
      const res = await fetch(`/api/incidents/${incidentId}/feedback/not-needs-human`, { method: 'POST' })
      if (!res.ok) console.error('Feedback API returned', res.status)
    } catch (err) {
      console.error('Failed to send feedback:', err)
    } finally {
      setFeedbackSending(false)
      setFeedbackSent(true)
    }
  }

  if (!incidentId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-500">
        <div className="w-16 h-16 rounded-full bg-slate-800/50 flex items-center justify-center mb-4">
          <svg className="w-8 h-8 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
        </div>
        <p className="text-sm">Select an incident to view details</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <span className="text-sm text-slate-500">Loading...</span>
        </div>
      </div>
    )
  }

  if (error || !details) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-red-400">
        <p className="text-sm">{error || 'Failed to load incident'}</p>
      </div>
    )
  }

  const { incident, triage, fix, test, verification } = details

  return (
    <div className="h-full overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 bg-slate-900/95 backdrop-blur-sm border-b border-slate-800 px-6 py-4 z-10">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-slate-100 truncate">
              {incident.service_name}
            </h2>
            <div className="flex items-center gap-2 mt-0.5">
              <p className="text-xs text-slate-500 font-mono">{incident.id}</p>
              <CloudLoggingLink gcpLogName={incident.gcp_log_name} gcpResourceType={incident.gcp_resource_type} loggingUrl={incident.gchat_metadata?.logging_url} serviceName={incident.service_name} />
              {incident.source === 'gchat' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 font-medium">
                  Google Chat
                </span>
              )}
              {incident.gchat_metadata?.sender_name && (
                <span className="text-[10px] text-slate-500">
                  from {incident.gchat_metadata.sender_name}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0">
            {incident.status === 'escalated' && triage?.classification !== 'transient' && (
              feedbackSent ? (
                <span className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600/20 border border-emerald-500/30 rounded-lg text-xs font-medium text-emerald-400">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Feedback Recorded
                </span>
              ) : (
                <button
                  onClick={handleNotNeedsHuman}
                  disabled={feedbackSending}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs font-medium text-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {feedbackSending ? (
                    <>
                      <div className="w-3.5 h-3.5 border-2 border-slate-400/30 border-t-slate-400 rounded-full animate-spin" />
                      Sending...
                    </>
                  ) : (
                    'Does Not Need Human Review'
                  )}
                </button>
              )
            )}
            <StatusBadge
              status={incident.status}
              classification={triage?.classification}
            />
          </div>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Analysis Section */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Analysis</h3>
            {incident.tenant_name && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-blue-500/10 border border-blue-500/30 rounded-md text-xs font-medium text-blue-400">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
                {incident.tenant_name}
              </span>
            )}
          </div>

          {triage ? (
            <div className="space-y-4">
              <ClassificationBadge
                classification={triage.classification}
                confidence={triage.confidence}
              />

              {/* Root Cause */}
              <div>
                <h4 className="text-xs font-medium text-slate-400 mb-2">Root Cause</h4>
                <div className="bg-slate-800/50 rounded-lg p-3 text-sm text-slate-300 leading-relaxed break-words overflow-hidden">
                  {triage.root_cause}
                </div>
              </div>

              {/* Metadata Grid */}
              <dl className="grid grid-cols-2 gap-4">
                <InfoCard label="Affected Service" value={triage.service_name} mono />
                <InfoCard label="File" value={triage.file_path} mono />
              </dl>

              {/* Suggested Fix */}
              {triage.suggested_fix && (
                <div>
                  <h4 className="text-xs font-medium text-slate-400 mb-2">Suggested Approach</h4>
                  <div className="bg-slate-800/50 rounded-lg p-3 text-sm text-slate-300 break-words overflow-hidden">
                    {triage.suggested_fix}
                  </div>
                </div>
              )}

              {/* Additional Context */}
              {triage.related_context?.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-slate-400 mb-2">Context</h4>
                  <ul className="space-y-1.5">
                    {triage.related_context.map((ctx, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-400">
                        <span className="text-slate-600 mt-0.5">•</span>
                        <span>{ctx}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Pre-Analysis Insights */}
              {triage.pre_analysis && (
                <div className="space-y-3">
                  <h4 className="text-xs font-medium text-slate-400 mb-2">Pre-Analysis Insights</h4>

                  {/* Pattern Match */}
                  {triage.pre_analysis.pattern_match && (
                    <div className="bg-purple-500/5 border border-purple-500/20 rounded-lg p-3 break-words overflow-hidden">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className="text-purple-400 text-xs font-medium">Pattern Matched</span>
                        <span className="text-xs text-purple-300/60 bg-purple-500/20 px-1.5 py-0.5 rounded">
                          +{((triage.pre_analysis.pattern_confidence_boost || 0) * 100).toFixed(0)}% confidence
                        </span>
                      </div>
                      <code className="text-xs text-purple-300/80 block mb-1 break-all">
                        {triage.pre_analysis.pattern_match}
                      </code>
                      {triage.pre_analysis.pattern_reason && (
                        <p className="text-xs text-purple-200/60 break-words">{triage.pre_analysis.pattern_reason}</p>
                      )}
                    </div>
                  )}

                  {/* Tenant Type (show only for demo) */}
                  {triage.pre_analysis.is_demo_tenant && (
                    <div className="bg-slate-500/10 border border-slate-500/20 rounded-lg p-3">
                      <div className="flex items-center gap-2">
                        <span className="text-slate-400 text-xs font-medium">Demo Tenant</span>
                        <span className="text-xs text-slate-500">Lower priority</span>
                      </div>
                    </div>
                  )}

                  {/* Infrastructure Health */}
                  {triage.pre_analysis.infra_health && triage.pre_analysis.infra_health.overall_status !== 'healthy' && (
                    <div className={`rounded-lg p-3 break-words overflow-hidden ${
                      triage.pre_analysis.infra_health.overall_status === 'critical'
                        ? 'bg-red-500/5 border border-red-500/20'
                        : 'bg-amber-500/5 border border-amber-500/20'
                    }`}>
                      <div className="flex items-center gap-2 mb-2 flex-wrap">
                        <span className={`text-xs font-medium ${
                          triage.pre_analysis.infra_health.overall_status === 'critical' ? 'text-red-400' : 'text-amber-400'
                        }`}>
                          Infrastructure: {triage.pre_analysis.infra_health.overall_status.toUpperCase()}
                        </span>
                        {triage.pre_analysis.infra_health.cross_tenant_affected && (
                          <span className="text-xs text-amber-300/60">
                            ({triage.pre_analysis.infra_health.affected_tenant_count} tenants affected)
                          </span>
                        )}
                      </div>
                      {triage.pre_analysis.infra_health.checks?.filter(c => c.status !== 'healthy').map((check, i) => (
                        <p key={i} className="text-xs text-slate-400 break-words">
                          • {check.component}: {check.message}
                        </p>
                      ))}
                      {triage.pre_analysis.infra_health.recommendations?.map((rec, i) => (
                        <p key={i} className="text-xs text-amber-300/70 mt-1">{rec}</p>
                      ))}
                    </div>
                  )}

                  {/* Runbook Suggestion */}
                  {triage.pre_analysis.runbook_suggestion && (
                    <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 break-words overflow-hidden">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-blue-400 text-xs font-medium">Suggested Runbook</span>
                      </div>
                      <p className="text-xs text-blue-300/80 break-words">{triage.pre_analysis.runbook_suggestion.name}</p>
                      {triage.pre_analysis.runbook_suggestion.section && (
                        <p className="text-xs text-blue-200/60 break-words">Section: {triage.pre_analysis.runbook_suggestion.section}</p>
                      )}
                      <p className="text-xs text-slate-500 mt-1 break-all">{triage.pre_analysis.runbook_suggestion.path}</p>
                    </div>
                  )}
                </div>
              )}

              {/* GCP Queries Used */}
              {triage.gcp_queries?.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-slate-400 mb-2">GCP Queries Used</h4>
                  <ul className="space-y-1.5">
                    {triage.gcp_queries.map((query, i) => (
                      <li key={i} className="text-xs font-mono text-slate-500 bg-slate-800/50 p-2 rounded border border-slate-700/50 break-all overflow-x-auto">
                        {query}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-3 text-slate-400 py-4">
              <div className="w-5 h-5 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
              <span className="text-sm">Analyzing error...</span>
            </div>
          )}
        </section>

        {/* Error Details */}
        <section>
          <SectionHeader>Error Details</SectionHeader>
          <CodeBlock code={incident.error_message} variant="error" maxHeight="120px" />
          {incident.stack_trace && (
            <div className="mt-3">
              <h4 className="text-xs font-medium text-slate-500 mb-2">Stack Trace</h4>
              <CodeBlock code={incident.stack_trace} variant="neutral" maxHeight="150px" />
            </div>
          )}
          {incident.tenant_name && (
            <p className="text-xs text-slate-600 mt-2">Tenant: {incident.tenant_name}</p>
          )}
        </section>

        {/* Code Fix */}
        {fix && (
          <section>
            <SectionHeader>Code Fix</SectionHeader>
            <div className="space-y-4">
              <div className="bg-slate-800/50 rounded-lg p-3 text-sm text-slate-300">
                {fix.explanation}
              </div>
              <p className="text-xs text-slate-500">
                File: <span className="font-mono text-blue-400">{fix.file_path}</span>
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <h5 className="text-xs font-medium text-red-400/80 mb-2">Before</h5>
                  <CodeBlock code={fix.original_code} variant="before" maxHeight="200px" />
                </div>
                <div>
                  <h5 className="text-xs font-medium text-emerald-400/80 mb-2">After</h5>
                  <CodeBlock code={fix.fixed_code} variant="after" maxHeight="200px" />
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Test Results */}
        {test && (
          <section>
            <SectionHeader>Test Results</SectionHeader>
            <div className={`rounded-lg border p-4 ${
              test.passed
                ? 'bg-emerald-500/5 border-emerald-500/20'
                : 'bg-red-500/5 border-red-500/20'
            }`}>
              <div className="flex items-center gap-2 mb-2">
                <span className={test.passed ? 'text-emerald-400' : 'text-red-400'}>
                  {test.passed ? '✓ Tests Passed' : '✗ Tests Failed'}
                </span>
              </div>
              {(test.tests_run > 0 || test.duration_ms > 0) && (
                <div className="flex gap-4 text-xs text-slate-500">
                  {test.tests_run > 0 && <span>{test.tests_passed}/{test.tests_run} passed</span>}
                  {test.duration_ms > 0 && <span>{(test.duration_ms / 1000).toFixed(1)}s</span>}
                  {test.coverage_percent && <span>{test.coverage_percent.toFixed(1)}% coverage</span>}
                </div>
              )}
            </div>
          </section>
        )}

        {/* PR Link */}
        {(incident.pr_url || verification?.pr_url) && (
          <section>
            <SectionHeader>Pull Request</SectionHeader>
            <a
              href={incident.pr_url || verification?.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm text-blue-400 transition-colors"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
              </svg>
              View Pull Request
              <svg className="w-3 h-3 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
          </section>
        )}

        {/* Verification */}
        {verification && (
          <section>
            <SectionHeader>Production Verification</SectionHeader>
            <div className={`rounded-lg border p-4 ${
              verification.status === 'success' ? 'bg-emerald-500/5 border-emerald-500/20' :
              verification.status === 'failed' ? 'bg-red-500/5 border-red-500/20' :
              'bg-amber-500/5 border-amber-500/20'
            }`}>
              <span className={
                verification.status === 'success' ? 'text-emerald-400' :
                verification.status === 'failed' ? 'text-red-400' :
                'text-amber-400'
              }>
                {verification.status === 'success' ? '✓ Verified' :
                 verification.status === 'failed' ? '✗ Failed' :
                 '⋯ In Progress'}
              </span>
              {verification.message && (
                <p className="text-sm text-slate-400 mt-2">{verification.message}</p>
              )}
            </div>
          </section>
        )}

        {/* Resolution for non-fixable */}
        {triage && triage.classification !== 'fixable' && !fix && (
          <section>
            <SectionHeader>Resolution</SectionHeader>
            <div className={`rounded-lg border p-4 break-words overflow-hidden ${
              triage.classification === 'transient' ? 'bg-slate-800/50 border-slate-700' :
              triage.classification === 'infra_issue' ? 'bg-amber-500/5 border-amber-500/20' :
              'bg-red-500/5 border-red-500/20'
            }`}>
              {triage.classification === 'transient' && (
                <>
                  <h4 className="text-slate-300 font-medium mb-1">No Action Required</h4>
                  <p className="text-sm text-slate-400">
                    This is a self-healing error. The system handles this automatically through retry mechanisms.
                  </p>
                </>
              )}
              {triage.classification === 'infra_issue' && (
                <>
                  <h4 className="text-amber-400 font-medium mb-1">Infrastructure Issue</h4>
                  <p className="text-sm text-amber-200/70 mb-2">
                    Not a code bug. Check infrastructure components.
                  </p>
                  {triage.runbook_reference && (
                    <p className="text-xs text-amber-200/60">Runbook: {triage.runbook_reference}</p>
                  )}
                  {triage.manual_steps?.length > 0 && (
                    <ol className="text-sm text-amber-200/70 mt-2 space-y-1 list-decimal list-inside">
                      {triage.manual_steps.map((step, i) => <li key={i}>{step}</li>)}
                    </ol>
                  )}
                  {triage.gcloud_commands?.length > 0 && (
                    <div className="mt-3">
                      <h5 className="text-xs font-medium text-amber-300/70 mb-2">Diagnostic Commands</h5>
                      <div className="space-y-1.5">
                        {triage.gcloud_commands.map((cmd, i) => (
                          <pre key={i} className="text-xs font-mono text-green-300/80 bg-slate-950 p-2 rounded border border-slate-700/50 overflow-x-auto whitespace-pre-wrap break-all">
                            $ {cmd}
                          </pre>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
              {triage.classification === 'needs_human' && (
                <>
                  <h4 className="text-red-400 font-medium mb-1">Human Review Required</h4>
                  <p className="text-sm text-red-200/70 mb-2">
                    This issue is too complex for automated resolution. Manual investigation needed.
                  </p>
                  {triage.runbook_reference && (
                    <p className="text-xs text-red-200/60 mb-2">Runbook: {triage.runbook_reference}</p>
                  )}
                  {triage.manual_steps?.length > 0 && (
                    <ol className="text-sm text-red-200/70 space-y-1 list-decimal list-inside">
                      {triage.manual_steps.map((step, i) => <li key={i}>{step}</li>)}
                    </ol>
                  )}
                  {triage.gcloud_commands?.length > 0 && (
                    <div className="mt-3">
                      <h5 className="text-xs font-medium text-red-300/70 mb-2">Diagnostic Commands</h5>
                      <div className="space-y-1.5">
                        {triage.gcloud_commands.map((cmd, i) => (
                          <pre key={i} className="text-xs font-mono text-green-300/80 bg-slate-950 p-2 rounded border border-slate-700/50 overflow-x-auto whitespace-pre-wrap break-all">
                            $ {cmd}
                          </pre>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

/**
 * Header metrics
 */
function MetricPill({ label, value, color, isActive, onClick }) {
  const colors = {
    default: 'text-slate-400',
    green: 'text-emerald-400',
    amber: 'text-amber-400',
    blue: 'text-blue-400',
    red: 'text-red-400',
    slate: 'text-slate-400',
  }
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-2 py-1 rounded-md transition-colors ${
        isActive ? 'bg-slate-800' : 'hover:bg-slate-800/50'
      }`}
    >
      <span className={`font-semibold tabular-nums ${colors[color] || colors.default}`}>{value}</span>
      <span className="text-slate-500 text-xs">{label}</span>
    </button>
  )
}

/**
 * Main dashboard
 */
function Dashboard({ source = 'gcp' }) {
  const { incidents, isConnected, metrics } = useIncidents()
  const [selectedIncidentId, setSelectedIncidentId] = useState(null)
  const [filter, setFilter] = useState('all')
  const [timeWindow, setTimeWindow] = useState(null) // null = all time, or minutes (5, 15, 60, 180, 360)
  const [debugInfo, setDebugInfo] = useState({ apiError: null, lastFetch: null })

  const incidentList = useMemo(() => {
    let list = Object.values(incidents)

    // Filter by source
    list = list.filter(inc => (inc.source || 'gcp') === source)

    // Apply time window filter
    if (timeWindow) {
      const cutoff = new Date(Date.now() - timeWindow * 60 * 1000)
      list = list.filter(inc => new Date(inc.createdAt) >= cutoff)
    }

    if (filter !== 'all') {
      list = list.filter(inc => {
        const classification = inc.triage?.classification || inc.escalation?.classification
        const status = inc.status

        if (filter === 'processing') {
          return ['active', 'triaging', 'fixing', 'testing', 'reviewing', 'verifying'].includes(status)
        }
        if (filter === 'no-action') {
          // No Action = transient (self-healing)
          return classification === 'transient'
        }
        if (filter === 'review') {
          // Review = needs_human or infra_issue (exclude transient/self-healing)
          if (classification === 'transient') return false
          const needsReview = classification === 'needs_human' || classification === 'infra_issue'
          const notFixed = status !== 'fixed' && status !== 'pr_created'
          return status === 'escalated' || (needsReview && notFixed)
        }
        if (filter === 'pr-raised') {
          // PR Raised = fixed or pr_created
          return status === 'fixed' || status === 'pr_created'
        }
        return true
      })
    }

    return list.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
  }, [incidents, filter, timeWindow, source])

  // Calculate accurate metrics from incidents (respects time window and source)
  const calculatedMetrics = useMemo(() => {
    let list = Object.values(incidents)

    // Filter by source
    list = list.filter(i => (i.source || 'gcp') === source)

    if (timeWindow) {
      const cutoff = new Date(Date.now() - timeWindow * 60 * 1000)
      list = list.filter(i => new Date(i.createdAt) >= cutoff)
    }

    const selfHealing = list.filter(i =>
      (i.triage?.classification || i.escalation?.classification) === 'transient'
    ).length
    const processing = list.filter(i =>
      ['active', 'triaging', 'fixing', 'testing', 'reviewing', 'verifying'].includes(i.status)
    ).length
    const noAction = selfHealing
    const review = list.filter(i => {
      const cls = i.triage?.classification || i.escalation?.classification
      return i.status === 'escalated' || cls === 'needs_human' || cls === 'infra_issue'
    }).length
    const prRaised = list.filter(i =>
      i.status === 'fixed' || i.status === 'pr_created'
    ).length

    return {
      total_incidents: list.length,
      processing,
      no_action_needed: noAction,
      review_needed: review,
      pr_raised: prRaised,
      selfHealing,
    }
  }, [incidents, timeWindow, source])

  return (
    <div className="h-full bg-slate-900 text-white flex flex-col">

      {/* Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <div className="w-96 border-r border-slate-800 flex flex-col bg-slate-900/50">
          {/* Filter tabs */}
          <div className="px-3 py-2 border-b border-slate-800">
            <div className="flex items-center gap-0.5 p-0.5 bg-slate-800/50 rounded-lg">
              {[
                { id: 'all', label: 'All' },
                { id: 'processing', label: 'Active' },
                { id: 'no-action', label: 'No Action' },
                { id: 'review', label: 'Review' },
                { id: 'pr-raised', label: 'Fixed' },
              ].map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setFilter(tab.id)}
                  className={`flex-1 px-2 py-1.5 text-[11px] font-medium rounded-md transition-colors whitespace-nowrap ${
                    filter === tab.id
                      ? 'bg-slate-700 text-slate-200'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {/* Time window filter */}
            <div className="flex items-center gap-1 mt-1.5">
              <span className="text-[10px] text-slate-600 mr-0.5">Window:</span>
              {[
                { id: null, label: 'All' },
                { id: 5, label: '5m' },
                { id: 15, label: '15m' },
                { id: 60, label: '1h' },
                { id: 180, label: '3h' },
                { id: 360, label: '6h' },
              ].map(opt => (
                <button
                  key={opt.id ?? 'all'}
                  onClick={() => setTimeWindow(opt.id)}
                  className={`px-1.5 py-0.5 text-[10px] font-medium rounded transition-colors ${
                    timeWindow === opt.id
                      ? 'bg-blue-600/30 text-blue-300 border border-blue-500/40'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto">
            {incidentList.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-slate-500 px-4">
                <p className="text-sm font-medium">No {source === 'gchat' ? 'chat cases' : 'incidents'} to display</p>
                <p className="text-xs text-slate-600 mt-1">{source === 'gchat' ? 'Monitoring Google Chat...' : 'Monitoring GCP logs...'}</p>
                {/* Debug info */}
                <div className="mt-6 p-4 bg-slate-800/50 rounded-lg text-xs text-left max-w-md w-full">
                  <p className="font-semibold text-slate-400 mb-2">Debug Info:</p>
                  <div className="space-y-1 text-slate-500">
                    <p>• Incidents in state: <span className="text-slate-300">{Object.keys(incidents).length}</span></p>
                    <p>• WebSocket: <span className={isConnected ? 'text-emerald-400' : 'text-red-400'}>{isConnected ? 'Connected' : 'Disconnected'}</span></p>
                    <p>• Server metrics total: <span className="text-slate-300">{metrics.total_incidents}</span></p>
                    <p>• Current filter: <span className="text-slate-300">{filter}</span></p>
                  </div>
                  <div className="mt-4 pt-4 border-t border-slate-700">
                    <p className="text-slate-400 mb-2">To test:</p>
                    <code className="block text-xs bg-slate-900 p-2 rounded break-all">
                      curl -X POST http://localhost:8000/webhook/test \<br/>
                      &nbsp;&nbsp;-H "Content-Type: application/json" \<br/>
                      &nbsp;&nbsp;-d '{"{"}error_message": "Test error", "service_name": "test-service"{"}"}'
                    </code>
                  </div>
                  <p className="mt-4 text-slate-600 text-[10px]">Check browser console (F12) for detailed logs</p>
                </div>
              </div>
            ) : (
              <div className="divide-y divide-slate-800/50">
                {incidentList.map(incident => (
                  <IncidentListItem
                    key={incident.id}
                    incident={incident}
                    isSelected={selectedIncidentId === incident.id}
                    onClick={() => setSelectedIncidentId(incident.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Detail */}
        <div className="flex-1 bg-slate-900">
          <IncidentDetail incidentId={selectedIncidentId} />
        </div>
      </div>
    </div>
  )
}

/**
 * Health check run list item
 */
function HealthCheckRunItem({ run, isSelected, onClick }) {
  const statusConfig = {
    running:   { dot: 'bg-blue-500 animate-pulse', label: 'Running', text: 'text-blue-400' },
    completed: { dot: 'bg-emerald-500', label: 'Passed', text: 'text-emerald-400' },
    failed:    { dot: 'bg-red-500', label: 'Failed', text: 'text-red-400' },
    timeout:   { dot: 'bg-amber-500', label: 'Timeout', text: 'text-amber-400' },
  }
  const config = statusConfig[run.status] || statusConfig.failed

  return (
    <div
      onClick={onClick}
      className={`group px-3 py-2.5 cursor-pointer transition-all border-l-2 ${
        isSelected
          ? 'bg-slate-800/80 border-l-blue-500'
          : 'border-l-transparent hover:bg-slate-800/40 hover:border-l-slate-600'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-0.5">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full flex-shrink-0 ${config.dot}`} />
          <span className={`text-xs font-medium ${config.text}`}>{config.label}</span>
        </div>
        {run.duration_seconds != null && (
          <span className="text-[10px] text-slate-500">{run.duration_seconds}s</span>
        )}
      </div>
      <div className="text-[10px] text-slate-500 ml-4">
        {new Date(run.started_at).toLocaleString()}
      </div>
    </div>
  )
}

/**
 * On-Call Checkout page - health check history with live streaming
 */
function CheckoutPage() {
  const [runs, setRuns] = useState([])
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [selectedRunOutput, setSelectedRunOutput] = useState(null)
  const [liveOutput, setLiveOutput] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [loadingOutput, setLoadingOutput] = useState(false)
  const [error, setError] = useState(null)
  const [summary, setSummary] = useState(null)
  const [summarizing, setSummarizing] = useState(false)
  const [copied, setCopied] = useState(false)
  const outputRef = useRef(null)

  // Fetch run history on mount
  const fetchRuns = useCallback(async () => {
    try {
      const res = await fetch('/api/health-checks')
      if (res.ok) {
        const data = await res.json()
        setRuns(data.runs)
      }
    } catch (e) {
      console.error('Failed to fetch health check runs:', e)
    }
  }, [])

  useEffect(() => { fetchRuns() }, [fetchRuns])

  // Fetch a single historical run's output
  const fetchRunOutput = useCallback(async (runId) => {
    setLoadingOutput(true)
    setSelectedRunOutput(null)
    try {
      const res = await fetch(`/api/health-checks/${runId}`)
      if (res.ok) {
        const data = await res.json()
        setSelectedRunOutput(data)
      }
    } catch (e) {
      console.error('Failed to fetch run output:', e)
    } finally {
      setLoadingOutput(false)
    }
  }, [])

  // Auto-scroll during live streaming
  const isViewingLive = isRunning && selectedRunId && runs.find(r => r.id === selectedRunId)?.status === 'running'
  useEffect(() => {
    if (isViewingLive && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [liveOutput, isViewingLive])

  // Trigger a new health check with SSE streaming
  const runHealthCheck = useCallback(async () => {
    if (isRunning) return
    setIsRunning(true)
    setLiveOutput('')
    setSelectedRunOutput(null)
    setError(null)

    try {
      const res = await fetch('/api/health-checks/run', { method: 'POST' })

      if (res.status === 409) {
        setError('Health check is already running')
        setIsRunning(false)
        return
      }
      if (!res.ok) {
        const err = await res.json()
        setError(err.error || 'Failed to start health check')
        setIsRunning(false)
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() // keep incomplete event in buffer

        for (const eventText of events) {
          if (!eventText.trim()) continue

          const eventMatch = eventText.match(/^event:\s*(\w+)\ndata:\s*(.+)$/s)
          if (!eventMatch) continue

          const [, eventType, dataStr] = eventMatch
          let data
          try { data = JSON.parse(dataStr) } catch { continue }

          if (eventType === 'metadata') {
            setSelectedRunId(data.id)
            setRuns(prev => [{
              id: data.id,
              started_at: data.started_at,
              status: 'running',
              completed_at: null,
              exit_code: null,
              duration_seconds: null,
            }, ...prev])
          } else if (eventType === 'output') {
            setLiveOutput(prev => prev + data.line)
          } else if (eventType === 'complete') {
            setRuns(prev => prev.map(r =>
              r.id === data.id
                ? { ...r, status: data.status, exit_code: data.exit_code, duration_seconds: data.duration_seconds, completed_at: new Date().toISOString() }
                : r
            ))
          } else if (eventType === 'error') {
            setRuns(prev => prev.map(r =>
              r.id === data.id ? { ...r, status: 'failed', completed_at: new Date().toISOString() } : r
            ))
            setError(data.message)
          }
        }
      }
    } catch (e) {
      console.error('Health check stream error:', e)
      setError(e.message)
    } finally {
      setIsRunning(false)
    }
  }, [isRunning])

  // Summarize a health check run
  const summarizeRun = useCallback(async (runId) => {
    setSummarizing(true)
    setSummary(null)
    setCopied(false)
    try {
      const res = await fetch(`/api/health-checks/${runId}/summarize`, { method: 'POST' })
      if (res.ok) {
        const data = await res.json()
        setSummary(data.summary)
      } else {
        const err = await res.json()
        setError(err.error || 'Failed to summarize')
      }
    } catch (e) {
      console.error('Summarize failed:', e)
      setError('Failed to summarize health check')
    } finally {
      setSummarizing(false)
    }
  }, [])

  const copySummary = useCallback(() => {
    if (!summary) return
    navigator.clipboard.writeText(summary).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [summary])

  // Clear summary when switching runs
  useEffect(() => {
    setSummary(null)
    setCopied(false)
  }, [selectedRunId])

  // Determine what output to display in right panel
  const displayOutput = isViewingLive ? liveOutput : (selectedRunOutput?.output || liveOutput || null)
  const selectedRun = runs.find(r => r.id === selectedRunId)

  return (
    <div className="h-full bg-slate-900 text-white flex flex-col">
      {/* Two-panel content */}
      <div className="flex-1 flex overflow-hidden">

        {/* Left panel: run history */}
        <div className="w-96 border-r border-slate-800 flex flex-col bg-slate-900/50">
          {/* Run button pinned at top */}
          <div className="px-3 py-3 border-b border-slate-800">
            <button
              onClick={runHealthCheck}
              disabled={isRunning}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-600/50 disabled:cursor-not-allowed rounded-lg text-sm font-medium text-white transition-colors"
            >
              {isRunning ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Running...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Run Health Check
                </>
              )}
            </button>
          </div>

          {/* Run history list */}
          <div className="flex-1 overflow-y-auto">
            {runs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-slate-500 px-4">
                <p className="text-sm font-medium">No runs yet</p>
                <p className="text-xs text-slate-600 mt-1">Click the button above to start</p>
              </div>
            ) : (
              <div className="divide-y divide-slate-800/50">
                {runs.map(run => (
                  <HealthCheckRunItem
                    key={run.id}
                    run={run}
                    isSelected={selectedRunId === run.id}
                    onClick={() => {
                      setSelectedRunId(run.id)
                      setError(null)
                      if (run.status !== 'running') {
                        fetchRunOutput(run.id)
                      }
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right panel: output display */}
        <div className="flex-1 bg-slate-900 flex flex-col">
          {!selectedRunId ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <div className="text-4xl mb-3 opacity-30">&#128203;</div>
                <p className="text-sm text-slate-500">Select a run to view output, or start a new health check</p>
                <p className="text-xs text-slate-600 mt-1">Checks AlloyDB, Pub/Sub, Cloud Run, tenant activity, alert pipeline health</p>
              </div>
            </div>
          ) : (
            <>
              {/* Run info header */}
              <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between flex-shrink-0">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-mono text-slate-400">{selectedRunId}</span>
                  {selectedRun && (
                    <div className="flex items-center gap-1.5">
                      <div className={`w-2 h-2 rounded-full ${
                        selectedRun.status === 'running' ? 'bg-blue-500 animate-pulse' :
                        selectedRun.status === 'completed' ? 'bg-emerald-500' :
                        selectedRun.status === 'timeout' ? 'bg-amber-500' : 'bg-red-500'
                      }`} />
                      <span className="text-xs text-slate-400">
                        {selectedRun.status === 'running' ? 'Running...' :
                         selectedRun.status === 'completed' ? 'Passed' :
                         selectedRun.status === 'timeout' ? 'Timeout' : 'Failed'}
                        {selectedRun.duration_seconds != null && ` in ${selectedRun.duration_seconds}s`}
                      </span>
                    </div>
                  )}
                </div>
                {selectedRun && selectedRun.status !== 'running' && (
                  <button
                    onClick={() => summarizeRun(selectedRunId)}
                    disabled={summarizing}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:bg-violet-600/50 disabled:cursor-not-allowed rounded-md text-xs font-medium text-white transition-colors"
                  >
                    {summarizing ? (
                      <>
                        <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Summarizing...
                      </>
                    ) : (
                      <>
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        Summarize
                      </>
                    )}
                  </button>
                )}
              </div>

              {/* Terminal output */}
              <div className="flex-1 overflow-auto p-4" ref={outputRef}>
                {error && (
                  <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 mb-3">
                    <p className="text-xs text-red-400">{error}</p>
                  </div>
                )}

                {summary && (
                  <div className="bg-violet-500/10 border border-violet-500/30 rounded-lg p-4 mb-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-violet-300">Summary for Google Chat</span>
                      <button
                        onClick={copySummary}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-violet-600 hover:bg-violet-500 rounded text-xs font-medium text-white transition-colors"
                      >
                        {copied ? (
                          <>
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                            Copied!
                          </>
                        ) : (
                          <>
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                            </svg>
                            Copy
                          </>
                        )}
                      </button>
                    </div>
                    <pre className="text-xs text-slate-200 whitespace-pre-wrap break-words font-sans leading-relaxed">{summary}</pre>
                  </div>
                )}

                {loadingOutput ? (
                  <div className="flex items-center justify-center h-full">
                    <div className="w-6 h-6 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
                  </div>
                ) : displayOutput ? (
                  <pre className="bg-slate-950 p-4 rounded-lg text-xs font-mono text-green-300/80 whitespace-pre-wrap break-words border border-slate-800">
                    {displayOutput}
                    {isViewingLive && <span className="animate-pulse text-green-400">|</span>}
                  </pre>
                ) : !isViewingLive && selectedRun?.status !== 'running' ? (
                  <div className="flex items-center justify-center h-full">
                    <p className="text-xs text-slate-500">No output available</p>
                  </div>
                ) : null}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * Navigation bar with page switching
 */
function NavBar() {
  const { incidents, isConnected } = useIncidents()
  const location = useLocation()
  const source = location.pathname === '/gchat' ? 'gchat' : 'gcp'
  const [showPrank, setShowPrank] = useState(false)

  const stats = useMemo(() => {
    const list = Object.values(incidents).filter(i => (i.source || 'gcp') === source)
    const selfHealing = list.filter(i =>
      (i.triage?.classification || i.escalation?.classification) === 'transient'
    ).length
    const processing = list.filter(i =>
      ['active', 'triaging', 'fixing', 'testing', 'reviewing', 'verifying'].includes(i.status)
    ).length
    const review = list.filter(i => {
      const cls = i.triage?.classification || i.escalation?.classification
      return i.status === 'escalated' || cls === 'needs_human' || cls === 'infra_issue'
    }).length
    const fixed = list.filter(i =>
      i.status === 'fixed' || i.status === 'pr_created'
    ).length
    return { total: list.length, processing, noAction: selfHealing, review, fixed }
  }, [incidents, source])

  return (
    <>
    <nav className="bg-slate-950/80 backdrop-blur-md border-b border-slate-800/60 px-5 py-2.5 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-pink-500/20 to-purple-600/20 ring-1 ring-pink-400/30 flex items-center justify-center shadow-lg shadow-pink-500/10">
            <img src="/oncall-logo.png" alt="On-Call Helper" className="w-8 h-8 object-contain" />
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-bold text-slate-100 tracking-tight">On-Call Helper</span>
            <span className="text-[10px] text-slate-500 font-medium">AI Incident Response</span>
          </div>
        </div>
        <div className="h-6 w-px bg-slate-800" />
        <div className="flex items-center gap-0.5">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `px-3.5 py-1.5 text-xs font-medium rounded-lg transition-all duration-150 ${
                isActive
                  ? 'bg-slate-800 text-white shadow-sm shadow-slate-900/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`
            }
          >
            GCP Incidents
          </NavLink>
          <NavLink
            to="/gchat"
            className={({ isActive }) =>
              `px-3.5 py-1.5 text-xs font-medium rounded-lg transition-all duration-150 ${
                isActive
                  ? 'bg-slate-800 text-white shadow-sm shadow-slate-900/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`
            }
          >
            Chat Cases
          </NavLink>
          <NavLink
            to="/checkout"
            className={({ isActive }) =>
              `px-3.5 py-1.5 text-xs font-medium rounded-lg transition-all duration-150 ${
                isActive
                  ? 'bg-slate-800 text-white shadow-sm shadow-slate-900/50'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`
            }
          >
            Health Check
          </NavLink>
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 tabular-nums"><span className="font-semibold text-slate-300">{stats.total}</span> Total</span>
          <span className="text-slate-600">|</span>
          <span className="text-slate-400 tabular-nums"><span className="font-semibold text-blue-400">{stats.processing}</span> Active</span>
          <span className="text-slate-400 tabular-nums"><span className="font-semibold text-slate-300">{stats.noAction}</span> No Action</span>
          <span className="text-slate-400 tabular-nums"><span className="font-semibold text-amber-400">{stats.review}</span> Review</span>
          <span className="text-slate-400 tabular-nums"><span className="font-semibold text-emerald-400">{stats.fixed}</span> Fixed</span>
        </div>
        <div className="h-4 w-px bg-slate-800" />
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500 animate-pulse' : 'bg-red-500'}`} />
          <span className="text-slate-500">{isConnected ? 'Live' : 'Offline'}</span>
        </div>
        <div className="h-4 w-px bg-slate-800" />
        <button
          onClick={() => setShowPrank(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg text-slate-400 hover:text-yellow-300 hover:bg-slate-800/40 transition-all duration-150"
          title="Switch to Light Mode"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="5"/>
            <line x1="12" y1="1" x2="12" y2="3"/>
            <line x1="12" y1="21" x2="12" y2="23"/>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
            <line x1="1" y1="12" x2="3" y2="12"/>
            <line x1="21" y1="12" x2="23" y2="12"/>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
          </svg>
          Light Mode
        </button>
      </div>
    </nav>
    {showPrank && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowPrank(false)}>
        <div
          className="relative bg-pink-500 text-white rounded-2xl p-10 shadow-2xl shadow-pink-500/40 max-w-md mx-4 text-center transform animate-bounce"
          onClick={e => e.stopPropagation()}
          style={{ animationIterationCount: 3, animationDuration: '0.5s' }}
        >
          <button onClick={() => setShowPrank(false)} className="absolute top-3 right-4 text-white/70 hover:text-white text-xl font-bold">&times;</button>
          <div className="text-5xl font-black mb-4 tracking-tight">SIKEEEEE!!!</div>
          <div className="text-lg font-semibold">Sorry Venki, the site doesn't support light mode.</div>
          <div className="mt-6">
            <button onClick={() => setShowPrank(false)} className="px-6 py-2 bg-white text-pink-600 font-bold rounded-lg hover:bg-pink-100 transition-colors">
              OK fine 😤
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  )
}

/**
 * App root with routing
 */
export default function App() {
  return (
    <div className="h-screen flex flex-col bg-slate-900">
      <NavBar />
      <div className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Dashboard source="gcp" />} />
          <Route path="/gchat" element={<Dashboard source="gchat" />} />
          <Route path="/checkout" element={<CheckoutPage />} />
        </Routes>
      </div>
    </div>
  )
}
