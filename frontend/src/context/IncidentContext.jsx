import React, { createContext, useContext, useReducer, useCallback, useEffect } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'

// Initial state
const initialState = {
  incidents: {},
  events: [],
  metrics: {
    total_incidents: 0,
    processing: 0,
    no_action_needed: 0,
    review_needed: 0,
    pr_raised: 0,
    mttr_seconds: null,
  },
  isConnected: false,
  clientId: null,
}

// Action types
const ActionTypes = {
  SET_CONNECTED: 'SET_CONNECTED',
  SET_CLIENT_ID: 'SET_CLIENT_ID',
  SET_METRICS: 'SET_METRICS',
  ADD_INCIDENT: 'ADD_INCIDENT',
  UPDATE_INCIDENT: 'UPDATE_INCIDENT',
  ADD_EVENT: 'ADD_EVENT',
  CLEAR_EVENTS: 'CLEAR_EVENTS',
}

// Reducer
function incidentReducer(state, action) {
  switch (action.type) {
    case ActionTypes.SET_CONNECTED:
      return { ...state, isConnected: action.payload }

    case ActionTypes.SET_CLIENT_ID:
      return { ...state, clientId: action.payload }

    case ActionTypes.SET_METRICS:
      return { ...state, metrics: { ...state.metrics, ...action.payload } }

    case ActionTypes.ADD_INCIDENT:
      return {
        ...state,
        incidents: {
          ...state.incidents,
          [action.payload.id]: action.payload,
        },
      }

    case ActionTypes.UPDATE_INCIDENT:
      const existing = state.incidents[action.payload.id] || {}
      return {
        ...state,
        incidents: {
          ...state.incidents,
          [action.payload.id]: { ...existing, ...action.payload },
        },
      }

    case ActionTypes.ADD_EVENT:
      return {
        ...state,
        events: [action.payload, ...state.events].slice(0, 100), // Keep last 100 events
      }

    case ActionTypes.CLEAR_EVENTS:
      return { ...state, events: [] }

    default:
      return state
  }
}

// Context
const IncidentContext = createContext(null)

// Provider component
export function IncidentProvider({ children }) {
  const [state, dispatch] = useReducer(incidentReducer, initialState)

  // Handle WebSocket messages
  const handleMessage = useCallback((message) => {
    const { type, data, timestamp } = message

    // Add to events feed
    dispatch({
      type: ActionTypes.ADD_EVENT,
      payload: { type, data, timestamp, id: `${type}-${Date.now()}` },
    })

    switch (type) {
      case 'welcome':
        dispatch({ type: ActionTypes.SET_CLIENT_ID, payload: data.client_id })
        if (data.metrics) {
          dispatch({ type: ActionTypes.SET_METRICS, payload: data.metrics })
        }
        break

      case 'metrics_update':
        if (data.metrics) {
          dispatch({ type: ActionTypes.SET_METRICS, payload: data.metrics })
        }
        break

      case 'incident_created':
        dispatch({
          type: ActionTypes.ADD_INCIDENT,
          payload: {
            id: data.incident_id,
            title: data.title,
            service: data.service,
            severity: data.severity,
            status: 'active',
            createdAt: timestamp,
            source: data.source || 'gcp',
            stages: [],
            occurrenceCount: data.occurrence_count || 1,
          },
        })
        break

      case 'triage_started':
      case 'triage_complete':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: type === 'triage_complete' ? 'triaged' : 'triaging',
            triage: type === 'triage_complete' ? {
              classification: data.classification,
              confidence: data.confidence,
              rootCause: data.root_cause,
            } : undefined,
          },
        })
        break

      case 'fix_started':
      case 'fix_generated':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: type === 'fix_generated' ? 'fixed_generated' : 'fixing',
            fix: type === 'fix_generated' ? {
              filePath: data.file_path,
              diffSummary: data.diff_summary,
              iteration: data.iteration,
            } : undefined,
          },
        })
        break

      case 'code_diff':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            codeDiff: {
              filePath: data.file_path,
              originalCode: data.original_code,
              fixedCode: data.fixed_code,
            },
          },
        })
        break

      case 'review_started':
      case 'review_complete':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: type === 'review_complete' ? 'reviewed' : 'reviewing',
          },
        })
        break

      case 'sandbox_started':
      case 'sandbox_complete':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: type === 'sandbox_complete' ? 'tested' : 'testing',
            testResult: type === 'sandbox_complete' ? {
              status: data.status,
              testsRun: data.tests_run,
              testsPassed: data.tests_passed,
            } : undefined,
          },
        })
        break

      case 'pr_created':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: 'pr_created',
            pr: {
              number: data.pr_number,
              url: data.pr_url,
            },
          },
        })
        break

      case 'verification_started':
      case 'verification_complete':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: type === 'verification_complete' ? 'verified' : 'verifying',
            verification: type === 'verification_complete' ? {
              status: data.status,
              errorsBefore: data.errors_before,
              errorsAfter: data.errors_after,
            } : undefined,
          },
        })
        break

      case 'incident_resolved':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: 'fixed',
            resolvedAt: timestamp,
          },
        })
        break

      case 'incident_escalated':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            status: 'escalated',
            escalation: {
              reason: data.reason,
              classification: data.classification,
            },
          },
        })
        break

      case 'agent_thinking':
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            thinking: {
              agent: data.agent,
              message: data.message,
              timestamp,
            },
          },
        })
        break

      case 'incident_updated':
        // Handle occurrence count updates from aggregation
        dispatch({
          type: ActionTypes.UPDATE_INCIDENT,
          payload: {
            id: data.incident_id,
            occurrenceCount: data.occurrence_count,
          },
        })
        break

      default:
        // Unknown event type - just log it
        console.log('Unknown event type:', type, data)
    }
  }, [])

  // Determine WebSocket URL
  const wsUrl = typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
    : 'ws://localhost:8000/ws'

  // WebSocket connection
  const {
    isConnected,
    send,
    subscribe,
    unsubscribe,
  } = useWebSocket(wsUrl, {
    onMessage: handleMessage,
    onConnect: () => dispatch({ type: ActionTypes.SET_CONNECTED, payload: true }),
    onDisconnect: () => dispatch({ type: ActionTypes.SET_CONNECTED, payload: false }),
  })

  // Fetch initial incidents (both GCP and GChat collections)
  const fetchIncidents = useCallback(async () => {
    const addIncidents = (data) => {
      if (data.incidents && data.incidents.length > 0) {
        data.incidents.forEach((incident) => {
          dispatch({
            type: ActionTypes.ADD_INCIDENT,
            payload: {
              id: incident.id,
              title: incident.title,
              service: incident.service_name || incident.service || 'unknown',
              severity: incident.severity,
              status: incident.status,
              createdAt: incident.created_at,
              source: incident.source || 'gcp',
              gchatMetadata: incident.gchat_metadata || null,
              triage: incident.triage_classification
                ? { classification: incident.triage_classification }
                : undefined,
              occurrenceCount: incident.occurrence_count || 1,
            },
          })
        })
      }
    }

    try {
      // Fetch both sources in parallel
      const [gcpRes, gchatRes] = await Promise.all([
        fetch('/api/incidents'),
        fetch('/api/incidents?source=gchat'),
      ])
      if (gcpRes.ok) addIncidents(await gcpRes.json())
      if (gchatRes.ok) addIncidents(await gchatRes.json())
    } catch (error) {
      console.error('Failed to fetch incidents:', error)
    }
  }, [])

  // Fetch incidents on mount
  useEffect(() => {
    fetchIncidents()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  
  // Also fetch when WebSocket connects to get latest updates
  useEffect(() => {
    if (isConnected) {
      fetchIncidents()
    }
  }, [isConnected]) // eslint-disable-line react-hooks/exhaustive-deps

  // Update an incident in the local state
  const updateIncident = useCallback((id, updates) => {
    dispatch({
      type: ActionTypes.UPDATE_INCIDENT,
      payload: { id, ...updates },
    })
  }, [])

  // Refresh metrics from the server
  const refreshMetrics = useCallback(async () => {
    try {
      const response = await fetch('/api/metrics')
      if (response.ok) {
        const metrics = await response.json()
        dispatch({ type: ActionTypes.SET_METRICS, payload: metrics })
      }
    } catch (error) {
      console.error('Failed to refresh metrics:', error)
    }
  }, [])

  // Context value
  const value = {
    ...state,
    isConnected,
    send,
    subscribe,
    unsubscribe,
    fetchIncidents,
    updateIncident,
    refreshMetrics,
    clearEvents: () => dispatch({ type: ActionTypes.CLEAR_EVENTS }),
  }

  return (
    <IncidentContext.Provider value={value}>
      {children}
    </IncidentContext.Provider>
  )
}

// Hook to use incident context
export function useIncidents() {
  const context = useContext(IncidentContext)
  if (!context) {
    throw new Error('useIncidents must be used within an IncidentProvider')
  }
  return context
}

export default IncidentContext
