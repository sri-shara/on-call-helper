import React, { useState, useEffect, useMemo } from 'react'
import { IncidentProvider, useIncidents } from './context/IncidentContext'

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

  return (
    <div
      onClick={onClick}
      className={`group px-4 py-3 cursor-pointer transition-all border-l-2 ${
        isSelected
          ? 'bg-slate-800/80 border-l-blue-500'
          : 'border-l-transparent hover:bg-slate-800/40 hover:border-l-slate-600'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <SeverityDot severity={incident.severity} />
            <span className="text-sm font-medium text-slate-200 truncate">
              {incident.service || 'Unknown Service'}
            </span>
          </div>
          <p className="text-xs text-slate-400 truncate mb-2 pl-4">
            {incident.title}
          </p>
          <div className="flex items-center gap-2 pl-4">
            <span className="text-[10px] text-slate-600 font-mono">{incident.id}</span>
            <span className="text-[10px] text-slate-600">·</span>
            <span className="text-[10px] text-slate-500">{timeAgo(incident.createdAt)}</span>
          </div>
        </div>
        <StatusBadge
          status={incident.status}
          classification={classification}
          size="sm"
        />
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
      className={`p-3 rounded-lg border text-xs font-mono overflow-auto ${variants[variant]}`}
      style={{ maxHeight }}
    >
      <code className={textColors[variant]}>{code}</code>
    </pre>
  )
}

/**
 * Incident detail panel
 */
function IncidentDetail({ incidentId }) {
  const [details, setDetails] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!incidentId) {
      setDetails(null)
      return
    }

    setLoading(true)
    setError(null)

    fetch(`/api/incidents/${incidentId}`)
      .then(res => {
        if (!res.ok) throw new Error('Failed to load incident')
        return res.json()
      })
      .then(data => {
        setDetails(data)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [incidentId])

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
            <p className="text-xs text-slate-500 font-mono mt-0.5">{incident.id}</p>
          </div>
          <StatusBadge
            status={incident.status}
            classification={triage?.classification}
          />
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Analysis Section */}
        <section>
          <SectionHeader>Analysis</SectionHeader>

          {triage ? (
            <div className="space-y-4">
              <ClassificationBadge
                classification={triage.classification}
                confidence={triage.confidence}
              />

              {/* Root Cause */}
              <div>
                <h4 className="text-xs font-medium text-slate-400 mb-2">Root Cause</h4>
                <div className="bg-slate-800/50 rounded-lg p-3 text-sm text-slate-300 leading-relaxed">
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
                  <div className="bg-slate-800/50 rounded-lg p-3 text-sm text-slate-300">
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

              {/* GCP Queries Used */}
              {triage.gcp_queries?.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-slate-400 mb-2">GCP Queries Used</h4>
                  <ul className="space-y-1.5">
                    {triage.gcp_queries.map((query, i) => (
                      <li key={i} className="text-xs font-mono text-slate-500 bg-slate-800/50 p-2 rounded border border-slate-700/50">
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
            <div className={`rounded-lg border p-4 ${
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
                </>
              )}
              {triage.classification === 'needs_human' && (
                <>
                  <h4 className="text-red-400 font-medium mb-1">Human Review Required</h4>
                  <p className="text-sm text-red-200/70">
                    This issue is too complex for automated resolution. Manual investigation needed.
                  </p>
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
function MetricPill({ label, value, color }) {
  const colors = {
    default: 'text-slate-400',
    green: 'text-emerald-400',
    amber: 'text-amber-400',
    blue: 'text-blue-400',
    red: 'text-red-400',
  }
  return (
    <div className="flex items-center gap-1.5">
      <span className={`font-semibold tabular-nums ${colors[color] || colors.default}`}>{value}</span>
      <span className="text-slate-500 text-xs">{label}</span>
    </div>
  )
}

/**
 * Main dashboard
 */
function Dashboard() {
  const { incidents, isConnected, metrics } = useIncidents()
  const [selectedIncidentId, setSelectedIncidentId] = useState(null)
  const [filter, setFilter] = useState('all')

  const incidentList = useMemo(() => {
    let list = Object.values(incidents)

    if (filter !== 'all') {
      list = list.filter(inc => {
        const classification = inc.triage?.classification || inc.escalation?.classification
        if (filter === 'fixed') return inc.status === 'fixed' || inc.status === 'pr_created'
        if (filter === 'processing') return ['active', 'triaging', 'fixing', 'testing', 'reviewing'].includes(inc.status)
        if (filter === 'escalated') return inc.status === 'escalated'
        if (filter === 'self-healing') return classification === 'transient'
        return true
      })
    }

    return list.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
  }, [incidents, filter])

  // Calculate accurate metrics from incidents
  const calculatedMetrics = useMemo(() => {
    const list = Object.values(incidents)
    const selfHealing = list.filter(i =>
      (i.triage?.classification || i.escalation?.classification) === 'transient'
    ).length
    return { ...metrics, selfHealing }
  }, [incidents, metrics])

  return (
    <div className="h-screen bg-slate-900 text-white flex flex-col">
      {/* Header */}
      <header className="bg-slate-900 border-b border-slate-800 px-6 py-3 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
              <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <div>
              <h1 className="text-sm font-semibold text-slate-100">On-Call Helper</h1>
              <p className="text-[10px] text-slate-500">AI-Powered Incident Response</p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="flex items-center gap-4 text-sm">
              <MetricPill label="Total" value={metrics.total_incidents} />
              <MetricPill label="Fixed" value={metrics.auto_fixed} color="green" />
              <MetricPill label="Escalated" value={metrics.escalated} color="amber" />
              <MetricPill label="Processing" value={metrics.processing} color="blue" />
            </div>

            <div className="flex items-center gap-2 pl-4 border-l border-slate-800">
              <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500' : 'bg-red-500'}`} />
              <span className="text-xs text-slate-500">
                {isConnected ? 'Live' : 'Offline'}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <div className="w-80 border-r border-slate-800 flex flex-col bg-slate-900/50">
          {/* Filter tabs */}
          <div className="px-4 py-3 border-b border-slate-800">
            <div className="flex items-center gap-1 p-1 bg-slate-800/50 rounded-lg">
              {[
                { id: 'all', label: 'All' },
                { id: 'processing', label: 'Active' },
                { id: 'fixed', label: 'Fixed' },
                { id: 'escalated', label: 'Review' },
              ].map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setFilter(tab.id)}
                  className={`flex-1 px-2 py-1 text-xs font-medium rounded-md transition-colors ${
                    filter === tab.id
                      ? 'bg-slate-700 text-slate-200'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto">
            {incidentList.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-slate-500">
                <p className="text-sm">No incidents</p>
                <p className="text-xs text-slate-600 mt-1">Monitoring GCP logs...</p>
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
 * App root
 */
export default function App() {
  return (
    <IncidentProvider>
      <Dashboard />
    </IncidentProvider>
  )
}
