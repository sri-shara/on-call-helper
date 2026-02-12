import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            // Suppress proxy errors - backend might not be running
            console.log('[Proxy] Backend connection error (backend may be down):', err.message)
          })
        },
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            // Suppress WebSocket proxy errors - backend might not be running
            // The frontend WebSocket hook will handle reconnection
            console.log('[WebSocket Proxy] Connection error (backend may be down):', err.message)
          })
          proxy.on('proxyReqWs', (proxyReq, req, socket) => {
            // Handle WebSocket upgrade errors gracefully
            socket.on('error', (err) => {
              // Suppress socket errors - frontend will handle reconnection
              console.log('[WebSocket Proxy] Socket error:', err.message)
            })
          })
        },
      },
    },
  },
})
