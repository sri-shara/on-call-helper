import { useEffect, useRef, useState, useCallback } from 'react'

/**
 * WebSocket hook with auto-reconnect.
 *
 * @param {string} url - WebSocket URL
 * @param {Object} options - Hook options
 * @param {function} options.onMessage - Message handler
 * @param {function} options.onConnect - Connection handler
 * @param {function} options.onDisconnect - Disconnection handler
 * @param {number} options.reconnectInterval - Reconnect interval in ms (default: 3000)
 * @param {number} options.maxRetries - Max reconnection attempts (default: 10)
 */
export function useWebSocket(url, options = {}) {
  const {
    onMessage,
    onConnect,
    onDisconnect,
    reconnectInterval = 3000,
    maxRetries = 10,
  } = options

  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)
  const [connectionError, setConnectionError] = useState(null)

  const wsRef = useRef(null)
  const retriesRef = useRef(0)
  const reconnectTimeoutRef = useRef(null)
  const pingIntervalRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return
    }

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        console.log('WebSocket connected')
        setIsConnected(true)
        setConnectionError(null)
        retriesRef.current = 0

        // Start ping interval
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }))
          }
        }, 30000)

        if (onConnect) {
          onConnect()
        }
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          setLastMessage(data)

          if (onMessage) {
            onMessage(data)
          }
        } catch (e) {
          console.error('Failed to parse WebSocket message:', e)
        }
      }

      ws.onclose = (event) => {
        console.log('WebSocket disconnected:', event.code, event.reason)
        setIsConnected(false)
        wsRef.current = null

        // Clear ping interval
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current)
          pingIntervalRef.current = null
        }

        if (onDisconnect) {
          onDisconnect()
        }

        // Attempt reconnect if not a clean close
        if (event.code !== 1000 && retriesRef.current < maxRetries) {
          retriesRef.current++
          console.log(`Reconnecting... attempt ${retriesRef.current}/${maxRetries}`)

          reconnectTimeoutRef.current = setTimeout(() => {
            connect()
          }, reconnectInterval)
        } else if (retriesRef.current >= maxRetries) {
          setConnectionError('Max reconnection attempts reached')
        }
      }

      ws.onerror = (error) => {
        console.error('WebSocket error:', error)
        setConnectionError('Connection error')
      }

    } catch (error) {
      console.error('Failed to create WebSocket:', error)
      setConnectionError(error.message)
    }
  }, [url, onMessage, onConnect, onDisconnect, reconnectInterval, maxRetries])

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }

    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current)
      pingIntervalRef.current = null
    }

    if (wsRef.current) {
      wsRef.current.close(1000, 'User disconnect')
      wsRef.current = null
    }

    retriesRef.current = maxRetries // Prevent auto-reconnect
    setIsConnected(false)
  }, [maxRetries])

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const message = typeof data === 'string' ? data : JSON.stringify(data)
      wsRef.current.send(message)
      return true
    }
    return false
  }, [])

  const subscribe = useCallback((incidentId) => {
    return send({ type: 'subscribe', incident_id: incidentId })
  }, [send])

  const unsubscribe = useCallback((incidentId) => {
    return send({ type: 'unsubscribe', incident_id: incidentId })
  }, [send])

  // Connect on mount
  useEffect(() => {
    connect()

    return () => {
      disconnect()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    isConnected,
    lastMessage,
    connectionError,
    send,
    subscribe,
    unsubscribe,
    connect,
    disconnect,
  }
}

export default useWebSocket
