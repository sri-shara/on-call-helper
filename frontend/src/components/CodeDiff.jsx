import React, { useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'

/**
 * Displays a code diff with syntax highlighting.
 */
export function CodeDiff({ incident }) {
  const [viewMode, setViewMode] = useState('split') // 'split' or 'unified'

  if (!incident?.codeDiff) {
    return (
      <div className="bg-gray-800 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white mb-4">Code Diff</h2>
        <div className="text-center py-8 text-gray-400 text-sm">
          No code changes to display
        </div>
      </div>
    )
  }

  const { filePath, originalCode, fixedCode } = incident.codeDiff

  // Determine language from file extension
  const getLanguage = (path) => {
    if (!path) return 'text'
    const ext = path.split('.').pop()?.toLowerCase()
    const languageMap = {
      go: 'go',
      py: 'python',
      js: 'javascript',
      jsx: 'jsx',
      ts: 'typescript',
      tsx: 'tsx',
      rs: 'rust',
      java: 'java',
      rb: 'ruby',
      yml: 'yaml',
      yaml: 'yaml',
      json: 'json',
      md: 'markdown',
    }
    return languageMap[ext] || 'text'
  }

  const language = getLanguage(filePath)

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Code Diff</h2>
          <p className="text-sm text-gray-400 font-mono mt-1">{filePath}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setViewMode('split')}
            className={`px-3 py-1 text-xs rounded transition-colors ${
              viewMode === 'split'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Split
          </button>
          <button
            onClick={() => setViewMode('unified')}
            className={`px-3 py-1 text-xs rounded transition-colors ${
              viewMode === 'unified'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            Unified
          </button>
        </div>
      </div>

      {viewMode === 'split' ? (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <div className="text-xs text-red-400 mb-2 flex items-center gap-1">
              <span className="w-4 h-4 rounded bg-red-500/20 flex items-center justify-center">-</span>
              Original
            </div>
            <div className="rounded overflow-hidden border border-red-500/30">
              <SyntaxHighlighter
                language={language}
                style={vscDarkPlus}
                customStyle={{
                  margin: 0,
                  padding: '1rem',
                  fontSize: '0.75rem',
                  maxHeight: '300px',
                  overflow: 'auto',
                }}
                wrapLines={true}
                wrapLongLines={true}
              >
                {originalCode || ''}
              </SyntaxHighlighter>
            </div>
          </div>
          <div>
            <div className="text-xs text-green-400 mb-2 flex items-center gap-1">
              <span className="w-4 h-4 rounded bg-green-500/20 flex items-center justify-center">+</span>
              Fixed
            </div>
            <div className="rounded overflow-hidden border border-green-500/30">
              <SyntaxHighlighter
                language={language}
                style={vscDarkPlus}
                customStyle={{
                  margin: 0,
                  padding: '1rem',
                  fontSize: '0.75rem',
                  maxHeight: '300px',
                  overflow: 'auto',
                }}
                wrapLines={true}
                wrapLongLines={true}
              >
                {fixedCode || ''}
              </SyntaxHighlighter>
            </div>
          </div>
        </div>
      ) : (
        <UnifiedDiff
          originalCode={originalCode}
          fixedCode={fixedCode}
          language={language}
        />
      )}

      {incident.fix?.diffSummary && (
        <div className="mt-4 p-3 bg-gray-700 rounded text-sm text-gray-300">
          <span className="text-gray-400">Summary: </span>
          {incident.fix.diffSummary}
        </div>
      )}
    </div>
  )
}

function UnifiedDiff({ originalCode, fixedCode, language }) {
  // Simple line-by-line diff
  const originalLines = (originalCode || '').split('\n')
  const fixedLines = (fixedCode || '').split('\n')

  // Build unified view
  const diffLines = []

  // Very simple diff - show removed lines then added lines
  // In production, you'd want a proper diff algorithm
  originalLines.forEach((line, i) => {
    diffLines.push({ type: 'removed', line, lineNum: i + 1 })
  })
  fixedLines.forEach((line, i) => {
    diffLines.push({ type: 'added', line, lineNum: i + 1 })
  })

  return (
    <div className="rounded overflow-hidden border border-gray-600 font-mono text-xs">
      <div className="max-h-96 overflow-auto">
        {diffLines.map((diff, i) => (
          <div
            key={i}
            className={`flex ${
              diff.type === 'removed'
                ? 'bg-red-900/30 text-red-200'
                : 'bg-green-900/30 text-green-200'
            }`}
          >
            <span className="w-8 text-center py-1 text-gray-500 border-r border-gray-700 flex-shrink-0">
              {diff.lineNum}
            </span>
            <span className="w-6 text-center py-1 border-r border-gray-700 flex-shrink-0">
              {diff.type === 'removed' ? '-' : '+'}
            </span>
            <pre className="py-1 px-2 flex-1 overflow-x-auto">
              {diff.line || ' '}
            </pre>
          </div>
        ))}
      </div>
    </div>
  )
}

export default CodeDiff
