/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Custom colors for status indicators
        'status-active': '#f59e0b',
        'status-fixing': '#3b82f6',
        'status-fixed': '#10b981',
        'status-escalated': '#ef4444',
      },
    },
  },
  plugins: [],
}
