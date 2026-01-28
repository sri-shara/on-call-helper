import React from 'react'

/**
 * Displays sandbox test status and results.
 */
export function SandboxStatus({ incident }) {
  if (!incident) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white mb-4">Sandbox Testing</h2>
        <div className="text-center py-4 text-gray-400 text-sm">
          Select an incident to view test status
        </div>
      </div>
    )
  }

  const { status, testResult } = incident

  // Determine test phase
  const getPhase = () => {
    if (status === 'testing') return 'running'
    if (status === 'tested' && testResult?.status === 'passed') return 'passed'
    if (status === 'tested' && testResult?.status !== 'passed') return 'failed'
    if (['pr_created', 'verifying', 'verified', 'fixed'].includes(status)) return 'passed'
    if (status === 'escalated') return 'failed'
    return 'pending'
  }

  const phase = getPhase()

  const phases = [
    { id: 'creating', label: 'Creating Sandbox', icon: '>' },
    { id: 'running', label: 'Running Tests', icon: '*' },
    { id: 'complete', label: 'Complete', icon: '+' },
  ]

  const getPhaseStatus = (phaseId) => {
    if (phase === 'pending') return 'pending'
    if (phase === 'running') {
      if (phaseId === 'creating') return 'complete'
      if (phaseId === 'running') return 'active'
      return 'pending'
    }
    if (phase === 'passed' || phase === 'failed') return 'complete'
    return 'pending'
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-white mb-4">Sandbox Testing</h2>

      {/* Progress Steps */}
      <div className="flex items-center justify-between mb-6">
        {phases.map((p, i) => (
          <React.Fragment key={p.id}>
            <div className="flex flex-col items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-mono ${
                  getPhaseStatus(p.id) === 'complete'
                    ? 'bg-green-600 text-white'
                    : getPhaseStatus(p.id) === 'active'
                    ? 'bg-blue-600 text-white animate-pulse'
                    : 'bg-gray-700 text-gray-400'
                }`}
              >
                {p.icon}
              </div>
              <span className="text-xs text-gray-400 mt-2">{p.label}</span>
            </div>
            {i < phases.length - 1 && (
              <div
                className={`flex-1 h-0.5 mx-2 ${
                  getPhaseStatus(phases[i + 1].id) !== 'pending'
                    ? 'bg-green-600'
                    : 'bg-gray-700'
                }`}
              />
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Test Results */}
      {testResult && (
        <div className="space-y-3">
          {/* Status Badge */}
          <div className="flex items-center justify-center">
            <div
              className={`px-4 py-2 rounded-full text-sm font-medium ${
                testResult.status === 'passed'
                  ? 'bg-green-500/20 text-green-400'
                  : 'bg-red-500/20 text-red-400'
              }`}
            >
              {testResult.status === 'passed' ? 'All Tests Passed' : 'Tests Failed'}
            </div>
          </div>

          {/* Test Counts */}
          <div className="grid grid-cols-3 gap-4 mt-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-white">
                {testResult.testsRun || 0}
              </div>
              <div className="text-xs text-gray-400">Total</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-400">
                {testResult.testsPassed || 0}
              </div>
              <div className="text-xs text-gray-400">Passed</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-red-400">
                {(testResult.testsRun || 0) - (testResult.testsPassed || 0)}
              </div>
              <div className="text-xs text-gray-400">Failed</div>
            </div>
          </div>

          {/* Progress Bar */}
          {testResult.testsRun > 0 && (
            <div className="mt-4">
              <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 transition-all duration-500"
                  style={{
                    width: `${(testResult.testsPassed / testResult.testsRun) * 100}%`,
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Pending State */}
      {!testResult && phase !== 'running' && (
        <div className="text-center py-4 text-gray-400 text-sm">
          {phase === 'pending'
            ? 'Waiting for sandbox tests to start...'
            : 'Test results not available'}
        </div>
      )}

      {/* Running State */}
      {phase === 'running' && (
        <div className="text-center py-4">
          <div className="inline-flex items-center gap-2 text-blue-400">
            <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
            <span>Running tests...</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default SandboxStatus
