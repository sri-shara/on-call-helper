import React from 'react'

/**
 * Displays production verification status.
 */
export function VerificationStatus({ incident }) {
  if (!incident) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white mb-4">Production Verification</h2>
        <div className="text-center py-4 text-gray-400 text-sm">
          Select an incident to view verification status
        </div>
      </div>
    )
  }

  const { status, verification, pr } = incident

  // Determine verification phase
  const getPhase = () => {
    if (status === 'verifying') return 'monitoring'
    if (status === 'verified' || verification?.status) return 'complete'
    if (status === 'fixed') return 'complete'
    if (status === 'pr_created') return 'pending'
    return 'not_started'
  }

  const phase = getPhase()

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-white mb-4">Production Verification</h2>

      {/* PR Link */}
      {pr?.url && (
        <div className="mb-4 p-3 bg-gray-700 rounded flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-400">Pull Request</p>
            <a
              href={pr.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:text-blue-300 font-mono"
            >
              PR #{pr.number}
            </a>
          </div>
          <div className="text-xs text-gray-500">
            {status === 'pr_created' ? 'Awaiting merge' : 'Merged'}
          </div>
        </div>
      )}

      {/* Verification Status */}
      {phase === 'not_started' && (
        <div className="text-center py-8 text-gray-400 text-sm">
          Verification will begin after PR is merged
        </div>
      )}

      {phase === 'pending' && (
        <div className="text-center py-8">
          <div className="text-gray-400 text-sm">Waiting for PR merge...</div>
          <p className="text-xs text-gray-500 mt-2">
            Production verification starts automatically after merge
          </p>
        </div>
      )}

      {phase === 'monitoring' && (
        <div className="space-y-4">
          <div className="text-center">
            <div className="inline-flex items-center gap-2 text-yellow-400">
              <div className="w-4 h-4 border-2 border-yellow-400 border-t-transparent rounded-full animate-spin" />
              <span>Monitoring production...</span>
            </div>
          </div>

          <div className="p-4 bg-gray-700 rounded">
            <p className="text-sm text-gray-300 text-center">
              Watching Cloud Logging for error recurrence
            </p>
            <p className="text-xs text-gray-500 text-center mt-2">
              Default monitoring period: 2 hours
            </p>
          </div>

          {/* Monitoring animation */}
          <div className="flex justify-center gap-1">
            {[0, 1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="w-2 h-8 bg-yellow-500/50 rounded-full animate-pulse"
                style={{ animationDelay: `${i * 0.1}s` }}
              />
            ))}
          </div>
        </div>
      )}

      {phase === 'complete' && verification && (
        <div className="space-y-4">
          {/* Status Badge */}
          <div className="flex items-center justify-center">
            <div
              className={`px-4 py-2 rounded-full text-sm font-medium ${
                verification.status === 'success'
                  ? 'bg-green-500/20 text-green-400'
                  : verification.status === 'partial'
                  ? 'bg-yellow-500/20 text-yellow-400'
                  : 'bg-red-500/20 text-red-400'
              }`}
            >
              {verification.status === 'success'
                ? 'Error Resolved'
                : verification.status === 'partial'
                ? 'Partially Resolved'
                : 'Error Persists'}
            </div>
          </div>

          {/* Error Counts */}
          <div className="grid grid-cols-2 gap-4">
            <div className="p-4 bg-gray-700 rounded text-center">
              <div className="text-2xl font-bold text-red-400">
                {verification.errorsBefore || 0}
              </div>
              <div className="text-xs text-gray-400">Errors Before</div>
            </div>
            <div className="p-4 bg-gray-700 rounded text-center">
              <div
                className={`text-2xl font-bold ${
                  (verification.errorsAfter || 0) === 0
                    ? 'text-green-400'
                    : (verification.errorsAfter || 0) < (verification.errorsBefore || 0)
                    ? 'text-yellow-400'
                    : 'text-red-400'
                }`}
              >
                {verification.errorsAfter || 0}
              </div>
              <div className="text-xs text-gray-400">Errors After</div>
            </div>
          </div>

          {/* Reduction percentage */}
          {verification.errorsBefore > 0 && (
            <div className="text-center">
              <span className="text-sm text-gray-400">
                {Math.round(
                  ((verification.errorsBefore - verification.errorsAfter) /
                    verification.errorsBefore) *
                    100
                )}
                % reduction
              </span>
            </div>
          )}
        </div>
      )}

      {/* Fixed without verification data */}
      {phase === 'complete' && !verification && status === 'fixed' && (
        <div className="text-center py-4">
          <div className="inline-flex items-center gap-2 text-green-400">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            <span>Incident Resolved</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default VerificationStatus
